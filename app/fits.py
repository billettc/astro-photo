"""Read a FITS header and turn it into capture details for a photo.

The image payload is never kept. Primary HDU cards are used, unless that
header is only a stub (NAXIS = 0) and the next extension has the real cards.
"""

import gzip
import json
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


def fits_by_photo(photos) -> dict:
    out = {}
    for photo in photos:
        payload = header_for_page(getattr(photo, "fits_header", None))
        if payload:
            out[str(photo.id)] = payload
    return out


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
