"""Line a photo up with its session plate solution.

A processed JPEG is often a crop of the FIT frame, then resized. The session
solution still describes the full frame, so catalog marks land too close to
the center. A scale and a shift about the photo center puts them back on the
stars.

A phone export of the portrait sensor can also be turned 90° or 270° before
the crop. The search tries those with the unrotated frame, and keeps the
plain mapping when the stars already agree. A native-scale window can also
be a fraction of a degree off that quarter turn.
"""

from dataclasses import dataclass

import numpy as np

from app.fits import sky_to_fits_pixel


@dataclass(frozen=True)
class FrameAlign:
    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0
    # Clockwise quarter turn of the file, after the row-order flip: 0, 90, 180, 270.
    turn: int = 0
    # Extra rotation in degrees about the JPEG center, after the quarter turn.
    # Positive is the sense of the formula in `_rotated_offset`.
    spin: float = 0.0

    def as_dict(self) -> dict:
        return {
            "sx": round(self.sx, 4),
            "sy": round(self.sy, 4),
            "tx": round(self.tx, 1),
            "ty": round(self.ty, 1),
            "turn": int(self.turn),
            "spin": round(self.spin, 2),
        }


IDENTITY = FrameAlign()

# Bump this when the search changes, so a stored answer from an older search
# is measured again. The viewer asks for it with the row order and the turn.
ALIGN_REV = 5

# Turns of the sensor file the export may have applied before the crop.
# 180 is left out: it has the same aspect as no turn, and a shift onto a
# bright nebula can outscore the real crop. display_pixel still understands it.
QUARTER_TURNS = (0, 90, 270)

# The crop search may drift this far off sy/sx. Farther than that is the
# anisotropic stretch of the wrong turn, not a real crop.
_RATIO_SLACK = 0.15

# A native-scale window of the sensor can sit this far from the JPEG center.
# The brightness search stays tighter: on a nebula, a wide shift lands on the glow.
_MARK_SHIFT = 0.20
_MARK_SPINS = (-1.0, -0.5, 0.0, 0.5, 1.0)


def display_pixel(
    fits_x: float,
    fits_y: float,
    fit_w: float,
    fit_h: float,
    jpeg_w: float,
    jpeg_h: float,
    flip_y: bool,
    turn: int = 0,
) -> tuple[float, float]:
    """JPEG pixel of a 1-based FITS pixel.

    `flip_y` puts FITS row 0 at the bottom. `turn` then rotates that bitmap
    clockwise. The rotated frame is scaled uniformly into the JPEG, so a
    later crop uses one scale on both axes.
    """
    x = float(fits_x) - 0.5
    y = (float(fit_h) + 0.5 - float(fits_y)) if flip_y else (float(fits_y) - 0.5)
    span_w = float(fit_w)
    span_h = float(fit_h)
    if turn == 90:
        x, y = span_h - y, x
        span_w, span_h = span_h, span_w
    elif turn == 180:
        x, y = span_w - x, span_h - y
    elif turn == 270:
        x, y = y, span_w - x
        span_w, span_h = span_h, span_w
    elif turn != 0:
        raise ValueError(f"turn must be a quarter turn, not {turn}")
    return x * (float(jpeg_w) / span_w), y * (float(jpeg_h) / span_h)


def frame_ratio(jpeg_w: float, jpeg_h: float, fit_w: float, fit_h: float, turn: int = 0) -> float:
    """sy/sx for a uniform crop after `turn`. The base map already fills the JPEG."""
    if turn in (90, 270):
        fit_w, fit_h = fit_h, fit_w
    if not fit_w or not fit_h or not jpeg_h:
        return 1.0
    return (float(jpeg_w) / float(fit_w)) / (float(jpeg_h) / float(fit_h))


