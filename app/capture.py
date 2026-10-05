"""Imaging sessions: one FIT header and one plate solution, shared by photos."""

import logging
import time
from pathlib import Path

from app.config import SOLVE_DIR
from app.database import ImagingSession, Photo, SessionLocal
from app.fits import (
    FitsError,
    PlateSolution,
    SolveError,
    header_for_page,
    session_name,
    solution_from_header,
)
from app.solver import solve_fits_file

log = logging.getLogger(__name__)


def adopt_legacy_fits(db) -> None:
    """One session per album that already has a FIT header.

    Existing albums are a single field, so every photo in the album joins that
    session. Pixels were not kept, so only a header WCS can be solved.
    """
    headed = db.query(Photo).filter(Photo.fits_header.isnot(None)).all()
    by_album: dict[int, list] = {}
    for photo in headed:
        by_album.setdefault(photo.album_id, []).append(photo)
    if not by_album:
        return

    for group in by_album.values():
        album = group[0].album
        chosen_raw = None
        chosen_page = None
        for photo in group:
            raw = (photo.fits_header or "").strip()
            page = header_for_page(raw)
            if not page:
                continue
            chosen_has_wcs = bool(chosen_page and solution_from_header(chosen_page["cards"]))
            if chosen_page is None or (solution_from_header(page["cards"]) and not chosen_has_wcs):
                chosen_raw = raw
                chosen_page = page
        for photo in album.photos:
            photo.fits_header = None
        if not chosen_page:
            continue
        session = build_session(
            album, chosen_page["name"] or "header.fit", chosen_page["cards"], chosen_raw
        )
        if session.solve_status != "solved":
            session.solve_status = "failed"
            session.solve_error = "Replace the FIT to plate-solve this session."
        db.add(session)
        db.flush()
        for photo in album.photos:
            photo.session_id = session.id
    db.commit()


def build_empty_session(album) -> ImagingSession:
    """A session with no FIT yet. Photos can be added, and a FIT can come later."""
    existing = list(album.sessions)
    order = max((item.sort_order for item in existing), default=-1) + 1
    return ImagingSession(
        album_id=album.id,
        name=f"Session {len(existing) + 1}",
        source_name="",
        sort_order=order,
        solve_status="none",
    )


def build_session(album, filename: str, cards: list[dict], header_json: str) -> ImagingSession:
    order = max((item.sort_order for item in album.sessions), default=-1) + 1
    session = ImagingSession(album_id=album.id, sort_order=order)
    apply_fits(session, filename, cards, header_json)
    return session


def apply_fits(session: ImagingSession, filename: str, cards: list[dict], header_json: str) -> None:
    """Store a new FIT header on this session and solve it again.

    Photos stay linked. A header that already has a WCS is solved immediately.
    Otherwise the caller plate-solves the pixels and then discards them.
    """
    width, height = _image_size(cards)
    session.name = session_name(cards, filename)
    session.source_name = Path(filename).name[:255] or "header.fit"
    session.fits_header = header_json
    session.image_width = width
    session.image_height = height
    session.center_ra = None
    session.center_dec = None
    session.rotation_deg = None
    session.pixel_scale = None
    session.parity = None
    session.solve_source = None
    session.solve_error = None
    session.solve_status = "pending"
    solution = solution_from_header(cards)
    if solution:
        write_solution(session, solution)


def write_solution(session: ImagingSession, solution: PlateSolution) -> None:
    session.center_ra = solution.ra
    session.center_dec = solution.dec
    session.rotation_deg = solution.rotation
    session.pixel_scale = solution.scale
    session.parity = solution.parity
    session.solve_status = "solved"
    session.solve_source = solution.source
    session.solve_error = None
    if solution.width:
        session.image_width = solution.width
    if solution.height:
        session.image_height = solution.height


def solve_file_for(session_id: int) -> Path:
    return SOLVE_DIR / f"session-{session_id}.fit"


