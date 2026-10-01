(() => {
  const C = window.AstroCore;
  const CATALOG = window.ASTRO_CATALOG || [];
  if (!C || !CATALOG.length) {
    console.error("AstroCore / ASTRO_CATALOG missing");
    return;
  }
  const { pad, sunAlt, moonEq, moonIllum, phaseName, altOf, angularSep, fmtT, fmtDur, esc, geocodeLocation, fetchForecast, fetchTimezoneOffset, getWiki, bigImg, TYPE_LABEL } = C;
  const $ = (id) => document.getElementById(id);

  const TYPE_DESC = {
    GAL: "A galaxy — stars, gas, and dust beyond the Milky Way.",
    NEB: "A nebula — interstellar gas and dust, often a stellar nursery.",
    OC: "An open cluster — a loose group of young stars.",
    GC: "A globular cluster — a dense sphere of ancient stars.",
    PN: "A planetary nebula — shell shed by a dying sun-like star.",
    SNR: "A supernova remnant — debris of an exploded star.",
  };

  const STORAGE_KEY = "astro-photo.planner";
  const DEFAULT_LOC = "Melrose, MA";

  function loadPrefs() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") || {};
    } catch {
      return {};
    }
  }

  function savePrefs(partial) {
    const next = { ...loadPrefs(), ...partial, updatedAt: Date.now() };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* ignore quota / private mode */
    }
    return next;
  }

  // time selects + restore last location (then auto-plan)
  (function initControls() {
    const t0 = $("t0"), t1 = $("t1"), loc = $("loc");
    if (!t0 || !t1 || !loc) return;
    for (let h = 0; h < 24; h++) {
      const lbl = (h % 12 || 12) + (h < 12 ? ":00 am" : ":00 pm");
      t0.add(new Option(lbl, h));
      t1.add(new Option(lbl, h));
    }
    const prefs = loadPrefs();
    t0.value = prefs.t0 != null ? String(prefs.t0) : "21";
    t1.value = prefs.t1 != null ? String(prefs.t1) : "3";
    loc.value = (prefs.loc || DEFAULT_LOC).trim() || DEFAULT_LOC;
    const d = new Date();
    $("date").value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());

    // Remember location as the user types (debounced lightly via change/blur too)
    const persistLoc = () => {
      const v = loc.value.trim();
      if (v) savePrefs({ loc: v });
    };
    loc.addEventListener("change", persistLoc);
    loc.addEventListener("blur", persistLoc);
    t0.addEventListener("change", () => savePrefs({ t0: +t0.value }));
    t1.addEventListener("change", () => savePrefs({ t1: +t1.value }));

    // Always reload a plan for the last saved location
    setTimeout(() => {
      if (typeof plan === "function") plan();
    }, 0);
  })();

  async function loadThumbs(targets) {
    const queue = targets.map((t, i) => ({ t, i }));
    const work = async () => {
      while (queue.length) {
        const { t, i } = queue.shift();
        const d = await getWiki(t.o.wiki || t.o.id);
        const el = document.querySelector('.planner-thumb[data-idx="' + i + '"]');
        if (el && d && d.thumbnail) {
          el.innerHTML =
            '<img src="' +
            d.thumbnail.source.replace(/"/g, "&quot;") +
            '" alt="" loading="lazy">';
        }
      }
    };
    await Promise.all([work(), work(), work(), work()]);
  }

  let lastFocus = null;
  async function openDetail(i) {
    const t = window._targets && window._targets[i];
    if (!t) return;
    lastFocus = document.activeElement;
    const o = t.o, off = window._off;
    const raH = Math.floor(o.ra), raM = Math.round((o.ra - raH) * 60);
    const decS = (o.dec >= 0 ? "+" : "−") + Math.abs(o.dec).toFixed(1) + "°";
    const typeLabel = TYPE_LABEL[o.type] || o.type || "object";
    const facts =
      '<div class="planner-facts">' +
      '<div class="kv"><span class="k">Type</span><span class="v">' + typeLabel + "</span></div>" +
      '<div class="kv"><span class="k">Magnitude</span><span class="v">' + Number(o.mag).toFixed(1) + "</span></div>" +
      '<div class="kv"><span class="k">Apparent size</span><span class="v">' + esc(String(o.size || "—")) + "</span></div>" +
      '<div class="kv"><span class="k">RA / Dec</span><span class="v">' + raH + "h " + pad(raM) + "m / " + decS + "</span></div>" +
      '<div class="kv"><span class="k">Tonight</span><span class="v">peak ' + Math.round(t.peak) + "° @ " + fmtT(t.peakT, off) + "</span></div>" +
      '<div class="kv"><span class="k">Moon separation</span><span class="v">' + Math.round(t.sep) + "°</span></div></div>";
    const bg = document.createElement("div");
    bg.className = "modal-bg";
    bg.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true">' +
      '<button type="button" class="mclose" aria-label="Close">×</button>' +
      '<div class="mimg-ph">loading image…</div>' +
      '<div class="mbody"><h3>' + esc(o.id) + " · " + esc(o.name) + "</h3>" +
      '<div class="msub">' + typeLabel + "</div>" +
      '<p class="mdesc">' + esc(o.desc || TYPE_DESC[o.type] || "") + "</p>" + facts +
      '<div class="mattr"></div></div></div>';
    document.body.appendChild(bg);
    const close = () => {
      bg.remove();
      document.removeEventListener("keydown", onKey);
      if (lastFocus) lastFocus.focus();
    };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    bg.addEventListener("click", (e) => { if (e.target === bg) close(); });
    bg.querySelector(".mclose").addEventListener("click", close);
    document.addEventListener("keydown", onKey);

    const d = await getWiki(o.wiki || o.id);
    if (!document.body.contains(bg)) return;
    const ph = bg.querySelector(".mimg-ph");
    const img = d ? bigImg(d) : null;
    if (img) {
      const im = new Image();
      im.className = "mimg";
      im.alt = o.name;
      im.onload = () => { if (ph.parentNode) ph.replaceWith(im); };
      im.onerror = () => {
        if (d.thumbnail && im.src !== d.thumbnail.source) im.src = d.thumbnail.source;
        else ph.textContent = "no image available";
      };
      im.src = img;
    } else ph.textContent = "no image available";
    if (d && d.extract) {
      bg.querySelector(".mdesc").textContent = d.extract;
      const url =
        d.content_urls && d.content_urls.desktop
          ? d.content_urls.desktop.page
          : "https://en.wikipedia.org/wiki/" + (o.wiki || o.id);
      bg.querySelector(".mattr").innerHTML =
        'Text &amp; image: <a href="' + url + '" target="_blank" rel="noopener">Wikipedia ↗</a> (CC BY-SA)';
    }
  }

  $("go").addEventListener("click", plan);
  $("loc").addEventListener("keydown", (e) => { if (e.key === "Enter") plan(); });

  async function plan() {
    const btn = $("go");
    btn.disabled = true;
    btn.textContent = "Working…";
    $("err").classList.add("hidden");
    $("resolved").textContent = "";
    try {
      const locInput = ($("loc").value || "").trim() || DEFAULT_LOC;
      $("loc").value = locInput;
      const place = await geocodeLocation(locInput);
      const lat = place.latitude, lon = place.longitude;
      const dateStr = $("date").value;
      if (!dateStr) throw new Error("Pick a session date.");
      const [Y, Mo, Dd] = dateStr.split("-").map(Number);
      const h0 = +$("t0").value, h1 = +$("t1").value;
      const crossesMidnight = h1 <= h0;
      // Prefer the resolved place label so the next visit is consistent
      savePrefs({
        loc: place.label || locInput,
        t0: h0,
        t1: h1,
        lat,
        lon,
      });
      $("loc").value = place.label || locInput;
      const d2 = new Date(Date.UTC(Y, Mo - 1, Dd));
      d2.setUTCDate(d2.getUTCDate() + 1);
      const endDate =
        d2.getUTCFullYear() + "-" + pad(d2.getUTCMonth() + 1) + "-" + pad(d2.getUTCDate());

      let wx = null, off = null;
      try {
        wx = await fetchForecast(lat, lon, dateStr, endDate);
        off = wx.utc_offset_seconds;
      } catch (e) {
        wx = null;
      }
      if (off === null) {
        try { off = await fetchTimezoneOffset(lat, lon); }
        catch (e) { off = Math.round(lon / 15) * 3600; }
      }

      const startMs = Date.UTC(Y, Mo - 1, Dd, h0, 0) - off * 1000;
      const endMs = Date.UTC(Y, Mo - 1, Dd, h1, 0) - off * 1000 + (crossesMidnight ? 86400000 : 0);
      const winMin = (endMs - startMs) / 60000;
      if (winMin <= 0 || winMin > 18 * 60) throw new Error("Viewing window must be between 0 and 18 hours.");

      $("resolved").innerHTML =
        "Resolved: <b>" + esc(place.label) + "</b> · " + lat.toFixed(3) + "°, " + lon.toFixed(3) +
        "° · UTC" + (off >= 0 ? "+" : "") + off / 3600;

      const STEP = 5 * 60000, samples = [];
      for (let t = startMs; t <= endMs; t += STEP) {
        samples.push({ t, moonAlt: null, sunAlt: sunAlt(t, lat, lon) });
      }
      const moonMid = moonEq((startMs + endMs) / 2);
      samples.forEach((s) => {
        const m = moonEq(s.t);
        s.moonAlt = altOf(s.t, m.ra, m.dec, lat, lon);
      });

      let wxHours = [];
      if (wx && wx.hourly) {
        const H = wx.hourly;
        for (let i = 0; i < H.time.length; i++) {
          const [ds, ts] = H.time[i].split("T");
          const [y, mo, da] = ds.split("-").map(Number);
          const hh = +ts.split(":")[0];
          const ms = Date.UTC(y, mo - 1, da, hh) - off * 1000;
          if (ms >= startMs - 3599000 && ms <= endMs) {
            wxHours.push({
              ms, cc: H.cloud_cover[i], lo: H.cloud_cover_low[i], mid: H.cloud_cover_mid[i],
              hi: H.cloud_cover_high[i], rh: H.relative_humidity_2m[i], dp: H.dew_point_2m[i],
              tp: H.temperature_2m[i], ws: H.wind_speed_10m[i], pp: H.precipitation_probability[i],
            });
          }
        }
      }

      const illum = moonIllum((startMs + endMs) / 2);
      const targets = [];
      for (const o of CATALOG) {
        let peak = -90, peakT = null, minutes = 0, segs = [], segStart = null;
        const alts = [];
        for (const s of samples) {
          const a = altOf(s.t, o.ra, o.dec, lat, lon);
          alts.push({ t: s.t, alt: a });
          const up = a >= 20;
          if (up) {
            minutes += 5;
            if (segStart === null) segStart = s.t;
            if (a > peak) {
              peak = a;
              peakT = s.t;
            }
          } else if (segStart !== null) {
            segs.push([segStart, s.t]);
            segStart = null;
          }
        }
        if (segStart !== null) segs.push([segStart, endMs]);
        if (minutes < 30) continue;
        const sep = angularSep(o.ra, o.dec, moonMid.ra, moonMid.dec);
        const moonUpFrac = samples.filter((s) => s.moonAlt > 0).length / samples.length;
        const moonPenalty = (illum.frac * moonUpFrac * Math.max(0, 60 - sep)) / 60;
        const score = (minutes / 60) * 10 + peak * 0.45 - moonPenalty * 35;
        targets.push({
          o,
          minutes,
          segs,
          peak,
          peakT,
          sep,
          score,
          alts,
          moonFlag: illum.frac > 0.3 && sep < 30 && moonUpFrac > 0.1,
        });
      }
      targets.sort((a, b) => b.score - a.score);
      render({ place, lat, lon, off, startMs, endMs, samples, wxHours, illum, moonMid, targets, dateStr, wxAvailable: !!(wx && wxHours.length) });
    } catch (e) {
      $("err").textContent = e.message || String(e);
      $("err").classList.remove("hidden");
      $("results").classList.add("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Plan session";
    }
  }

  function render(R) {
    $("results").classList.remove("hidden");
    const { startMs, endMs, off, samples, wxHours, illum, targets } = R;
    const W = 920, H = 190, padL = 8, padR = 8, plotW = W - padL - padR;
    const x = (ms) => padL + ((ms - startMs) / (endMs - startMs)) * plotW;
    const D2R = Math.PI / 180;

    let svg = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Night conditions timeline">';
    let twiOpen = null;
    for (let i = 0; i < samples.length; i++) {
      const s = samples[i], bright = s.sunAlt > -18;
      if (bright && twiOpen === null) twiOpen = s.t;
      if ((!bright || i === samples.length - 1) && twiOpen !== null) {
        const tEnd = bright ? s.t : samples[i - 1].t;
        svg += '<rect x="' + x(twiOpen) + '" y="0" width="' + Math.max(2, x(tEnd) - x(twiOpen)) + '" height="' + (H - 26) + '" fill="rgba(143,183,255,.10)"/>';
        twiOpen = null;
      }
    }
    if (wxHours.length) {
      let p = "M " + x(Math.max(startMs, wxHours[0].ms)) + " 0 ";
      for (const w of wxHours) {
        const cx = Math.min(Math.max(x(w.ms), padL), W - padR);
        p += "L " + cx + " " + (w.cc / 100) * (H - 26) + " ";
      }
      p += "L " + Math.min(x(wxHours[wxHours.length - 1].ms), W - padR) + " 0 Z";
      svg += '<path d="' + p + '" fill="#3D4877" opacity=".55"/>';
    }
    const yAlt = (a) => H - 26 - (Math.max(0, a) / 90) * (H - 26);
    svg += '<line x1="' + padL + '" y1="' + yAlt(20) + '" x2="' + (W - padR) + '" y2="' + yAlt(20) + '" stroke="#2A335E" stroke-dasharray="4 4"/>' +
      '<text x="' + (padL + 4) + '" y="' + (yAlt(20) - 4) + '" fill="#8B93A7" font-size="10" font-family="ui-monospace,monospace">20°</text>';
    let mp = "", started = false;
    for (const s of samples) {
      if (s.moonAlt > 0) {
        mp += (started ? "L" : "M") + " " + x(s.t) + " " + yAlt(s.moonAlt) + " ";
        started = true;
      } else started = false;
    }
    if (mp) svg += '<path d="' + mp + '" fill="none" stroke="#F0B860" stroke-width="2"/>';
    const firstHr = Math.ceil(startMs / 3600000) * 3600000;
    for (let t = firstHr; t <= endMs; t += 3600000) {
      svg += '<line x1="' + x(t) + '" y1="' + (H - 26) + '" x2="' + x(t) + '" y2="' + (H - 20) + '" stroke="#3A4470"/>' +
        '<text x="' + x(t) + '" y="' + (H - 6) + '" fill="#8B93A7" font-size="10.5" font-family="ui-monospace,monospace" text-anchor="middle">' + fmtT(t, off) + "</text>";
    }
    svg += '<line x1="' + padL + '" y1="' + (H - 26) + '" x2="' + (W - padR) + '" y2="' + (H - 26) + '" stroke="#2A335E"/></svg>';
    $("stripchart").innerHTML = svg;
    $("strip-sub").textContent = fmtT(startMs, off) + " → " + fmtT(endMs, off) + " local · " + R.dateStr;

    let skyHtml = "<h3>Sky / weather</h3>";
    if (R.wxAvailable) {
      const avg = (a) => Math.round(a.reduce((s, v) => s + v, 0) / a.length);
      const cc = avg(wxHours.map((w) => w.cc));
      const best = wxHours.reduce((m, w) => (w.cc < m.cc ? w : m), wxHours[0]);
      const tps = wxHours.map((w) => w.tp);
      const dpSpread = Math.min(...wxHours.map((w) => w.tp - w.dp));
      skyHtml += '<div class="big">' + cc + '%<small> avg cloud</small></div>' +
        '<div class="kv"><span class="k">Clearest hour</span><span class="v">' + fmtT(best.ms, off) + " (" + best.cc + "%)</span></div>" +
        '<div class="kv"><span class="k">Temp range</span><span class="v">' + Math.round(Math.min(...tps)) + "–" + Math.round(Math.max(...tps)) + "°F</span></div>" +
        '<div class="kv"><span class="k">Min temp−dew</span><span class="v">' + dpSpread.toFixed(1) + "°F</span></div>" +
        '<div class="kv"><span class="k">Wind / precip</span><span class="v">' + avg(wxHours.map((w) => w.ws)) + " mph / " + Math.max(...wxHours.map((w) => w.pp || 0)) + "%</span></div>";
    } else {
      skyHtml += '<p class="muted">Forecast not available for this date (Open-Meteo ~16 days). Moon and targets below are still computed.</p>';
    }
    $("card-sky").innerHTML = skyHtml;

    const mUp = samples.filter((s) => s.moonAlt > 0);
    const pct = Math.round(illum.frac * 100), pname = phaseName(illum.elong);
    let moonState;
    if (!mUp.length) moonState = "Below horizon all window";
    else if (samples[0].moonAlt > 0 && samples[samples.length - 1].moonAlt > 0 && mUp.length === samples.length) moonState = "Up the entire window";
    else if (samples[0].moonAlt > 0) moonState = "Sets ≈ " + fmtT(mUp[mUp.length - 1].t, off);
    else moonState = "Rises ≈ " + fmtT(mUp[0].t, off);
    const maxMoonAlt = Math.max(...samples.map((s) => s.moonAlt));
    const r = 26, k = Math.cos(illum.elong * D2R), waxing = illum.elong < 180;
    const rx = Math.abs(r * k);
    const sweep1 = waxing ? 1 : 0;
    const sweep2 = k > 0 ? (waxing ? 0 : 1) : waxing ? 1 : 0;
    const discSvg =
      '<svg class="moondisc" width="64" height="64" viewBox="-32 -32 64 64" aria-label="Moon phase">' +
      '<circle r="' + r + '" fill="#283057"/>' +
      '<path d="M 0 -' + r + " A " + r + " " + r + " 0 0 " + sweep1 + " 0 " + r +
      " A " + rx + " " + r + " 0 0 " + sweep2 + " 0 -" + r + ' Z" fill="#E8DFC8"/></svg>';
    $("card-moon").innerHTML =
      "<h3>Moon</h3><div class=\"moonrow\">" + discSvg +
      '<div><div class="big">' + pct + '%<small> lit</small></div><div class="muted">' + pname + "</div></div></div>" +
      '<div class="kv"><span class="k">During window</span><span class="v">' + moonState + "</span></div>" +
      '<div class="kv"><span class="k">Max altitude</span><span class="v">' + (maxMoonAlt > 0 ? Math.round(maxMoonAlt) + "°" : "—") + "</span></div>";

    let v, cls, detail;
    if (!R.wxAvailable) {
      v = "Astronomy only"; cls = "verdict-mid"; detail = "No forecast for this date — check back within 16 days.";
    } else {
      const avgCC = wxHours.reduce((s, w) => s + w.cc, 0) / wxHours.length;
      const moonBad = illum.frac > 0.6 && mUp.length > samples.length * 0.5;
      if (avgCC < 25 && !moonBad) { v = "Go"; cls = "verdict-good"; detail = "Clear skies, manageable moon."; }
      else if (avgCC < 25 && moonBad) { v = "Go — narrowband"; cls = "verdict-mid"; detail = "Clear but bright moon; favor emission targets."; }
      else if (avgCC < 55) { v = "Marginal"; cls = "verdict-mid"; detail = "Partial cloud — watch the hourly trend."; }
      else { v = "Stand down"; cls = "verdict-bad"; detail = "Heavy cloud through most of the window."; }
    }
    $("card-verdict").innerHTML =
      "<h3>Session verdict</h3><div class=\"big " + cls + '">' + v + "</div>" +
      '<p class="muted">' + detail + "</p>" +
      '<div class="kv"><span class="k">Targets ≥ 20° (≥ 30 min)</span><span class="v">' + targets.length + "</span></div>" +
      (targets[0] ? '<div class="kv"><span class="k">Top pick</span><span class="v">' + esc(targets[0].o.id) + " · " + esc(targets[0].o.name) + "</span></div>" : "");

    window._targets = targets;
    window._off = off;
    $("tgt-sub").textContent =
      targets.length + " objects · best first · click a card for a photo & description";
    if (!targets.length) {
      $("targets").innerHTML =
        '<div class="empty muted">Nothing stays high enough (≥ 20°) for 30+ minutes in this window — try longer hours or another date.</div>';
      return;
    }

    const TYPE_PLAIN = {
      GAL: "Galaxy",
      NEB: "Nebula",
      OC: "Star cluster",
      GC: "Globular cluster",
      PN: "Planetary nebula",
      SNR: "Supernova remnant",
    };

    function brightnessLabel(mag) {
      const m = Number(mag);
      if (!Number.isFinite(m)) return { label: "Brightness unknown", tip: "" };
      if (m <= 4) return { label: "Bright", tip: "mag " + m.toFixed(1) + " — easy in binoculars" };
      if (m <= 7) return { label: "Moderately bright", tip: "mag " + m.toFixed(1) + " — small telescope / binoculars under dark skies" };
      if (m <= 9) return { label: "Fairly faint", tip: "mag " + m.toFixed(1) + " — needs a telescope and darker sky" };
      return { label: "Faint", tip: "mag " + m.toFixed(1) + " — harder; dark skies help a lot" };
    }

    function sizeLabel(sizeStr) {
      if (!sizeStr || sizeStr === "—") return { label: "Size unknown", tip: "" };
      const raw = String(sizeStr);
      let arcmin = null;
      const m = raw.match(/([\d.]+)/);
      if (m) {
        arcmin = parseFloat(m[1]);
        if (/°/.test(raw) && !/′|'/.test(raw)) arcmin *= 60;
      }
      if (!Number.isFinite(arcmin)) return { label: "About " + raw + " on sky", tip: raw };
      const moons = arcmin / 30;
      let label;
      if (moons < 0.5) label = "Tiny on the sky";
      else if (moons < 1.5) label = "About Moon-sized";
      else if (moons < 3) label = "Larger than the Moon";
      else label = "Very large on the sky";
      return {
        label: label + " (~" + moons.toFixed(1) + "× Moon)",
        tip: raw + " ≈ " + moons.toFixed(1) + " full Moons across",
      };
    }

    function moonLabel(sep, moonFlag) {
      if (moonFlag) {
        return {
          label: "Moon nearby — caution",
          tip: "Only " + Math.round(sep) + "° from a bright Moon; glow may wash out the target",
          cls: "chip-warn",
        };
      }
      if (sep >= 60) {
        return {
          label: "Well away from the Moon",
          tip: Math.round(sep) + "° from the Moon",
          cls: "chip-good",
        };
      }
      return {
        label: "Moon somewhat nearby",
        tip: Math.round(sep) + "° from the Moon",
        cls: "",
      };
    }

    function heightLabel(peak) {
      if (peak >= 60) return "high overhead";
      if (peak >= 40) return "nicely high";
      if (peak >= 20) return "low but workable";
      return "very low";
    }

    function altitudeChart(t, idx) {
      const W = 640;
      const H = 120;
      const padL = 34;
      const padR = 10;
      const padT = 10;
      const padB = 22;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;
      const maxAlt = Math.max(90, Math.ceil((t.peak || 30) / 10) * 10);
      const xAt = (ms) => padL + ((ms - startMs) / (endMs - startMs)) * plotW;
      const yAt = (alt) => padT + plotH - (Math.max(0, Math.min(maxAlt, alt)) / maxAlt) * plotH;
      const alts = t.alts || [];

      let path = "";
      alts.forEach((p, idx) => {
        const x = xAt(p.t);
        const y = yAt(p.alt);
        path += (idx ? "L" : "M") + " " + x.toFixed(1) + " " + y.toFixed(1) + " ";
      });

      // Fill under curve, and a separate band only where altitude ≥ 20°
      let area = "";
      let goodFill = "";
      if (alts.length) {
        area =
          "M " +
          xAt(alts[0].t).toFixed(1) +
          " " +
          yAt(0).toFixed(1) +
          " ";
        alts.forEach((p) => {
          area += "L " + xAt(p.t).toFixed(1) + " " + yAt(p.alt).toFixed(1) + " ";
        });
        area +=
          "L " +
          xAt(alts[alts.length - 1].t).toFixed(1) +
          " " +
          yAt(0).toFixed(1) +
          " Z";

        let run = null;
        const flush = (from, to) => {
          if (!from || !to) return;
          goodFill +=
            "M " +
            xAt(from.t).toFixed(1) +
            " " +
            yAt(0).toFixed(1) +
            " ";
          let i0 = alts.indexOf(from);
          let i1 = alts.indexOf(to);
          for (let i = i0; i <= i1; i++) {
            goodFill +=
              "L " +
              xAt(alts[i].t).toFixed(1) +
              " " +
              yAt(alts[i].alt).toFixed(1) +
              " ";
          }
          goodFill +=
            "L " +
            xAt(to.t).toFixed(1) +
            " " +
            yAt(0).toFixed(1) +
            " Z ";
        };
        alts.forEach((p) => {
          if (p.alt >= 20) {
            if (!run) run = { from: p, to: p };
            else run.to = p;
          } else if (run) {
            flush(run.from, run.to);
            run = null;
          }
        });
        if (run) flush(run.from, run.to);
      }

      const yTicks = [0, 20, 45, 70, maxAlt].filter((v, i, a) => a.indexOf(v) === i && v <= maxAlt);
      let grid = "";
      yTicks.forEach((deg) => {
        const y = yAt(deg);
        grid +=
          '<line x1="' +
          padL +
          '" y1="' +
          y +
          '" x2="' +
          (W - padR) +
          '" y2="' +
          y +
          '" stroke="' +
          (deg === 20 ? "rgba(122,162,255,0.45)" : "rgba(180,200,255,0.12)") +
          '" stroke-dasharray="' +
          (deg === 20 ? "4 3" : "2 4") +
          '"/>' +
          '<text x="' +
          (padL - 4) +
          '" y="' +
          (y + 3) +
          '" text-anchor="end" fill="#8b93a7" font-size="10" font-family="ui-monospace,monospace">' +
          deg +
          "°</text>";
      });

      // Hour ticks
      const firstHr = Math.ceil(startMs / 3600000) * 3600000;
      for (let t = firstHr; t <= endMs; t += 3600000) {
        const x = xAt(t);
        grid +=
          '<line x1="' +
          x +
          '" y1="' +
          padT +
          '" x2="' +
          x +
          '" y2="' +
          (padT + plotH) +
          '" stroke="rgba(180,200,255,0.08)"/>' +
          '<text x="' +
          x +
          '" y="' +
          (H - 6) +
          '" text-anchor="middle" fill="#8b93a7" font-size="10" font-family="ui-monospace,monospace">' +
          fmtT(t, off) +
          "</text>";
      }

      let peakMark = "";
      if (t.peakT != null) {
        peakMark =
          '<circle cx="' +
          xAt(t.peakT).toFixed(1) +
          '" cy="' +
          yAt(t.peak).toFixed(1) +
          '" r="3.5" fill="#F0B860"/>' +
          '<text x="' +
          xAt(t.peakT).toFixed(1) +
          '" y="' +
          (yAt(t.peak) - 8).toFixed(1) +
          '" text-anchor="middle" fill="#F0B860" font-size="10" font-family="ui-monospace,monospace">' +
          Math.round(t.peak) +
          "°</text>";
      }

      return (
        '<div class="alt-chart" data-target-idx="' +
        String(idx) +
        '">' +
        '<svg viewBox="0 0 ' +
        W +
        " " +
        H +
        '" preserveAspectRatio="none" class="alt-svg" role="img" aria-label="Altitude over time for ' +
        esc(t.o.name) +
        '">' +
        grid +
        (area
          ? '<path d="' + area + '" fill="rgba(122,162,255,0.12)"/>'
          : "") +
        (goodFill
          ? '<path d="' + goodFill + '" fill="rgba(109,222,168,0.18)"/>'
          : "") +
        '<path d="' +
        path +
        '" fill="none" stroke="#7aa2ff" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round"/>' +
        peakMark +
        '<line class="alt-cursor" x1="' +
        padL +
        '" y1="' +
        padT +
        '" x2="' +
        padL +
        '" y2="' +
        (padT + plotH) +
        '" stroke="rgba(232,236,247,0.55)" stroke-width="1" visibility="hidden"/>' +
        '<circle class="alt-dot" cx="0" cy="0" r="4" fill="#e8ecf7" stroke="#7aa2ff" stroke-width="2" visibility="hidden"/>' +
        "</svg>" +
        '<div class="alt-readout muted">Move over the chart to see altitude at that time</div>' +
        "</div>"
      );
    }

    $("targets").innerHTML = targets
      .map((t, i) => {
        const win = t.segs.map(([a, b]) => fmtT(a, off) + " – " + fmtT(b, off)).join(", ");
        const bright = brightnessLabel(t.o.mag);
        const size = sizeLabel(t.o.size);
        const moon = moonLabel(t.sep, t.moonFlag);
        const typePlain =
          TYPE_PLAIN[t.o.type] || TYPE_LABEL[t.o.type] || t.o.type || "Deep-sky object";
        const rank = i === 0 ? '<span class="target-badge best">Top pick</span>' : "";
        const moonBadge = t.moonFlag
          ? '<span class="target-badge warn">Moon interference</span>'
          : '<span class="target-badge good">Clear of Moon</span>';

        return (
          '<article class="target-card">' +
          '<button type="button" class="planner-thumb" data-idx="' +
          i +
          '" aria-label="Open details for ' +
          esc(t.o.name) +
          '"><span class="ph">' +
          esc(String(t.o.id).split(" ")[0]) +
          "</span></button>" +
          '<div class="target-main">' +
          '<div class="target-top">' +
          '<div class="target-title-block">' +
          '<h3 class="target-title"><button type="button" data-idx="' +
          i +
          '">' +
          esc(t.o.name) +
          "</button></h3>" +
          '<div class="target-id muted">' +
          esc(t.o.id) +
          " · " +
          esc(typePlain) +
          "</div>" +
          "</div>" +
          '<div class="target-badges">' +
          rank +
          moonBadge +
          "</div>" +
          "</div>" +
          '<p class="target-summary"><strong>Shoot between</strong> ' +
          esc(win) +
          ' <span class="muted">(' +
          fmtDur(t.minutes) +
          ")</span>. " +
          "<strong>Highest</strong> around " +
          fmtT(t.peakT, off) +
          " — " +
          heightLabel(t.peak) +
          " (" +
          Math.round(t.peak) +
          "°).</p>" +
          '<div class="target-timeline" aria-label="Altitude of this object during your session">' +
          altitudeChart(t, i) +
          '<div class="timeline-hint muted">Curve = altitude (°). Green band = above 20° (good for imaging). Hover for exact time &amp; degrees.</div>' +
          "</div>" +
          '<div class="target-chips">' +
          '<span class="chip" title="' +
          esc(bright.tip) +
          '"><span class="chip-k">Brightness</span> ' +
          esc(bright.label) +
          "</span>" +
          '<span class="chip" title="' +
          esc(size.tip) +
          '"><span class="chip-k">Size</span> ' +
          esc(size.label) +
          "</span>" +
          '<span class="chip ' +
          moon.cls +
          '" title="' +
          esc(moon.tip) +
          '"><span class="chip-k">Moon</span> ' +
          esc(moon.label) +
          "</span>" +
          "</div>" +
          "</div>" +
          "</article>"
        );
      })
      .join("");

    // Hover readout: altitude at pointer time
    $("targets").querySelectorAll(".alt-chart").forEach((wrap) => {
      const idx = +wrap.dataset.targetIdx;
      const t = targets[idx];
      if (!t || !t.alts || !t.alts.length) return;
      const svg = wrap.querySelector(".alt-svg");
      const cursor = wrap.querySelector(".alt-cursor");
      const dot = wrap.querySelector(".alt-dot");
      const readout = wrap.querySelector(".alt-readout");
      const W = 640;
      const H = 120;
      const padL = 34;
      const padR = 10;
      const padT = 10;
      const padB = 22;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;
      const maxAlt = Math.max(90, Math.ceil((t.peak || 30) / 10) * 10);
      const yAt = (alt) =>
        padT + plotH - (Math.max(0, Math.min(maxAlt, alt)) / maxAlt) * plotH;

      const sampleAt = (ms) => {
        let best = t.alts[0];
        let bestD = Math.abs(best.t - ms);
        for (const p of t.alts) {
          const d = Math.abs(p.t - ms);
          if (d < bestD) {
            best = p;
            bestD = d;
          }
        }
        return best;
      };

      const onMove = (e) => {
        const rect = svg.getBoundingClientRect();
        const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        // map screen x through viewBox (preserveAspectRatio none → linear)
        const vbX = frac * W;
        if (vbX < padL || vbX > W - padR) return;
        const ms = startMs + ((vbX - padL) / plotW) * (endMs - startMs);
        const p = sampleAt(ms);
        const x = padL + ((p.t - startMs) / (endMs - startMs)) * plotW;
        const y = yAt(p.alt);
        cursor.setAttribute("x1", x);
        cursor.setAttribute("x2", x);
        cursor.setAttribute("visibility", "visible");
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", y);
        dot.setAttribute("visibility", "visible");
        const above = p.alt >= 20 ? "high enough to shoot" : "too low";
        readout.textContent =
          fmtT(p.t, off) +
          " · altitude " +
          Math.round(p.alt) +
          "° (" +
          above +
          ")";
        readout.classList.toggle("is-good", p.alt >= 20);
      };
      const onLeave = () => {
        cursor.setAttribute("visibility", "hidden");
        dot.setAttribute("visibility", "hidden");
        readout.textContent = "Move over the chart to see altitude at that time";
        readout.classList.remove("is-good");
      };
      svg.addEventListener("mousemove", onMove);
      svg.addEventListener("mouseleave", onLeave);
    });

    $("targets").querySelectorAll("[data-idx]").forEach((el) => {
      el.addEventListener("click", () => openDetail(+el.dataset.idx));
    });
    loadThumbs(targets);
  }
})();
