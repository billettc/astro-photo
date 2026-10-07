(() => {
  // CRITICAL: cancel browser default for file drops (opens blob: URL otherwise).
  // Must run in capture phase and must NOT depend on dataTransfer.types —
  // on "drop", types is often empty even when files are present.
  const cancelFileNav = (e) => {
    e.preventDefault();
  };
  window.addEventListener("dragover", cancelFileNav, true);
  window.addEventListener("drop", cancelFileNav, true);

  // Generate album description with Grok
  const sessionsRoot = document.getElementById("sessions");
  if (sessionsRoot && sessionsRoot.querySelector("[data-solve-status='pending']")) {
    const sessionAlbumId = sessionsRoot.dataset.albumId;
    const pollSessions = async () => {
      try {
        const res = await fetch(`/admin/albums/${sessionAlbumId}/sessions.json`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!res.ok) return;
        const rows = await res.json();
        let still = false;
        rows.forEach((row) => {
          const el = sessionsRoot.querySelector(`[data-session-id="${row.id}"]`);
          if (!el) return;
          el.dataset.solveStatus = row.status || "";
          const label = el.querySelector("[data-solve-label]");
          if (label) {
            label.textContent = row.label || "";
            label.classList.toggle("solve-failed", row.status === "failed");
          }
          const retry = el.querySelector(".session-retry");
          if (retry) {
            retry.hidden = !(
              row.status === "failed" && (row.label || "").includes("star catalog")
            );
          }
          if (row.status === "pending") still = true;
        });
        if (still) setTimeout(pollSessions, 2000);
      } catch {
        /* leave the last status on screen */
      }
    };
    setTimeout(pollSessions, 2000);
  }

  const genBtn = document.getElementById("generate-description-btn");
  const genStatus = document.getElementById("generate-description-status");
  const descField = document.getElementById("album-description");
  if (genBtn && descField) {
    genBtn.addEventListener("click", async () => {
      const albumId = genBtn.dataset.albumId;
      if (!albumId) return;
      const prev = genBtn.textContent;
      genBtn.disabled = true;
      genBtn.textContent = "Generating…";
      if (genStatus) {
        genStatus.textContent = "Grok is researching — will load description.md when ready…";
      }
      try {
        const res = await fetch(`/admin/albums/${albumId}/generate-description`, {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.detail || `HTTP ${res.status}`);
        }
        descField.value = data.description || "";
        descField.focus();
        if (genStatus) {
          genStatus.textContent = "Loaded from description.md — edit if you like, then Save settings";
        }
      } catch (err) {
        if (genStatus) {
          genStatus.textContent = err.message || "Generation failed";
        }
      } finally {
        genBtn.disabled = false;
        genBtn.textContent = prev;
      }
    });
  }

  // Copy share link buttons
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const value = btn.getAttribute("data-copy");
      try {
        await navigator.clipboard.writeText(value);
        const prev = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => (btn.textContent = prev), 1500);
      } catch {
        prompt("Copy this link:", value);
      }
    });
  });

  // One drop zone per imaging session. Images become photos. A FIT file
  // updates that session's header and plate solve.
  const isSessionFile = (file) => {
    const name = (file.name || "").toLowerCase();
    if (/\.(fit|fits|fts)(\.gz)?$/.test(name)) return true;
    if (/\.(jpe?g|png|webp|gif)$/.test(name)) return true;
    return Boolean(file.type && file.type.startsWith("image/"));
  };

  document.querySelectorAll(".session-drop").forEach((form) => {
    const input = form.querySelector(".session-file-input");
    const list = form.querySelector(".file-list");
    const dropzone = form.querySelector(".dropzone");
    const uploadBtn = form.querySelector("[data-upload-btn]");
    if (!input || !list || !dropzone) return;

    const render = () => {
      list.innerHTML = "";
      [...input.files].forEach((file) => {
        const li = document.createElement("li");
        li.textContent = `${file.name} (${Math.round(file.size / 1024)} KB)`;
        list.appendChild(li);
      });
    };

    const assignFiles = (fileList) => {
      const accepted = [...fileList].filter(isSessionFile);
      if (!accepted.length) return false;
      const dt = new DataTransfer();
      accepted.forEach((file) => dt.items.add(file));
      input.files = dt.files;
      input.removeAttribute("required");
      render();
      return true;
    };

    input.addEventListener("change", () => {
      render();
      if (input.files.length) input.removeAttribute("required");
    });

    dropzone.addEventListener("dragenter", (e) => {
      e.preventDefault();
      dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      dropzone.classList.add("dragover");
    });
    dropzone.addEventListener("dragleave", (e) => {
      if (!dropzone.contains(e.relatedTarget)) dropzone.classList.remove("dragover");
    });
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
      const files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      if (!assignFiles(files)) return;
      if (uploadBtn) {
        uploadBtn.disabled = true;
        uploadBtn.textContent = "Uploading…";
      }
      form.submit();
    });
  });

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
      redirect: "manual",
    });
    if (res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400)) {
      throw new Error("Not signed in");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : "Couldn't save order");
    }
    return data;
  }

  // Drag a .reorder-handle to reorder direct children. Saves on pointer-up.
  // The held item becomes a gap in the list, and a copy follows the pointer.
  function bindReorder(container, itemSelector, { onCommit, statusEl, idleText, axis }) {
    let drag = null;
    let busy = false;

    const itemNodes = () => [...container.querySelectorAll(`:scope > ${itemSelector}`)];
    const itemIds = () => itemNodes().map((el) => el.dataset.id);

    const showStatus = (text) => {
      if (!statusEl) return;
      statusEl.textContent = text;
      if (text && text !== idleText) {
        window.setTimeout(() => {
          if (statusEl.textContent === text) statusEl.textContent = idleText || "";
        }, 1600);
      }
    };

    const restore = (order) => {
      const byId = new Map(itemNodes().map((el) => [el.dataset.id, el]));
      for (const id of order) {
        const el = byId.get(id);
        if (el) container.appendChild(el);
      }
    };

    const axisNow = () => {
      if (axis === "grid") return "grid";
      const dir = getComputedStyle(container).flexDirection;
      return dir === "column" || dir === "column-reverse" ? "y" : "x";
    };

    const placeSlot = (x, y) => {
      const mode = axisNow();
      const others = itemNodes().filter((el) => el !== drag.item);
      let before = null;
      if (mode === "y") {
        for (const el of others) {
          const rect = el.getBoundingClientRect();
          if (y < rect.top + rect.height / 2) {
            before = el;
            break;
          }
        }
      } else if (mode === "x") {
        for (const el of others) {
          const rect = el.getBoundingClientRect();
          if (x < rect.left + rect.width / 2) {
            before = el;
            break;
          }
        }
      } else {
        let closest = null;
        let best = Infinity;
        let after = false;
        for (const el of others) {
          const rect = el.getBoundingClientRect();
          const cx = rect.left + rect.width / 2;
          const cy = rect.top + rect.height / 2;
          const dist = (x - cx) ** 2 + (y - cy) ** 2;
          if (dist < best) {
            best = dist;
            closest = el;
            const dx = x - cx;
            const dy = y - cy;
            after = Math.abs(dx) > Math.abs(dy) ? dx > 0 : dy > 0;
          }
        }
        if (!closest) return;
        before = after ? closest.nextElementSibling : closest;
        if (before === drag.item) before = drag.item.nextElementSibling;
      }
      if (before === drag.item) return;
      container.insertBefore(drag.item, before);
    };

    const placeGhost = (x, y) => {
      const ghost = drag.ghost;
      const mode = axisNow();
      // Large album cards shrink so the drop gap stays visible. Thumbs stay full size.
      const scale = drag.h > 160 ? 0.5 : 1.15;
      const visualW = drag.w * scale;
      const visualH = drag.h * scale;
      ghost.style.transformOrigin = "top left";
      ghost.style.transform = `scale(${scale})`;
      const rail = container.getBoundingClientRect();
      let left;
      let top;
      if (mode === "y" && rail.left > window.innerWidth * 0.55) {
        // Vertical strip on the right: hold the photo over the main image.
        left = x - visualW - 20;
        top = y - visualH / 2;
      } else if (mode === "y") {
        left = x + 20;
        top = y - visualH / 2;
      } else {
        // Grid and horizontal strip: float the item just above the landing gap.
        left = x - visualW / 2;
        top = y - visualH - 18;
        if (top < 8) top = y + 18;
      }
      left = Math.max(8, Math.min(left, window.innerWidth - visualW - 8));
      top = Math.max(8, Math.min(top, window.innerHeight - visualH - 8));
      ghost.style.left = `${left}px`;
      ghost.style.top = `${top}px`;
    };

    const autoScroll = (x, y) => {
      const mode = axisNow();
      const bounds = container.getBoundingClientRect();
      const edge = 36;
      if (mode === "y" || mode === "grid") {
        if (y > bounds.bottom - edge) container.scrollTop += 18;
        else if (y < bounds.top + edge) container.scrollTop -= 18;
      }
      if (mode === "x") {
        if (x > bounds.right - edge) container.scrollLeft += 18;
        else if (x < bounds.left + edge) container.scrollLeft -= 18;
      }
      if (mode === "grid") {
        if (y > window.innerHeight - 56) window.scrollBy(0, 18);
        else if (y < 56) window.scrollBy(0, -18);
      }
    };

    const cleanup = () => {
      if (!drag) return;
      window.clearInterval(drag.timer);
      document.body.classList.remove("is-reordering");
      if (drag.ghost) drag.ghost.remove();
      drag.item.classList.remove("reorder-slot");
    };

    container.addEventListener("pointerdown", (event) => {
      if (busy || event.button !== 0) return;
      const handle = event.target.closest(".reorder-handle");
      if (!handle || !container.contains(handle)) return;
      const item = handle.closest(itemSelector);
      if (!item || item.parentElement !== container) return;
      event.preventDefault();
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      const rect = item.getBoundingClientRect();
      const ghost = item.cloneNode(true);
      ghost.classList.add("reorder-ghost");
      ghost.classList.remove("reorder-slot");
      ghost.removeAttribute("id");
      ghost.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
      ghost.style.width = `${rect.width}px`;
      ghost.style.height = `${rect.height}px`;
      document.body.appendChild(ghost);
      item.classList.add("reorder-slot");
      document.body.classList.add("is-reordering");
      drag = {
        pointerId: event.pointerId,
        item,
        ghost,
        start: itemIds(),
        w: rect.width,
        h: rect.height,
        x: event.clientX,
        y: event.clientY,
        timer: 0,
      };
      placeGhost(event.clientX, event.clientY);
      drag.timer = window.setInterval(() => {
        if (!drag) return;
        autoScroll(drag.x, drag.y);
        placeSlot(drag.x, drag.y);
      }, 40);
    });

    container.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      placeGhost(event.clientX, event.clientY);
      placeSlot(event.clientX, event.clientY);
      autoScroll(event.clientX, event.clientY);
    });

    const finish = async (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const start = drag.start;
      cleanup();
      drag = null;
      const next = itemIds();
      if (next.join(",") === start.join(",")) return;
      busy = true;
      try {
        await onCommit(next);
        showStatus("Order saved");
      } catch (err) {
        restore(start);
        showStatus(err.message || "Couldn't save order");
      } finally {
        busy = false;
      }
    };

    container.addEventListener("pointerup", finish);
    container.addEventListener("pointercancel", finish);
  }

  const albumGrid = document.getElementById("album-grid");
  if (albumGrid && albumGrid.dataset.reorder === "1") {
    const albumStatus = document.getElementById("album-reorder-status");
    bindReorder(albumGrid, ".album-card-wrap", {
      axis: "grid",
      statusEl: albumStatus,
      idleText: albumStatus ? albumStatus.textContent : "",
      onCommit: (ids) =>
        postJson("/admin/albums/reorder", { ids: ids.map((id) => Number(id)) }),
    });
  }

  // Album viewer: main image + thumbnails + zoom/pan
  const gallery = document.getElementById("gallery");
  if (!gallery) return;

  let photos = [];
  try {
    photos = JSON.parse(gallery.dataset.photos || "[]");
  } catch {
    photos = [];
  }
  if (!photos.length) return;

  const frame = document.getElementById("viewer-frame");
  const img = document.getElementById("viewer-img");
  if (!frame || !img) return;
  const cap = document.getElementById("viewer-cap");
  const zoomLabel = document.getElementById("zoom-reset");
  const saveBtn = document.getElementById("zoom-save");
  const saveStatus = document.getElementById("zoom-save-status");
  let thumbs = [...gallery.querySelectorAll(".thumb")];
  const isAdmin = gallery.dataset.admin === "1";
  const isCoverEditor = gallery.dataset.editor === "cover";
  const albumId = Number(gallery.dataset.albumId) || null;
  const deleteForm = document.getElementById("delete-form");
  let coverId = Number(gallery.dataset.coverId) || null;
  let coverZoom = Number(gallery.dataset.coverZoom);
  let coverPanX = Number(gallery.dataset.coverPanx);
  let coverPanY = Number(gallery.dataset.coverPany);
  if (!Number.isFinite(coverZoom)) coverZoom = 1;
  if (!Number.isFinite(coverPanX)) coverPanX = 0;
  if (!Number.isFinite(coverPanY)) coverPanY = 0;

  const indexFromHash = () => {
    const m = (location.hash || "").match(/^#photo-(\d+)$/);
    if (m) {
      const id = Number(m[1]);
      const found = photos.findIndex((p) => p.id === id);
      if (found >= 0) return found;
    }
    if (isCoverEditor && coverId) {
      const found = photos.findIndex((p) => p.id === coverId);
      if (found >= 0) return found;
    }
    return 0;
  };

  let index = indexFromHash();
  let scale = 1; // 1 = fit-to-frame; >1 zooms in
  let tx = 0;
  let ty = 0;
  let fitRatio = 1; // natural → fitted scale factor
  let naturalW = 0;
  let naturalH = 0;
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  let overlayOn = false;
  let overlayReady = false;
  let overlayFlipKey = "";
  let overlayFlipY = true;
  let overlayAlign = null;
  let overlayAlignKey = "";
  let overlayAlignToken = 0;
  // "" until a measurement is asked for, then pending, ready, or failed.
  let overlayAlignState = "";
  let overlayAlignNote = "";
  let overlayAlignSlow = false;
  let overlayAlignFallback = false;
  let overlayAlignActive = false;
  let overlayAlignTimer = 0;
  let overlayAlignHint = 0;
  let updateSkyOverlay = () => {};

  const MIN_ZOOM = 1;
  const MAX_ZOOM = 6;

  const photoZoom = (photo) => {
    if (isCoverEditor) {
      if (coverId && photo.id === coverId) {
        return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, coverZoom || 1));
      }
      return 1;
    }
    const z = Number(photo.zoom);
    if (!Number.isFinite(z)) return 1;
    return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
  };

  const photoPan = (photo) => {
    if (isCoverEditor) {
      if (coverId && photo.id === coverId) {
        return {
          x: Math.max(-1, Math.min(1, coverPanX || 0)),
          y: Math.max(-1, Math.min(1, coverPanY || 0)),
        };
      }
      return { x: 0, y: 0 };
    }
    const x = Number(photo.panx);
    const y = Number(photo.pany);
    return {
      x: Number.isFinite(x) ? Math.max(-1, Math.min(1, x)) : 0,
      y: Number.isFinite(y) ? Math.max(-1, Math.min(1, y)) : 0,
    };
  };

  const maxPan = () => {
    const fw = frame.clientWidth;
    const fh = frame.clientHeight;
    const drawnW = naturalW * fitRatio * scale;
    const drawnH = naturalH * fitRatio * scale;
    return {
      x: Math.max(0, (drawnW - fw) / 2),
      y: Math.max(0, (drawnH - fh) / 2),
    };
  };

  /** Current pan as -1..1 fractions of max travel (stable across viewport sizes). */
  const normalizedPan = () => {
    const m = maxPan();
    return {
      x: m.x > 0 ? Math.max(-1, Math.min(1, tx / m.x)) : 0,
      y: m.y > 0 ? Math.max(-1, Math.min(1, ty / m.y)) : 0,
    };
  };

  const applySavedPan = (photo) => {
    const pan = photoPan(photo);
    const m = maxPan();
    tx = pan.x * m.x;
    ty = pan.y * m.y;
  };

  const mediaUrl = (photo, thumb = false) => {
    const path = (photo.src || "").split("?")[0];
    const params = new URLSearchParams();
    if (thumb) params.set("size", "thumb");
    params.set("v", String(photo.v || 0));
    return `${path}?${params.toString()}`;
  };

  const applyTransform = () => {
    if (!img || !frame) return;
    // Keep the bitmap at natural pixel size and scale via transform so the
    // browser resamples from the full decode — not from an already-shrunk CSS size.
    img.style.width = `${naturalW}px`;
    img.style.height = `${naturalH}px`;
    const s = fitRatio * scale;
    img.style.transform = `translate(-50%, -50%) translate(${tx}px, ${ty}px) scale(${s})`;
    frame.classList.toggle("is-zoomed", scale > 1.01);
    if (zoomLabel) zoomLabel.textContent = `${Math.round(scale * 100)}%`;
    updateSkyOverlay();
  };

  const clampPan = () => {
    const m = maxPan();
    tx = Math.min(m.x, Math.max(-m.x, tx));
    ty = Math.min(m.y, Math.max(-m.y, ty));
  };

  const fitImage = (applyDefault) => {
    const fw = frame.clientWidth;
    const fh = frame.clientHeight;
    if (!naturalW || !naturalH || !fw || !fh) return;
    fitRatio = Math.min(fw / naturalW, fh / naturalH);
    if (applyDefault) {
      scale = photoZoom(photos[index]);
      applySavedPan(photos[index]);
    }
    clampPan();
    applyTransform();
  };

  const setZoom = (next, cx, cy) => {
    const prev = scale;
    scale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    if (cx != null && cy != null && prev > 0) {
      const rect = frame.getBoundingClientRect();
      const ox = cx - rect.left - rect.width / 2;
      const oy = cy - rect.top - rect.height / 2;
      const factor = scale / prev;
      tx = ox - (ox - tx) * factor;
      ty = oy - (oy - ty) * factor;
    }
    if (scale <= 1.01) {
      scale = 1;
      tx = 0;
      ty = 0;
    }
    clampPan();
    applyTransform();
  };

  let fitsById = {};
  const fitsDataEl = document.getElementById("photo-fits-data");
  if (fitsDataEl) {
    try {
      fitsById = JSON.parse(fitsDataEl.textContent || "{}");
    } catch {
      fitsById = {};
    }
  }

  const renderFitsInto = (root, payload) => {
    if (!root) return;
    const summary = root.querySelector("[data-fits-summary]");
    const cards = root.querySelector("[data-fits-cards]");
    const file = root.querySelector("[data-fits-file]");
    const full = root.querySelector("[data-fits-full]");
    if (!payload) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    const title = root.querySelector("[data-session-title]");
    if (title) title.textContent = payload.title || "Session";
    if (file) file.textContent = payload.name || "";
    const solve = root.querySelector("[data-solve]");
    if (solve) {
      solve.replaceChildren();
      const info = payload.solve;
      if (info) {
        solve.className = "solve-block";
        if (info.status === "solved" && info.center) {
          [
            ["Center", info.center],
            ["Rotation", info.rotation],
            ["Scale", info.scale],
          ].forEach(([label, value]) => {
            if (!value) return;
            const line = document.createElement("div");
            line.className = "kv";
            const k = document.createElement("span");
            k.className = "k";
            k.textContent = label;
            const v = document.createElement("span");
            v.className = "v";
            v.textContent = value;
            line.append(k, v);
            solve.appendChild(line);
          });
        } else {
          const note = document.createElement("p");
          note.className = info.status === "failed" ? "solve-failed" : "muted";
          note.textContent =
            info.status === "pending" ? "Solving…" : info.error || "Plate solve failed.";
          solve.appendChild(note);
        }
      }
    }
    if (summary) {
      summary.replaceChildren();
      const rows = payload.summary || [];
      if (!rows.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "No capture keywords in this header.";
        summary.appendChild(empty);
      }
      rows.forEach((row) => {
        const line = document.createElement("div");
        line.className = "kv";
        const k = document.createElement("span");
        k.className = "k";
        k.textContent = row.label || "";
        const v = document.createElement("span");
        v.className = "v";
        v.textContent = row.value || "";
        line.append(k, v);
        summary.appendChild(line);
      });
    }
    if (cards) {
      cards.replaceChildren();
      (payload.cards || []).forEach((card) => {
        const line = document.createElement("div");
        line.className = "fits-card";
        const k = document.createElement("span");
        k.className = "fits-key";
        k.textContent = card.k || "";
        const v = document.createElement("span");
        v.className = "fits-val";
        v.textContent = card.v || "";
        line.append(k, v);
        if (card.c) {
          const c = document.createElement("span");
          c.className = "fits-cmt";
          c.textContent = card.c;
          line.append(c);
        }
        cards.appendChild(line);
      });
    }
    if (full) full.hidden = !(payload.cards || []).length;
  };

  // Sky marks. The projection matches app/fits.py sky_to_fits_pixel and fits_to_display.
  const SVG_NS = "http://www.w3.org/2000/svg";
  const overlayBtn = document.getElementById("overlay-toggle");
  const overlayStatus = document.getElementById("overlay-status");
  const overlay = document.getElementById("sky-overlay");
  const overlayDetail = document.getElementById("overlay-detail");
  const moonEl = document.getElementById("moon-overlay");
  let overlayDetailKey = "";
  let moonAnchor = null;
  const SCALE_STEPS = [
    [30, "30″"],
    [60, "1′"],
    [120, "2′"],
    [300, "5′"],
    [600, "10′"],
    [900, "15′"],
    [1800, "30′"],
    [3600, "1°"],
    [7200, "2°"],
  ];

  const svgEl = (name, attrs) => {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  };

  const photoWcs = (photo) => {
    const wcs = photo ? fitsById[String(photo.id)]?.solve?.wcs : null;
    if (!wcs) return null;
    const width = Number(wcs.width);
    const height = Number(wcs.height);
    const pixelScale = Number(wcs.scale);
    if (!width || !height || !pixelScale) return null;
    if (!Number.isFinite(Number(wcs.ra)) || !Number.isFinite(Number(wcs.dec))) return null;
    return {
      ra: Number(wcs.ra),
      dec: Number(wcs.dec),
      rotation: Number(wcs.rotation) || 0,
      scale: pixelScale,
      parity: Number(wcs.parity) < 0 ? -1 : 1,
      width,
      height,
      source: fitsById[String(photo.id)]?.solve?.source || "",
    };
  };

  const cdParts = (wcs) => {
    const seconds = wcs.scale / 3600;
    const theta = (wcs.rotation * Math.PI) / 180;
    const cosT = Math.cos(theta);
    const sinT = Math.sin(theta);
    if (wcs.parity < 0) {
      return [-seconds * cosT, seconds * sinT, seconds * sinT, seconds * cosT];
    }
    return [seconds * cosT, seconds * sinT, -seconds * sinT, seconds * cosT];
  };

  const tangentToFits = (xiDeg, etaDeg, wcs) => {
    const [a, b, c, d] = cdParts(wcs);
    const det = a * d - b * c;
    if (Math.abs(det) < 1e-20) return null;
    const dx = (d * xiDeg - b * etaDeg) / det;
    const dy = (-c * xiDeg + a * etaDeg) / det;
    return [(wcs.width + 1) / 2 + dx, (wcs.height + 1) / 2 + dy];
  };

  const skyToFits = (ra, dec, wcs) => {
    const rad = Math.PI / 180;
    const raR = ra * rad;
    const decR = dec * rad;
    const ra0R = wcs.ra * rad;
    const dec0R = wcs.dec * rad;
    const cosC =
      Math.sin(dec0R) * Math.sin(decR) +
      Math.cos(dec0R) * Math.cos(decR) * Math.cos(raR - ra0R);
    if (cosC <= 1e-8) return null;
    const xi = (Math.cos(decR) * Math.sin(raR - ra0R)) / cosC;
    const eta =
      (Math.cos(dec0R) * Math.sin(decR) -
        Math.sin(dec0R) * Math.cos(decR) * Math.cos(raR - ra0R)) /
      cosC;
    const deg = 180 / Math.PI;
    return tangentToFits(xi * deg, eta * deg, wcs);
  };

  // Same quarter turns as display_pixel in app/align.py. 0 leaves the file as it is.
  const overlayTurn = () => {
    const turn = overlayAlign && Number(overlayAlign.turn);
    return turn === 90 || turn === 180 || turn === 270 ? turn : 0;
  };

  const fitsToDisplay = (fitsX, fitsY, wcs, flipY) => {
    const fileX = fitsX - 0.5;
    const fileY = flipY ? wcs.height + 0.5 - fitsY : fitsY - 0.5;
    let x = fileX;
    let y = fileY;
    let spanW = wcs.width;
    let spanH = wcs.height;
    const turn = overlayTurn();
    if (turn === 90) {
      x = spanH - fileY;
      y = fileX;
      spanW = wcs.height;
      spanH = wcs.width;
    } else if (turn === 180) {
      x = spanW - fileX;
      y = spanH - fileY;
    } else if (turn === 270) {
      x = fileY;
      y = spanW - fileX;
      spanW = wcs.height;
      spanH = wcs.width;
    }
    return [x * (naturalW / spanW), y * (naturalH / spanH)];
  };

  // A processed photo is often a crop of the FIT. Scale, spin, and shift about the center.
  const alignDisplay = (x, y) => {
    if (!overlayAlign || !naturalW || !naturalH) return [x, y];
    const cx = naturalW / 2;
    const cy = naturalH / 2;
    const dx = overlayAlign.sx * (x - cx);
    const dy = overlayAlign.sy * (y - cy);
    const spin = Number(overlayAlign.spin) || 0;
    const ang = (spin * Math.PI) / 180;
    const turnC = Math.cos(ang);
    const turnS = Math.sin(ang);
    return [
      cx + turnC * dx + turnS * dy + overlayAlign.tx,
      cy - turnS * dx + turnC * dy + overlayAlign.ty,
    ];
  };

  const overlayAlignScale = () =>
    overlayAlign ? (overlayAlign.sx + overlayAlign.sy) / 2 : 1;

  const displayToFrame = (x, y) => {
    const drawn = fitRatio * scale;
    return [
      frame.clientWidth / 2 + tx + (x - naturalW / 2) * drawn,
      frame.clientHeight / 2 + ty + (y - naturalH / 2) * drawn,
    ];
  };

  const frameToDisplay = (frameX, frameY) => {
    const drawn = fitRatio * scale;
    if (!drawn) return [naturalW / 2, naturalH / 2];
    return [
      (frameX - frame.clientWidth / 2 - tx) / drawn + naturalW / 2,
      (frameY - frame.clientHeight / 2 - ty) / drawn + naturalH / 2,
    ];
  };

  const overlayArcsecPerPx = (wcs) => {
    if (!wcs || !naturalW || !fitRatio || !scale) return 0;
    const fitAlongWidth = overlayTurn() === 90 || overlayTurn() === 270 ? wcs.height : wcs.width;
    const perPx =
      (Number(wcs.scale) * (fitAlongWidth / naturalW)) / (fitRatio * scale * overlayAlignScale());
    return Number.isFinite(perPx) && perPx > 0 ? perPx : 0;
  };

  const catalogLabel = (obj) => {
    const name = obj.name || "";
    if (!name || /^cluster in /i.test(name)) return obj.id || name;
    return name;
  };

  const catalogKind = (type) =>
    ({
      STAR: "Star",
      NEB: "Nebula",
      OC: "Open cluster",
      GC: "Globular cluster",
      GAL: "Galaxy",
      PN: "Planetary nebula",
      SNR: "Supernova remnant",
    })[type] || "";

  const detailKey = (obj) => `${obj.id || obj.name || ""}|${obj.ra}|${obj.dec}`;

  const formatRa = (hours) => {
    const sign = hours < 0 ? "−" : "";
    const abs = Math.abs(hours);
    const h = Math.floor(abs);
    const minutes = (abs - h) * 60;
    return `${sign}${h}h ${minutes.toFixed(1)}m`;
  };

  const formatDec = (deg) => {
    const sign = deg < 0 ? "−" : "+";
    const abs = Math.abs(deg);
    const d = Math.floor(abs);
    const minutes = (abs - d) * 60;
    return `${sign}${d}° ${minutes.toFixed(1)}′`;
  };

  // Keep a hundredth when the catalog has one. toFixed(1) turns 2.15 into 2.1.
  const formatMag = (value) => {
    const mag = Number(value);
    const hundredths = Math.round((mag + Number.EPSILON) * 100) / 100;
    const tenths = Math.round(hundredths * 10) / 10;
    if (Math.abs(hundredths - tenths) < 0.001) return tenths.toFixed(1);
    return hundredths.toFixed(2);
  };

  const closeOverlayDetail = () => {
    overlayDetailKey = "";
    if (!overlayDetail) return;
    overlayDetail.hidden = true;
    overlayDetail.replaceChildren();
  };

  // Catalog id plus type, e.g. "NGC 7538 · Nebula". A nickname is kept on its own line.
  const overlayCopyText = (obj) => {
    const label = catalogLabel(obj);
    const kind = catalogKind(obj.type);
    const official = [obj.id ? String(obj.id) : label, kind].filter(Boolean).join(" · ");
    if (!label || label === official || official.startsWith(`${label} ·`)) return official || label;
    return `${label}\n${official}`;
  };

  const copyOverlayLabel = async (text, selectEl, button) => {
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(selectEl);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      button.textContent = "Selected";
    }
    window.setTimeout(() => {
      if (button.isConnected) button.textContent = "Copy";
    }, 1200);
  };

  const showOverlayDetail = (obj) => {
    if (!overlayDetail) return;
    const key = detailKey(obj);
    if (overlayDetailKey === key) {
      closeOverlayDetail();
      updateSkyOverlay();
      return;
    }
    overlayDetailKey = key;
    const label = catalogLabel(obj);
    const kind = catalogKind(obj.type);
    const id = obj.id && String(obj.id) !== label ? String(obj.id) : "";
    const facts = [id, kind].filter(Boolean);
    const copyText = overlayCopyText(obj);
    const nameEl = document.createElement("p");
    nameEl.className = "overlay-detail-name";
    nameEl.textContent = label;
    const copyBlock = document.createElement("div");
    copyBlock.className = "overlay-detail-copy";
    copyBlock.append(nameEl);
    if (facts.length) {
      const meta = document.createElement("p");
      meta.textContent = facts.join(" · ");
      copyBlock.append(meta);
    }
    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn tiny";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      copyOverlayLabel(copyText, copyBlock, copyBtn);
    });
    const head = document.createElement("div");
    head.className = "overlay-detail-head";
    head.append(copyBlock, copyBtn);
    overlayDetail.replaceChildren(head);
    const where = document.createElement("p");
    where.textContent = `${formatRa(Number(obj.ra))}    ${formatDec(Number(obj.dec))}`;
    overlayDetail.append(where);
    const extra = [];
    if (obj.size) extra.push(`Size ${obj.size}`);
    if (obj.mag != null && obj.mag !== "" && Number.isFinite(Number(obj.mag))) {
      extra.push(`mag ${formatMag(obj.mag)}`);
    }
    if (extra.length) {
      const line = document.createElement("p");
      line.textContent = extra.join(" · ");
      overlayDetail.append(line);
    }
    const moon = moonSkySize(obj.size);
    if (moon) {
      const moonLine = document.createElement("p");
      moonLine.textContent = moon;
      overlayDetail.append(moonLine);
    }
    overlayDetail.hidden = false;
    updateSkyOverlay();
  };

  if (overlayDetail) {
    overlayDetail.addEventListener("pointerdown", (event) => event.stopPropagation());
    overlayDetail.addEventListener("dblclick", (event) => event.stopPropagation());
  }

  const catalogRadiusArcsec = (size) => {
    if (!size) return 0;
    const text = String(size);
    const value = parseFloat(text);
    if (!Number.isFinite(value) || value <= 0) return 0;
    let arcsec = value * 60;
    if (text.includes("°")) arcsec = value * 3600;
    else if (text.includes("″") || text.includes('"')) arcsec = value;
    return arcsec / 2;
  };

  // The full Moon is about 30′ across. Compare the catalog's long axis with that disk.
  const MOON_DIAMETER_ARCSEC = 30 * 60;

  const moonSkySize = (size) => {
    const extent = catalogRadiusArcsec(size) * 2;
    if (!extent) return "";
    const pct = (extent / MOON_DIAMETER_ARCSEC) * 100;
    if (pct < 0.5) return "<1% of moon sky size";
    return `${Math.round(pct)}% of moon sky size`;
  };

  // Session-only. A new photo starts the disk near the upper right of the frame.
  const placeMoon = (wcs) => {
    if (!moonEl) return;
    const arcsec = overlayArcsecPerPx(wcs);
    const show = Boolean(
      overlayOn && wcs && overlayReady && arcsec && overlayAlignState === "ready" && overlayAlign
    );
    if (!show || !frame.clientWidth || !frame.clientHeight) {
      moonEl.hidden = true;
      return;
    }
    if (!moonAnchor) {
      const [x, y] = frameToDisplay(frame.clientWidth * 0.72, frame.clientHeight * 0.28);
      moonAnchor = { x, y };
    }
    const diameter = MOON_DIAMETER_ARCSEC / arcsec;
    const [cx, cy] = displayToFrame(moonAnchor.x, moonAnchor.y);
    moonEl.hidden = false;
    moonEl.style.width = `${diameter}px`;
    moonEl.style.height = `${diameter}px`;
    moonEl.style.left = `${cx - diameter / 2}px`;
    moonEl.style.top = `${cy - diameter / 2}px`;
  };

  if (moonEl) {
    let moonDrag = null;
    moonEl.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.stopPropagation();
      event.preventDefault();
      const rect = moonEl.getBoundingClientRect();
      const frameRect = frame.getBoundingClientRect();
      moonDrag = {
        dx: event.clientX - (rect.left + rect.width / 2),
        dy: event.clientY - (rect.top + rect.height / 2),
        left: frameRect.left,
        top: frameRect.top,
      };
      moonEl.classList.add("is-dragging");
      moonEl.setPointerCapture(event.pointerId);
    });
    moonEl.addEventListener("pointermove", (event) => {
      if (!moonDrag) return;
      const [x, y] = frameToDisplay(
        event.clientX - moonDrag.left - moonDrag.dx,
        event.clientY - moonDrag.top - moonDrag.dy
      );
      moonAnchor = { x, y };
      placeMoon(photoWcs(photos[index]));
    });
    const endMoonDrag = (event) => {
      if (!moonDrag) return;
      moonDrag = null;
      moonEl.classList.remove("is-dragging");
      try {
        moonEl.releasePointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
    };
    moonEl.addEventListener("pointerup", endMoonDrag);
    moonEl.addEventListener("pointercancel", endMoonDrag);
    moonEl.addEventListener("click", (event) => event.stopPropagation());
    moonEl.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      event.preventDefault();
    });
  }

  const overlayObjects = () => {
    const seen = [];
    const out = [];
    const add = (obj) => {
      const ra = Number(obj && obj.ra);
      const dec = Number(obj && obj.dec);
      if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
      const idKey = String(obj.id || obj.name || "")
        .toUpperCase()
        .replace(/\s+/g, "");
      if (idKey && seen.some((item) => item.idKey === idKey)) return;
      const candidate = { ra, dec };
      if (seen.some((item) => sameSky(item, candidate))) return;
      seen.push({ idKey, ra, dec });
      out.push(obj);
    };
    (window.ASTRO_CATALOG || []).forEach(add);
    (window.ASTRO_OVERLAY || []).forEach(add);
    (window.ASTRO_STARS || []).forEach(add);
    out.sort((a, b) => (Number(a.mag) || 99) - (Number(b.mag) || 99));
    return out;
  };

  const sameSky = (a, b) => {
    const rad = Math.PI / 180;
    const dec1 = a.dec * rad;
    const dec2 = b.dec * rad;
    const cos =
      Math.sin(dec1) * Math.sin(dec2) +
      Math.cos(dec1) * Math.cos(dec2) * Math.cos((a.ra - b.ra) * 15 * rad);
    return Math.acos(Math.min(1, Math.max(-1, cos))) * (180 / Math.PI) < 0.07;
  };

  const diskMean = (pixels, sw, sh, sx, sy, radius) => {
    const r = Math.max(4, Math.min(26, radius));
    if (sx - r < 0 || sy - r < 0 || sx + r >= sw || sy + r >= sh) return null;
    let total = 0;
    let n = 0;
    const r2 = r * r;
    const x0 = Math.floor(sx - r);
    const x1 = Math.ceil(sx + r);
    const y0 = Math.floor(sy - r);
    const y1 = Math.ceil(sy + r);
    for (let y = y0; y <= y1; y += 1) {
      for (let x = x0; x <= x1; x += 1) {
        const dx = x + 0.5 - sx;
        const dy = y + 0.5 - sy;
        if (dx * dx + dy * dy > r2) continue;
        const i = (y * sw + x) * 4;
        total += (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3;
        n += 1;
      }
    }
    return n ? total / n : null;
  };

  const voteOverlayFlip = (wcs) => {
    const choose = window.chooseOverlayFlip;
    if (typeof choose !== "function" || !img || !naturalW || !naturalH) return null;
    const sw = 360;
    const sh = Math.max(1, Math.round((naturalH * sw) / naturalW));
    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(img, 0, 0, sw, sh);
    let pixels;
    try {
      pixels = ctx.getImageData(0, 0, sw, sh).data;
    } catch (err) {
      return null;
    }
    const arcsec = (wcs.scale * (wcs.width / naturalW)) * (naturalW / sw);
    let flipScore = 0;
    let plainScore = 0;
    let count = 0;
    overlayObjects().forEach((obj) => {
      const radiusAs = catalogRadiusArcsec(obj.size);
      if (radiusAs < 90 || radiusAs > 900) return;
      const fits = skyToFits(Number(obj.ra) * 15, Number(obj.dec), wcs);
      if (!fits) return;
      const [fitsX, fitsY] = fits;
      if (fitsX < 0.5 || fitsY < 0.5 || fitsX > wcs.width + 0.5 || fitsY > wcs.height + 0.5) return;
      const up = fitsToDisplay(fitsX, fitsY, wcs, true);
      const plain = fitsToDisplay(fitsX, fitsY, wcs, false);
      const sampleR = radiusAs / arcsec;
      const upMean = diskMean(pixels, sw, sh, (up[0] * sw) / naturalW, (up[1] * sh) / naturalH, sampleR);
      const plainMean = diskMean(
        pixels,
        sw,
        sh,
        (plain[0] * sw) / naturalW,
        (plain[1] * sh) / naturalH,
        sampleR
      );
      if (upMean == null || plainMean == null) return;
      flipScore += upMean;
      plainScore += plainMean;
      count += 1;
    });
    if (!count) return null;
    return choose(flipScore, plainScore, count, wcs.source || "");
  };

  const overlayFlipFor = (photo, wcs) => {
    const key = `${photo && photo.id}:${photo && photo.v}:${wcs.width}x${wcs.height}`;
    if (key === overlayFlipKey) return overlayFlipY;
    const voted = voteOverlayFlip(wcs);
    overlayFlipY = voted == null ? (wcs.source || "") !== "solver" : voted;
    overlayFlipKey = key;
    return overlayFlipY;
  };

  const formatArc = (arcsec) => {
    if (arcsec >= 3600) {
      const degrees = Math.round((arcsec / 3600) * 10) / 10;
      return `${degrees}°`;
    }
    if (arcsec >= 60) {
      const minutes = Math.round((arcsec / 60) * 10) / 10;
      return `${minutes}′`;
    }
    return `${Math.round(arcsec)}″`;
  };

  const pickScale = (arcsecPerPx, maxPx) => {
    let best = null;
    let bestScore = Infinity;
    SCALE_STEPS.forEach(([arcsec, label]) => {
      const px = arcsec / arcsecPerPx;
      if (px < 40 || px > maxPx) return;
      const score = Math.abs(Math.log(px / Math.min(110, maxPx)));
      if (score < bestScore) {
        best = { arcsec, label, px };
        bestScore = score;
      }
    });
    if (best) return best;
    const px = Math.max(40, Math.min(maxPx, SCALE_STEPS[0][0] / arcsecPerPx));
    return { arcsec: px * arcsecPerPx, label: formatArc(px * arcsecPerPx), px };
  };

  const paintText = (parent, x, y, text, attrs) => {
    const node = svgEl("text", {
      x,
      y,
      fill: "#f6d98a",
      stroke: "#05070d",
      "stroke-width": 3.5,
      "paint-order": "stroke",
      "font-size": 13,
      "font-family": "DM Sans, sans-serif",
      ...attrs,
    });
    node.textContent = text;
    parent.appendChild(node);
  };

  const drawSkyOverlay = (wcs, flipY) => {
    const fw = frame.clientWidth;
    const fh = frame.clientHeight;
    overlay.replaceChildren();
    overlay.setAttribute("viewBox", `0 0 ${fw} ${fh}`);
    if (!fw || !fh || !naturalW || !naturalH || !fitRatio) return;

    const arcsecPerPx = overlayArcsecPerPx(wcs);
    const drawn = fitRatio * scale;
    const photoLeft = fw / 2 + tx - (naturalW / 2) * drawn;
    const photoRight = photoLeft + naturalW * drawn;
    const objects = svgEl("g", { id: "sky-objects" });
    const placed = [];
    const catalog = overlayObjects();
    catalog.forEach((obj) => {
      const ra = Number(obj.ra) * 15;
      const dec = Number(obj.dec);
      if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
      const fits = skyToFits(ra, dec, wcs);
      if (!fits) return;
      const [fitsX, fitsY] = fits;
      if (fitsX < 0.5 || fitsY < 0.5 || fitsX > wcs.width + 0.5 || fitsY > wcs.height + 0.5) return;
      const [jpegX, jpegY] = alignDisplay(...fitsToDisplay(fitsX, fitsY, wcs, flipY));
      const [cx, cy] = displayToFrame(jpegX, jpegY);
      const radiusArcsec = catalogRadiusArcsec(obj.size);
      const rawRadius = radiusArcsec > 0 ? radiusArcsec / arcsecPerPx : 8;
      const cap = Math.min(fw, fh) * 0.22;
      // A circle bigger than the view would look like the object's real size. Mark the center instead.
      const radius = rawRadius > cap ? 11 : Math.max(6, rawRadius);
      const label = catalogLabel(obj);
      const mark = svgEl("g", {
        class: detailKey(obj) === overlayDetailKey ? "sky-mark is-open" : "sky-mark",
      });
      if (label) mark.setAttribute("aria-label", label);
      const openMark = (event) => {
        event.stopPropagation();
        event.preventDefault();
        showOverlayDetail(obj);
      };
      mark.addEventListener("pointerdown", openMark);
      mark.addEventListener("click", (event) => event.stopPropagation());
      mark.addEventListener("dblclick", (event) => {
        event.stopPropagation();
        event.preventDefault();
      });
      mark.appendChild(
        svgEl("circle", {
          cx,
          cy,
          r: Math.max(radius, 14),
          fill: "transparent",
        })
      );
      mark.appendChild(
        svgEl("circle", {
          cx,
          cy,
          r: radius,
          fill: "none",
          stroke: "rgba(0,0,0,0.8)",
          "stroke-width": 3.5,
        })
      );
      mark.appendChild(
        svgEl("circle", {
          class: "sky-ring",
          cx,
          cy,
          r: radius,
          fill: "none",
          stroke: "#f6d98a",
          "stroke-width": 1.35,
        })
      );
      if (label) {
        const widthGuess = label.length * 8.2 + 8;
        const half = widthGuess / 2;
        let lx = cx;
        if (photoRight - photoLeft > widthGuess + 12) {
          lx = Math.min(photoRight - 6 - half, Math.max(photoLeft + 6 + half, cx));
        }
        let ly = cy - radius - 8;
        if (ly < 16) ly = cy + radius + 16;
        for (let attempt = 0; attempt < 8; attempt += 1) {
          const box = { x: lx - half, y: ly - 12, w: widthGuess, h: 16 };
          const hit = placed.some(
            (item) =>
              box.x < item.x + item.w &&
              box.x + box.w > item.x &&
              box.y < item.y + item.h &&
              box.y + box.h > item.y
          );
          if (!hit) break;
          ly += 16;
        }
        placed.push({ x: lx - half, y: ly - 12, w: widthGuess, h: 16 });
        mark.appendChild(
          svgEl("rect", {
            x: lx - half,
            y: ly - 14,
            width: widthGuess,
            height: 18,
            fill: "transparent",
          })
        );
        paintText(mark, lx, ly, label, { "text-anchor": "middle" });
      }
      objects.appendChild(mark);
    });
    overlay.appendChild(objects);

    const north = tangentToFits(0, 0.01, wcs);
    const east = tangentToFits(0.01, 0, wcs);
    const origin = tangentToFits(0, 0, wcs);
    if (north && east && origin) {
      const toScreen = (fitsPoint) => {
        const display = alignDisplay(...fitsToDisplay(fitsPoint[0], fitsPoint[1], wcs, flipY));
        return displayToFrame(display[0], display[1]);
      };
      const center = toScreen(origin);
      const northPt = toScreen(north);
      const eastPt = toScreen(east);
      const normalize = (point, length) => {
        const dx = point[0] - center[0];
        const dy = point[1] - center[1];
        const mag = Math.hypot(dx, dy) || 1;
        return [(dx / mag) * length, (dy / mag) * length];
      };
      const northDir = normalize(northPt, 22);
      const eastDir = normalize(eastPt, 16);
      const pivotX = 52;
      const pivotY = fh - 48;
      const compass = svgEl("g", { id: "sky-compass" });
      compass.appendChild(
        svgEl("circle", {
          cx: pivotX,
          cy: pivotY,
          r: 27,
          fill: "rgba(8,10,16,0.45)",
          stroke: "rgba(244,247,255,0.35)",
          "stroke-width": 1,
        })
      );
      const arrow = (dir, color) =>
        svgEl("line", {
          x1: pivotX,
          y1: pivotY,
          x2: pivotX + dir[0],
          y2: pivotY + dir[1],
          stroke: color,
          "stroke-width": 1.6,
          "stroke-linecap": "round",
        });
      compass.appendChild(arrow(northDir, "#9eb6ff"));
      compass.appendChild(arrow(eastDir, "#f4f7ff"));
      paintText(compass, pivotX + northDir[0] * 1.45, pivotY + northDir[1] * 1.45, "N", {
        fill: "#d5e2ff",
        "font-size": 12,
        "text-anchor": "middle",
        "dominant-baseline": "middle",
      });
      paintText(compass, pivotX + eastDir[0] * 1.7, pivotY + eastDir[1] * 1.7, "E", {
        fill: "#f4f7ff",
        "font-size": 11,
        "text-anchor": "middle",
        "dominant-baseline": "middle",
      });
      overlay.appendChild(compass);
    }

    if (arcsecPerPx > 0) {
      const bar = pickScale(arcsecPerPx, Math.min(150, fw * 0.36));
      const y = fh - 36;
      const x2 = fw - 28;
      const x1 = x2 - bar.px;
      const scaleG = svgEl("g", { id: "sky-scale" });
      scaleG.appendChild(
        svgEl("line", {
          x1,
          y1: y,
          x2,
          y2: y,
          stroke: "#05070d",
          "stroke-width": 4,
          "stroke-linecap": "butt",
        })
      );
      scaleG.appendChild(
        svgEl("line", {
          x1,
          y1: y,
          x2,
          y2: y,
          stroke: "#f4f7ff",
          "stroke-width": 1.6,
          "stroke-linecap": "butt",
        })
      );
      [x1, x2].forEach((tick) => {
        scaleG.appendChild(
          svgEl("line", {
            x1: tick,
            y1: y - 5,
            x2: tick,
            y2: y + 5,
            stroke: "#f4f7ff",
            "stroke-width": 1.4,
          })
        );
      });
      paintText(scaleG, (x1 + x2) / 2, y - 10, bar.label, {
        fill: "#f4f7ff",
        "font-size": 12,
        "text-anchor": "middle",
      });
      overlay.appendChild(scaleG);
    }
  };

  const stopAlignWait = () => {
    if (overlayAlignTimer) clearTimeout(overlayAlignTimer);
    overlayAlignTimer = 0;
    if (overlayAlignHint) clearTimeout(overlayAlignHint);
    overlayAlignHint = 0;
    overlayAlignActive = false;
  };

  const resetOverlayAlign = () => {
    stopAlignWait();
    overlayAlign = null;
    overlayAlignKey = "";
    overlayAlignState = "";
    overlayAlignNote = "";
    overlayAlignSlow = false;
    overlayAlignFallback = false;
    overlayAlignToken += 1;
  };

  updateSkyOverlay = () => {
    const wcs = photoWcs(photos[index]);
    const canAsk = Boolean(overlayOn && wcs && overlayReady);
    let flipY = overlayFlipY;
    if (canAsk) {
      flipY = overlayFlipFor(photos[index], wcs);
      ensureOverlayAlign(photos[index], flipY);
    }
    const waiting = canAsk && overlayAlignState === "pending";
    const show = Boolean(canAsk && overlayAlignState === "ready" && overlayAlign);
    if (overlayBtn) {
      overlayBtn.hidden = !wcs;
      overlayBtn.setAttribute("aria-pressed", overlayOn && wcs ? "true" : "false");
      if (waiting) overlayBtn.setAttribute("aria-busy", "true");
      else overlayBtn.removeAttribute("aria-busy");
    }
    if (overlayStatus) {
      if (!overlayOn) overlayStatus.textContent = "";
      else if (waiting && overlayAlignSlow) overlayStatus.textContent = "Measuring the sky…";
      else overlayStatus.textContent = overlayAlignNote || "";
    }
    if (!overlay) return;
    // SVG elements do not honor the hidden property, so toggle the attribute.
    if (show) overlay.removeAttribute("hidden");
    else overlay.setAttribute("hidden", "");
    if (show) drawSkyOverlay(wcs, flipY);
    else {
      overlay.replaceChildren();
      closeOverlayDetail();
    }
    placeMoon(wcs);
  };

  const albumSlug = () => {
    const match = window.location.pathname.match(/^\/a\/([^/]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const alignFromPayload = (data) => {
    if (!data) return null;
    const sx = Number(data.sx);
    const sy = Number(data.sy);
    const tx = Number(data.tx);
    const ty = Number(data.ty);
    if (!(sx > 0.5 && sx < 2 && sy > 0.5 && sy < 2) || !Number.isFinite(tx) || !Number.isFinite(ty)) {
      return null;
    }
    const turn = Number(data.turn);
    const spin = Number(data.spin);
    return {
      sx,
      sy,
      tx,
      ty,
      turn: turn === 90 || turn === 180 || turn === 270 ? turn : 0,
      spin: Number.isFinite(spin) ? spin : 0,
    };
  };

  const ensureOverlayAlign = (photo, flipY) => {
    const slug = albumSlug();
    if (!slug || !photo || !photo.id) return;
    const key = `${photo.id}:${photo.v || 0}`;
    if (key === overlayAlignKey && overlayAlignState === "ready") return;
    if (key === overlayAlignKey && overlayAlignState === "pending" && overlayAlignActive) return;
    if (key === overlayAlignKey && overlayAlignState === "failed") return;
    const restart = key === overlayAlignKey && overlayAlignState === "pending";
    overlayAlignKey = key;
    if (!restart) {
      overlayAlign = null;
      overlayAlignNote = "";
      overlayAlignSlow = false;
      overlayAlignFallback = false;
    }
    overlayAlignState = "pending";
    overlayAlignActive = true;
    const token = ++overlayAlignToken;
    const armHint = () => {
      if (overlayAlignSlow || overlayAlignHint) return;
      overlayAlignHint = setTimeout(() => {
        overlayAlignHint = 0;
        if (token !== overlayAlignToken || overlayAlignState !== "pending" || !overlayOn) return;
        overlayAlignSlow = true;
        updateSkyOverlay();
      }, 300);
    };
    const pull = (retry) => {
      const query = retry ? "&retry=1" : "";
      fetch(`/a/${encodeURIComponent(slug)}/photos/${photo.id}/align?flip=${flipY ? 1 : 0}${query}`)
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          if (token !== overlayAlignToken || overlayAlignKey !== key) return;
          if (!data) {
            stopAlignWait();
            overlayAlign = null;
            overlayAlignState = "failed";
            overlayAlignFallback = false;
            overlayAlignNote = "The sky measurement failed.";
            updateSkyOverlay();
            return;
          }
          if (!overlayOn && data.status === "pending") return;
          if (data.status === "pending") {
            overlayAlignState = "pending";
            overlayAlign = null;
            armHint();
            overlayAlignTimer = setTimeout(() => pull(false), 2000);
            updateSkyOverlay();
            return;
          }
          stopAlignWait();
          const align = alignFromPayload(data);
          if (data.status === "failed" || !align) {
            if (align) {
              overlayAlign = align;
              overlayAlignState = "ready";
              overlayAlignFallback = true;
              overlayAlignNote = data.detail || "Showing the last sky measurement.";
            } else {
              overlayAlign = null;
              overlayAlignState = "failed";
              overlayAlignFallback = false;
              overlayAlignNote = (data && data.detail) || "The sky measurement failed.";
            }
          } else {
            overlayAlign = align;
            overlayAlignState = "ready";
            overlayAlignFallback = false;
            overlayAlignNote = "";
          }
          // The server tries both row orders and each quarter turn. Its choice
          // replaces the vote, which runs before the crop is known.
          if (align && (data.flip === 0 || data.flip === 1)) overlayFlipY = data.flip === 1;
          updateSkyOverlay();
        })
        .catch(() => {
          if (token !== overlayAlignToken || overlayAlignKey !== key) return;
          stopAlignWait();
          overlayAlign = null;
          overlayAlignState = "failed";
          overlayAlignFallback = false;
          overlayAlignNote = "The sky measurement failed.";
          updateSkyOverlay();
        });
    };
    armHint();
    pull(!restart);
  };

  overlayBtn?.addEventListener("click", () => {
    overlayOn = !overlayOn;
    if (!overlayOn) stopAlignWait();
    else if (overlayAlignState === "failed" || overlayAlignFallback) overlayAlignKey = "";
    updateSkyOverlay();
  });

  const renderPhotoFits = (photo) => {
    const payload = photo ? fitsById[String(photo.id)] : null;
    renderFitsInto(document.getElementById("photo-capture"), payload || null);

    const panel = document.getElementById("fits-panel");
    if (!panel || !photo?.id) return;
    const assign = document.getElementById("session-assign-form");
    if (assign) assign.action = `/admin/photos/${photo.id}/session`;
    const sessionSelect = document.getElementById("session-assign");
    if (sessionSelect) sessionSelect.value = photo.session ? String(photo.session) : "";
    renderFitsInto(document.getElementById("fits-preview"), payload || null);
  };

  const syncAdminForms = (photo) => {
    if (!isAdmin || !photo?.id) return;
    const deleteUrl = `/admin/photos/${photo.id}/delete`;
    if (deleteForm) {
      deleteForm.setAttribute("action", deleteUrl);
      deleteForm.action = deleteUrl;
    }
    thumbs.forEach((t) => {
      const id = Number(t.dataset.id);
      const isCover = !!(coverId && id === coverId);
      t.classList.toggle("is-cover", isCover);
      let badge = t.querySelector(".thumb-cover-badge");
      if (isCover && !badge) {
        badge = document.createElement("span");
        badge.className = "thumb-cover-badge";
        badge.textContent = "Thumb";
        t.appendChild(badge);
      } else if (!isCover && badge) {
        badge.remove();
      }
    });
    if (history.replaceState) {
      history.replaceState(null, "", `#photo-${photo.id}`);
    } else {
      location.hash = `photo-${photo.id}`;
    }
  };

  if (deleteForm) {
    deleteForm.addEventListener("submit", () => {
      const photo = photos[index];
      if (!photo?.id) return;
      const url = `/admin/photos/${photo.id}/delete`;
      deleteForm.setAttribute("action", url);
      deleteForm.action = url;
    });
  }

  const select = (i) => {
    index = (i + photos.length) % photos.length;
    const photo = photos[index];
    overlayReady = false;
    resetOverlayAlign();
    closeOverlayDetail();
    moonAnchor = null;
    scale = 1;
    tx = 0;
    ty = 0;
    applyTransform();
    if (cap) cap.textContent = photo.name || "";
    img.alt = photo.name || "";
    if (saveStatus) saveStatus.textContent = "";
    syncAdminForms(photo);
    renderPhotoFits(photo);

    thumbs.forEach((t, ti) => {
      const on = ti === index;
      t.classList.toggle("is-active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    });
    const active = thumbs[index];
    if (active) {
      active.scrollIntoView({
        behavior: "smooth",
        inline: "nearest",
        block: "nearest",
      });
    }

    const nextSrc = mediaUrl(photo, false);
    const current = img.getAttribute("src");
    const revealLoaded = () => {
      if (!img.naturalWidth) return;
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      overlayReady = true;
      fitImage(true);
    };
    img.onload = revealLoaded;
    if (current === nextSrc && img.complete && img.naturalWidth) {
      revealLoaded();
      return;
    }
    img.src = nextSrc;
    if (img.complete && img.naturalWidth) revealLoaded();
  };

  // Load initial photo (hash or first) with its saved zoom/pan
  select(index);

  thumbs.forEach((t) => {
    t.addEventListener("click", () => select(Number(t.dataset.index)));
  });

  if (isAdmin && albumId && !isCoverEditor) {
    const strip = document.getElementById("thumb-strip");
    const photoStatus = document.getElementById("photo-reorder-status");
    const syncPhotoOrder = () => {
      const selectedId = photos[index] && photos[index].id;
      const nodes = [...strip.querySelectorAll(":scope > .thumb")];
      const byId = new Map(photos.map((photo) => [photo.id, photo]));
      const next = [];
      nodes.forEach((node, i) => {
        node.dataset.index = String(i);
        const photo = byId.get(Number(node.dataset.id));
        if (photo) next.push(photo);
      });
      photos = next;
      thumbs = nodes;
      const nextIndex = photos.findIndex((photo) => photo.id === selectedId);
      index = nextIndex >= 0 ? nextIndex : 0;
    };
    if (strip) {
      bindReorder(strip, ".thumb", {
        statusEl: photoStatus,
        idleText: "",
        onCommit: async (ids) => {
          await postJson(`/admin/albums/${albumId}/photos/reorder`, {
            ids: ids.map((id) => Number(id)),
          });
          syncPhotoOrder();
        },
      });
    }
  }

  document.getElementById("viewer-prev")?.addEventListener("click", () => select(index - 1));
  document.getElementById("viewer-next")?.addEventListener("click", () => select(index + 1));
  document.getElementById("zoom-in")?.addEventListener("click", () => setZoom(scale * 1.25));
  document.getElementById("zoom-out")?.addEventListener("click", () => setZoom(scale / 1.25));
  document.getElementById("zoom-reset")?.addEventListener("click", () => {
    // Reset to this photo's saved zoom + center
    const photo = photos[index];
    scale = photoZoom(photo);
    applySavedPan(photo);
    clampPan();
    applyTransform();
  });

  const reloadCurrentPhoto = (photo, data) => {
    if (data?.src) photo.src = data.src.split("?")[0];
    if (typeof data?.file_version === "number") photo.v = data.file_version;

    const mainSrc = mediaUrl(photo, false);
    const thumbSrc = mediaUrl(photo, true);

    const thumbImg = thumbs[index]?.querySelector("img");
    if (thumbImg) thumbImg.src = thumbSrc;

    overlayReady = false;
    resetOverlayAlign();
    const revealLoaded = () => {
      if (!img.naturalWidth) return;
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      overlayReady = true;
      fitImage(true);
    };
    img.onload = revealLoaded;
    img.src = mainSrc;
    if (img.complete && img.naturalWidth) revealLoaded();
  };

  if (isAdmin && saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const photo = photos[index];
      if (!photo?.id) return;
      const pan = normalizedPan();
      saveBtn.disabled = true;
      if (saveStatus) saveStatus.textContent = "Saving…";
      try {
        let res;
        if (isCoverEditor && albumId) {
          res = await fetch(`/admin/albums/${albumId}/thumbnail`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              photo_id: photo.id,
              zoom: scale,
              pan_x: pan.x,
              pan_y: pan.y,
            }),
            credentials: "same-origin",
          });
        } else {
          res = await fetch(`/admin/photos/${photo.id}/zoom`, {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ zoom: scale, pan_x: pan.x, pan_y: pan.y }),
            credentials: "same-origin",
          });
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (isCoverEditor) {
          coverId = data.cover_photo_id;
          coverZoom = data.cover_zoom;
          coverPanX = data.cover_pan_x;
          coverPanY = data.cover_pan_y;
          gallery.dataset.coverId = String(coverId);
          gallery.dataset.coverZoom = String(coverZoom);
          gallery.dataset.coverPanx = String(coverPanX);
          gallery.dataset.coverPany = String(coverPanY);
          if (data.cover_version != null) {
            gallery.dataset.coverVersion = String(data.cover_version);
          }
          syncAdminForms(photo);
          if (saveStatus) {
            saveStatus.textContent = "Thumbnail saved";
          }
        } else {
          photo.zoom = data.default_zoom;
          photo.panx = data.default_pan_x;
          photo.pany = data.default_pan_y;
          if (saveStatus) {
            saveStatus.textContent = `Saved ${Math.round(data.default_zoom * 100)}% + center`;
          }
        }
      } catch {
        if (saveStatus) saveStatus.textContent = "Save failed";
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  const rotatePhoto = async (direction) => {
    const photo = photos[index];
    if (!photo?.id) return;
    const leftBtn = document.getElementById("rotate-left");
    const rightBtn = document.getElementById("rotate-right");
    leftBtn && (leftBtn.disabled = true);
    rightBtn && (rightBtn.disabled = true);
    if (saveStatus) saveStatus.textContent = "Rotating…";
    try {
      const res = await fetch(`/admin/photos/${photo.id}/rotate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ direction }),
        credentials: "same-origin",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      reloadCurrentPhoto(photo, data);
      if (saveStatus) saveStatus.textContent = "Rotation saved";
    } catch {
      if (saveStatus) saveStatus.textContent = "Rotate failed";
    } finally {
      leftBtn && (leftBtn.disabled = false);
      rightBtn && (rightBtn.disabled = false);
    }
  };

  if (isAdmin) {
    document.getElementById("rotate-left")?.addEventListener("click", () => rotatePhoto("left"));
    document.getElementById("rotate-right")?.addEventListener("click", () => rotatePhoto("right"));
  }

  frame.addEventListener("dblclick", (e) => {
    if (scale > 1.05) setZoom(1);
    else setZoom(2.5, e.clientX, e.clientY);
  });

  frame.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (overlayDetailKey) {
      closeOverlayDetail();
      updateSkyOverlay();
    }
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    frame.classList.add("is-panning");
    frame.setPointerCapture(e.pointerId);
  });
  frame.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    if (scale <= 1.01) return;
    tx += e.clientX - lastX;
    ty += e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    clampPan();
    applyTransform();
  });
  const endPan = (e) => {
    dragging = false;
    frame.classList.remove("is-panning");
    try {
      frame.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  };
  frame.addEventListener("pointerup", endPan);
  frame.addEventListener("pointercancel", endPan);

  window.addEventListener("resize", () => fitImage(false));

  // Fullscreen 1:1 (actual pixel size) dialog
  const pixelDialog = document.getElementById("pixel-dialog");
  const pixelImg = document.getElementById("pixel-dialog-img");
  const pixelScroller = document.getElementById("pixel-dialog-scroller");
  const pixelTitle = document.getElementById("pixel-dialog-title");
  const pixelSize = document.getElementById("pixel-dialog-size");

  const isPixelOpen = () => pixelDialog && !pixelDialog.hidden;

  const openPixelDialog = () => {
    if (!pixelDialog || !pixelImg || isCoverEditor) return;
    const photo = photos[index];
    if (!photo) return;
    const src = mediaUrl(photo, false);
    pixelTitle.textContent = photo.name || photo.id || "1:1";
    pixelSize.textContent = "Loading…";
    pixelImg.onload = () => {
      const w = pixelImg.naturalWidth;
      const h = pixelImg.naturalHeight;
      // Force layout size to natural pixels (1:1 CSS px)
      pixelImg.style.width = w + "px";
      pixelImg.style.height = h + "px";
      pixelImg.style.maxWidth = "none";
      pixelImg.style.maxHeight = "none";
      pixelSize.textContent = w + " × " + h + " px · 1:1";
      // Center scroll on the image if larger than viewport
      requestAnimationFrame(() => {
        if (!pixelScroller) return;
        pixelScroller.scrollLeft = Math.max(
          0,
          (pixelImg.offsetWidth - pixelScroller.clientWidth) / 2
        );
        pixelScroller.scrollTop = Math.max(
          0,
          (pixelImg.offsetHeight - pixelScroller.clientHeight) / 2
        );
      });
    };
    pixelImg.alt = photo.name || "";
    pixelImg.src = src;
    pixelDialog.hidden = false;
    document.body.classList.add("pixel-dialog-open");
    document.getElementById("pixel-close")?.focus();
  };

  const closePixelDialog = () => {
    if (!pixelDialog) return;
    pixelDialog.hidden = true;
    document.body.classList.remove("pixel-dialog-open");
    pixelImg.src = "";
  };

  const showPixelAt = (i) => {
    select(i);
    // Wait a tick so select() can update img; reopen with new src
    openPixelDialog();
  };

  document.getElementById("view-1to1")?.addEventListener("click", openPixelDialog);
  document.getElementById("pixel-close")?.addEventListener("click", closePixelDialog);
  document.getElementById("pixel-prev")?.addEventListener("click", () => {
    showPixelAt(index - 1);
  });
  document.getElementById("pixel-next")?.addEventListener("click", () => {
    showPixelAt(index + 1);
  });
  pixelDialog?.addEventListener("click", (e) => {
    if (e.target === pixelDialog || e.target === pixelScroller) {
      // don't close on scroller empty click only if clicking the backdrop chrome
    }
  });
  // Double-click main viewer also opens 1:1 when already zoomed high? Keep dblclick zoom;
  // optional: long-press not needed.

  document.addEventListener("keydown", (e) => {
    if (e.target && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
    if (isPixelOpen()) {
      if (e.key === "Escape") {
        e.preventDefault();
        closePixelDialog();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        showPixelAt(index - 1);
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        showPixelAt(index + 1);
        return;
      }
      return;
    }
    if (e.key === "ArrowLeft") select(index - 1);
    if (e.key === "ArrowRight") select(index + 1);
    if (e.key === "+" || e.key === "=") setZoom(scale * 1.25);
    if (e.key === "-" || e.key === "_") setZoom(scale / 1.25);
    if (e.key === "0") setZoom(1);
    if (e.key === "f" || e.key === "F") openPixelDialog();
  });
})();
