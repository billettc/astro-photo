"""The overlay crop correction, without calling the star catalog."""

import unittest

import numpy as np

from app.align import _accept, align_frame, display_pixel, frame_ratio, pick_align


def _paint(width, height, points):
    lum = np.zeros((height, width), dtype=np.float32)
    for x, y in points:
        ix = int(round(x))
        iy = int(round(y))
        # A brighter center, so the peak finder sees a star rather than a flat tile.
        lum[iy - 2 : iy + 3, ix - 2 : ix + 3] = 160
        lum[iy - 1 : iy + 2, ix - 1 : ix + 2] = 220
        lum[iy, ix] = 255
    return lum


def _crop(points, width, height, sx, sy, tx, ty):
    cx = width / 2
    cy = height / 2
    moved = np.column_stack(
        (
            cx + sx * (points[:, 0] - cx) + tx,
            cy + sy * (points[:, 1] - cy) + ty,
        )
    )
    return moved


class AlignFrameTests(unittest.TestCase):
    def _stars(self):
        xs = (90, 170, 250, 330, 400)
        ys = (70, 160, 260)
        return np.array([(x, y) for x in xs for y in ys], dtype=np.float64)

    def test_keeps_a_frame_whose_stars_already_land(self):
        stars = self._stars()
        found = align_frame(_paint(480, 340, stars), stars)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.03)
        self.assertAlmostEqual(found.sy, 1.0, delta=0.03)
        self.assertAlmostEqual(found.tx, 0.0, delta=6)
        self.assertAlmostEqual(found.ty, 0.0, delta=6)

    def test_recovers_a_crop_of_the_frame(self):
        stars = self._stars()
        sx, sy, tx, ty = 1.12, 1.18, -16.0, 10.0
        moved = _crop(stars, 480, 340, sx, sy, tx, ty)
        found = align_frame(_paint(480, 340, moved), stars, sy_ratio=sy / sx)
        self.assertAlmostEqual(found.sx, sx, delta=0.04)
        self.assertAlmostEqual(found.sy, sy, delta=0.04)
        self.assertAlmostEqual(found.tx, tx, delta=8)
        self.assertAlmostEqual(found.ty, ty, delta=8)

    def test_recovers_a_shift_that_falls_between_coarse_samples(self):
        stars = self._stars()
        sx, sy, tx, ty = 1.08, 1.14, 7.0, -9.0
        moved = _crop(stars, 480, 340, sx, sy, tx, ty)
        found = align_frame(_paint(480, 340, moved), stars, sy_ratio=sy / sx)
        self.assertAlmostEqual(found.sx, sx, delta=0.04)
        self.assertAlmostEqual(found.sy, sy, delta=0.04)
        self.assertAlmostEqual(found.tx, tx, delta=8)
        self.assertAlmostEqual(found.ty, ty, delta=8)

    def test_recovers_a_tall_crop_past_the_old_scale_limit(self):
        stars = self._stars()
        sx, sy, tx, ty = 1.34, 1.56, 8.0, -6.0
        moved = _crop(stars, 480, 340, sx, sy, tx, ty)
        inside = moved[
            (moved[:, 0] > 4)
            & (moved[:, 0] < 476)
            & (moved[:, 1] > 4)
            & (moved[:, 1] < 336)
        ]
        self.assertGreaterEqual(len(inside), 8)
        found = align_frame(_paint(480, 340, inside), stars, sy_ratio=sy / sx)
        self.assertAlmostEqual(found.sx, sx, delta=0.04)
        self.assertAlmostEqual(found.sy, sy, delta=0.04)
        self.assertAlmostEqual(found.tx, tx, delta=8)
        self.assertAlmostEqual(found.ty, ty, delta=8)

    def test_picks_the_row_order_whose_stars_match(self):
        stars = self._stars()
        height = 340
        flipped = np.column_stack((stars[:, 0], height - 1 - stars[:, 1]))
        image = _paint(480, height, flipped)
        wrong = flipped + np.array([0.0, 90.0])
        flip, found = pick_align(image, [(False, wrong), (True, flipped)])
        self.assertTrue(flip)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.04)
        self.assertAlmostEqual(found.sy, 1.0, delta=0.04)

    def test_a_small_gain_does_not_move_the_frame(self):
        self.assertFalse(_accept(100, 10, 120, 13))
        self.assertFalse(_accept(100, 10, 200, 12))
        self.assertTrue(_accept(100, 10, 200, 16))
        self.assertFalse(_accept(0, 0, 50, 7))

    def test_quarter_turn_maps_the_portrait_sensor(self):
        # 90° clockwise of file order, half-pixel FITS origin, full frame.
        x, y = display_pixel(1175.56, 1899.27, 2160, 3840, 3840, 2160, False, 90)
        self.assertAlmostEqual(x, 1941.23, delta=0.02)
        self.assertAlmostEqual(y, 1175.06, delta=0.02)
        x, y = display_pixel(1607.77, 1487.91, 2160, 3840, 3840, 2160, False, 90)
        self.assertAlmostEqual(x, 2352.59, delta=0.02)
        self.assertAlmostEqual(y, 1607.27, delta=0.02)
        plain = display_pixel(100.5, 40.5, 200, 80, 200, 80, False, 0)
        self.assertEqual(plain, (100.0, 40.0))
        flipped = display_pixel(100.5, 40.5, 200, 80, 200, 80, True, 0)
        self.assertAlmostEqual(flipped[1], 40.0, places=4)
        # A landscape export of the portrait sensor is square in pixel scale.
        self.assertAlmostEqual(frame_ratio(1600, 900, 2160, 3840, 0), 3.1605, delta=0.001)
        self.assertAlmostEqual(frame_ratio(1600, 900, 2160, 3840, 90), 1.0, places=4)
        self.assertAlmostEqual(frame_ratio(1600, 900, 2160, 3840, 270), 1.0, places=4)

    def test_picks_the_quarter_turn_whose_stars_match(self):
        stars = self._stars()
        image = _paint(480, 340, stars)
        shifted = stars + np.array([30.0, -20.0])
        _flip, found = pick_align(
            image,
            [
                (False, shifted, 0, 3.2),
                (False, stars, 90, 1.0),
            ],
        )
        self.assertEqual(found.turn, 90)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.04)
        self.assertAlmostEqual(found.sy, 1.0, delta=0.04)

    def test_keeps_the_unrotated_frame_when_it_matches(self):
        stars = self._stars()
        image = _paint(480, 340, stars)
        decoy = np.column_stack((340 - stars[:, 1], stars[:, 0]))
        flip, found = pick_align(
            image,
            [
                (False, stars, 0, 1.0),
                (True, decoy, 270, 1.0),
            ],
        )
        self.assertEqual(found.turn, 0)
        self.assertFalse(flip)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.04)

    def test_matching_does_not_erase_the_photo(self):
        # A large subtraction inside a function can reuse the photo as its
        # output. The search has to leave the pixels it was given.
        stars = self._stars()
        lum = _paint(480, 340, stars)
        original = lum.copy()
        align_frame(lum, stars)
        self.assertTrue(np.array_equal(lum, original))

    def test_a_bright_patch_does_not_pull_stars_off_their_peaks(self):
        stars = self._stars()
        lum = _paint(480, 340, stars)
        lum[30:110, 300:460] = 255
        found = align_frame(lum, stars)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.04)
        self.assertAlmostEqual(found.sy, 1.0, delta=0.04)
        self.assertAlmostEqual(found.tx, 0.0, delta=8)
        self.assertAlmostEqual(found.ty, 0.0, delta=8)
        self.assertAlmostEqual(found.spin, 0.0, delta=0.35)

    def test_recovers_a_native_window_beside_a_bright_patch(self):
        stars = self._stars()
        sx, sy, tx, ty = 1.11, 1.11, 4.0, -2.0
        moved = _crop(stars, 480, 340, sx, sy, tx, ty)
        lum = _paint(480, 340, moved)
        lum[20:100, 20:140] = 255
        found = align_frame(lum, stars, sy_ratio=1.0)
        self.assertAlmostEqual(found.sx, sx, delta=0.04)
        self.assertAlmostEqual(found.sy, sy, delta=0.04)
        self.assertAlmostEqual(found.tx, tx, delta=8)
        self.assertAlmostEqual(found.ty, ty, delta=8)

    def test_recovers_a_small_spin(self):
        # Stars far from the center, so one degree is not the same as a shift.
        xs = (60, 450, 840)
        ys = (50, 320, 600)
        stars = np.array([(x, y) for x in xs for y in ys], dtype=np.float64)
        spun = _spin_about_center(stars, 900, 660, 1.0)
        found = align_frame(_paint(900, 660, spun), stars)
        self.assertAlmostEqual(found.spin, 1.0, delta=0.35)
        self.assertAlmostEqual(found.sx, 1.0, delta=0.04)
        self.assertAlmostEqual(found.tx, 0.0, delta=10)
        self.assertAlmostEqual(found.ty, 0.0, delta=10)


def _spin_about_center(points, width, height, spin):
    """The same center spin `_rotated_offset` applies, in degrees."""
    ang = np.radians(spin)
    c, s = np.cos(ang), np.sin(ang)
    dx = points[:, 0] - width / 2
    dy = points[:, 1] - height / 2
    return np.column_stack((width / 2 + c * dx + s * dy, height / 2 - s * dx + c * dy))
