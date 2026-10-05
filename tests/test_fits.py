import gzip
import io
import math
import unittest

from app.fits import (
    SolveError,
    _card_values,
    _cd_matrix,
    _tan_pix_to_world,
    fits_to_display,
    header_for_page,
    is_fits_filename,
    load_fits_header,
    pack_header,
    parse_fits_header,
    pointing_from_header,
    radec_to_tan_arcsec,
    sky_to_fits_pixel,
    solution_from_header,
    tan_arcsec_to_radec,
)


def card(keyword, value=None, comment=""):
    if value is None:
        text = f"{keyword:<8}{comment}"
    elif isinstance(value, bool):
        text = f"{keyword:<8}= {'T' if value else 'F':>20}"
        if comment:
            text += f" / {comment}"
    elif isinstance(value, str):
        text = f"{keyword:<8}= '{value}'"
        if comment:
            text += f" / {comment}"
    else:
        text = f"{keyword:<8}= {value:>20}"
        if comment:
            text += f" / {comment}"
    if len(text) > 80:
        raise AssertionError(text)
    return text


def fits_bytes(lines):
    raw = bytearray()
    for line in lines:
        encoded = line.encode("ascii")
        if len(encoded) > 80:
            raise AssertionError(line)
        raw.extend(encoded.ljust(80))
    raw.extend(b" " * ((-len(raw)) % 2880))
    return bytes(raw)


def solved_header():
    scale = 2 / 3600
    lines = [
        card("SIMPLE", True),
        card("BITPIX", 16),
        card("NAXIS", 2),
        card("NAXIS1", 100),
        card("NAXIS2", 80),
        card("OBJECT", "M42"),
        card("EXPTIME", 300.0),
        card("CRPIX1", 50.5),
        card("CRPIX2", 40.5),
        card("CRVAL1", 83.8),
        card("CRVAL2", -5.391111),
        card("CD1_1", -scale),
        card("CD1_2", 0.0),
        card("CD2_1", 0.0),
        card("CD2_2", scale),
        card("CTYPE1", "RA---TAN"),
        card("CTYPE2", "DEC--TAN"),
        "END",
    ]
    return lines


def sample_header():
    return [
        card("SIMPLE", True, "conforms to FITS"),
        card("BITPIX", 16),
        card("NAXIS", 2),
        card("NAXIS1", 4),
        card("NAXIS2", 2),
        card("EXTEND", True),
        card("OBJECT", "M42", "target"),
        card("IMAGETYP", "LIGHT"),
        card("DATE-OBS", "2024-11-02T05:14:33.123"),
        card("EXPTIME", 300.0, "seconds"),
        card("FILTER", "Ha"),
        card("TELESCOP", "Esprit 100"),
        card("INSTRUME", "ASI2600MM"),
        card("GAIN", 100),
        card("OFFSET", 50),
        card("CCD-TEMP", -10.5),
        card("XBINNING", 2),
        card("YBINNING", 2),
        card("FOCALLEN", 550),
        card("OBJCTRA", "05 35 17"),
        card("OBJCTDEC", "-05 23 28"),
        card("COMMENT", comment="a note"),
        "HIERARCH ESO TEL AIRM = 1.42 / airmass",
        "END",
    ]