def project_stars(
    stars, wcs: dict, jpeg_w: int, jpeg_h: int, flip_y: bool, turn: int = 0
) -> np.ndarray:
    """Uncorrected JPEG pixels of catalog stars. RA and Dec are degrees."""
    points = []
    fit_w = float(wcs["width"])
    fit_h = float(wcs["height"])
    for ra, dec in stars:
        try:
            fits_x, fits_y = sky_to_fits_pixel(
                float(ra),
                float(dec),
                ra0=float(wcs["ra"]),
                dec0=float(wcs["dec"]),
                rotation_deg=float(wcs["rotation"]),
                scale_arcsec=float(wcs["scale"]),
                parity=int(wcs["parity"]),
                width=int(wcs["width"]),
                height=int(wcs["height"]),
            )
        except Exception:
            continue
        points.append(
            display_pixel(fits_x, fits_y, fit_w, fit_h, jpeg_w, jpeg_h, flip_y, turn)
        )
    if not points:
        return np.zeros((0, 2), dtype=np.float64)
    return np.array(points, dtype=np.float64)


def align_frame(lum: np.ndarray, stars: np.ndarray, sy_ratio: float = 1.0) -> FrameAlign:
    """Crop correction that lands `stars` on the sharp peaks in `lum`.

    `sy_ratio` is sy/sx for a uniform crop of this sensor. The projection
    already stretches the two axes by the JPEG's aspect, so a tall crop needs
    a larger sy than sx. Searching that line stays fast past a scale of 1.36.
    """
    if lum.ndim != 2 or stars.ndim != 2 or stars.shape[0] < 8 or stars.shape[1] != 2:
        return IDENTITY
    height, width = lum.shape
    if height < 16 or width < 16:
        return IDENTITY
    kept = _inside(stars, width, height, margin=0.25)
    if kept.shape[0] < 8:
        return IDENTITY

    ratio = sy_ratio if sy_ratio and sy_ratio > 0 else 1.0
    # Stars on a nebula are a sharper signal than the glow. A native-scale
    # window, including one turned by about a degree, is accepted here. A
    # bright patch must not win once the catalog already lands on peaks.
    marked = _star_marks(lum)
    if marked is not None:
        placed = _align_to_marks(marked, kept, ratio)
        if placed is not None:
            return placed

    hp = _high_pass(lum, 6)
    # The true match is only about 0.01 in scale and a few pixels in shift.
    # A coarser grid lands on the shoulder and loses to a chance alignment.
    # A crop enlarges the frame, so the scale stays at or above 1, and the
    # shift stays inside 12% of the photo. 1.8 covers a window cut from the
    # full frame, such as a portrait export of a wider sensor crop.
    fine = _disk_max(hp, 4)
    sx_max = 1.8 if ratio <= 1 else max(1.0, 1.8 / ratio)
    found = _search(
        fine,
        kept,
        scale_step=0.02,
        scale_min=1.0,
        scale_max=sx_max,
        shift_limit=0.12 * min(height, width),
        shift_step=4,
        sy_ratio=ratio,
    )
    refined = _search(
        fine,
        kept,
        scale_step=0.01,
        scale_min=max(1.0, found.sx - 0.04),
        scale_max=found.sx + 0.04,
        sy_min=max(1.0, found.sy - 0.04),
        sy_max=found.sy + 0.04,
        shift_limit=15,
        shift_step=3,
        tx0=found.tx,
        ty0=found.ty,
        around=True,
    )
    identity_sum, identity_hits = _evaluate(fine, kept, IDENTITY)
    best_sum, best_hits = _evaluate(fine, kept, refined)
    if not _accept(identity_sum, identity_hits, best_sum, best_hits):
        return IDENTITY
    return refined


def score_alignment(lum: np.ndarray, stars: np.ndarray, align: FrameAlign) -> tuple[float, int]:
    """Sum and hit count of `align` on the same field the search uses."""
    if lum.ndim != 2 or stars.ndim != 2 or stars.shape[0] < 8:
        return 0.0, 0
    height, width = lum.shape
    kept = _inside(stars, width, height, margin=0.25)
    if kept.shape[0] < 8:
        return 0.0, 0
    fine = _disk_max(_high_pass(lum, 6), 4)
    return _evaluate(fine, kept, align)


