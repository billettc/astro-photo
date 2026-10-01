/* Shared astronomy + Open-Meteo helpers for planner / dashboard */
(() => {
  const D2R = Math.PI / 180;
  const R2D = 180 / Math.PI;
  const sin = (x) => Math.sin(x * D2R);
  const cos = (x) => Math.cos(x * D2R);
  const norm360 = (x) => ((x % 360) + 360) % 360;
  const pad = (n) => String(n).padStart(2, "0");

  const US_STATES = {
    al: "alabama", ak: "alaska", az: "arizona", ar: "arkansas", ca: "california",
    co: "colorado", ct: "connecticut", de: "delaware", fl: "florida", ga: "georgia",
    hi: "hawaii", id: "idaho", il: "illinois", in: "indiana", ia: "iowa", ks: "kansas",
    ky: "kentucky", la: "louisiana", me: "maine", md: "maryland", ma: "massachusetts",
    mi: "michigan", mn: "minnesota", ms: "mississippi", mo: "missouri", mt: "montana",
    ne: "nebraska", nv: "nevada", nh: "new hampshire", nj: "new jersey", nm: "new mexico",
    ny: "new york", nc: "north carolina", nd: "north dakota", oh: "ohio", ok: "oklahoma",
    or: "oregon", pa: "pennsylvania", ri: "rhode island", sc: "south carolina",
    sd: "south dakota", tn: "tennessee", tx: "texas", ut: "utah", vt: "vermont",
    va: "virginia", wa: "washington", wv: "west virginia", wi: "wisconsin", wy: "wyoming",
    dc: "district of columbia",
  };

  function jd(ms) {
    return ms / 86400000 + 2440587.5;
  }

  function gmst(ms) {
    return norm360(280.46061837 + 360.98564736629 * (jd(ms) - 2451545.0));
  }

  function sunEcl(ms) {
    const T = (jd(ms) - 2451545) / 36525;
    const M = norm360(357.52911 + 35999.05029 * T);
    const L = norm360(
      280.46646 + 36000.76983 * T + 1.914602 * sin(M) + 0.019993 * sin(2 * M)
    );
    return { lon: L, M };
  }

  function moonEq(ms) {
    const T = (jd(ms) - 2451545) / 36525;
    const Lp = norm360(218.3164477 + 481267.88123421 * T);
    const D = norm360(297.8501921 + 445267.1114034 * T);
    const M = norm360(357.5291092 + 35999.0502909 * T);
    const Mp = norm360(134.9633964 + 477198.8675055 * T);
    const F = norm360(93.272095 + 483202.0175233 * T);
    const lon =
      Lp +
      6.288774 * sin(Mp) +
      1.274027 * sin(2 * D - Mp) +
      0.658314 * sin(2 * D) +
      0.213618 * sin(2 * Mp) -
      0.185116 * sin(M) -
      0.114332 * sin(2 * F) +
      0.058793 * sin(2 * D - 2 * Mp) +
      0.057066 * sin(2 * D - M - Mp) +
      0.053322 * sin(2 * D + Mp) +
      0.045758 * sin(2 * D - M);
    const lat =
      5.128122 * sin(F) +
      0.280602 * sin(Mp + F) +
      0.277693 * sin(Mp - F) +
      0.173237 * sin(2 * D - F) +
      0.055413 * sin(2 * D - Mp + F) +
      0.046271 * sin(2 * D - Mp - F);
    const eps = 23.439291 - 0.0130042 * T;
    const ra = norm360(
      Math.atan2(sin(lon) * cos(eps) - Math.tan(lat * D2R) * sin(eps), cos(lon)) * R2D
    );
    const dec =
      Math.asin(Math.sin(lat * D2R) * cos(eps) + Math.cos(lat * D2R) * sin(eps) * sin(lon)) *
      R2D;
    return { ra: ra / 15, dec, lon, lat };
  }

  function moonIllum(ms) {
    const m = moonEq(ms);
    const s = sunEcl(ms);
    const elong = norm360(m.lon - s.lon);
    const frac = (1 - cos(elong)) / 2;
    return { frac, elong };
  }

  function phaseName(elong) {
    const names = [
      "New Moon",
      "Waxing Crescent",
      "First Quarter",
      "Waxing Gibbous",
      "Full Moon",
      "Waning Gibbous",
      "Last Quarter",
      "Waning Crescent",
    ];
    return names[Math.floor(((elong + 22.5) % 360) / 45)];
  }

  function altOf(ms, raH, dec, lat, lon) {
    const lst = norm360(gmst(ms) + lon);
    const ha = norm360(lst - raH * 15);
    return Math.asin(sin(lat) * sin(dec) + cos(lat) * cos(dec) * cos(ha)) * R2D;
  }

  function sunAlt(ms, lat, lon) {
    const s = sunEcl(ms);
    const T = (jd(ms) - 2451545) / 36525;
    const eps = 23.439291 - 0.0130042 * T;
    const ra = norm360(Math.atan2(sin(s.lon) * cos(eps), cos(s.lon)) * R2D) / 15;
    const dec = Math.asin(sin(eps) * sin(s.lon)) * R2D;
    return altOf(ms, ra, dec, lat, lon);
  }

  function angularSep(ra1, dec1, ra2, dec2) {
    const a =
      sin(dec1) * sin(dec2) + cos(dec1) * cos(dec2) * cos((ra1 - ra2) * 15);
    return Math.acos(Math.min(1, Math.max(-1, a))) * R2D;
  }

  function fmtT(ms, off) {
    const d = new Date(ms + off * 1000);
    let h = d.getUTCHours();
    const m = d.getUTCMinutes();
    const ap = h >= 12 ? "p" : "a";
    h = h % 12 || 12;
    return h + ":" + pad(m) + ap;
  }

  function fmtDur(min) {
    const h = Math.floor(min / 60);
    const m = min % 60;
    return (h ? h + "h" : "") + (m ? (h ? " " : "") + m + "m" : "") || "0m";
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
    );
  }

  async function geocodeLocation(query) {
    const q = (query || "").trim();
    if (!q) throw new Error("Enter a location.");
    const parts = q.split(",").map((s) => s.trim()).filter(Boolean);
    const cityQ = parts[0];
    const regionQ = (parts[1] || "").toLowerCase();
    const g = await fetch(
      "https://geocoding-api.open-meteo.com/v1/search?name=" +
        encodeURIComponent(cityQ) +
        "&count=10&language=en&format=json"
    ).then((r) => r.json());
    if (!g.results || !g.results.length) {
      throw new Error(
        'Could not find "' + cityQ + '" — check the spelling, or try a nearby larger town.'
      );
    }
    let place = g.results[0];
    if (regionQ) {
      const full = US_STATES[regionQ] || regionQ;
      const hit = g.results.find((r) => {
        const a1 = (r.admin1 || "").toLowerCase();
        const cc = (r.country_code || "").toLowerCase();
        return (
          a1 === full ||
          a1 === regionQ ||
          cc === regionQ ||
          (r.country || "").toLowerCase() === regionQ
        );
      });
      if (hit) place = hit;
      else if (US_STATES[regionQ]) {
        throw new Error(
          'Found "' +
            cityQ +
            '" but not in ' +
            parts[1].toUpperCase() +
            " — closest match was " +
            place.name +
            ", " +
            (place.admin1 || place.country) +
            ". Check the state, or omit it."
        );
      }
    }
    return {
      name: place.name,
      admin1: place.admin1 || "",
      country: place.country || "",
      country_code: place.country_code || "",
      latitude: place.latitude,
      longitude: place.longitude,
      elevation: place.elevation,
      label:
        place.name +
        (place.admin1 ? ", " + place.admin1 : "") +
        ", " +
        (place.country_code || place.country || ""),
    };
  }

  async function fetchForecast(lat, lon, startDate, endDate) {
    const url =
      "https://api.open-meteo.com/v1/forecast?latitude=" +
      lat +
      "&longitude=" +
      lon +
      "&hourly=cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,relative_humidity_2m,dew_point_2m,temperature_2m,wind_speed_10m,precipitation_probability,visibility" +
      "&daily=sunrise,sunset,moonrise,moonset" +
      "&temperature_unit=fahrenheit&wind_speed_unit=mph" +
      (startDate && endDate
        ? "&start_date=" + startDate + "&end_date=" + endDate
        : "&forecast_days=3") +
      "&timezone=auto";
    const wx = await fetch(url).then((r) => r.json());
    if (wx.error) throw new Error(wx.reason || "Weather fetch failed");
    return wx;
  }

  async function fetchTimezoneOffset(lat, lon) {
    const wz = await fetch(
      "https://api.open-meteo.com/v1/forecast?latitude=" +
        lat +
        "&longitude=" +
        lon +
        "&timezone=auto"
    ).then((r) => r.json());
    return wz.utc_offset_seconds ?? Math.round(lon / 15) * 3600;
  }

  const wikiCache = {};
  function getWiki(title) {
    if (!wikiCache[title]) {
      wikiCache[title] = fetch(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" +
          encodeURIComponent(title)
      )
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
    }
    return wikiCache[title];
  }

  function bigImg(d) {
    if (d && d.thumbnail) {
      const u = d.thumbnail.source;
      const up = u.replace(/\/(\d+)px-/, "/1000px-");
      if (up !== u) return up;
    }
    if (d && d.originalimage) return d.originalimage.source;
    return d && d.thumbnail ? d.thumbnail.source : null;
  }

  /** Rough seeing score 0–1 from wind + humidity + cloud (higher = better). */
  function seeingScore(cloud, windMph, humidity) {
    const c = Math.max(0, Math.min(100, cloud ?? 50)) / 100;
    const w = Math.max(0, Math.min(40, windMph ?? 10)) / 40;
    const h = Math.max(0, Math.min(100, humidity ?? 60)) / 100;
    return Math.max(0, Math.min(1, 1 - c * 0.55 - w * 0.25 - h * 0.2));
  }

  window.AstroCore = {
    pad,
    jd,
    gmst,
    sunEcl,
    moonEq,
    moonIllum,
    phaseName,
    altOf,
    sunAlt,
    angularSep,
    fmtT,
    fmtDur,
    esc,
    geocodeLocation,
    fetchForecast,
    fetchTimezoneOffset,
    getWiki,
    bigImg,
    seeingScore,
    US_STATES,
    TYPE_LABEL: {
      GAL: "galaxy",
      NEB: "nebula",
      OC: "open cl.",
      GC: "glob. cl.",
      PN: "planetary",
      SNR: "SNR",
    },
  };
})();
