"""The photo overlay names Sadr and the Gamma Cygni Nebula on the solved frame."""

import json
import math
import unittest
from pathlib import Path

from app.fits import sky_to_fits_pixel

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"

# Solved Sadr session: center, rotation, scale, and sensor size.
SADR = dict(
    ra0=305.560151674513,
    dec0=40.352756453405824,
    rotation_deg=180.38539099762906,
    scale_arcsec=3.6654791517996284,
    parity=1,
    width=2160,
    height=3840,
)


def _objects(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        rows.append(json.loads(line))
    return rows


def _separation(ra_hours_a, dec_a, ra_hours_b, dec_b):
    rad = math.radians
    dec1, dec2 = rad(dec_a), rad(dec_b)
    dra = rad((ra_hours_b - ra_hours_a) * 15)
    a = math.sin((dec2 - dec1) / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin(dra / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))


class OverlayCatalogTests(unittest.TestCase):
    def test_sadr_and_ic1318_both_land_in_the_frame(self):
        stars = _objects(JS / "star-catalog.js")
        overlay = _objects(JS / "overlay-catalog.js")
        sadr = next(star for star in stars if star.get("name") == "Sadr")
        nebula = next(obj for obj in overlay if obj["id"] == "IC 1318")

        self.assertEqual(sadr["id"], "γ Cyg")
        self.assertEqual(sadr["type"], "STAR")
        self.assertAlmostEqual(sadr["ra"], 20.370473, places=5)
        self.assertAlmostEqual(sadr["dec"], 40.25668, places=4)
        self.assertLessEqual(sadr["mag"], 4)

        self.assertEqual(nebula["name"], "Gamma Cygni Nebula")
        self.assertAlmostEqual(nebula["ra"], 20.28, places=4)
        self.assertAlmostEqual(nebula["dec"], 41.9567, places=4)
        # Farther apart than the overlay's same-position merge, so both are drawn.
        self.assertGreater(_separation(sadr["ra"], sadr["dec"], nebula["ra"], nebula["dec"]), 0.07)

        for obj in (sadr, nebula):
            x, y = sky_to_fits_pixel(obj["ra"] * 15, obj["dec"], **SADR)
            self.assertGreater(x, 0.5)
            self.assertLess(x, SADR["width"] + 0.5)
            self.assertGreater(y, 0.5)
            self.assertLess(y, SADR["height"] + 0.5)

        self.assertEqual(len(stars), 515)
        self.assertTrue(all(star["mag"] <= 4 and star["type"] == "STAR" for star in stars))
        self.assertNotIn("Blaze Star", {star.get("name") for star in stars})
        page = (ROOT / "app" / "templates" / "album.html").read_text(encoding="utf-8")
        self.assertIn("star-catalog.js", page)
        script = (JS / "app.js").read_text(encoding="utf-8")
        self.assertIn("ASTRO_STARS", script)
