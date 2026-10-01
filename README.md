# Astro Photo

A simple web app to upload photos into albums and share them with a link.

## Features

- Create photo albums with titles and descriptions
- Drag-and-drop multi-photo upload
- Public share links (`/a/<slug>`)
- Optional per-album passwords
- Show/hide albums on the home page
- Admin dashboard protected by a single password
- Lightbox gallery on shared albums

## Local development

`SECRET_KEY` and `ADMIN_PASSWORD` are required. Copy the example env file and set both before starting:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a && source .env && set +a
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 — log in at `/admin/login`.

## Docker

The app listens on port 8000. Set `SECRET_KEY` and `ADMIN_PASSWORD` in `.env` first:

```bash
cp .env.example .env
docker compose up -d --build
```
