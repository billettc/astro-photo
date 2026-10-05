"""Read a FITS header and turn it into capture details for a session.

Primary HDU cards are used, unless that header is only a stub (NAXIS = 0)
and the next extension has the real cards. The image payload is not stored.
"""

import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path

BLOCK = 2880
CARD = 80
MAX_HEADER_BLOCKS = 256

FITS_SUFFIXES = (".fit", ".fits", ".fts", ".fit.gz", ".fits.gz", ".fts.gz")

# Cards worth surfacing above the full header. First match wins.
_SUMMARY = (
    ("Object", ("OBJECT",)),
    ("Type", ("IMAGETYP",)),
    ("Date", ("DATE-OBS",)),
    ("Exposure", ("EXPTIME", "EXPOSURE")),
    ("Filter", ("FILTER",)),
    ("Telescope", ("TELESCOP",)),
    ("Camera", ("INSTRUME",)),
    ("Gain", ("GAIN",)),
    ("Offset", ("OFFSET",)),
    ("Temperature", ("CCD-TEMP",)),
    ("Binning", None),
    ("Focal length", ("FOCALLEN",)),
    ("RA", ("OBJCTRA", "RA")),
    ("Dec", ("OBJCTDEC", "DEC")),
)

_CAPTURE_KEYS = frozenset(
    {
        "OBJECT",
        "EXPTIME",
        "EXPOSURE",
        "INSTRUME",
        "TELESCOP",
        "FILTER",
        "DATE-OBS",
    }
)

_UNIT_LABELS = {
    "Exposure": "s",
    "Temperature": "°C",
    "Focal length": "mm",
}


class FitsError(ValueError):
    """The upload is not a readable FITS header."""


class SolveError(ValueError):
    """The frame could not be plate-solved."""


@dataclass
class PlateSolution:
    ra: float
    dec: float
    rotation: float
    scale: float
    parity: int
    source: str
    width: int
    height: int


@dataclass
class Pointing:
    """Mount center and pixel scale used to search the catalog."""

    ra: float
    dec: float
    scale: float
    width: int
    height: int


def is_fits_filename(name: str) -> bool:
    lower = Path(name or "").name.lower()
    return any(lower.endswith(suffix) for suffix in FITS_SUFFIXES)


def load_fits_header(fileobj) -> list[dict]:
    """Parse a .fit/.fits/.fts stream, including a gzip wrapper."""
    magic = fileobj.read(2)
    fileobj.seek(0)
    if magic == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=fileobj, mode="rb") as stream:
            return parse_fits_header(stream)
    return parse_fits_header(fileobj)


def parse_fits_header(stream) -> list[dict]:
    primary_raw = _read_raw_cards(stream)
    if primary_raw[0][:8].decode("latin-1").strip() != "SIMPLE":
        raise FitsError("Not a FITS file.")
    primary = _decode_cards(primary_raw)
    if not primary:
        raise FitsError("That FITS header is empty.")
    if _has_capture_keys(primary) or not _is_stub(primary):
        return primary

    try:
        ext_raw = _read_raw_cards(stream)
    except FitsError:
        return primary
    if ext_raw[0][:8].decode("latin-1").strip() != "XTENSION":
        return primary
    extension = _decode_cards(ext_raw)
    if extension and (_has_capture_keys(extension) or not _has_capture_keys(primary)):
        return extension
    return primary


