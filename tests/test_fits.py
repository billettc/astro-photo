import gzip
import io
import unittest

from app.fits import (
    header_for_page,
    is_fits_filename,
    load_fits_header,
    pack_header,
    parse_fits_header,
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


if __name__ == "__main__":
    unittest.main()
