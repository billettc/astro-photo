import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
THUMB_DIR = DATA_DIR / "thumbs"
COVER_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "astro.db"

def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


SECRET_KEY = _required_env("SECRET_KEY")
ADMIN_PASSWORD = _required_env("ADMIN_PASSWORD")
APP_NAME = os.environ.get("APP_NAME", "Astro Photo")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
# FIT frames are large. The header is stored, the image is plate-solved, then discarded.
FIT_MAX_UPLOAD_MB = int(os.environ.get("FIT_MAX_UPLOAD_MB", "512"))
SOLVE_DIR = DATA_DIR / "solve"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".tif", ".tiff"}

# Thumbnail max edge length
THUMB_SIZE = 480
# Full web master max edge (kept for sharp zoom / 1:1). Caps huge raws for disk.
FULL_SIZE = int(os.environ.get("FULL_SIZE", "5120"))
# Legacy name used by older code paths
PREVIEW_SIZE = FULL_SIZE
# Baked album-card cover (4:3) — sharp final thumbnail
COVER_WIDTH = 1600
COVER_HEIGHT = 1200
# JPEG quality for full-size masters
FULL_JPEG_QUALITY = int(os.environ.get("FULL_JPEG_QUALITY", "92"))