def pack_header(filename: str, cards: list[dict]) -> str:
    payload = {
        "name": Path(filename).name,
        "cards": [
            {"k": card["k"], "v": card["v"], "c": card.get("c") or ""}
            for card in cards
            if card.get("k") and card["k"] != "END"
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def header_for_page(raw: str | None) -> dict | None:
    """JSON-ready header for the album page, or None when the photo has none."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    cards = data.get("cards")
    if not isinstance(cards, list) or not cards:
        return None
    clean = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        key = str(card.get("k") or "").strip()
        if not key or key == "END":
            continue
        clean.append(
            {
                "k": key,
                "v": str(card.get("v") or ""),
                "c": str(card.get("c") or ""),
            }
        )
    if not clean:
        return None
    return {
        "name": str(data.get("name") or ""),
        "summary": summarize_cards(clean),
        "cards": clean,
    }


def summarize_cards(cards: list[dict]) -> list[dict]:
    rows = []
    for label, keys in _SUMMARY:
        if keys is None:
            value = _binning(cards)
        else:
            value = _lookup(cards, keys)
            if value is not None:
                value = _format_summary(label, value)
        if value:
            rows.append({"label": label, "value": value})
    return rows


def _read_raw_cards(stream) -> list[bytes]:
    cards: list[bytes] = []
    found_end = False
    for _ in range(MAX_HEADER_BLOCKS):
        block = stream.read(BLOCK)
        if len(block) < BLOCK:
            break
        for offset in range(0, BLOCK, CARD):
            card = block[offset : offset + CARD]
            cards.append(card)
            if card[:8].decode("latin-1").strip() == "END":
                found_end = True
                break
        if found_end:
            break
    if not cards or not found_end:
        raise FitsError("This FITS header has no END card.")
    return cards


def _decode_cards(raw_cards: list[bytes]) -> list[dict]:
    cards: list[dict] = []
    index = 0
    total = len(raw_cards)
    while index < total:
        raw = raw_cards[index]
        keyword = raw[:8].decode("latin-1").strip()
        if keyword == "END":
            break
        if keyword == "CONTINUE" and cards and cards[-1].pop("_cont", False):
            extra = _parse_continue(raw)
            if extra.endswith("&") and _next_is_continue(raw_cards, index):
                extra = extra[:-1]
                cards[-1]["_cont"] = True
            cards[-1]["v"] += extra
            index += 1
            continue

        parsed = _parse_card(raw)
        index += 1
        if parsed is None:
            continue
        if (
            parsed.pop("_quoted", False)
            and parsed["v"].endswith("&")
            and _next_is_continue(raw_cards, index - 1)
        ):
            parsed["v"] = parsed["v"][:-1]
            parsed["_cont"] = True
        cards.append(parsed)

    for card in cards:
        card.pop("_cont", None)
    return cards


def _parse_card(raw: bytes) -> dict | None:
    text = raw.decode("latin-1")
    keyword = text[:8].rstrip()
    if keyword == "HIERARCH":
        body = text[8:]
        eq = body.find("=")
        if eq == -1:
            comment = " ".join(body.split())
            return {"k": "HIERARCH", "v": comment, "c": ""} if comment else None
        long_key = " ".join(body[:eq].split())
        value, comment = _parse_value(body[eq + 1 :])
        return {"k": long_key or "HIERARCH", "v": value, "c": comment}

    if len(text) > 8 and text[8] == "=":
        value, comment = _parse_value(text[9:])
        quoted = text[9:].lstrip(" ").startswith("'")
        return {"k": keyword.strip(), "v": value, "c": comment, "_quoted": quoted}

    comment = text[8:].strip()
    key = keyword.strip() or "COMMENT"
    if not comment and not keyword.strip():
        return None
    return {"k": key, "v": comment, "c": ""}


def _parse_continue(raw: bytes) -> str:
    text = raw.decode("latin-1")
    value, _comment = _parse_value(text[8:])
    return value


def _parse_value(field: str) -> tuple[str, str]:
    text = field.lstrip(" ")
    if not text:
        return "", ""
    if text[0] == "'":
        chars: list[str] = []
        index = 1
        while index < len(text):
            if text[index] == "'":
                if index + 1 < len(text) and text[index + 1] == "'":
                    chars.append("'")
                    index += 2
                    continue
                index += 1
                break
            chars.append(text[index])
            index += 1
        value = "".join(chars).rstrip(" ")
        rest = text[index:].lstrip(" ")
        comment = rest[1:].strip() if rest.startswith("/") else ""
        return value, comment
    if "/" in text:
        raw, comment = text.split("/", 1)
        return raw.strip(), comment.strip()
    return text.strip(), ""


def _next_is_continue(raw_cards: list[bytes], index: int) -> bool:
    nxt = index + 1
    if nxt >= len(raw_cards):
        return False
    return raw_cards[nxt][:8].decode("latin-1").strip() == "CONTINUE"


def _lookup(cards: list[dict], keys: tuple[str, ...]) -> str | None:
    wanted = set(keys)
    for card in cards:
        if card["k"] in wanted and str(card["v"]).strip():
            return str(card["v"]).strip()
    return None


def _has_capture_keys(cards: list[dict]) -> bool:
    return _lookup(cards, tuple(_CAPTURE_KEYS)) is not None


def _is_stub(cards: list[dict]) -> bool:
    return _int_card(cards, "NAXIS") == 0


def _int_card(cards: list[dict], key: str) -> int | None:
    value = _lookup(cards, (key,))
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _format_summary(label: str, value: str) -> str:
    if label == "Date" and "T" in value:
        return value.replace("T", " ", 1)
    unit = _UNIT_LABELS.get(label)
    if unit and _as_float(value) is not None:
        return f"{_trim_number(value)} {unit}"
    if _as_float(value) is not None and label in {"Gain", "Offset"}:
        return _trim_number(value)
    return value


def _binning(cards: list[dict]) -> str | None:
    x_bin = _lookup(cards, ("XBINNING",))
    y_bin = _lookup(cards, ("YBINNING",))
    if x_bin and y_bin:
        return f"{_trim_number(x_bin)}×{_trim_number(y_bin)}"
    if x_bin or y_bin:
        return _trim_number(x_bin or y_bin)
    return None


def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _trim_number(value: str) -> str:
    number = _as_float(value)
    if number is None:
        return value
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.6g}"


def session_name(cards: list[dict], filename: str) -> str:
    obj = _lookup(cards, ("OBJECT",))
    if obj:
        return obj[:200]
    stem = Path(filename).name
    return (stem or "Session")[:200]


def solution_from_header(cards: list[dict]) -> PlateSolution | None:
    """Image-center solution when the header already carries a WCS."""
    values = _card_values(cards)
    ra0 = _as_float(values.get("CRVAL1", ""))
    dec0 = _as_float(values.get("CRVAL2", ""))
    cd = _cd_matrix(values)
    if ra0 is None or dec0 is None or cd is None:
        return None
    width = _int_card(cards, "NAXIS1") or 0
    height = _int_card(cards, "NAXIS2") or 0
    crpix1 = _as_float(values.get("CRPIX1", ""))
    crpix2 = _as_float(values.get("CRPIX2", ""))
    if crpix1 is None:
        crpix1 = (width + 1) / 2 if width else 1
    if crpix2 is None:
        crpix2 = (height + 1) / 2 if height else 1
    center_x = (width + 1) / 2 if width else crpix1
    center_y = (height + 1) / 2 if height else crpix2
    ra, dec = _tan_pix_to_world(center_x, center_y, crpix1, crpix2, ra0, dec0, cd)
    scale = _pixel_scale(cd)
    if scale <= 0:
        return None
    det = cd[0][0] * cd[1][1] - cd[0][1] * cd[1][0]
    return PlateSolution(
        ra=ra % 360,
        dec=max(-90.0, min(90.0, dec)),
        rotation=_rotation(cd) % 360,
        scale=scale,
        parity=-1 if det < 0 else 1,
        source="header",
        width=width,
        height=height,
    )


def pointing_from_header(cards: list[dict], width: int, height: int) -> Pointing:
    """RA, Dec, and arcsec/pixel from the mount keywords, for a catalog search."""
    values = _card_values(cards)
    ra = _ra_degrees(values)
    dec = _dec_degrees(values)
    if ra is None or dec is None:
        raise SolveError("The header has no RA and Dec, so the field cannot be searched.")
    scale = _scale_hint(values)
    if scale is None or scale <= 0:
        raise SolveError(
            "The header has no pixel scale. It needs focal length and pixel size, or a WCS."
        )
    if width <= 0 or height <= 0:
        raise SolveError("The header has no image size.")
    if max(width, height) * scale / 3600 > 12:
        raise SolveError(
            "This field is wider than 12°. The server solver is for telescope frames."
        )
    return Pointing(ra=ra % 360, dec=dec, scale=scale, width=width, height=height)


def radec_to_tan_arcsec(ra: float, dec: float, ra0: float, dec0: float) -> tuple[float, float]:
    """Offset from (ra0, dec0) on the tangent plane, in arcseconds. +x is east."""
    ra_r = math.radians(ra)
    dec_r = math.radians(dec)
    ra0_r = math.radians(ra0)
    dec0_r = math.radians(dec0)
    cos_c = math.sin(dec0_r) * math.sin(dec_r) + math.cos(dec0_r) * math.cos(
        dec_r
    ) * math.cos(ra_r - ra0_r)
    if cos_c <= 1e-8:
        raise SolveError("That pointing is too far from the frame to project.")
    xi = math.cos(dec_r) * math.sin(ra_r - ra0_r) / cos_c
    eta = (
        math.cos(dec0_r) * math.sin(dec_r)
        - math.sin(dec0_r) * math.cos(dec_r) * math.cos(ra_r - ra0_r)
    ) / cos_c
    arcsec = 3600 * 180 / math.pi
    return xi * arcsec, eta * arcsec


def tan_arcsec_to_radec(xi: float, eta: float, ra0: float, dec0: float) -> tuple[float, float]:
    """Inverse of radec_to_tan_arcsec. xi and eta are arcseconds."""
    return _intermediate_to_radec(xi / 3600, eta / 3600, ra0, dec0)


def _cd_from_plate(scale_arcsec: float, rotation_deg: float, parity: int):
    """CD matrix from the stored plate solution. Degrees per pixel.

    rotation_deg is the position angle of increasing FITS Y, east of north.
    parity -1 is east-left of north, the usual astronomical frame.
    """
    seconds = scale_arcsec / 3600.0
    theta = math.radians(rotation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    if parity < 0:
        return ((-seconds * cos_t, seconds * sin_t), (seconds * sin_t, seconds * cos_t))
    return ((seconds * cos_t, seconds * sin_t), (-seconds * sin_t, seconds * cos_t))


def sky_to_fits_pixel(
    ra: float,
    dec: float,
    *,
    ra0: float,
    dec0: float,
    rotation_deg: float,
    scale_arcsec: float,
    parity: int,
    width: int,
    height: int,
) -> tuple[float, float]:
    """1-based FITS pixel of a sky position. The tangent point is the image center."""
    xi_as, eta_as = radec_to_tan_arcsec(ra, dec, ra0, dec0)
    xi = xi_as / 3600.0
    eta = eta_as / 3600.0
    cd = _cd_from_plate(scale_arcsec, rotation_deg, parity)
    a, b = cd[0]
    c, d = cd[1]
    det = a * d - b * c
    if abs(det) < 1e-20:
        raise SolveError("The plate solution has no scale.")
    dx = (d * xi - b * eta) / det
    dy = (-c * xi + a * eta) / det
    return (width + 1) / 2 + dx, (height + 1) / 2 + dy


def fits_to_display(
    fits_x: float,
    fits_y: float,
    fit_w: float,
    fit_h: float,
    image_w: float,
    image_h: float,
) -> tuple[float, float]:
    """JPEG pixel of a 1-based FITS pixel when FITS Y points up.

    Origin is the top-left. Some exports, including Seestar, save file order
    instead, with row 0 at the top. The viewer picks that row order, then a
    crop correction, so a resized export of the same frame can still line up.
    """
    x = (fits_x - 0.5) * (image_w / fit_w)
    y = (fit_h + 0.5 - fits_y) * (image_h / fit_h)
    return x, y


def _card_values(cards: list[dict]) -> dict:
    out = {}
    for card in cards:
        key = card.get("k")
        if key and key not in out:
            out[key] = str(card.get("v") or "")
    return out


def _cd_matrix(values: dict) -> tuple[tuple[float, float], tuple[float, float]] | None:
    keys = ("CD1_1", "CD1_2", "CD2_1", "CD2_2")
    if all(values.get(key, "").strip() for key in keys):
        nums = [_as_float(values[key]) for key in keys]
        if any(number is None for number in nums):
            return None
        return ((nums[0], nums[1]), (nums[2], nums[3]))
    cdelt1 = _as_float(values.get("CDELT1", ""))
    cdelt2 = _as_float(values.get("CDELT2", ""))
    if cdelt1 is None or cdelt2 is None:
        return None
    crota = _as_float(values.get("CROTA2", "") or "0") or 0.0
    cos_a = math.cos(math.radians(crota))
    sin_a = math.sin(math.radians(crota))
    return (
        (cdelt1 * cos_a, -cdelt2 * sin_a),
        (cdelt1 * sin_a, cdelt2 * cos_a),
    )


def _pixel_scale(cd) -> float:
    sx = math.hypot(cd[0][0], cd[1][0]) * 3600
    sy = math.hypot(cd[0][1], cd[1][1]) * 3600
    if sx <= 0 or sy <= 0:
        return max(sx, sy)
    return (sx + sy) / 2


def _rotation(cd) -> float:
    """Position angle of increasing FITS Y, degrees east of north."""
    return math.degrees(math.atan2(cd[0][1], cd[1][1]))


def _tan_pix_to_world(px, py, crpix1, crpix2, ra0, dec0, cd) -> tuple[float, float]:
    dx = px - crpix1
    dy = py - crpix2
    xi = cd[0][0] * dx + cd[0][1] * dy
    eta = cd[1][0] * dx + cd[1][1] * dy
    return _intermediate_to_radec(xi, eta, ra0, dec0)


def _intermediate_to_radec(xi_deg, eta_deg, ra0, dec0) -> tuple[float, float]:
    xi = math.radians(xi_deg)
    eta = math.radians(eta_deg)
    ra0_r = math.radians(ra0)
    dec0_r = math.radians(dec0)
    denom = math.cos(dec0_r) - eta * math.sin(dec0_r)
    ra = math.degrees(ra0_r + math.atan2(xi, denom))
    dec = math.degrees(
        math.atan2(
            math.sin(dec0_r) + eta * math.cos(dec0_r),
            math.hypot(xi, denom),
        )
    )
    return ra, dec


def _ra_degrees(values: dict) -> float | None:
    if values.get("OBJCTRA", "").strip():
        return _sexagesimal(values["OBJCTRA"], hours=True)
    if values.get("RA", "").strip():
        return _sexagesimal(values["RA"], hours=False, hours_if_split=True)
    return _as_float(values.get("CRVAL1", ""))


def _dec_degrees(values: dict) -> float | None:
    for key in ("OBJCTDEC", "DEC"):
        if values.get(key, "").strip():
            return _sexagesimal(values[key], hours=False)
    return _as_float(values.get("CRVAL2", ""))


def _sexagesimal(value: str, hours: bool, hours_if_split: bool = False) -> float | None:
    text = value.strip().lower()
    negative = text.startswith("-")
    text = (
        text.replace("°", " ")
        .replace("'", " ")
        .replace('"', " ")
        .replace("h", " ")
        .replace("m", " ")
        .replace("s", " ")
        .replace("d", " ")
        .replace(":", " ")
        .replace("+", " ")
        .replace("-", " ")
    )
    try:
        nums = [float(part) for part in text.split()]
    except ValueError:
        return None
    if not nums:
        return None
    as_hours = hours or (hours_if_split and len(nums) > 1)
    if len(nums) == 1:
        number = nums[0] * (15 if as_hours else 1)
    else:
        number = abs(nums[0]) + nums[1] / 60
        if len(nums) > 2:
            number += nums[2] / 3600
        if as_hours:
            number *= 15
    if negative:
        number = -abs(number)
    return number


def _scale_hint(values: dict) -> float | None:
    for key in ("PIXSCALE", "SECPIX"):
        scale = _as_float(values.get(key, ""))
        if scale and scale > 0:
            return scale
    cdelt = _as_float(values.get("CDELT1", ""))
    if cdelt is None:
        cdelt = _as_float(values.get("CDELT2", ""))
    if cdelt:
        return abs(cdelt) * 3600
    focal = _as_float(values.get("FOCALLEN", ""))
    pix = None
    for key in ("XPIXSZ", "PIXSIZE1", "PIXSIZE", "PIXELSIZE"):
        pix = _as_float(values.get(key, ""))
        if pix:
            break
    if not focal or not pix or focal <= 0 or pix <= 0:
        return None
    binning = _as_float(values.get("XBINNING", "") or "1") or 1
    return 206.265 * pix * binning / focal