class FitsHeaderTests(unittest.TestCase):
    def test_capture_summary_and_comments(self):
        cards = parse_fits_header(io.BytesIO(fits_bytes(sample_header())))
        by_key = {card["k"]: card for card in cards}
        self.assertEqual(by_key["OBJECT"]["v"], "M42")
        self.assertEqual(by_key["OBJECT"]["c"], "target")
        self.assertEqual(by_key["COMMENT"]["v"], "a note")
        self.assertEqual(by_key["ESO TEL AIRM"]["v"], "1.42")
        self.assertEqual(by_key["SIMPLE"]["v"], "T")

        page = header_for_page(pack_header("M42_300s.fit", cards))
        summary = {row["label"]: row["value"] for row in page["summary"]}
        self.assertEqual(page["name"], "M42_300s.fit")
        self.assertEqual(summary["Object"], "M42")
        self.assertEqual(summary["Date"], "2024-11-02 05:14:33.123")
        self.assertEqual(summary["Exposure"], "300 s")
        self.assertEqual(summary["Filter"], "Ha")
        self.assertEqual(summary["Temperature"], "-10.5 °C")
        self.assertEqual(summary["Binning"], "2×2")
        self.assertEqual(summary["Focal length"], "550 mm")
        self.assertEqual(summary["Gain"], "100")
        self.assertEqual(summary["RA"], "05 35 17")
        self.assertNotIn("SIMPLE", {card["k"] for card in page["cards"] if False})

    def test_escaped_quote_and_continue(self):
        lines = [
            card("SIMPLE", True),
            card("BITPIX", 16),
            card("NAXIS", 0),
            "OBJECT  = 'it''s a long &'",
            "CONTINUE  'name&'",
            "CONTINUE  ' tonight'",
            "END",
        ]
        cards = parse_fits_header(io.BytesIO(fits_bytes(lines)))
        self.assertEqual(cards[3]["k"], "OBJECT")
        self.assertEqual(cards[3]["v"], "it's a long name tonight")

    def test_stub_primary_uses_extension(self):
        primary = [
            card("SIMPLE", True),
            card("BITPIX", 16),
            card("NAXIS", 0),
            card("EXTEND", True),
            "END",
        ]
        extension = [
            card("XTENSION", "IMAGE"),
            card("BITPIX", 16),
            card("NAXIS", 2),
            card("NAXIS1", 2),
            card("NAXIS2", 2),
            card("OBJECT", "NGC 7000"),
            card("EXPTIME", 120),
            "END",
        ]
        blob = fits_bytes(primary) + fits_bytes(extension) + b"\x00" * 2880
        cards = parse_fits_header(io.BytesIO(blob))
        self.assertEqual(cards[0]["k"], "XTENSION")
        self.assertIn("NGC 7000", [card["v"] for card in cards])

    def test_primary_wins_when_it_has_the_target(self):
        lines = [
            card("SIMPLE", True),
            card("BITPIX", 8),
            card("NAXIS", 0),
            card("OBJECT", "Primary"),
            "END",
        ]
        blob = fits_bytes(lines) + b"not a header"
        cards = parse_fits_header(io.BytesIO(blob))
        self.assertEqual([card["v"] for card in cards if card["k"] == "OBJECT"], ["Primary"])

    def test_gzip_and_rejection(self):
        raw = fits_bytes(sample_header())
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(raw)
        buf.seek(0)
        cards = load_fits_header(buf)
        self.assertTrue(any(card["v"] == "M42" for card in cards))

        with self.assertRaises(Exception):
            parse_fits_header(io.BytesIO(b"not fits at all" + b"\x00" * 3000))

    def test_filename_suffixes(self):
        self.assertTrue(is_fits_filename("frame.FIT"))
        self.assertTrue(is_fits_filename("frame.fits.gz"))
        self.assertFalse(is_fits_filename("frame.jpg"))
        self.assertFalse(is_fits_filename("frame.fit.txt"))

    def test_header_wcs_is_the_image_center(self):
        cards = parse_fits_header(io.BytesIO(fits_bytes(solved_header())))
        solution = solution_from_header(cards)
        self.assertEqual(solution.source, "header")
        self.assertAlmostEqual(solution.ra, 83.8, places=4)
        self.assertAlmostEqual(solution.dec, -5.391111, places=4)
        self.assertAlmostEqual(solution.scale, 2.0, places=3)
        self.assertAlmostEqual(solution.rotation, 0.0, places=3)
        self.assertEqual(solution.parity, -1)

    def test_rotated_wcs(self):
        scale = 2 / 3600
        lines = [
            card("SIMPLE", True),
            card("BITPIX", 16),
            card("NAXIS", 2),
            card("NAXIS1", 10),
            card("NAXIS2", 10),
            card("CRPIX1", 5.5),
            card("CRPIX2", 5.5),
            card("CRVAL1", 10.0),
            card("CRVAL2", 20.0),
            card("CD1_1", 0.0),
            card("CD1_2", scale),
            card("CD2_1", scale),
            card("CD2_2", 0.0),
            "END",
        ]
        solution = solution_from_header(parse_fits_header(io.BytesIO(fits_bytes(lines))))
        self.assertAlmostEqual(solution.rotation, 90.0, places=3)
        self.assertAlmostEqual(solution.ra, 10.0, places=4)
        self.assertAlmostEqual(solution.dec, 20.0, places=4)

    def test_pointing_from_mount_keywords(self):
        lines = sample_header()
        lines.insert(-1, card("XPIXSZ", 3.76))
        cards = parse_fits_header(io.BytesIO(fits_bytes(lines)))
        hint = pointing_from_header(cards, 100, 80)
        self.assertAlmostEqual(hint.ra, 83.820833, places=3)
        self.assertAlmostEqual(hint.dec, -5.391111, places=3)
        self.assertAlmostEqual(hint.scale, 206.265 * 3.76 * 2 / 550, places=3)

    def test_pointing_requires_a_scale(self):
        cards = parse_fits_header(io.BytesIO(fits_bytes(sample_header())))
        with self.assertRaises(SolveError):
            pointing_from_header(cards, 100, 80)

    def test_tangent_plane_roundtrip(self):
        ra, dec = tan_arcsec_to_radec(1200, -800, 83.8, -5.4)
        xi, eta = radec_to_tan_arcsec(ra, dec, 83.8, -5.4)
        self.assertAlmostEqual(xi, 1200, places=3)
        self.assertAlmostEqual(eta, -800, places=3)

    def test_sky_pixel_matches_the_header_and_places_north_up(self):
        cards = parse_fits_header(io.BytesIO(fits_bytes(solved_header())))
        solution = solution_from_header(cards)
        cd = _cd_matrix(_card_values(cards))
        crpix1 = (solution.width + 1) / 2
        crpix2 = (solution.height + 1) / 2
        for px, py in ((1, 1), (50.5, 40.5), (100, 80), (12, 70)):
            ra, dec = _tan_pix_to_world(px, py, crpix1, crpix2, solution.ra, solution.dec, cd)
            fx, fy = sky_to_fits_pixel(
                ra,
                dec,
                ra0=solution.ra,
                dec0=solution.dec,
                rotation_deg=solution.rotation,
                scale_arcsec=solution.scale,
                parity=solution.parity,
                width=solution.width,
                height=solution.height,
            )
            self.assertAlmostEqual(fx, px, places=2)
            self.assertAlmostEqual(fy, py, places=2)

        north_x, north_y = sky_to_fits_pixel(
            solution.ra,
            solution.dec + 20 / 3600,
            ra0=solution.ra,
            dec0=solution.dec,
            rotation_deg=0,
            scale_arcsec=2,
            parity=-1,
            width=100,
            height=80,
        )
        self.assertAlmostEqual(north_x, 50.5, places=2)
        self.assertGreater(north_y, 40.5)
        cos_d = math.cos(math.radians(solution.dec))
        east_x, east_y = sky_to_fits_pixel(
            solution.ra + (20 / 3600) / cos_d,
            solution.dec,
            ra0=solution.ra,
            dec0=solution.dec,
            rotation_deg=0,
            scale_arcsec=2,
            parity=-1,
            width=100,
            height=80,
        )
        self.assertLess(east_x, 50.5)
        self.assertAlmostEqual(east_y, 40.5, places=2)

        center_x, center_y = fits_to_display(50.5, 40.5, 100, 80, 200, 160)
        self.assertAlmostEqual(center_x, 100)
        self.assertAlmostEqual(center_y, 80)
        _, up = fits_to_display(50.5, 50.5, 100, 80, 200, 160)
        self.assertLess(up, center_y)

    def test_positive_parity_puts_east_to_the_right(self):
        ra0, dec0 = 30.0, 10.0
        cos_d = math.cos(math.radians(dec0))
        east_x, east_y = sky_to_fits_pixel(
            ra0 + (10 / 3600) / cos_d,
            dec0,
            ra0=ra0,
            dec0=dec0,
            rotation_deg=0,
            scale_arcsec=2,
            parity=1,
            width=40,
            height=40,
        )
        self.assertAlmostEqual(east_x, 25.5, places=2)
        self.assertAlmostEqual(east_y, 20.5, places=2)


if __name__ == "__main__":
    unittest.main()