def pick_align(lum: np.ndarray, candidates, sy_ratio: float = 1.0):
    """Row order, quarter turn, and crop whose stars land on the most peaks.

    Each candidate is `(flip, star_pixels)` or
    `(flip, star_pixels, turn, sy_ratio)`. A per-candidate ratio wins over
    the shared one, because a turned frame has a different aspect. The higher
    hit count decides. A match that leaves the aspect line is the wrong turn
    stretched across the photo, so that candidate keeps the plain mapping.
    """
    ranked = []
    for item in candidates:
        flip = item[0]
        stars = item[1]
        turn = int(item[2]) if len(item) > 2 else 0
        ratio = float(item[3]) if len(item) > 3 else sy_ratio
        found = align_frame(lum, stars, sy_ratio=ratio)
        # A result that leaves the aspect line is the wrong turn stretched
        # across the photo. Its chance hits must not beat a real crop.
        if found.sx > 0 and abs((found.sy / found.sx) - ratio) > _RATIO_SLACK:
            continue
        chosen = FrameAlign(found.sx, found.sy, found.tx, found.ty, turn, found.spin)
        star_sum, star_hits = _mark_score(lum, stars, chosen)
        bright_sum, bright_hits = score_alignment(lum, stars, chosen)
        ranked.append((star_hits, star_sum, bright_hits, bright_sum, bool(flip), chosen))
    if not ranked:
        return False, IDENTITY
    # Star peaks decide when any orientation actually lands on them. The
    # brightness sum is only the fallback for a frame whose peaks are not stars.
    if max(item[0] for item in ranked) >= 8:
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    else:
        ranked.sort(key=lambda item: (item[2], item[3]), reverse=True)
    _star_hits, _star_sum, _bright_hits, _bright_sum, flip, chosen = ranked[0]
    return flip, chosen


def _star_marks(lum: np.ndarray):
    """Disks on compact peaks. None when the frame does not show enough of them."""
    from app.solver import detect_stars

    found = detect_stars(lum, limit=40)
    if len(found) < 8:
        return None
    field = np.zeros(lum.shape, dtype=np.float32)
    height, width = field.shape
    radius = 6
    for _flux, fits_x, fits_y in found:
        cx = int(round(float(fits_x) - 1))
        cy = int(round(float(fits_y) - 1))
        y0 = max(0, cy - radius)
        y1 = min(height, cy + radius + 1)
        x0 = max(0, cx - radius)
        x1 = min(width, cx + radius + 1)
        if y0 >= y1 or x0 >= x1:
            continue
        yy, xx = np.ogrid[y0:y1, x0:x1]
        dist2 = (yy - cy) ** 2 + (xx - cx) ** 2
        disk = dist2 <= radius * radius
        # The center is worth more than the rim, so a tie across the disk
        # settles on the star instead of the first cell that touches it.
        patch = field[y0:y1, x0:x1]
        np.maximum(patch, np.where(disk, 255 - 18 * np.sqrt(dist2), 0), out=patch)
    return field


def _mark_score(lum: np.ndarray, stars: np.ndarray, align: FrameAlign):
    marked = _star_marks(lum)
    if marked is None or stars.ndim != 2 or stars.shape[0] < 8:
        return 0.0, 0
    height, width = marked.shape
    kept = _inside(stars, width, height, margin=0.25)
    if kept.shape[0] < 8:
        return 0.0, 0
    return _evaluate(marked, kept, align)


