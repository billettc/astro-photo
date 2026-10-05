import io
import uuid
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageOps

from app.config import (
    ALLOWED_EXTENSIONS,
    COVER_DIR,
    COVER_HEIGHT,
    COVER_WIDTH,
    FULL_JPEG_QUALITY,
    FULL_SIZE,
    SOLVE_DIR,
    THUMB_DIR,
    THUMB_SIZE,
    UPLOAD_DIR,
)


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    SOLVE_DIR.mkdir(parents=True, exist_ok=True)


def is_allowed_filename(name: str) -> bool:
    return Path(name).suffix.lower() in ALLOWED_EXTENSIONS


def new_filename(original_name: str) -> str:
    ext = Path(original_name).suffix.lower() or ".jpg"
    if ext == ".jpeg":
        ext = ".jpg"
    return f"{uuid.uuid4().hex}{ext}"


def album_upload_dir(album_slug: str) -> Path:
    path = UPLOAD_DIR / album_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def album_thumb_dir(album_slug: str) -> Path:
    path = THUMB_DIR / album_slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def process_image(
    data: bytes, album_slug: str, original_name: str
) -> Tuple[str, int, int, int]:
    """Save original + thumbnail. Returns (filename, width, height, size_bytes)."""
    ensure_dirs()
    filename = new_filename(original_name)
    upload_path = album_upload_dir(album_slug) / filename
    thumb_path = album_thumb_dir(album_slug) / filename

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)

    # Convert palette/CMYK/etc to RGB for consistent output
    if img.mode in ("RGBA", "LA"):
        background = Image.new("RGB", img.size, (12, 14, 22))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Keep a high-res master so in-viewer zoom / 1:1 stay sharp
    master = img.copy()
    master.thumbnail((FULL_SIZE, FULL_SIZE), Image.Resampling.LANCZOS)
    master.save(
        upload_path,
        format="JPEG",
        quality=FULL_JPEG_QUALITY,
        optimize=True,
        exif=b"",
    )
    size_bytes = upload_path.stat().st_size
    width, height = master.size

    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(thumb_path, format="JPEG", quality=85, optimize=True, exif=b"")

    # Normalize stored filename to .jpg if we re-encoded
    if upload_path.suffix.lower() not in {".jpg", ".jpeg"}:
        jpg_name = f"{Path(filename).stem}.jpg"
        new_upload = album_upload_dir(album_slug) / jpg_name
        new_thumb = album_thumb_dir(album_slug) / jpg_name
        upload_path.rename(new_upload)
        if thumb_path.exists():
            thumb_path.rename(new_thumb)
        filename = jpg_name

    return filename, width, height, size_bytes


def delete_photo_files(album_slug: str, filename: str) -> None:
    for base in (UPLOAD_DIR / album_slug, THUMB_DIR / album_slug):
        path = base / filename
        if path.exists():
            path.unlink()


def delete_album_files(album_slug: str) -> None:
    import shutil

    for base in (UPLOAD_DIR / album_slug, THUMB_DIR / album_slug):
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
    cover = cover_path(album_slug)
    if cover.exists():
        cover.unlink()


def cover_path(album_slug: str) -> Path:
    return COVER_DIR / f"{album_slug}.jpg"


def render_album_cover(
    album_slug: str,
    filename: str,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Path:
    """
    Bake a sharp 4:3 cover JPEG matching the thumbnail editor framing
    (contain-fit → zoom → pan inside a 4:3 frame).
    """
    ensure_dirs()
    src_path = album_upload_dir(album_slug) / filename
    if not src_path.exists():
        raise FileNotFoundError(filename)

    zoom = max(1.0, min(6.0, float(zoom)))
    pan_x = max(-1.0, min(1.0, float(pan_x)))
    pan_y = max(-1.0, min(1.0, float(pan_y)))

    img = Image.open(src_path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")

    src_w, src_h = img.size
    frame_w, frame_h = COVER_WIDTH, COVER_HEIGHT

    fit = min(frame_w / src_w, frame_h / src_h)
    drawn_w = src_w * fit * zoom
    drawn_h = src_h * fit * zoom
    max_pan_x = max(0.0, (drawn_w - frame_w) / 2.0)
    max_pan_y = max(0.0, (drawn_h - frame_h) / 2.0)
    offset_x = pan_x * max_pan_x
    offset_y = pan_y * max_pan_y

    # Top-left of the scaled image in frame coordinates (same as the CSS viewer)
    img_left = frame_w / 2.0 + offset_x - drawn_w / 2.0
    img_top = frame_h / 2.0 + offset_y - drawn_h / 2.0

    scaled = img.resize(
        (max(1, round(drawn_w)), max(1, round(drawn_h))),
        Image.Resampling.LANCZOS,
    )
    out = Image.new("RGB", (frame_w, frame_h), (8, 10, 16))
    out.paste(scaled, (round(img_left), round(img_top)))

    dest = cover_path(album_slug)
    out.save(dest, format="JPEG", quality=92, optimize=True, exif=b"")
    return dest


def rotate_photo_files(
    album_slug: str, filename: str, degrees: int
) -> Tuple[int, int, int]:
    """
    Permanently rotate full + thumbnail JPEGs.
    degrees: positive = counter-clockwise (Pillow), typically 90 or -90.
    Returns (width, height, size_bytes) of the full image after rotation.
    """
    ensure_dirs()
    upload_path = album_upload_dir(album_slug) / filename
    thumb_path = album_thumb_dir(album_slug) / filename
    if not upload_path.exists():
        raise FileNotFoundError(filename)

    # Normalize to 90-degree steps
    degrees = int(degrees) % 360
    if degrees not in (90, 180, 270):
        raise ValueError("degrees must be 90, 180, or 270")

    img = Image.open(upload_path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    rotated = img.rotate(degrees, expand=True)

    # Save without EXIF so browsers don't re-apply an old Orientation tag
    rotated.save(upload_path, format="JPEG", quality=88, optimize=True, exif=b"")
    width, height = rotated.size
    size_bytes = upload_path.stat().st_size

    thumb = rotated.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
    thumb.save(thumb_path, format="JPEG", quality=82, optimize=True, exif=b"")

    return width, height, size_bytes
