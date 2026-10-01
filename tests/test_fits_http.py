"""Upload a FIT header onto a photo and see it on the album page."""

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="astro-fits-")
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["DATA_DIR"] = _TMP
os.environ["FIT_MAX_UPLOAD_MB"] = "1"

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from tests.test_fits import fits_bytes, sample_header


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (10, 20, 40)).save(buf, format="JPEG")
    return buf.getvalue()


class FitsUploadTests(unittest.TestCase):
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
        uploaded = cls.client.post(
            f"/admin/albums/{cls.album_id}/upload",
            files=[("files", ("orion.jpg", _jpeg(), "image/jpeg"))],
            follow_redirects=True,
        )
        if uploaded.status_code != 200:
            raise AssertionError(uploaded.status_code)
        marker = 'action="/admin/photos/'
        start = uploaded.text.index(marker) + len(marker)
        cls.photo_id = uploaded.text[start:].split("/", 1)[0]

    def test_header_lands_on_the_album_and_can_be_removed(self):
        client = self.client
        stored = client.post(
            f"/admin/photos/{self.photo_id}/fits",
            files={"file": ("M42_300s.fit", fits_bytes(sample_header()), "application/octet-stream")},
            follow_redirects=True,
        )
        self.assertNotIn("fits_error", stored.text)
        self.assertIn("M42_300s.fit", stored.text)
        self.assertIn("300 s", stored.text)
        self.assertIn('"Object"', stored.text)
        self.assertIn("M42", stored.text)

        album = client.get("/a/orion-night")
        self.assertEqual(album.status_code, 200)
        self.assertIn("This frame", album.text)
        match = re.search(
            r'id="photo-fits-data">(.*?)</script>', album.text, re.DOTALL
        )
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))["1"]
        summary = {row["label"]: row["value"] for row in payload["summary"]}
        self.assertEqual(payload["name"], "M42_300s.fit")
        self.assertEqual(summary["Object"], "M42")
        self.assertEqual(summary["Exposure"], "300 s")
        self.assertEqual(summary["Temperature"], "-10.5 °C")
        self.assertEqual(summary["Binning"], "2×2")

        cleared = client.post(
            f"/admin/photos/{self.photo_id}/fits/delete",
            follow_redirects=True,
        )
        self.assertNotIn("M42_300s.fit", cleared.text)
        after = client.get("/a/orion-night")
        self.assertNotIn("M42_300s.fit", after.text)
        self.assertNotIn("300 s", after.text)

    def test_rejects_a_non_fits_file_and_an_oversize_upload(self):
        client = self.client
        bad = client.post(
            f"/admin/photos/{self.photo_id}/fits",
            files={"file": ("notes.fit", b"hello this is not fits", "application/octet-stream")},
            follow_redirects=False,
        )
        self.assertIn("fits_error", bad.headers["location"])

        wrong_type = client.post(
            f"/admin/photos/{self.photo_id}/fits",
            files={"file": ("notes.jpg", b"nope", "image/jpeg")},
            follow_redirects=False,
        )
        self.assertIn("Choose%20a%20.fit", wrong_type.headers["location"])

        huge = client.post(
            f"/admin/photos/{self.photo_id}/fits",
            files={"file": ("huge.fits", b"\x00" * (1024 * 1024 + 10), "application/octet-stream")},
            follow_redirects=False,
        )
        self.assertIn("larger%20than%201%20MB", huge.headers["location"])
        self.assertTrue(Path(_TMP, "astro.db").exists())

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
