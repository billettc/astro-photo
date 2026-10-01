import secrets
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slugify import slugify
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    NotAuthenticated,
    admin_from_request,
    check_admin_password,
    create_admin_token,
    create_album_unlock_token,
    hash_password,
    is_album_unlocked,
    require_admin,
    verify_password,
)
from app.config import APP_NAME, FIT_MAX_UPLOAD_MB, MAX_UPLOAD_MB, THUMB_DIR, UPLOAD_DIR
from app.fits import FitsError, fits_by_photo, is_fits_filename, load_fits_header, pack_header
from app.database import (
    DEFAULT_DESCRIPTION_PROMPT,
    Album,
    Photo,
    SessionLocal,
    get_db,
    get_or_create_settings,
    init_db,
)
from app.grok_client import album_description_prompt, generate_text
from app.markdown_render import render_markdown
from app.storage import (
    cover_path,
    delete_album_files,
    delete_photo_files,
    ensure_dirs,
    is_allowed_filename,
    process_image,
    render_album_cover,
    rotate_photo_files,
)

app = FastAPI(title=APP_NAME)
BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["markdown"] = render_markdown
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    init_db()


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)


def _ctx(request: Request, db: Optional[Session] = None, **extra):
    own_db = False
    if db is None:
        db = SessionLocal()
        own_db = True
    try:
        settings = get_or_create_settings(db)
        return {
            "request": request,
            "app_name": settings.site_name or APP_NAME,
            "settings": settings,
            "is_admin": admin_from_request(request),
            **extra,
        }
    finally:
        if own_db:
            db.close()


def _album_cards(albums, db: Session):
    cards = []
    for album in albums:
        cover = None
        if album.cover_photo_id:
            cover = db.query(Photo).filter(Photo.id == album.cover_photo_id).first()
        if not cover and album.photos:
            cover = album.photos[0]
        cards.append({"album": album, "cover": cover, "count": len(album.photos)})
    return cards


async def _reorder_ids_from_request(request: Request) -> list[int]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Expected JSON") from None
    return _reorder_ids(body)


def _reorder_ids(body: object) -> list[int]:
    raw = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        raise HTTPException(400, "ids must be a list")
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            raise HTTPException(400, "ids must be integers") from None
    return ids


def _apply_sort_order(rows_by_id: dict, ids: list[int]) -> None:
    if len(ids) != len(set(ids)) or set(ids) != set(rows_by_id):
        raise HTTPException(400, "Order must include each item once")
    for index, item_id in enumerate(ids):
        rows_by_id[item_id].sort_order = index


