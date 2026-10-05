"""Plate-solve a FIT image on the server.

A header that already has a WCS is used as-is. Otherwise the brightest stars
are matched to a Gaia patch around the mount pointing. The FIT file itself
is not kept.
"""

import gzip
import logging
import math
from itertools import combinations
from pathlib import Path

import httpx
import numpy as np

from app.fits import (
    FitsError,
    PlateSolution,
    Pointing,
    SolveError,
    load_fits_header,
    pointing_from_header,
    radec_to_tan_arcsec,
    solution_from_header,
    tan_arcsec_to_radec,
    _decode_cards,
    _int_card,
    _is_stub,
    _lookup,
    _read_raw_cards,
)

_GAIA_URL = "https://gea.esac.esa.int/tap-server/tap/sync"
_MAX_PIXELS = 50_000_000
log = logging.getLogger(__name__)


def solve_fits_file(path: str | Path) -> PlateSolution:
    path = Path(path)
    with path.open("rb") as stream:
        cards = load_fits_header(stream)
    found = solution_from_header(cards)
    if found:
        return found
    image = read_fits_image(path)
    height, width = image.shape
    hint = pointing_from_header(cards, int(width), int(height))
    catalog = fetch_gaia(hint)
    return match_image(image, hint, catalog)


def read_fits_image(path: str | Path):
    """Return the first 2-D image as a float32 array, FITS origin at [0, 0]."""
    with _open_fits(path) as stream:
        cards, data = _read_image_hdu(stream)
        if data is None and _is_stub(cards):
            try:
                _cards, data = _read_image_hdu(stream)
            except FitsError:
                data = None
        if data is None:
            raise SolveError("This FIT has no image to plate-solve.")
        if data.size > _MAX_PIXELS:
            raise SolveError("This image is too large to plate-solve.")
        return data


def catalog_mag(area: float) -> int:
    """Bright-star limit for a field of this size, in square degrees.

    Asking Gaia to sort a wide, faint patch (magnitude 15 over a few degrees)
    runs past the sync timeout. A brighter limit returns in seconds, and the
    brightest stars are enough to match the frame.
    """
    if area >= 20:
        return 10
    if area >= 4:
        return 11
    if area >= 0.5:
        return 13
    return 16


def fetch_gaia(hint: Pointing, limit: int = 40) -> list[tuple[float, float]]:
    width_deg = hint.width * hint.scale / 3600
    height_deg = hint.height * hint.scale / 3600
    radius = min(6.0, max(0.05, 0.5 * math.hypot(width_deg, height_deg) * 1.4))
    mag = catalog_mag(width_deg * height_deg)
    query = (
        "SELECT ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE "
        "CONTAINS(POINT('ICRS',ra,dec),"
        f"CIRCLE('ICRS',{hint.ra:.6f},{hint.dec:.6f},{radius:.5f}))=1 "
        f"AND phot_g_mean_mag < {mag}"
    )
    body = {
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "json",
        "QUERY": query,
    }
    last = None
    stars = None
    for attempt in range(2):
        try:
            response = httpx.post(
                _GAIA_URL,
                data=body,
                headers={"User-Agent": "astro-photo"},
                timeout=40,
            )
            response.raise_for_status()
            stars = parse_gaia_payload(response.json(), limit=limit)
            last = None
            break
        except httpx.HTTPStatusError as exc:
            last = exc
            log.warning("gaia tap HTTP %s", exc.response.status_code)
            if exc.response.status_code < 500:
                break
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            last = exc
            log.warning("gaia tap failed: %s", exc)
    if last is not None or stars is None:
        raise SolveError("The star catalog could not be reached.") from last
    if len(stars) < 4:
        raise SolveError("The star catalog has too few stars in this field.")
    return stars


def parse_gaia_payload(payload: dict, limit: int = 40) -> list[tuple[float, float]]:
    ranked = []
    for row in payload.get("data") or []:
        if not isinstance(row, list) or len(row) < 2:
            continue
        try:
            ra, dec = float(row[0]), float(row[1])
        except (TypeError, ValueError):
            continue
        mag = 99.0
        if len(row) > 2 and row[2] is not None:
            try:
                mag = float(row[2])
            except (TypeError, ValueError):
                mag = 99.0
        ranked.append((mag, ra, dec))
    ranked.sort()
    return [(ra, dec) for _mag, ra, dec in ranked[:limit]]


