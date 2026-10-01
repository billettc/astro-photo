/* Astronomy weather dashboard — uses window.AstroCore + window.ASTRO_CATALOG */
(() => {
  const C = window.AstroCore;
  const CATALOG = window.ASTRO_CATALOG || [];
  if (!C || !CATALOG.length) {
    console.error("AstroCore / ASTRO_CATALOG missing");
    return;
  }

  const {
    pad,
    moonEq,
    moonIllum,
    phaseName,
    altOf,
    sunAlt,
    fmtT,
    fmtDur,
    esc,
    geocodeLocation,
    fetchForecast,
    seeingScore,
    TYPE_LABEL,
  } = C;

  const $ = (id) => document.getElementById(id);
  const STORAGE_KEY = "astro-photo.locations";
  const SELECTED_KEY = "astro-photo.locationId";

  const SEED = [
    {
      id: "cherry-springs",
      name: "Cherry Springs, PA",
      query: "Cherry Springs, PA",
      lat: 41.6601,
      lon: -77.8213,
      bortle: 2,
    },
    {
      id: "melrose",
      name: "Melrose, MA",
      query: "Melrose, MA",
      lat: 42.4584,
      lon: -71.0662,
      bortle: 5,
    },
  ];

  function loadLocations() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const arr = JSON.parse(raw);
        if (Array.isArray(arr) && arr.length) return arr;
      }
    } catch (e) {
      /* ignore */
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(SEED));
    return SEED.slice();
  }

  function saveLocations(list) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  }

  let locations = loadLocations();
  let selectedId = localStorage.getItem(SELECTED_KEY) || (locations[0] && locations[0].id);
  let busy = false;

  function currentLoc() {
    return locations.find((l) => l.id === selectedId) || locations[0];
  }

  function setAlert(msg) {
    const el = $("weatherAlert");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function setLoading(on) {
    const el = $("loadingOverlay");
    if (el) el.hidden = !on;
  }

  function uid() {
    return "loc-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
  }

  function fillLocSelect() {
    const sel = $("locSelect");
    if (!sel) return;
    sel.innerHTML = "";
    locations.forEach((l) => {
      sel.add(new Option(l.name, l.id));
    });
    if (!locations.some((l) => l.id === selectedId) && locations[0]) {
      selectedId = locations[0].id;
    }
    sel.value = selectedId;
    const loc = currentLoc();
    if ($("bortleInput") && loc) $("bortleInput").value = loc.bortle;
  }

  function showLocForm(show) {
    const form = $("locForm");
    if (!form) return;
    form.hidden = !show;
    if (show) {
      if ($("locNameInput")) $("locNameInput").value = "";
      if ($("locQueryInput")) $("locQueryInput").value = "";
      if ($("locBortleNew")) $("locBortleNew").value = "4";
      if ($("locNameInput")) $("locNameInput").focus();
    }
  }

  function avg(arr) {
    if (!arr.length) return 0;
    return arr.reduce((s, v) => s + (v ?? 0), 0) / arr.length;
  }

  function parseHourly(wx) {
    const off = wx.utc_offset_seconds || 0;
    const H = wx.hourly;
    const out = [];
    if (!H || !H.time) return { hours: out, off };
    for (let i = 0; i < H.time.length; i++) {
      const [ds, ts] = H.time[i].split("T");
      const [y, mo, da] = ds.split("-").map(Number);
      const hh = +ts.split(":")[0];
      const mm = +(ts.split(":")[1] || 0);
      const ms = Date.UTC(y, mo - 1, da, hh, mm) - off * 1000;
      out.push({
        ms,
        cloud: H.cloud_cover[i],
        wind: H.wind_speed_10m[i],
        humidity: H.relative_humidity_2m[i],
        temp: H.temperature_2m[i],
        label: H.time[i],
      });
    }
    return { hours: out, off };
  }

  function pickNear(hours, t) {
    if (!hours.length) return null;
    let best = hours[0];
    let bestD = Math.abs(hours[0].ms - t);
    for (let i = 1; i < hours.length; i++) {
      const d = Math.abs(hours[i].ms - t);
      if (d < bestD) {
        best = hours[i];
        bestD = d;
      }
    }
    return best;
  }

  function overallViewingScore(cloud, seeing01, windMph, moonFrac, moonUp) {
    const clear = Math.max(0, Math.min(100, 100 - (cloud ?? 50)));
    const seeing = Math.max(0, Math.min(100, (seeing01 ?? 0.5) * 100));
    const wind = Math.max(0, Math.min(100, 100 - ((windMph ?? 10) / 30) * 100));
    let moon = 100;
    if (moonUp) moon = Math.max(0, Math.min(100, 100 - (moonFrac ?? 0) * 100));
    return Math.round(clear * 0.35 + seeing * 0.3 + moon * 0.2 + wind * 0.15);
  }

  function drawMoonPhase(canvas, elong) {
    if (!canvas || !canvas.getContext) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const r = Math.min(w, h) / 2 - 2;
    const D2R = Math.PI / 180;
    const k = Math.cos(elong * D2R);
    const waxing = elong < 180;
    const rx = Math.max(0.01, Math.abs(r * k));
    // Mirror planner SVG disc: outer arc + terminator ellipse arc
    const sweepOuter = waxing ? 1 : 0;
    const sweepInner = k > 0 ? (waxing ? 0 : 1) : waxing ? 1 : 0;

    ctx.clearRect(0, 0, w, h);

    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = "#283057";
    ctx.fill();

    // SVG sweep-flag 1 = clockwise ≈ canvas counterclockwise false
    ctx.beginPath();
    ctx.moveTo(cx, cy - r);
    ctx.arc(cx, cy, r, -Math.PI / 2, Math.PI / 2, sweepOuter === 0);
    ctx.ellipse(
      cx,
      cy,
      rx,
      r,
      0,
      Math.PI / 2,
      -Math.PI / 2,
      sweepInner === 0
    );
    ctx.closePath();
    ctx.fillStyle = "#E8DFC8";
    ctx.fill();

    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(232,223,200,0.25)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function nightlySamples(now, lat, lon, off) {
    // Sample from now through next ~16 hours (or until morning sun)
    const step = 10 * 60000;
    const end = now + 16 * 3600000;
    const samples = [];
    for (let t = now; t <= end; t += step) {
      const m = moonEq(t);
      samples.push({
        t,
        sun: sunAlt(t, lat, lon),
        moon: altOf(t, m.ra, m.dec, lat, lon),
        moonRa: m.ra,
        moonDec: m.dec,
      });
    }
    return samples;
  }

  function moonFreeWindows(samples, off) {
    const night = samples.filter((s) => s.sun < -6);
    if (!night.length) return "—";
    const free = [];
    let start = null;
    for (let i = 0; i < night.length; i++) {
      const s = night[i];
      const isFree = s.moon < 0;
      if (isFree && start === null) start = s.t;
      if ((!isFree || i === night.length - 1) && start !== null) {
        const endT = isFree && i === night.length - 1 ? s.t : night[i - 1].t;
        if (endT > start) free.push([start, endT]);
        start = null;
      }
    }
    if (!free.length) {
      const anyMoonDown = night.some((s) => s.moon < 0);
      return anyMoonDown ? "Brief gaps only" : "Moon up all night";
    }
    return free
      .map(([a, b]) => fmtT(a, off) + "–" + fmtT(b, off))
      .join(", ");
  }

  function objectPeakTonight(o, now, lat, lon) {
    const step = 15 * 60000;
    const end = now + 14 * 3600000;
    let peak = -90;
    let peakT = now;
    const altNow = altOf(now, o.ra, o.dec, lat, lon);
    for (let t = now; t <= end; t += step) {
      if (sunAlt(t, lat, lon) > -6) continue;
      const a = altOf(t, o.ra, o.dec, lat, lon);
      if (a > peak) {
        peak = a;
        peakT = t;
      }
    }
    return { altNow, peak, peakT };
  }

  function recommendDSOs(lat, lon, bortle, moonFrac, moonUp, now, off) {
    const month = new Date(now).getUTCMonth() + 1;
    const scored = [];
    for (const o of CATALOG) {
      if (o.minBortle != null && !(o.minBortle >= bortle)) continue;
      const inSeason = !o.season || !o.season.length || o.season.indexOf(month) !== -1;
      if (!inSeason) continue;

      const { altNow, peak, peakT } = objectPeakTonight(o, now, lat, lon);
      if (peak < 15) continue;

      let score = peak * 0.5 + Math.max(0, altNow) * 0.25;
      score += (10 - Math.min(10, Number(o.mag) || 10)) * 2;
      if (o.season && o.season.indexOf(month) !== -1) score += 8;

      if (moonUp && moonFrac > 0.4 && o.moonTolerant === false) {
        score -= 25;
      } else if (moonUp && moonFrac > 0.4 && o.moonTolerant === true) {
        score += 4;
      }

      scored.push({ o, altNow, peak, peakT, score });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, 8);
  }

  function renderHourlyStrip(hours, now, off) {
    const el = $("hourlyStrip");
    if (!el) return;
    const next = hours.filter((h) => h.ms >= now - 1800000).slice(0, 24);
    if (!next.length) {
      el.innerHTML = '<p class="muted">No hourly cloud data.</p>';
      return;
    }
    el.innerHTML = next
      .map((h) => {
        const pct = Math.max(0, Math.min(100, h.cloud ?? 0));
        const hgt = Math.max(4, Math.round((pct / 100) * 72));
        const label = fmtT(h.ms, off);
        const tone =
          pct < 25 ? "#6ddea8" : pct < 55 ? "#F0B860" : "#ff6b7a";
        return (
          '<div class="dash-hour" title="' +
          esc(label) +
          " · " +
          pct +
          '% cloud" style="display:inline-flex;flex-direction:column;align-items:center;gap:4px;width:28px">' +
          '<div style="height:72px;display:flex;align-items:flex-end;width:100%;justify-content:center">' +
          '<div style="width:14px;height:' +
          hgt +
          "px;border-radius:4px 4px 2px 2px;background:" +
          tone +
          ';opacity:0.85"></div></div>' +
          '<span class="muted" style="font-size:0.65rem">' +
          esc(label) +
          "</span></div>"
        );
      })
      .join("");
    el.style.display = "flex";
    el.style.gap = "2px";
    el.style.overflowX = "auto";
    el.style.paddingBottom = "0.25rem";
  }

  function renderDSOList(items, off) {
    const el = $("dsoList");
    if (!el) return;
    if (!items.length) {
      el.innerHTML = '<p class="muted">No seasonal targets match this sky brightness.</p>';
      return;
    }
    el.innerHTML = items
      .map((t) => {
        const type = (TYPE_LABEL && TYPE_LABEL[t.o.type]) || t.o.type || "";
        const nowStr =
          t.altNow > 0 ? Math.round(t.altNow) + "° now" : "below horizon";
        const peakStr =
          t.peak > 0
            ? "peak " + Math.round(t.peak) + "° @ " + fmtT(t.peakT, off)
            : "—";
        return (
          '<div class="dash-dso" style="display:flex;justify-content:space-between;gap:1rem;padding:0.55rem 0;border-bottom:1px solid rgba(180,200,255,0.08)">' +
          "<div><strong>" +
          esc(t.o.id) +
          "</strong> " +
          esc(t.o.name) +
          '<div class="muted" style="font-size:0.85rem">' +
          esc(type) +
          " · mag " +
          Number(t.o.mag).toFixed(1) +
          (t.o.moonTolerant === false ? " · moon-sensitive" : "") +
          "</div></div>" +
          '<div class="muted" style="text-align:right;font-size:0.85rem;white-space:nowrap">' +
          esc(nowStr) +
          "<br>" +
          esc(peakStr) +
          "</div></div>"
        );
      })
      .join("");
  }

  function setCard(id, value, suffix) {
    const el = $(id);
    if (!el) return;
    el.textContent = value + (suffix || "");
  }

  function dailyField(wx, key, dayIndex) {
    const d = wx && wx.daily;
    if (!d || !d[key] || d[key][dayIndex] == null) return null;
    return d[key][dayIndex];
  }

  function parseIsoLocal(iso, off) {
    if (!iso || iso === "null") return null;
    // Open-Meteo daily times are local ISO without Z
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return null;
    return (
      Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) - (off || 0) * 1000
    );
  }

  async function refresh() {
    if (busy) return;
    const loc = currentLoc();
    if (!loc) {
      setAlert("Add a location to begin.");
      return;
    }
    busy = true;
    setLoading(true);
    setAlert("");
    try {
      let lat = loc.lat;
      let lon = loc.lon;
      if (lat == null || lon == null) {
        const place = await geocodeLocation(loc.query || loc.name);
        lat = place.latitude;
        lon = place.longitude;
        loc.lat = lat;
        loc.lon = lon;
        if (!loc.name) loc.name = place.label;
        saveLocations(locations);
      }

      if ($("displayName")) $("displayName").textContent = loc.name;
      if ($("displayCoords")) {
        $("displayCoords").textContent =
          lat.toFixed(4) + "°, " + lon.toFixed(4) + "° · Bortle " + loc.bortle;
      }

      const wx = await fetchForecast(lat, lon);
      const { hours, off } = parseHourly(wx);
      const now = Date.now();
      const near = pickNear(hours, now) || { cloud: 50, wind: 10, humidity: 60 };
      const nextNight = hours.filter((h) => h.ms >= now && h.ms <= now + 12 * 3600000);
      const cloudAvg = avg((nextNight.length ? nextNight : hours.slice(0, 12)).map((h) => h.cloud));
      const windAvg = avg((nextNight.length ? nextNight : hours.slice(0, 12)).map((h) => h.wind));
      const humAvg = avg((nextNight.length ? nextNight : hours.slice(0, 12)).map((h) => h.humidity));

      const see = seeingScore(cloudAvg, windAvg, humAvg);
      const illum = moonIllum(now);
      const moonFrac = illum.frac;
      const samples = nightlySamples(now, lat, lon, off);
      const moonNow = samples[0] ? samples[0].moon : altOf(now, moonEq(now).ra, moonEq(now).dec, lat, lon);
      const moonUp = moonNow > 0;
      const moonPeakAlt = Math.max(...samples.map((s) => s.moon), moonNow);
      const moonPeakSample = samples.reduce((b, s) => (s.moon > b.moon ? s : b), samples[0] || { moon: moonNow, t: now });

      const score = overallViewingScore(cloudAvg, see, windAvg, moonFrac, moonUp);
      if ($("overallScore")) $("overallScore").textContent = String(score);

      setCard("cardCloud", Math.round(cloudAvg) + "%");
      setCard("cardSeeing", Math.round(see * 100));
      setCard("cardWind", Math.round(windAvg) + " mph");
      setCard("cardHumidity", Math.round(humAvg) + "%");

      // Sun / moon daily times — use day 0, or day 1 if after sunset
      let dayIdx = 0;
      const sunrise0 = parseIsoLocal(dailyField(wx, "sunrise", 0), off);
      const sunset0 = parseIsoLocal(dailyField(wx, "sunset", 0), off);
      if (sunset0 && now > sunset0) dayIdx = 1;

      const sunriseMs = parseIsoLocal(dailyField(wx, "sunrise", dayIdx), off) || sunrise0;
      const sunsetMs = parseIsoLocal(dailyField(wx, "sunset", dayIdx), off) || sunset0;
      const moonriseMs = parseIsoLocal(dailyField(wx, "moonrise", dayIdx), off);
      const moonsetMs = parseIsoLocal(dailyField(wx, "moonset", dayIdx), off);

      if ($("sunriseTime")) $("sunriseTime").textContent = sunriseMs ? fmtT(sunriseMs, off) : "—";
      if ($("sunsetTime")) $("sunsetTime").textContent = sunsetMs ? fmtT(sunsetMs, off) : "—";
      if ($("daylightDuration")) {
        if (sunriseMs && sunsetMs && sunsetMs > sunriseMs) {
          $("daylightDuration").textContent = fmtDur(Math.round((sunsetMs - sunriseMs) / 60000));
        } else {
          $("daylightDuration").textContent = "—";
        }
      }

      if ($("moonPhaseName")) $("moonPhaseName").textContent = phaseName(illum.elong);
      if ($("moonIllum")) $("moonIllum").textContent = Math.round(moonFrac * 100) + "%";
      if ($("moonriseTime")) $("moonriseTime").textContent = moonriseMs ? fmtT(moonriseMs, off) : "—";
      if ($("moonsetTime")) $("moonsetTime").textContent = moonsetMs ? fmtT(moonsetMs, off) : "—";
      if ($("moonPeak")) {
        $("moonPeak").textContent =
          moonPeakAlt > 0
            ? Math.round(moonPeakAlt) + "° @ " + fmtT(moonPeakSample.t, off)
            : "Below horizon";
      }

      drawMoonPhase($("moonCanvas"), illum.elong);

      if ($("moonFreeWindow")) {
        $("moonFreeWindow").textContent = moonFreeWindows(samples, off);
      }

      renderHourlyStrip(hours, now, off);

      const picks = recommendDSOs(lat, lon, Number(loc.bortle) || 5, moonFrac, moonUp, now, off);
      renderDSOList(picks, off);
    } catch (e) {
      setAlert(e.message || String(e));
    } finally {
      busy = false;
      setLoading(false);
    }
  }

  // —— events ——
  fillLocSelect();
  showLocForm(false);

  if ($("locSelect")) {
    $("locSelect").addEventListener("change", () => {
      selectedId = $("locSelect").value;
      localStorage.setItem(SELECTED_KEY, selectedId);
      const loc = currentLoc();
      if ($("bortleInput") && loc) $("bortleInput").value = loc.bortle;
      refresh();
    });
  }

  if ($("bortleInput")) {
    $("bortleInput").addEventListener("change", () => {
      const loc = currentLoc();
      if (!loc) return;
      const v = Math.max(1, Math.min(9, Number($("bortleInput").value) || loc.bortle));
      loc.bortle = v;
      $("bortleInput").value = v;
      saveLocations(locations);
      refresh();
    });
  }

  if ($("addLocBtn")) {
    $("addLocBtn").addEventListener("click", () => showLocForm(true));
  }
  if ($("cancelLocBtn")) {
    $("cancelLocBtn").addEventListener("click", () => showLocForm(false));
  }
  if ($("saveLocBtn")) {
    $("saveLocBtn").addEventListener("click", async () => {
      const name = ($("locNameInput") && $("locNameInput").value.trim()) || "";
      const query = ($("locQueryInput") && $("locQueryInput").value.trim()) || "";
      const bortle = Math.max(1, Math.min(9, Number($("locBortleNew") && $("locBortleNew").value) || 4));
      if (!query) {
        setAlert("Enter a city / location to geocode.");
        return;
      }
      setLoading(true);
      setAlert("");
      try {
        const place = await geocodeLocation(query);
        const entry = {
          id: uid(),
          name: name || place.label,
          query: query,
          lat: place.latitude,
          lon: place.longitude,
          bortle: bortle,
        };
        locations.push(entry);
        saveLocations(locations);
        selectedId = entry.id;
        localStorage.setItem(SELECTED_KEY, selectedId);
        fillLocSelect();
        showLocForm(false);
        await refresh();
      } catch (e) {
        setAlert(e.message || String(e));
        setLoading(false);
      }
    });
  }

  if ($("refreshBtn")) {
    $("refreshBtn").addEventListener("click", () => refresh());
  }

  refresh();
})();
