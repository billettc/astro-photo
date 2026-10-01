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

  // Upload dropzone
  const input = document.getElementById("file-input");
  const list = document.getElementById("file-list");
  const dropzone = document.getElementById("dropzone");
  const form = document.getElementById("upload-form");
  const uploadBtn = document.getElementById("upload-btn");

  if (input && list && dropzone && form) {
    const render = () => {
      list.innerHTML = "";
      [...input.files].forEach((f) => {
        const li = document.createElement("li");
        li.textContent = `${f.name} (${Math.round(f.size / 1024)} KB)`;
        list.appendChild(li);
      });
    };

    const assignFiles = (fileList) => {
      const images = [...fileList].filter(
        (f) => !f.type || f.type.startsWith("image/")
      );
      if (!images.length) return false;
      const dt = new DataTransfer();
      images.forEach((f) => dt.items.add(f));
      input.files = dt.files;
      // native required input is satisfied once files are set
      input.removeAttribute("required");
      render();
      return true;
    };

    input.addEventListener("change", () => {
      render();
      if (input.files.length) {
        input.removeAttribute("required");
      }
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
      if (!dropzone.contains(e.relatedTarget)) {
        dropzone.classList.remove("dragover");
      }
    });
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
      const files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      if (assignFiles(files)) {
        if (uploadBtn) {
          uploadBtn.disabled = true;
          uploadBtn.textContent = "Uploading…";
        }
        form.submit();
      }
    });
  }

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
    scale = 1;
    tx = 0;
    ty = 0;
    applyTransform();
    if (cap) cap.textContent = photo.name || "";
    img.alt = photo.name || "";
    if (saveStatus) saveStatus.textContent = "";
    syncAdminForms(photo);

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
    if (current === nextSrc && img.complete && img.naturalWidth) {
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      fitImage(true);
      return;
    }
    img.onload = () => {
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      fitImage(true);
    };
    img.src = nextSrc;
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

    img.onload = () => {
      naturalW = img.naturalWidth;
      naturalH = img.naturalHeight;
      fitImage(true);
    };
    img.src = mainSrc;
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
  frame.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      setZoom(scale * factor, e.clientX, e.clientY);
    },
    { passive: false }
  );

  frame.addEventListener("dblclick", (e) => {
    if (scale > 1.05) setZoom(1);
    else setZoom(2.5, e.clientX, e.clientY);
  });

  frame.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
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
