"""Create an imaging session from a FIT file and show it on the album page."""

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_TMP = tempfile.mkdtemp(prefix="astro-fits-")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["DATA_DIR"] = _TMP
os.environ["FIT_MAX_UPLOAD_MB"] = "1"

from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.align import ALIGN_REV
from app.capture import adopt_legacy_fits, finish_solve, solve_file_for, solve_payload
from app.database import ImagingSession, Photo, SessionLocal
from app.fits import PlateSolution, SolveError, pack_header
from app.main import _align_failed, _align_refreshing, app
from tests.test_fits import fits_bytes, sample_header, solved_header


def _wcs_cards(name: str) -> list[dict]:
    return [
        {"k": "OBJECT", "v": name, "c": ""},
        {"k": "NAXIS1", "v": "10", "c": ""},
        {"k": "NAXIS2", "v": "10", "c": ""},
        {"k": "CRPIX1", "v": "5.5", "c": ""},
        {"k": "CRPIX2", "v": "5.5", "c": ""},
        {"k": "CRVAL1", "v": "10", "c": ""},
        {"k": "CRVAL2", "v": "20", "c": ""},
        {"k": "CD1_1", "v": "-0.001", "c": ""},
        {"k": "CD1_2", "v": "0", "c": ""},
        {"k": "CD2_1", "v": "0", "c": ""},
        {"k": "CD2_2", "v": "0.001", "c": ""},
    ]


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (10, 20, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def _session_id_from(html: str, source: str) -> str:
    start = html.index(source)
    form = html.rfind('action="/admin/sessions/', 0, start)
    if form < 0:
        raise AssertionError(f"no session form for {source}")
    rest = html[form + len('action="/admin/sessions/') :]
    return rest.split("/", 1)[0]


class SessionUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()
        cls.client.post("/admin/login", data={"password": "test-admin"})
        created = cls.client.post(
            "/admin/albums",
            data={"title": "Orion Night", "description": "The sword", "is_public": "on"},
            follow_redirects=False,
        )
        location = created.headers["location"]
        cls.album_id = location.rstrip("/").split("/")[-1]
        created_session = cls.client.post(
            f"/admin/albums/{cls.album_id}/sessions",
            files={"file": ("setup.fit", fits_bytes(solved_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        if "fits_error" in created_session.text:
            raise AssertionError("setup session failed")
        cls.session_id = _session_id_from(created_session.text, "setup.fit")
        uploaded = cls.client.post(
            f"/admin/albums/{cls.album_id}/upload",
            data={"session_id": cls.session_id},
            files=[("files", ("orion.jpg", _jpeg(), "image/jpeg"))],
            follow_redirects=True,
        )
        if uploaded.status_code != 200:
            raise AssertionError(uploaded.status_code)
        marker = 'action="/admin/photos/'
        start = uploaded.text.index(marker) + len(marker)
        cls.photo_id = uploaded.text[start:].split("/", 1)[0]

    def test_overlay_align_is_remembered_for_the_row_order(self):
        _align_failed.clear()
        _align_refreshing.clear()
        db = SessionLocal()
        photo = db.get(Photo, int(self.photo_id))
        slug = photo.album.slug
        db.close()

        # Nothing is stored yet. The page hears that a measurement is running
        # and draws nothing until the next read.
        with patch(
            "app.main.frame_align_for_photo",
            return_value={"sx": 1.05, "sy": 1.05, "tx": 2.0, "ty": -3.0, "flip": 0, "turn": 0},
        ) as first:
            pending = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json(), {"status": "pending"})
        self.assertEqual(first.call_count, 1)
        ready = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0")
        self.assertEqual(ready.json()["status"], "ready")
        self.assertAlmostEqual(ready.json()["sx"], 1.05)
        self.assertEqual(ready.json()["flip"], 0)
        self.assertEqual(first.call_count, 1)

        db = SessionLocal()
        photo = db.get(Photo, int(self.photo_id))
        photo.align_sx = 1.17
        photo.align_sy = 1.23
        photo.align_tx = -70
        photo.align_ty = 40
        photo.align_flip = 0
        photo.align_version = photo.file_version or 0
        photo.align_rev = ALIGN_REV
        db.commit()
        db.close()

        cached = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0")
        self.assertEqual(cached.status_code, 200)
        body = cached.json()
        self.assertEqual(body["status"], "ready")
        self.assertAlmostEqual(body["sx"], 1.17)
        self.assertAlmostEqual(body["sy"], 1.23)
        self.assertAlmostEqual(body["tx"], -70)
        self.assertAlmostEqual(body["ty"], 40)
        self.assertEqual(body["flip"], 0)
        self.assertEqual(body["turn"], 0)

        missing = self.client.get(f"/a/{slug}/photos/999999/align")
        self.assertEqual(missing.status_code, 404)

        # One stored answer covers either requested row order.
        other = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=1")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["status"], "ready")
        self.assertEqual(other.json()["flip"], 0)
        self.assertAlmostEqual(other.json()["sx"], 1.17)

        db = SessionLocal()
        photo = db.get(Photo, int(self.photo_id))
        photo.align_rev = 0
        db.commit()
        db.close()
        # The catalog is down. The page waits, then the last crop is the fallback.
        with patch(
            "app.main.frame_align_for_photo",
            side_effect=HTTPException(503, "The star catalog could not be reached."),
        ) as blocked:
            waiting = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0")
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json(), {"status": "pending"})
        self.assertEqual(blocked.call_count, 1)
        kept = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0")
        self.assertEqual(kept.status_code, 200)
        self.assertEqual(kept.json()["status"], "failed")
        self.assertIn("star catalog", kept.json()["detail"])
        self.assertAlmostEqual(kept.json()["sx"], 1.17)
        self.assertEqual(kept.json()["flip"], 0)
        self.assertEqual(blocked.call_count, 1)
        db = SessionLocal()
        self.assertEqual(db.get(Photo, int(self.photo_id)).align_rev, 0)
        db.close()

        with patch(
            "app.main.frame_align_for_photo",
            return_value={"sx": 1.0, "sy": 1.0, "tx": 0.0, "ty": 0.0, "flip": 1, "turn": 90},
        ) as measured:
            fresh = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=0&retry=1")
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(fresh.json(), {"status": "pending"})
        self.assertEqual(measured.call_count, 1)
        again = self.client.get(f"/a/{slug}/photos/{self.photo_id}/align?flip=1")
        self.assertEqual(again.json()["status"], "ready")
        self.assertEqual(again.json()["flip"], 1)
        self.assertEqual(again.json()["ty"], 0.0)
        self.assertEqual(again.json()["turn"], 90)
        self.assertEqual(measured.call_count, 1)

    def test_create_session_then_drop_photos_and_a_fit(self):
        client = self.client
        created = client.post(
            "/admin/albums",
            data={"title": "Empty Field", "description": "", "is_public": "on"},
            follow_redirects=False,
        )
        album_id = created.headers["location"].rstrip("/").split("/")[-1]
        page = client.get(f"/admin/albums/{album_id}")
        self.assertIn("Create session", page.text)
        self.assertIn("No sessions yet.", page.text)
        self.assertNotIn("session-drop", page.text)
        self.assertNotIn('id="upload-form"', page.text)

        missing = client.post(
            f"/admin/albums/{album_id}/upload",
            files=[("files", ("stray.jpg", _jpeg(), "image/jpeg"))],
            follow_redirects=False,
        )
        self.assertEqual(missing.status_code, 303)
        self.assertIn("upload_error", missing.headers["location"])
        refused = client.get(missing.headers["location"])
        self.assertIn("Select an imaging session before uploading photos.", refused.text)
        self.assertNotIn("stray.jpg", refused.text)

        made = client.post(
            f"/admin/albums/{album_id}/sessions",
            follow_redirects=True,
        )
        self.assertIn("Session 1", made.text)
        self.assertIn("No FIT file yet.", made.text)
        self.assertEqual(made.text.count("session-drop"), 1)
        session_id = re.search(r'action="/admin/sessions/(\d+)/files"', made.text).group(1)

        added = client.post(
            f"/admin/sessions/{session_id}/files",
            files=[("files", ("frame.jpg", _jpeg(), "image/jpeg"))],
            follow_redirects=True,
        )
        self.assertIn("frame.jpg", added.text)
        self.assertIn("1 photo", added.text)
        self.assertIn("No FIT file yet.", added.text)

        foreign = client.post(
            f"/admin/albums/{self.album_id}/upload",
            data={"session_id": "999999"},
            files=[("files", ("stray-foreign.jpg", _jpeg(), "image/jpeg"))],
            follow_redirects=True,
        )
        self.assertIn("That session is not in this album.", foreign.text)
        self.assertNotIn("stray-foreign.jpg", foreign.text)

        solved = client.post(
            f"/admin/sessions/{session_id}/files",
            files=[
                ("files", ("field.fit", fits_bytes(solved_header()), "application/octet-stream")),
                ("files", ("second.jpg", _jpeg(), "image/jpeg")),
            ],
            follow_redirects=True,
        )
        self.assertNotIn("fits_error", solved.text)
        self.assertIn("field.fit", solved.text)
        self.assertIn("Solved from the FIT header", solved.text)
        self.assertIn("2 photos", solved.text)
        self.assertNotIn("No FIT file yet.", solved.text)
        self.assertEqual(self._session_id(solved.text, source="field.fit"), session_id)

        too_many = client.post(
            f"/admin/sessions/{session_id}/files",
            files=[
                ("files", ("a.fit", fits_bytes(solved_header()), "application/octet-stream")),
                ("files", ("b.fit", fits_bytes(sample_header()), "application/octet-stream")),
            ],
            follow_redirects=False,
        )
        self.assertIn("one%20FIT", too_many.headers["location"])
        kept = client.get(too_many.headers["location"])
        self.assertIn("field.fit", kept.text)
        self.assertNotIn("b.fit", kept.text)

    def test_solved_header_becomes_a_session_shared_by_the_photo(self):
        client = self.client
        client.post(
            f"/admin/photos/{self.photo_id}/session",
            data={"session_id": ""},
            follow_redirects=True,
        )
        stored = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("M42_300s.fit", fits_bytes(solved_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        self.assertNotIn("fits_error", stored.text)
        self.assertIn("M42_300s.fit", stored.text)
        self.assertIn("Solved from the FIT header", stored.text)
        self.assertIn("5h 35m", stored.text)

        before = client.get("/a/orion-night")
        match = re.search(r'id="photo-fits-data">(.*?)</script>', before.text, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertNotIn(self.photo_id, json.loads(match.group(1)))

        assigned = client.post(
            f"/admin/photos/{self.photo_id}/session",
            data={"session_id": self._session_id(stored.text)},
            follow_redirects=True,
        )
        self.assertIn("1 photo", assigned.text)

        album = client.get("/a/orion-night")
        payload = json.loads(
            re.search(r'id="photo-fits-data">(.*?)</script>', album.text, re.DOTALL).group(1)
        )[self.photo_id]
        self.assertEqual(payload["title"], "M42")
        self.assertEqual(payload["name"], "M42_300s.fit")
        self.assertEqual(payload["solve"]["status"], "solved")
        self.assertEqual(payload["solve"]["source"], "header")
        self.assertIn("5h 35m", payload["solve"]["center"])
        wcs = payload["solve"]["wcs"]
        self.assertAlmostEqual(wcs["ra"], 83.8, places=4)
        self.assertAlmostEqual(wcs["dec"], -5.391111, places=4)
        self.assertAlmostEqual(wcs["rotation"], 0.0, places=3)
        self.assertAlmostEqual(wcs["scale"], 2.0, places=3)
        self.assertEqual(wcs["parity"], -1)
        self.assertEqual(wcs["width"], 100)
        self.assertEqual(wcs["height"], 80)
        self.assertIn('id="overlay-toggle"', album.text)
        self.assertIn('id="overlay-status"', album.text)
        self.assertIn('aria-pressed="false"', album.text)
        self.assertIn('id="sky-overlay"', album.text)
        self.assertIn("catalog.js", album.text)
        self.assertIn("overlay-catalog.js", album.text)
        self.assertIn("star-catalog.js", album.text)
        self.assertIn("overlay-flip.js", album.text)
        summary = {row["label"]: row["value"] for row in payload["summary"]}
        self.assertEqual(summary["Exposure"], "300 s")

    def test_replacing_a_fit_updates_the_same_session(self):
        client = self.client
        stored = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("retry.fit", fits_bytes(sample_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        session_id = self._session_id(stored.text, source="retry.fit")
        self.assertIn(f'action="/admin/sessions/{session_id}/files"', stored.text)
        client.post(
            f"/admin/photos/{self.photo_id}/session",
            data={"session_id": session_id},
            follow_redirects=True,
        )

        bad = client.post(
            f"/admin/sessions/{session_id}/files",
            files=[("files", ("notes.txt", b"nope", "text/plain"))],
            follow_redirects=False,
        )
        self.assertIn("Drop%20images", bad.headers["location"])
        kept = client.get(f"/admin/albums/{self.album_id}")
        self.assertIn("retry.fit", kept.text)

        replaced = client.post(
            f"/admin/sessions/{session_id}/files",
            files=[("files", ("retry-solved.fit", fits_bytes(solved_header()), "application/octet-stream"))],
            follow_redirects=True,
        )
        self.assertNotIn("fits_error", replaced.text)
        self.assertIn("retry-solved.fit", replaced.text)
        self.assertNotIn(">retry.fit<", replaced.text)
        self.assertIn("Solved from the FIT header", replaced.text)
        self.assertEqual(self._session_id(replaced.text, source="retry-solved.fit"), session_id)
        self.assertIn(f'action="/admin/sessions/{session_id}/files"', replaced.text)

        album = client.get("/a/orion-night")
        payload = json.loads(
            re.search(r'id="photo-fits-data">(.*?)</script>', album.text, re.DOTALL).group(1)
        )[self.photo_id]
        self.assertEqual(payload["name"], "retry-solved.fit")
        self.assertEqual(payload["title"], "M42")
        self.assertEqual(payload["solve"]["status"], "solved")
        self.assertEqual(payload["solve"]["source"], "header")

    def test_a_header_without_a_solution_is_kept_and_marked_failed(self):
        stored = self.client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("plain.fit", fits_bytes(sample_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        self.assertNotIn("fits_error", stored.text)
        self.assertIn("plain.fit", stored.text)
        self.assertIn("truncated", stored.text.lower())
        unfinished = SimpleNamespace(
            solve_status="failed",
            solve_source="",
            solve_error="truncated",
            center_ra=1.0,
            center_dec=2.0,
            rotation_deg=0.0,
            pixel_scale=2.0,
            parity=-1,
            image_width=10,
            image_height=10,
        )
        self.assertNotIn("wcs", solve_payload(unfinished))

    def test_solver_result_is_stored_on_the_session(self):
        solution = PlateSolution(
            ra=84.0,
            dec=-5.4,
            rotation=12.5,
            scale=1.5,
            parity=-1,
            source="solver",
            width=40,
            height=30,
        )
        with patch("app.capture.solve_fits_file", return_value=solution):
            stored = self.client.post(
                f"/admin/albums/{self.album_id}/sessions",
                files={"file": ("again.fit", fits_bytes(sample_header()), "application/octet-stream")},
                follow_redirects=True,
            )
        self.assertIn("Solved on the server", stored.text)
        self.assertIn("1.50″/px", stored.text)

    def test_try_again_resubmits_a_kept_fit(self):
        client = self.client
        made = client.post(f"/admin/albums/{self.album_id}/sessions", follow_redirects=True)
        session_id = int(re.findall(r'action="/admin/sessions/(\d+)/files"', made.text)[-1])
        kept = solve_file_for(session_id)
        kept.parent.mkdir(parents=True, exist_ok=True)
        source = kept.with_name(f"incoming-{session_id}.fit")
        source.write_bytes(b"pixels")
        try:
            with patch(
                "app.capture.solve_fits_file",
                side_effect=SolveError("The star catalog could not be reached."),
            ):
                finish_solve(session_id, str(source))
            self.assertFalse(source.exists())
            self.assertTrue(kept.is_file())

            page = client.get(f"/admin/albums/{self.album_id}")
            action = f'action="/admin/sessions/{session_id}/solve"'
            start = page.text.index(action)
            tag = page.text.rfind("<form", 0, start)
            self.assertNotIn("hidden", page.text[tag:start])

            with patch("app.main.finish_solve") as mocked:
                again = client.post(f"/admin/sessions/{session_id}/solve", follow_redirects=True)
            mocked.assert_called_once()
            self.assertEqual(mocked.call_args[0][0], session_id)
            self.assertIn("Solving", again.text)
            kept.unlink()
            missing = client.post(f"/admin/sessions/{session_id}/solve", follow_redirects=True)
            self.assertIn("Drop the FIT on this session again", missing.text)
        finally:
            source.unlink(missing_ok=True)
            kept.unlink(missing_ok=True)

    def test_rejects_a_non_fits_file_and_an_oversize_upload(self):
        client = self.client
        bad = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("notes.fit", b"hello this is not fits", "application/octet-stream")},
            follow_redirects=False,
        )
        self.assertIn("fits_error", bad.headers["location"])

        wrong_type = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("notes.jpg", b"nope", "image/jpeg")},
            follow_redirects=False,
        )
        self.assertIn("Choose%20a%20.fit", wrong_type.headers["location"])

        huge = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("huge.fits", b"\x00" * (1024 * 1024 + 10), "application/octet-stream")},
            follow_redirects=False,
        )
        self.assertIn("larger%20than%201%20MB", huge.headers["location"])
        self.assertTrue(Path(_TMP, "astro.db").exists())

    def test_delete_session_keeps_the_photo(self):
        client = self.client
        stored = client.post(
            f"/admin/albums/{self.album_id}/sessions",
            files={"file": ("drop.fit", fits_bytes(solved_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        session_id = self._session_id(stored.text, source="drop.fit")
        client.post(
            f"/admin/photos/{self.photo_id}/session",
            data={"session_id": session_id},
            follow_redirects=True,
        )
        cleared = client.post(
            f"/admin/sessions/{session_id}/delete",
            follow_redirects=True,
        )
        self.assertNotIn("drop.fit", cleared.text)
        page = client.get(f"/admin/albums/{self.album_id}")
        self.assertIn("orion.jpg", page.text)

    def test_legacy_album_becomes_one_session(self):
        db = SessionLocal()
        try:
            photo = db.get(Photo, int(self.photo_id))
            photo.session_id = None
            photo.fits_header = pack_header("legacy.fit", _wcs_cards("Legacy"))
            plain = Photo(
                album_id=photo.album_id,
                filename="plain.jpg",
                original_name="plain.jpg",
                sort_order=20,
            )
            other = Photo(
                album_id=photo.album_id,
                filename="other.jpg",
                original_name="other.jpg",
                sort_order=21,
                fits_header=pack_header(
                    "other.fit",
                    [{"k": "OBJECT", "v": "Other", "c": ""}, {"k": "EXPTIME", "v": "10", "c": ""}],
                ),
            )
            db.add(plain)
            db.add(other)
            db.commit()
            adopt_legacy_fits(db)
            db.refresh(photo)
            db.refresh(plain)
            db.refresh(other)
            self.assertIsNone(photo.fits_header)
            self.assertIsNone(other.fits_header)
            self.assertEqual(photo.capture_session.name, "Legacy")
            self.assertEqual(photo.capture_session.solve_source, "header")
            self.assertEqual(plain.session_id, photo.session_id)
            self.assertEqual(other.session_id, photo.session_id)
            self.assertEqual(
                db.query(Photo).filter(Photo.album_id == photo.album_id, Photo.session_id != photo.session_id).count(),
                0,
            )
        finally:
            db.close()

    def _session_id(self, html: str, source: str = "M42_300s.fit") -> str:
        return _session_id_from(html, source)

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
