/* Which way up the photo is, for the sky overlay.
   Keep the margins in step with tests/test_overlay_flip.py. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.chooseOverlayFlip = api.chooseOverlayFlip;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function chooseOverlayFlip(flipScore, plainScore, count, source) {
    if (count >= 1 && Number.isFinite(flipScore) && Number.isFinite(plainScore)) {
      var margin = count >= 2 ? 8 * count : 25;
      var lead = count >= 2 ? 1.35 : 2;
      if (flipScore > plainScore + margin && flipScore > plainScore * lead) return true;
      if (plainScore > flipScore + margin && plainScore > flipScore * lead) return false;
    }
    return (source || "") !== "solver";
  }
  return { chooseOverlayFlip: chooseOverlayFlip };
});