def _align_to_marks(field, stars, ratio: float):
    """Crop, shift, and small spin that land catalog stars on detected peaks."""
    # The brightest catalog stars carry the match. The rest only slow the grid.
    if len(stars) > 40:
        stars = stars[:40]
    height, width = field.shape
    shift_limit = _MARK_SHIFT * min(height, width)
    sx_max = 1.8 if ratio <= 1 else max(1.0, 1.8 / ratio)
    coarse = []
    for spin in _MARK_SPINS:
        found = _search(
            field,
            stars,
            scale_step=0.02,
            scale_min=1.0,
            scale_max=sx_max,
            shift_limit=shift_limit,
            shift_step=8,
            sy_ratio=ratio,
            spin=spin,
        )
        chosen = FrameAlign(found.sx, found.sy, found.tx, found.ty, 0, spin)
        total, _hits = _evaluate(field, stars, chosen)
        coarse.append((total, chosen))
    _total, best = max(coarse, key=lambda item: item[0])
    refined_sum = -1.0
    refined_best = best
    for spin in _steps(best.spin - 0.5, best.spin + 0.5, 0.25):
        found = _search(
            field,
            stars,
            scale_step=0.01,
            scale_min=max(1.0, best.sx - 0.04),
            scale_max=best.sx + 0.04,
            sy_min=max(1.0, best.sy - 0.04),
            sy_max=best.sy + 0.04,
            shift_limit=30,
            shift_step=3,
            tx0=best.tx,
            ty0=best.ty,
            around=True,
            spin=float(spin),
        )
        chosen = FrameAlign(found.sx, found.sy, found.tx, found.ty, 0, float(spin))
        total, _hits = _evaluate(field, stars, chosen)
        if total > refined_sum:
            refined_sum = total
            refined_best = chosen
    ident_sum, ident_hits = _evaluate(field, stars, IDENTITY)
    best_sum, best_hits = _evaluate(field, stars, refined_best)
    if best_hits >= 8 and _accept(ident_sum, ident_hits, best_sum, best_hits):
        return refined_best
    # The catalog already sits on the peaks. Leave it there instead of
    # letting the brightness search slide onto the nebula.
    if ident_hits >= 8:
        return IDENTITY
    return None


def _accept(identity_sum: float, identity_hits: int, best_sum: float, best_hits: int) -> bool:
    if best_hits < 8 or best_hits < identity_hits + 4:
        return False
    if identity_sum <= 0:
        return best_sum > 0
    return best_sum > identity_sum * 1.25


def _search(
    field,
    stars,
    *,
    scale_step,
    scale_min,
    scale_max,
    shift_limit,
    shift_step,
    tx0=0.0,
    ty0=0.0,
    sy_min=None,
    sy_max=None,
    around=False,
    sy_ratio=None,
    spin=0.0,
) -> FrameAlign:
    height, width = field.shape
    cx = width / 2
    cy = height / 2
    scales = _steps(scale_min, scale_max, scale_step)
    sy_values = scales if sy_min is None else _steps(sy_min, sy_max, scale_step)
    if around:
        shifts_x = _steps(tx0 - shift_limit, tx0 + shift_limit, shift_step)
        shifts_y = _steps(ty0 - shift_limit, ty0 + shift_limit, shift_step)
    else:
        shifts_x = _steps(-shift_limit, shift_limit, shift_step)
        shifts_y = shifts_x
    # A disk score is flat around the true shift, so the first max sits on the
    # corner of that plateau. Average every cell that ties the best score.
    best_peak = -1.0
    weight = 0.0
    acc_sx = acc_sy = acc_tx = acc_ty = 0.0
    dx = stars[:, 0] - cx
    dy = stars[:, 1] - cy
    ang = float(np.radians(spin or 0.0))
    turn_c = float(np.cos(ang))
    turn_s = float(np.sin(ang))
    for sx in scales:
        row_sy = sy_values
        if sy_ratio is not None:
            sy = float(sx) * float(sy_ratio)
            if sy < 1.0 or sy > 1.8:
                continue
            row_sy = (sy,)
        for sy in row_sy:
            ox = sx * dx
            oy = sy * dy
            base_x = cx + turn_c * ox + turn_s * oy
            base_y = cy - turn_s * ox + turn_c * oy
            total = _score_shifts(field, base_x, base_y, shifts_x, shifts_y)
            if total.size == 0:
                continue
            peak = float(total.max())
            if peak <= 0 or peak + 1e-6 < best_peak:
                continue
            matched = total >= peak - 1e-3
            if peak > best_peak + 1e-6:
                best_peak = peak
                weight = 0.0
                acc_sx = acc_sy = acc_tx = acc_ty = 0.0
            ixs, iys = np.nonzero(matched)
            count = float(ixs.size)
            if count == 0:
                continue
            weight += count
            acc_sx += float(sx) * count
            acc_sy += float(sy) * count
            acc_tx += float(shifts_x[ixs].sum())
            acc_ty += float(shifts_y[iys].sum())
    if weight <= 0:
        return IDENTITY
    return FrameAlign(acc_sx / weight, acc_sy / weight, acc_tx / weight, acc_ty / weight)


