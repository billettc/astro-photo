"""The overlay row-order choice. The live copy is app/static/js/overlay-flip.js."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OverlayFlipTests(unittest.TestCase):
    def test_brighter_nebulae_pick_the_row_order(self):
        script = r"""
const { chooseOverlayFlip } = require("./app/static/js/overlay-flip.js");
const assert = require("assert");
assert.strictEqual(chooseOverlayFlip(70.3, 174.3, 4, "solver"), false);
assert.strictEqual(chooseOverlayFlip(174.3, 70.3, 4, "header"), true);
assert.strictEqual(chooseOverlayFlip(81, 18, 1, "solver"), true);
assert.strictEqual(chooseOverlayFlip(18, 81, 1, "solver"), false);
assert.strictEqual(chooseOverlayFlip(40, 18, 1, "solver"), false);
assert.strictEqual(chooseOverlayFlip(40, 18, 1, "header"), true);
assert.strictEqual(chooseOverlayFlip(22, 20, 4, "solver"), false);
assert.strictEqual(chooseOverlayFlip(22, 20, 4, "header"), true);
assert.strictEqual(chooseOverlayFlip(null, 10, 3, "solver"), false);
console.log("ok");
"""
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)