def finish_solve(session_id: int, path: str) -> None:
    keep = False
    db = SessionLocal()
    try:
        session = db.get(ImagingSession, session_id)
        if session is None:
            return
        try:
            write_solution(session, solve_fits_file(path))
        except (SolveError, FitsError) as exc:
            session.solve_status = "failed"
            session.solve_error = str(exc)[:500]
            keep = "star catalog could not be reached" in str(exc)
        except Exception:
            log.exception("plate solve failed for session %s", session_id)
            session.solve_status = "failed"
            session.solve_error = "Plate solving failed."
        db.commit()
    finally:
        db.close()
        if keep:
            _retain_solve_file(session_id, path)
        else:
            Path(path).unlink(missing_ok=True)
            stable = solve_file_for(session_id)
            if stable != Path(path):
                stable.unlink(missing_ok=True)


def _retain_solve_file(session_id: int, path: str) -> None:
    """Keep the FIT so Try again can plate-solve without a new upload."""
    src = Path(path)
    if not src.is_file():
        return
    SOLVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = solve_file_for(session_id)
    if src.resolve() == dest.resolve():
        return
    dest.unlink(missing_ok=True)
    src.replace(dest)


def sweep_solve_files() -> None:
    if not SOLVE_DIR.exists():
        return
    cutoff = time.time() - 86400
    for path in SOLVE_DIR.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def capture_by_photo(photos) -> dict:
    out = {}
    for photo in photos:
        session = photo.capture_session
        if not session or not session.fits_header:
            continue
        page = header_for_page(session.fits_header)
        if not page:
            continue
        page["title"] = session.name
        page["solve"] = solve_payload(session)
        out[str(photo.id)] = page
    return out


def solve_payload(session: ImagingSession) -> dict:
    payload = {
        "status": session.solve_status or "pending",
        "source": session.solve_source or "",
        "error": session.solve_error or "",
    }
    solved = (
        session.solve_status == "solved"
        and session.center_ra is not None
        and session.center_dec is not None
    )
    if solved:
        payload["center"] = f"{format_ra(session.center_ra)}   {format_dec(session.center_dec)}"
        if session.rotation_deg is not None:
            payload["rotation"] = f"{session.rotation_deg:.1f}°"
        if session.pixel_scale is not None:
            payload["scale"] = f"{session.pixel_scale:.2f}″/px"
        if (
            session.rotation_deg is not None
            and session.pixel_scale
            and session.parity is not None
            and session.image_width
            and session.image_height
        ):
            payload["wcs"] = {
                "ra": session.center_ra,
                "dec": session.center_dec,
                "rotation": session.rotation_deg,
                "scale": session.pixel_scale,
                "parity": int(session.parity),
                "width": int(session.image_width),
                "height": int(session.image_height),
            }
    return payload


def solve_label(session: ImagingSession) -> str:
    payload = solve_payload(session)
    if payload["status"] == "solved" and payload.get("center"):
        where = "from the FIT header" if payload["source"] == "header" else "on the server"
        parts = [f"Solved {where}", payload["center"]]
        if payload.get("scale"):
            parts.append(payload["scale"])
        if payload.get("rotation"):
            parts.append(payload["rotation"])
        return " · ".join(parts)
    if payload["status"] == "pending":
        return "Solving…"
    if payload["status"] == "none":
        return "No FIT file yet."
    return payload["error"] or "Plate solve failed."


def session_status_rows(sessions) -> list[dict]:
    return [
        {
            "id": session.id,
            "status": session.solve_status,
            "label": solve_label(session),
        }
        for session in sessions
    ]


def format_ra(degrees: float) -> str:
    hours = (degrees % 360) / 15
    whole = int(hours)
    minutes = (hours - whole) * 60
    minute = int(minutes)
    second = (minutes - minute) * 60
    return f"{whole}h {minute:02d}m {second:04.1f}s"


def format_dec(degrees: float) -> str:
    sign = "-" if degrees < 0 else "+"
    dec = abs(degrees)
    whole = int(dec)
    minutes = (dec - whole) * 60
    minute = int(minutes)
    second = (minutes - minute) * 60
    return f"{sign}{whole}° {minute:02d}′ {second:04.1f}″"


def _image_size(cards: list[dict]) -> tuple[int, int]:
    try:
        data = {card["k"]: card["v"] for card in cards}
        return int(float(data.get("NAXIS1") or 0)), int(float(data.get("NAXIS2") or 0))
    except (TypeError, ValueError):
        return 0, 0