def match_image(image, hint: Pointing, catalog: list[tuple[float, float]]) -> PlateSolution:
    detected = detect_stars(image, limit=12)
    if len(detected) < 4:
        raise SolveError("Not enough stars were found in the image.")
    if len(catalog) < 4:
        raise SolveError("The star catalog has too few stars in this field.")
    image_pts = [(x, y) for _flux, x, y in detected]
    # The cone is wider than the sensor, so the brightest catalog stars can
    # lie off the frame. Triangles only work for stars that can be on it.
    catalog_xy = _stars_on_frame(catalog[:40], hint)
    if len(catalog_xy) < 4:
        catalog_xy = [
            radec_to_tan_arcsec(ra, dec, hint.ra, hint.dec) for ra, dec in catalog[:40]
        ]
    # Eight is not enough on a wide frame: the brightest detections and the
    # brightest on-frame catalog stars are often different sets. Twelve stays
    # under a tenth of a second and still shares a triangle.
    image_match = image_pts[:12]
    catalog_match = catalog_xy[:12]
    needed = 4 if len(image_match) < 7 else 5
    best = _best_match(image_match, catalog_match, catalog_xy, hint.scale, needed)
    if best is None:
        raise SolveError("The stars in this frame did not match the catalog.")
    scale, rot, origin, sky_origin, flip = best
    cx = (hint.width + 1) / 2
    cy = (hint.height + 1) / 2
    xi, eta = _apply((cx, cy), scale, rot, origin, sky_origin, flip)
    ra, dec = tan_arcsec_to_radec(xi, eta, hint.ra, hint.dec)
    up = _apply((origin[0], origin[1] + 1), scale, rot, origin, sky_origin, flip)
    base = _apply(origin, scale, rot, origin, sky_origin, flip)
    rotation = math.degrees(math.atan2(up[0] - base[0], up[1] - base[1])) % 360
    return PlateSolution(
        ra=ra % 360,
        dec=max(-90.0, min(90.0, dec)),
        rotation=rotation,
        scale=scale,
        parity=-1 if flip else 1,
        source="solver",
        width=hint.width,
        height=hint.height,
    )


def detect_stars(image, limit: int = 12) -> list[tuple[float, float, float]]:
    """Brightest local peaks as (window sum, fits_x, fits_y). FITS pixels are 1-based."""
    height, width = image.shape
    step = max(1, int(math.ceil(max(height, width) / 900)))
    small = image[::step, ::step]
    if small.shape[0] < 5 or small.shape[1] < 5:
        return []
    med = float(np.median(small))
    mad = float(np.median(np.abs(small - med)))
    sigma = 1.4826 * mad
    if sigma < 1e-3:
        sigma = max(1.0, 0.05 * (float(small.max()) - med))
    thresh = med + 6 * sigma
    center = small[1:-1, 1:-1]
    mask = center >= thresh
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            mask &= center > small[1 + dy : small.shape[0] - 1 + dy, 1 + dx : small.shape[1] - 1 + dx]
    rows, cols = np.nonzero(mask)
    candidates = []
    for row, col in zip(rows.tolist(), cols.tolist()):
        full_row = (row + 1) * step
        full_col = (col + 1) * step
        r0 = max(0, full_row - step)
        r1 = min(height, full_row + step + 1)
        c0 = max(0, full_col - step)
        c1 = min(width, full_col + step + 1)
        window = image[r0:r1, c0:c1]
        local_row, local_col = np.unravel_index(int(np.argmax(window)), window.shape)
        fits_x = c0 + int(local_col) + 1
        fits_y = r0 + int(local_row) + 1
        # Clipped cores share one peak value, and those ties sort by x, so the
        # right edge crowds out the frame. The window sum still grows with the bloom.
        flux = float(window.sum())
        candidates.append((flux, fits_x, fits_y))
    candidates.sort(reverse=True)
    kept = []
    for flux, x, y in candidates:
        if any(math.hypot(x - px, y - py) < 5 for _flux, px, py in kept):
            continue
        kept.append((flux, x, y))
        if len(kept) >= limit:
            break
    return kept


def _stars_on_frame(catalog, hint: Pointing) -> list[tuple[float, float]]:
    """Tangent-plane positions that can land on the sensor.

    Stars inside the inscribed circle are on the chip at any rotation, so
    they come first. Stars farther out, toward the corners, might be on it
    and follow. A wider circle lets brighter stars from just beside the
    sensor crowd out the ones that were actually recorded.
    """
    half_w = 0.5 * hint.width * hint.scale
    half_h = 0.5 * hint.height * hint.scale
    inner = min(half_w, half_h)
    outer = math.hypot(half_w, half_h)
    certain = []
    possible = []
    for ra, dec in catalog:
        try:
            xi, eta = radec_to_tan_arcsec(ra, dec, hint.ra, hint.dec)
        except SolveError:
            continue
        radius = math.hypot(xi, eta)
        if radius <= inner:
            certain.append((xi, eta))
        elif radius <= outer:
            possible.append((xi, eta))
    return certain + possible