def unique_slug(db: Session, title: str) -> str:
    base = slugify(title)[:60] or secrets.token_hex(4)
    slug = base
    n = 2
    while db.query(Album).filter(Album.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


# ── Public pages ──────────────────────────────────────────────


@app.get("/planner", response_class=HTMLResponse)
def planner_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("planner.html", _ctx(request, db))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("dashboard.html", _ctx(request, db))


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    is_admin = admin_from_request(request)
    q = db.query(Album).order_by(Album.sort_order.asc(), Album.created_at.desc())
    if not is_admin:
        q = q.filter(Album.is_public == True)  # noqa: E712
    albums = q.all()
    return templates.TemplateResponse(
        "home.html",
        _ctx(request, db, album_cards=_album_cards(albums, db)),
    )


@app.get("/a/{slug}", response_class=HTMLResponse)
def view_album(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    unlocked: Optional[str] = None,
):
    album = db.query(Album).filter(Album.slug == slug).first()
    if not album:
        raise HTTPException(404, "Album not found")

    is_admin = admin_from_request(request)
    needs_password = bool(album.password_hash) and not is_admin
    unlocked_cookie = request.cookies.get(f"album_{slug}")
    is_unlocked = is_admin or not needs_password or is_album_unlocked(
        unlocked_cookie, slug
    )

    if needs_password and not is_unlocked:
        return templates.TemplateResponse(
            "album_lock.html",
            _ctx(request, album=album, error=None),
            status_code=200,
        )

    return templates.TemplateResponse(
        "album.html",
        _ctx(
            request,
            album=album,
            photos=album.photos,
            photo_fits=fits_by_photo(album.photos),
        ),
    )


@app.post("/a/{slug}/unlock")
def unlock_album(
    slug: str,
    request: Request,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    album = db.query(Album).filter(Album.slug == slug).first()
    if not album:
        raise HTTPException(404, "Album not found")
    if not album.password_hash or not verify_password(password, album.password_hash):
        return templates.TemplateResponse(
            "album_lock.html",
            _ctx(request, album=album, error="Incorrect password"),
            status_code=401,
        )
    response = RedirectResponse(f"/a/{slug}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        f"album_{slug}",
        create_album_unlock_token(slug),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.get("/media/{slug}/{filename}")
def media(slug: str, filename: str, size: str = "full"):
    base = THUMB_DIR if size == "thumb" else UPLOAD_DIR
    path = base / slug / filename
    if not path.exists() or ".." in filename or "/" in filename:
        raise HTTPException(404)
    # Allow long cache only when clients pass a version query (?v=); browsers
    # still revalidate when that version changes after rotate.
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/cover/{slug}")
def album_cover(slug: str):
    """Sharp baked album-card cover (written on Save thumbnail)."""
    path = cover_path(slug)
    if not path.exists() or ".." in slug or "/" in slug:
        raise HTTPException(404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


# ── Admin auth ────────────────────────────────────────────────


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if admin_from_request(request):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("login.html", _ctx(request, db, error=None))


@app.post("/admin/login")
def login(request: Request, password: str = Form(...), db: Session = Depends(get_db)):
    if not check_admin_password(password):
        return templates.TemplateResponse(
            "login.html",
            _ctx(request, db, error="Wrong password"),
            status_code=401,
        )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        create_admin_token(),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.get("/admin/logout")
def logout():
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/admin", include_in_schema=False)
def admin_root():
    """Dashboard removed — home is the album list for everyone."""
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@app.get(
    "/admin/settings",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin)],
)
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = get_or_create_settings(db)
    return templates.TemplateResponse(
        "settings.html",
        _ctx(request, db, settings=settings, saved=False),
    )


@app.post(
    "/admin/settings",
    dependencies=[Depends(require_admin)],
)
def save_settings(
    request: Request,
    site_name: str = Form(...),
    home_title: str = Form(...),
    home_tagline: str = Form(""),
    footer_text: str = Form(""),
    description_prompt: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_or_create_settings(db)
    settings.site_name = site_name.strip()[:120] or "Astro Photo"
    settings.home_title = home_title.strip()[:200] or "Photo albums"
    settings.home_tagline = home_tagline.strip()[:500]
    settings.footer_text = footer_text.strip()[:200]
    prompt = description_prompt.strip()
    settings.description_prompt = prompt[:8000] if prompt else DEFAULT_DESCRIPTION_PROMPT
    db.commit()
    db.refresh(settings)
    return templates.TemplateResponse(
        "settings.html",
        _ctx(request, db, settings=settings, saved=True),
    )


@app.get(
    "/admin/albums/new",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin)],
)
def new_album_form(request: Request):
    return templates.TemplateResponse("album_form.html", _ctx(request, album=None))


@app.post("/admin/albums", dependencies=[Depends(require_admin)])
def create_album(
    title: str = Form(...),
    description: str = Form(""),
    password: str = Form(""),
    is_public: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    title = title.strip()
    if not title:
        raise HTTPException(400, "Title required")
    # New albums land at the front. sort_order may be negative until the next drag.
    front = db.query(Album.sort_order).order_by(Album.sort_order.asc()).limit(1).scalar()
    album = Album(
        title=title,
        description=description.strip(),
        slug=unique_slug(db, title),
        password_hash=hash_password(password) if password.strip() else None,
        is_public=is_public is not None,
        sort_order=0 if front is None else int(front) - 1,
    )
    db.add(album)
    db.commit()
    db.refresh(album)
    return RedirectResponse(
        f"/admin/albums/{album.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get(
    "/admin/albums/{album_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_admin)],
)
def admin_album(album_id: int, request: Request, db: Session = Depends(get_db)):
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)
    photos = list(album.photos)
    cover_photo = None
    if album.cover_photo_id:
        cover_photo = next((p for p in photos if p.id == album.cover_photo_id), None)
    if not cover_photo and photos:
        cover_photo = photos[0]
    fits_error = (request.query_params.get("fits_error") or "").strip()[:300]
    return templates.TemplateResponse(
        "admin_album.html",
        _ctx(
            request,
            album=album,
            photos=photos,
            cover_photo=cover_photo,
            photo_fits=fits_by_photo(photos),
            fits_error=fits_error,
            fit_max_mb=FIT_MAX_UPLOAD_MB,
        ),
    )


@app.post(
    "/admin/albums/{album_id}/generate-description",
    dependencies=[Depends(require_admin)],
)
def generate_album_description(album_id: int, db: Session = Depends(get_db)):
    """Use Grok (server CLI or xAI API) to draft an album description."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)
    settings = get_or_create_settings(db)
    photo_names = [p.original_name for p in album.photos]
    prompt = album_description_prompt(
        album.title,
        photo_names,
        template=settings.description_prompt,
    )
    try:
        description = generate_text(prompt)
    except Exception as exc:  # noqa: BLE001 — surface to the UI
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "description": description}


@app.post(
    "/admin/albums/{album_id}/edit",
    dependencies=[Depends(require_admin)],
)
def edit_album(
    album_id: int,
    title: str = Form(...),
    description: str = Form(""),
    password: str = Form(""),
    clear_password: Optional[str] = Form(None),
    is_public: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)
    album.title = title.strip()
    album.description = description.strip()
    album.is_public = is_public is not None
    if clear_password:
        album.password_hash = None
    elif password.strip():
        album.password_hash = hash_password(password.strip())
    db.commit()
    return RedirectResponse(
        f"/admin/albums/{album_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post(
    "/admin/albums/{album_id}/delete",
    dependencies=[Depends(require_admin)],
)
def delete_album(album_id: int, db: Session = Depends(get_db)):
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)
    slug = album.slug
    db.delete(album)
    db.commit()
    delete_album_files(slug)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/admin/albums/reorder", dependencies=[Depends(require_admin)])
async def reorder_albums(request: Request, db: Session = Depends(get_db)):
    """Save the home-page album order. `ids` is every album, first to last."""
    ids = await _reorder_ids_from_request(request)
    albums = db.query(Album).all()
    _apply_sort_order({album.id: album for album in albums}, ids)
    db.commit()
    return {"ok": True}


@app.post(
    "/admin/albums/{album_id}/photos/reorder",
    dependencies=[Depends(require_admin)],
)
async def reorder_photos(
    album_id: int, request: Request, db: Session = Depends(get_db)
):
    """Save photo order inside one album. `ids` is every photo, first to last."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)
    ids = await _reorder_ids_from_request(request)
    photos = list(album.photos)
    _apply_sort_order({photo.id: photo for photo in photos}, ids)
    db.commit()
    return {"ok": True}


@app.post(
    "/admin/albums/{album_id}/upload",
    dependencies=[Depends(require_admin)],
)
async def upload_photos(
    album_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)

    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    next_order = max((p.sort_order for p in album.photos), default=-1) + 1
    added = 0

    for f in files:
        if not f.filename or not is_allowed_filename(f.filename):
            continue
        data = await f.read()
        if not data or len(data) > max_bytes:
            continue
        try:
            filename, width, height, size_bytes = process_image(
                data, album.slug, f.filename
            )
        except Exception:
            continue
        photo = Photo(
            album_id=album.id,
            filename=filename,
            original_name=f.filename,
            content_type="image/jpeg",
            width=width,
            height=height,
            size_bytes=size_bytes,
            sort_order=next_order + added,
        )
        db.add(photo)
        added += 1

    db.commit()
    return RedirectResponse(
        f"/admin/albums/{album_id}", status_code=status.HTTP_303_SEE_OTHER
    )


def _fits_redirect(album_id: int, photo_id: int, error: str | None = None):
    if error:
        location = (
            f"/admin/albums/{album_id}?fits_error={quote(error)}#photo-{photo_id}"
        )
    else:
        location = f"/admin/albums/{album_id}#photo-{photo_id}"
    return RedirectResponse(location, status_code=status.HTTP_303_SEE_OTHER)


@app.post(
    "/admin/photos/{photo_id}/fits",
    dependencies=[Depends(require_admin)],
)
async def upload_fits(
    photo_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Store the FITS header for one photo. The image payload is discarded."""
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)
    album_id = photo.album_id
    name = file.filename or ""
    if not is_fits_filename(name):
        return _fits_redirect(
            album_id, photo.id, "Choose a .fit, .fits, or .fts file."
        )

    max_bytes = FIT_MAX_UPLOAD_MB * 1024 * 1024
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0:
        return _fits_redirect(album_id, photo.id, "That file is empty.")
    if size > max_bytes:
        return _fits_redirect(
            album_id,
            photo.id,
            f"That file is larger than {FIT_MAX_UPLOAD_MB} MB.",
        )

    try:
        cards = load_fits_header(file.file)
    except FitsError as exc:
        return _fits_redirect(album_id, photo.id, str(exc))
    except Exception:
        return _fits_redirect(album_id, photo.id, "Could not read that FITS header.")

    photo.fits_header = pack_header(name, cards)
    db.commit()
    return _fits_redirect(album_id, photo.id)


@app.post(
    "/admin/photos/{photo_id}/fits/delete",
    dependencies=[Depends(require_admin)],
)
def delete_fits(photo_id: int, db: Session = Depends(get_db)):
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)
    photo.fits_header = None
    album_id = photo.album_id
    db.commit()
    return _fits_redirect(album_id, photo.id)


@app.post(
    "/admin/photos/{photo_id}/delete",
    dependencies=[Depends(require_admin)],
)
def delete_photo(photo_id: int, db: Session = Depends(get_db)):
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)
    album_id = photo.album_id
    slug = photo.album.slug
    filename = photo.filename
    db.delete(photo)
    db.commit()
    delete_photo_files(slug, filename)
    return RedirectResponse(
        f"/admin/albums/{album_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@app.post(
    "/admin/photos/{photo_id}/cover",
    dependencies=[Depends(require_admin)],
)
def set_cover(photo_id: int, db: Session = Depends(get_db)):
    """Legacy cover endpoint — sets cover photo only (no framing)."""
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)
    album = photo.album
    album.cover_photo_id = photo.id
    db.commit()
    return RedirectResponse(
        f"/admin/albums/{album.id}#photo-{photo.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/admin/albums/{album_id}/thumbnail",
    dependencies=[Depends(require_admin)],
)
async def save_album_thumbnail(
    album_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Set album cover photo + zoom/pan framing for the album card thumbnail."""
    album = db.query(Album).filter(Album.id == album_id).first()
    if not album:
        raise HTTPException(404)

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        photo_id = body.get("photo_id")
        zoom = body.get("zoom", 1.0)
        pan_x = body.get("pan_x", 0.0)
        pan_y = body.get("pan_y", 0.0)
    else:
        form = await request.form()
        photo_id = form.get("photo_id")
        zoom = form.get("zoom", 1.0)
        pan_x = form.get("pan_x", 0.0)
        pan_y = form.get("pan_y", 0.0)

    try:
        photo_id_i = int(photo_id)
        zoom_f = float(zoom)
        pan_x_f = float(pan_x)
        pan_y_f = float(pan_y)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid thumbnail parameters")

    photo = (
        db.query(Photo)
        .filter(Photo.id == photo_id_i, Photo.album_id == album.id)
        .first()
    )
    if not photo:
        raise HTTPException(404, "Photo not in this album")

    album.cover_photo_id = photo.id
    album.cover_zoom = max(1.0, min(6.0, zoom_f))
    album.cover_pan_x = max(-1.0, min(1.0, pan_x_f))
    album.cover_pan_y = max(-1.0, min(1.0, pan_y_f))

    try:
        render_album_cover(
            album.slug,
            photo.filename,
            zoom=album.cover_zoom,
            pan_x=album.cover_pan_x,
            pan_y=album.cover_pan_y,
        )
    except FileNotFoundError:
        raise HTTPException(404, "Image file missing")

    album.cover_version = int(album.cover_version or 0) + 1
    db.commit()
    db.refresh(album)

    if _wants_json(request):
        return {
            "ok": True,
            "album_id": album.id,
            "cover_photo_id": album.cover_photo_id,
            "cover_zoom": album.cover_zoom,
            "cover_pan_x": album.cover_pan_x,
            "cover_pan_y": album.cover_pan_y,
            "cover_version": album.cover_version,
            "cover_url": f"/cover/{album.slug}?v={album.cover_version}",
        }
    return RedirectResponse(
        f"/admin/albums/{album.id}#photo-{photo.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    content_type = (request.headers.get("content-type") or "").lower()
    return "application/json" in content_type or "application/json" in accept.split(",")[0]


@app.post(
    "/admin/photos/{photo_id}/zoom",
    dependencies=[Depends(require_admin)],
)
async def set_photo_zoom(
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)

    zoom = None
    zoom_pct = None
    pan_x = None
    pan_y = None
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        zoom = body.get("zoom")
        zoom_pct = body.get("zoom_pct")
        if "pan_x" in body:
            pan_x = body.get("pan_x")
        if "pan_y" in body:
            pan_y = body.get("pan_y")
    else:
        form = await request.form()
        zoom = form.get("zoom")
        zoom_pct = form.get("zoom_pct")
        if "pan_x" in form:
            pan_x = form.get("pan_x")
        if "pan_y" in form:
            pan_y = form.get("pan_y")

    try:
        if zoom_pct is not None and str(zoom_pct).strip() != "":
            zoom_f = float(zoom_pct) / 100.0
        else:
            zoom_f = float(zoom)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid zoom")

    # Clamp to the same range the viewer allows
    photo.default_zoom = max(1.0, min(6.0, zoom_f))

    # Pan is optional — only update when the client sends it (viewer "Save view")
    if pan_x is not None or pan_y is not None:
        try:
            pan_x_f = float(pan_x if pan_x is not None else 0.0)
            pan_y_f = float(pan_y if pan_y is not None else 0.0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid pan")
        photo.default_pan_x = max(-1.0, min(1.0, pan_x_f))
        photo.default_pan_y = max(-1.0, min(1.0, pan_y_f))

    db.commit()

    if _wants_json(request):
        return {
            "ok": True,
            "id": photo.id,
            "default_zoom": photo.default_zoom,
            "default_pan_x": photo.default_pan_x,
            "default_pan_y": photo.default_pan_y,
        }
    return RedirectResponse(
        f"/admin/albums/{photo.album_id}#photo-{photo.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post(
    "/admin/photos/{photo_id}/rotate",
    dependencies=[Depends(require_admin)],
)
async def rotate_photo(
    photo_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Permanently rotate a photo 90° left (CCW) or right (CW)."""
    photo = db.query(Photo).filter(Photo.id == photo_id).first()
    if not photo:
        raise HTTPException(404)

    direction = "right"
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        body = await request.json()
        direction = (body.get("direction") or "right").lower()
    else:
        form = await request.form()
        direction = str(form.get("direction") or "right").lower()

    # Pillow rotate(): positive degrees = counter-clockwise
    if direction in ("left", "ccw", "anticlockwise"):
        degrees = 90
    elif direction in ("right", "cw", "clockwise"):
        degrees = 270  # 90° clockwise
    else:
        raise HTTPException(400, "direction must be left or right")

    try:
        width, height, size_bytes = rotate_photo_files(
            photo.album.slug, photo.filename, degrees
        )
    except FileNotFoundError:
        raise HTTPException(404, "Image file missing")
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    photo.width = width
    photo.height = height
    photo.size_bytes = size_bytes
    photo.file_version = int(photo.file_version or 0) + 1

    album = photo.album
    cover_url = None
    if album.cover_photo_id == photo.id:
        try:
            render_album_cover(
                album.slug,
                photo.filename,
                zoom=album.cover_zoom or 1.0,
                pan_x=album.cover_pan_x or 0.0,
                pan_y=album.cover_pan_y or 0.0,
            )
            album.cover_version = int(album.cover_version or 0) + 1
            cover_url = f"/cover/{album.slug}?v={album.cover_version}"
        except FileNotFoundError:
            pass

    db.commit()
    db.refresh(photo)

    v = photo.file_version
    payload = {
        "ok": True,
        "id": photo.id,
        "width": width,
        "height": height,
        "file_version": v,
        "src": f"/media/{photo.album.slug}/{photo.filename}?v={v}",
        "thumb": f"/media/{photo.album.slug}/{photo.filename}?size=thumb&v={v}",
        "cover_url": cover_url,
        "cover_version": album.cover_version if cover_url else None,
    }
    if _wants_json(request):
        return payload
    return RedirectResponse(
        f"/admin/albums/{photo.album_id}#photo-{photo.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME}