def _score_shifts(field, base_x, base_y, shifts_x, shifts_y):
    height, width = field.shape
    ox = base_x[None, :] + shifts_x[:, None]
    oy = base_y[None, :] + shifts_y[:, None]
    # (tx, ty, star) by broadcasting the two shift axes.
    xs = np.rint(ox[:, None, :]).astype(np.int32)
    ys = np.rint(oy[None, :, :]).astype(np.int32)
    ok = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    xs = np.clip(xs, 0, width - 1)
    ys = np.clip(ys, 0, height - 1)
    values = field[ys, xs]
    values = np.where(ok, values, 0)
    return values.sum(axis=2)


def _rotated_offset(dx, dy, spin: float):
    """Scale offsets, then spin about the center. `spin` is degrees."""
    ang = float(np.radians(spin or 0.0))
    turn_c = float(np.cos(ang))
    turn_s = float(np.sin(ang))
    return turn_c * dx + turn_s * dy, -turn_s * dx + turn_c * dy


def _evaluate(field, stars, align: FrameAlign):
    height, width = field.shape
    cx = width / 2
    cy = height / 2
    rx, ry = _rotated_offset(
        align.sx * (stars[:, 0] - cx),
        align.sy * (stars[:, 1] - cy),
        align.spin,
    )
    xs = np.rint(cx + rx + align.tx).astype(np.int32)
    ys = np.rint(cy + ry + align.ty).astype(np.int32)
    ok = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    values = np.zeros(len(stars), dtype=np.float64)
    values[ok] = field[ys[ok], xs[ok]]
    return float(values.sum()), int((values > 30).sum())


def _inside(stars, width, height, margin):
    x_pad = width * margin
    y_pad = height * margin
    ok = (
        (stars[:, 0] > -x_pad)
        & (stars[:, 0] < width + x_pad)
        & (stars[:, 1] > -y_pad)
        & (stars[:, 1] < height + y_pad)
    )
    return stars[ok]


def _steps(start, stop, step):
    if step <= 0:
        return np.array([start], dtype=np.float64)
    count = int(np.floor((stop - start) / step + 0.5)) + 1
    count = max(1, count)
    return np.linspace(start, stop, count)


def _high_pass(image, radius):
    """Pixels above a local mean, in a new array.

    On Python 3.14, NumPy reuses a large left operand as the output of a
    subtraction inside a function. Subtracting the mean from the photo in
    place would erase it, and the star peaks would become nebula edges.
    """
    high = np.empty(image.shape, dtype=np.float32)
    np.subtract(image, _box_mean(image, radius), out=high)
    np.maximum(high, 0, out=high)
    return high


def _box_mean(image, radius):
    padded = np.pad(image, radius, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    height, width = image.shape
    total = (
        integral[2 * radius + 1 : 2 * radius + 1 + height, 2 * radius + 1 : 2 * radius + 1 + width]
        - integral[0:height, 2 * radius + 1 : 2 * radius + 1 + width]
        - integral[2 * radius + 1 : 2 * radius + 1 + height, 0:width]
        + integral[0:height, 0:width]
    )
    return total / float((2 * radius + 1) ** 2)


def _disk_max(image, radius):
    out = image.copy()
    height, width = image.shape
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0 or dx * dx + dy * dy > radius * radius:
                continue
            src_y0 = max(0, -dy)
            src_y1 = min(height, height - dy)
            src_x0 = max(0, -dx)
            src_x1 = min(width, width - dx)
            patch = image[src_y0:src_y1, src_x0:src_x1]
            dst_y0 = max(0, dy)
            dst_x0 = max(0, dx)
            dest = out[dst_y0 : dst_y0 + patch.shape[0], dst_x0 : dst_x0 + patch.shape[1]]
            np.maximum(dest, patch, out=dest)
    return out