def _best_match(image_pts, triangle_cat, inlier_cat, hint_scale, needed):
    image_tris = _triangles(image_pts)
    cat_tris = _triangles(triangle_cat)
    best = None
    best_key = (-1, 0.0)
    for image_tri in image_tris:
        for cat_tri in cat_tris:
            if abs(image_tri[0] - cat_tri[0]) > 0.04 or abs(image_tri[1] - cat_tri[1]) > 0.04:
                continue
            pairs = list(zip(image_tri[2], cat_tri[2]))
            p0 = image_pts[pairs[2][0]]
            p1 = image_pts[pairs[0][0]]
            s0 = triangle_cat[pairs[2][1]]
            s1 = triangle_cat[pairs[0][1]]
            for flip in (False, True):
                transform = _similarity(p0, p1, s0, s1, flip)
                if transform is None:
                    continue
                scale = transform[0]
                if scale <= 0 or not (hint_scale / 3 <= scale <= hint_scale * 3):
                    continue
                mapped = [_apply(point, *transform) for point in image_pts]
                count, rms = _inliers(mapped, inlier_cat, max(3.0, 4 * scale))
                if count < needed:
                    continue
                key = (count, -rms)
                if key > best_key:
                    best_key = key
                    best = transform
    return best


def _triangles(points):
    found = []
    for indexes in combinations(range(len(points)), 3):
        tri = _triangle([points[i] for i in indexes], indexes)
        if tri is not None:
            found.append(tri)
    return found


def _triangle(points, indexes):
    sides = []
    for index in range(3):
        other = [points[i] for i in range(3) if i != index]
        length = math.hypot(other[0][0] - other[1][0], other[0][1] - other[1][1])
        sides.append((length, indexes[index]))
    sides.sort()
    longest = sides[2][0]
    if longest < 3:
        return None
    return (sides[0][0] / longest, sides[1][0] / longest, [item[1] for item in sides])


def _similarity(p0, p1, s0, s1, flip):
    vx, vy = p1[0] - p0[0], p1[1] - p0[1]
    if flip:
        vx = -vx
    sx, sy = s1[0] - s0[0], s1[1] - s0[1]
    pixel = math.hypot(vx, vy)
    sky = math.hypot(sx, sy)
    if pixel < 1e-6 or sky < 1e-6:
        return None
    scale = sky / pixel
    rot = math.atan2(sy, sx) - math.atan2(vy, vx)
    return scale, rot, p0, s0, flip


def _apply(point, scale, rot, origin, sky_origin, flip):
    dx = point[0] - origin[0]
    dy = point[1] - origin[1]
    if flip:
        dx = -dx
    cos_r = math.cos(rot)
    sin_r = math.sin(rot)
    return (
        sky_origin[0] + scale * (cos_r * dx - sin_r * dy),
        sky_origin[1] + scale * (sin_r * dx + cos_r * dy),
    )


def _inliers(mapped, catalog_xy, tolerance):
    used = set()
    total = 0.0
    count = 0
    for x, y in mapped:
        best_i = None
        best_d = tolerance
        for index, (cx, cy) in enumerate(catalog_xy):
            if index in used:
                continue
            distance = math.hypot(x - cx, y - cy)
            if distance <= best_d:
                best_d = distance
                best_i = index
        if best_i is not None:
            used.add(best_i)
            count += 1
            total += best_d * best_d
    rms = math.sqrt(total / count) if count else 1e9
    return count, rms


def _open_fits(path):
    raw = open(path, "rb")
    magic = raw.read(2)
    raw.seek(0)
    if magic == b"\x1f\x8b":
        return _GzipWrap(raw, gzip.GzipFile(fileobj=raw, mode="rb"))
    return raw


class _GzipWrap:
    def __init__(self, raw, gz):
        self.raw = raw
        self.gz = gz

    def __enter__(self):
        return self.gz

    def __exit__(self, exc_type, exc, tb):
        self.gz.close()
        self.raw.close()


def _read_image_hdu(stream):
    raw_cards = _read_raw_cards(stream)
    cards = _decode_cards(raw_cards)
    naxis = _int_card(cards, "NAXIS") or 0
    if naxis == 0:
        return cards, None
    if naxis != 2:
        raise SolveError("Only a 2-axis FIT image can be plate-solved.")
    if _lookup(cards, ("ZIMAGE", "ZCMPTYPE")):
        raise SolveError("Compressed FIT images are not supported. Upload an uncompressed file.")
    width = _int_card(cards, "NAXIS1") or 0
    height = _int_card(cards, "NAXIS2") or 0
    bitpix = _int_card(cards, "BITPIX")
    dtype = {8: "u1", 16: ">i2", 32: ">i4", -32: ">f4", -64: ">f8"}.get(bitpix or 0)
    if not width or not height or dtype is None:
        raise SolveError("This FIT image uses an unsupported pixel format.")
    if width * height > _MAX_PIXELS:
        raise SolveError("This image is too large to plate-solve.")
    nbytes = width * height * abs(bitpix) // 8
    padded = nbytes + ((-nbytes) % 2880)
    blob = stream.read(padded)
    if len(blob) < nbytes:
        raise SolveError("The FIT image is truncated.")
    data = np.frombuffer(blob[:nbytes], dtype=dtype).astype(np.float32)
    data = data.reshape((height, width))
    bzero = float(_lookup(cards, ("BZERO",)) or 0)
    bscale = float(_lookup(cards, ("BSCALE",)) or 1)
    if bscale != 1 or bzero != 0:
        data = data * np.float32(bscale) + np.float32(bzero)
    return cards, data
