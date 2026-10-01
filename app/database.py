from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from app.config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Album(Base):
    __tablename__ = "albums"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    slug = Column(String(80), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    is_public = Column(Boolean, default=True)
    cover_photo_id = Column(Integer, nullable=True)
    # Framing for the album card thumbnail (independent of per-photo gallery view)
    cover_zoom = Column(Float, default=1.0)
    cover_pan_x = Column(Float, default=0.0)
    cover_pan_y = Column(Float, default=0.0)
    # Bumped when the baked cover JPEG is rewritten
    cover_version = Column(Integer, default=0)
    # Lower numbers appear first on the home page. New albums take the front.
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    photos = relationship(
        "Photo",
        back_populates="album",
        cascade="all, delete-orphan",
        order_by="Photo.sort_order, Photo.created_at",
    )


class Photo(Base):
    __tablename__ = "photos"

    id = Column(Integer, primary_key=True, index=True)
    album_id = Column(Integer, ForeignKey("albums.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    original_name = Column(String(255), nullable=False)
    content_type = Column(String(100), default="image/jpeg")
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    caption = Column(String(500), default="")
    sort_order = Column(Integer, default=0)
    # 1.0 = fit-to-frame; higher values start zoomed in when the photo is shown
    default_zoom = Column(Float, default=1.0)
    # Pan as fraction of max travel at that zoom (-1..1). 0 = centered.
    default_pan_x = Column(Float, default=0.0)
    default_pan_y = Column(Float, default=0.0)
    # Bumped when the file bytes change (e.g. rotate) so browsers skip stale caches
    file_version = Column(Integer, default=0)
    # Extracted FITS header JSON ({name, cards}). The FIT image is not stored.
    fits_header = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    album = relationship("Album", back_populates="photos")


DEFAULT_DESCRIPTION_PROMPT = (
    'Write a album description for an astrophotography album titled "{title}".\n\n'
    "Photo filenames in the album:\n{photos}\n\n"
    "Mention the subject if you can infer it from the title or filenames. "
    "You may use light Markdown (*emphasis*, **bold**, short lists, links). "
    "Keep it suitable for a public gallery page. Output only the description.\n\n"
    "Description should containt distance and size information."
)

# Older defaults we replace when upgrading existing installs
_LEGACY_DESCRIPTION_PROMPTS = {
    (
        'Write a short album description (2–4 sentences) for an astrophotography '
        'album titled "{title}".\n\n'
        "Photo filenames in the album:\n{photos}\n\n"
        "Mention the subject if you can infer it from the title or filenames. "
        "You may use light Markdown (*emphasis*, **bold**, short lists, links). "
        "Keep it suitable for a public gallery page. Output only the description."
    ),
}


class SiteSettings(Base):
    """Singleton row (id=1) for site-wide copy and branding."""

    __tablename__ = "site_settings"

    id = Column(Integer, primary_key=True)
    site_name = Column(String(120), default="Astro Photo")
    home_title = Column(String(200), default="Photo albums")
    home_tagline = Column(
        String(500), default="Upload photos, create an album, share a link."
    )
    footer_text = Column(String(200), default="share the night sky")
    # Template for Grok "Generate with Grok" — placeholders: {title}, {photos}
    description_prompt = Column(Text, default=DEFAULT_DESCRIPTION_PROMPT)


DEFAULT_SETTINGS = {
    "site_name": "Astro Photo",
    "home_title": "Photo albums",
    "home_tagline": "Upload photos, create an album, share a link.",
    "footer_text": "share the night sky",
    "description_prompt": DEFAULT_DESCRIPTION_PROMPT,
}


def get_or_create_settings(db) -> SiteSettings:
    row = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if row:
        current = (row.description_prompt or "").strip()
        if not current or current in _LEGACY_DESCRIPTION_PROMPTS:
            row.description_prompt = DEFAULT_DESCRIPTION_PROMPT
            db.commit()
            db.refresh(row)
        return row
    row = SiteSettings(id=1, **DEFAULT_SETTINGS)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _migrate_sqlite() -> None:
    """Add columns introduced after first deploy (SQLite has no ALTER via create_all)."""
    with engine.begin() as conn:
        photo_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(photos)")).fetchall()
        }
        if "default_zoom" not in photo_cols:
            conn.execute(
                text(
                    "ALTER TABLE photos ADD COLUMN default_zoom FLOAT NOT NULL DEFAULT 1.0"
                )
            )
        if "file_version" not in photo_cols:
            conn.execute(
                text(
                    "ALTER TABLE photos ADD COLUMN file_version INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "default_pan_x" not in photo_cols:
            conn.execute(
                text(
                    "ALTER TABLE photos ADD COLUMN default_pan_x FLOAT NOT NULL DEFAULT 0.0"
                )
            )
        if "default_pan_y" not in photo_cols:
            conn.execute(
                text(
                    "ALTER TABLE photos ADD COLUMN default_pan_y FLOAT NOT NULL DEFAULT 0.0"
                )
            )
        if "fits_header" not in photo_cols:
            conn.execute(text("ALTER TABLE photos ADD COLUMN fits_header TEXT"))

        album_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(albums)")).fetchall()
        }
        if "cover_zoom" not in album_cols:
            conn.execute(
                text(
                    "ALTER TABLE albums ADD COLUMN cover_zoom FLOAT NOT NULL DEFAULT 1.0"
                )
            )
        if "cover_pan_x" not in album_cols:
            conn.execute(
                text(
                    "ALTER TABLE albums ADD COLUMN cover_pan_x FLOAT NOT NULL DEFAULT 0.0"
                )
            )
        if "cover_pan_y" not in album_cols:
            conn.execute(
                text(
                    "ALTER TABLE albums ADD COLUMN cover_pan_y FLOAT NOT NULL DEFAULT 0.0"
                )
            )
        if "cover_version" not in album_cols:
            conn.execute(
                text(
                    "ALTER TABLE albums ADD COLUMN cover_version INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "sort_order" not in album_cols:
            conn.execute(
                text(
                    "ALTER TABLE albums ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
                )
            )
            # Keep the previous newest-first listing until someone reorders.
            conn.execute(
                text(
                    """
                    UPDATE albums
                    SET sort_order = (
                        SELECT COUNT(*)
                        FROM albums AS newer
                        WHERE newer.created_at > albums.created_at
                           OR (
                                newer.created_at = albums.created_at
                                AND newer.id > albums.id
                           )
                    )
                    """
                )
            )

        settings_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(site_settings)")).fetchall()
        }
        if settings_cols and "description_prompt" not in settings_cols:
            # SQLite can't bind a long default easily — add nullable then backfill
            conn.execute(
                text("ALTER TABLE site_settings ADD COLUMN description_prompt TEXT")
            )
            conn.execute(
                text(
                    "UPDATE site_settings SET description_prompt = :p "
                    "WHERE description_prompt IS NULL OR description_prompt = ''"
                ),
                {"p": DEFAULT_DESCRIPTION_PROMPT},
            )


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    # Ensure singleton settings row exists
    db = SessionLocal()
    try:
        get_or_create_settings(db)
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
