"""The server matcher recovers a known field without calling Gaia."""

import math
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from app.fits import Pointing, tan_arcsec_to_radec
from app.solver import _apply, catalog_mag, detect_stars, fetch_gaia, match_image, parse_gaia_payload


def _paint(image, x, y, amp):
    col = int(round(x)) - 1
    row = int(round(y)) - 1
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            rr = row + dy
            cc = col + dx
            if 0 <= rr < image.shape[0] and 0 <= cc < image.shape[1]:
                image[rr, cc] += amp * math.exp(-(dx * dx + dy * dy) / 1.2)


class SolverMatchTests(unittest.TestCase):
    def test_match_recovers_center_scale_and_rotation(self):
        width, height = 120, 100
        scale = 10.0
        rot = math.radians(20)
        ra0, dec0 = 83.8, -5.4
        center = ((width + 1) / 2, (height + 1) / 2)
        pixels = [
            (30, 28),
            (96, 32),
            (38, 78),
            (104, 84),
            (70, 58),
            (24, 60),
            (82, 48),
            (54, 36),
        ]
        image = np.zeros((height, width), dtype=np.float32)
        catalog = []
        for index, (x, y) in enumerate(pixels):
            xi, eta = _apply((x, y), scale, rot, center, (0.0, 0.0), False)
            catalog.append(tan_arcsec_to_radec(xi, eta, ra0, dec0))
            _paint(image, x, y, 220 - index * 18)

        found = detect_stars(image)
        self.assertGreaterEqual(len(found), 8)

        hint = Pointing(ra=ra0 + 0.02, dec=dec0 - 0.015, scale=12.0, width=width, height=height)
        # Brighter stars just beside the sensor used to occupy the triangle
        # list and hide the stars that are actually on the chip.
        inner = 0.5 * min(width, height) * hint.scale
        outer = 0.5 * math.hypot(width * hint.scale, height * hint.scale)
        ring = (inner + outer) / 2
        beside = [
            tan_arcsec_to_radec(
                ring * math.cos(math.radians(angle)),
                ring * math.sin(math.radians(angle)),
                hint.ra,
                hint.dec,
            )
            for angle in range(0, 360, 45)
        ]
        outside = tan_arcsec_to_radec(50000, 50000, ra0, dec0)
        solution = match_image(image, hint, beside + [outside] * 6 + catalog)
        self.assertEqual(solution.source, "solver")
        self.assertAlmostEqual(solution.ra, ra0, delta=0.03)
        self.assertAlmostEqual(solution.dec, dec0, delta=0.03)
        self.assertAlmostEqual(solution.scale, scale, delta=0.4)
        self.assertAlmostEqual(solution.rotation, (-math.degrees(rot)) % 360, delta=1.5)

    def test_clipped_stars_outrank_the_right_edge(self):
        height, width = 80, 140
        image = np.full((height, width), 100, dtype=np.float32)
        stars = [
            (30, 20),
            (50, 40),
            (36, 62),
            (90, 24),
            (100, 58),
            (70, 44),
            (24, 48),
            (110, 36),
        ]
        for index, (x, y) in enumerate(stars):
            _paint(image, x, y, 800 - index * 40)
        image[image > 500] = 500
        for x, y in stars:
            image[y - 1, x - 1] = 501
        for y in range(8, 68, 5):
            image[y, width - 3] = 501
        found = detect_stars(image, limit=8)
        self.assertEqual(len(found), 8)
        for _flux, x, y in found:
            self.assertLess(x, width - 10)
            self.assertTrue(
                any(math.hypot(x - sx, y - sy) < 3 for sx, sy in stars),
                (x, y),
            )

    def test_gaia_payload_keeps_the_brightest_stars(self):
        stars = parse_gaia_payload({"data": [[1.5, -2.25, 8.1], [3, 4]]})
        self.assertEqual(stars, [(1.5, -2.25), (3.0, 4.0)])
        ordered = parse_gaia_payload(
            {"data": [[10, 1, 12.0], [20, 2, 6.0], [30, 3, 9.0]]}
        )
        self.assertEqual(ordered, [(20.0, 2.0), (30.0, 3.0), (10.0, 1.0)])

    def test_wide_field_uses_a_brighter_catalog_limit(self):
        self.assertEqual(catalog_mag(9), 11)
        self.assertEqual(catalog_mag(0.1), 16)

    def test_fetch_gaia_retries_a_timeout(self):
        hint = Pointing(ra=10, dec=20, scale=2, width=100, height=80)
        payload = {"data": [[10, 20, 8], [11, 21, 9], [12, 22, 7], [13, 23, 6]]}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        calls = {"n": 0}

        def post(*_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("timed out")
            return Response()

        with patch("app.solver.httpx.post", side_effect=post):
            stars = fetch_gaia(hint)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(stars[0], (13.0, 23.0))


if __name__ == "__main__":
    unittest.main()
