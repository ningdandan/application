(() => {
  const artists = Array.isArray(window.ARTISTS) ? window.ARTISTS : [];

  const NEED_SHORT = {
    "Printers(2d&3d, materials not included)": "Printers",
    "DJ Station": "DJ Station",
  };

  const state = {
    query: "",
    tags: new Set(),
    hours: new Set(),
    needs: new Set(),
    groupShow: false,
    hasPortfolio: false,
    hasIg: false,
    hasMedia: false,
    view: "grid",
  };

  const els = {
    grid: document.getElementById("artist-grid"),
    empty: document.getElementById("empty"),
    count: document.getElementById("result-count"),
    stats: document.getElementById("stats"),
    search: document.getElementById("search"),
    tagFilters: document.getElementById("tag-filters"),
    hourFilters: document.getElementById("hour-filters"),
    needFilters: document.getElementById("need-filters"),
    groupShow: document.getElementById("group-show"),
    hasPortfolio: document.getElementById("has-portfolio"),
    hasIg: document.getElementById("has-ig"),
    hasMedia: document.getElementById("has-media"),
    clear: document.getElementById("clear-filters"),
    drawer: document.getElementById("drawer"),
    drawerContent: document.getElementById("drawer-content"),
  };

  function uniqueSorted(items) {
    return [...new Set(items)].sort((a, b) => a.localeCompare(b));
  }

  function shortNeed(n) {
    return NEED_SHORT[n] || n;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hourLabel(h) {
    const m = h.match(/9\/(\d+)\s+(\w+)/);
    if (!m) return h;
    return `${m[2].slice(0, 3)} 9/${m[1]}`;
  }

  function renderStats() {
    const withMedia = artists.filter((a) => a.media && (a.media.profile || (a.media.samples || []).length)).length;
    const showYes = artists.filter((a) => a.groupShow).length;
    els.stats.innerHTML = `
      <div class="stat"><strong>${artists.length}</strong><span>Applicants</span></div>
      <div class="stat"><strong>${withMedia}</strong><span>With media</span></div>
      <div class="stat"><strong>${showYes}</strong><span>Group show</span></div>
    `;
  }

  function makeChips(container, values, key, labelFn = (v) => v) {
    container.innerHTML = "";
    values.forEach((value) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.textContent = labelFn(value);
      btn.dataset.value = value;
      btn.addEventListener("click", () => {
        const set = state[key];
        if (set.has(value)) set.delete(value);
        else set.add(value);
        btn.classList.toggle("is-active", set.has(value));
        render();
      });
      container.appendChild(btn);
    });
  }

  function buildFilters() {
    const tags = uniqueSorted(artists.flatMap((a) => a.tags));
    const hours = uniqueSorted(artists.flatMap((a) => a.hours));
    // Preferred order for hours
    const hourOrder = ["9/25 Friday 5pm", "9/26 Saturday 5pm", "9/27 Sunday 5pm"];
    hours.sort((a, b) => {
      const ia = hourOrder.indexOf(a);
      const ib = hourOrder.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });

    const needs = uniqueSorted(artists.flatMap((a) => a.need));
    const needOrder = [
      "Lights",
      "Cameras",
      "Projectors",
      "Printers(2d&3d, materials not included)",
      "Speakers",
      "DJ Station",
      "Books",
      "Coffee",
    ];
    needs.sort((a, b) => {
      const ia = needOrder.indexOf(a);
      const ib = needOrder.indexOf(b);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });

    makeChips(els.tagFilters, tags, "tags");
    makeChips(els.hourFilters, hours, "hours", hourLabel);
    makeChips(els.needFilters, needs, "needs", shortNeed);
  }

  function matches(artist) {
    const q = state.query.trim().toLowerCase();
    if (q) {
      const hay = [
        artist.name,
        artist.description,
        artist.instagram,
        artist.portfolio,
        artist.expect,
        artist.bring,
        ...(artist.tags || []),
        ...(artist.need || []),
      ]
        .join(" ")
        .toLowerCase();
      if (!hay.includes(q)) return false;
    }

    if (state.tags.size) {
      const hit = artist.tags.some((t) => state.tags.has(t));
      if (!hit) return false;
    }

    if (state.hours.size) {
      const hit = artist.hours.some((h) => state.hours.has(h));
      if (!hit) return false;
    }

    if (state.needs.size) {
      const hit = artist.need.some((n) => state.needs.has(n));
      if (!hit) return false;
    }

    if (state.groupShow && !artist.groupShow) return false;
    if (state.hasPortfolio && !artist.portfolio) return false;
    if (state.hasIg && !artist.instagram) return false;
    if (state.hasMedia) {
      const m = mediaOf(artist);
      if (!m.profile && !m.samples.length) return false;
    }

    return true;
  }

  function mediaOf(artist) {
    const m = artist.media || {};
    return {
      profile: m.profile || null,
      samples: Array.isArray(m.samples) ? m.samples : [],
    };
  }

  function cardHtml(artist, index) {
    const tags = artist.tags
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
      .join("");
    const hours = artist.hours
      .slice(0, 2)
      .map((h) => `<span class="pill">${escapeHtml(hourLabel(h))}</span>`)
      .join("");
    const show = artist.groupShow
      ? `<span class="pill accent">Show</span>`
      : "";
    const delay = Math.min(index * 0.03, 0.35);
    const media = mediaOf(artist);
    const thumb = media.profile || media.samples[0];
    const samples = media.samples.slice(0, 3);

    const portrait = thumb
      ? `<div class="card-portrait"><img src="${escapeHtml(thumb)}" alt="" loading="lazy" /></div>`
      : `<div class="card-portrait is-empty"><span>${escapeHtml((artist.name || "?").slice(0, 1))}</span></div>`;

    const strip =
      samples.length > 0
        ? `<div class="card-strip">${samples
            .map((s) => `<img src="${escapeHtml(s)}" alt="" loading="lazy" />`)
            .join("")}</div>`
        : "";

    const links =
      state.view === "list"
        ? `<div class="card-links">
            ${artist.instagram ? `<span>${escapeHtml(artist.instagram)}</span>` : ""}
            ${artist.portfolio ? `<span>Portfolio</span>` : ""}
          </div>`
        : "";

    return `
      <button type="button" class="card" data-id="${artist.id}" style="animation-delay:${delay}s">
        ${portrait}
        <div class="card-body">
          <h2 class="card-name">${escapeHtml(artist.name)}</h2>
          <div class="card-tags">${tags}</div>
          <p class="card-desc">${escapeHtml(artist.description || "No description provided.")}</p>
          ${strip}
          <div class="card-meta">${hours}${show}</div>
        </div>
        ${links}
      </button>
    `;
  }

  function render() {
    const filtered = artists.filter(matches);
    els.grid.classList.toggle("is-list", state.view === "list");
    els.grid.innerHTML = filtered.map(cardHtml).join("");
    els.count.textContent = `${filtered.length} of ${artists.length} applicants`;
    els.empty.hidden = filtered.length > 0;

    els.grid.querySelectorAll(".card").forEach((card) => {
      card.addEventListener("click", () => {
        const id = Number(card.dataset.id);
        openDrawer(artists.find((a) => a.id === id));
      });
    });
  }

  function openDrawer(artist) {
    if (!artist) return;

    const tags = artist.tags
      .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
      .join("");

    const hours = artist.hours.length
      ? `<div class="hour-list">${artist.hours
          .map((h) => `<span class="pill">${escapeHtml(h)}</span>`)
          .join("")}</div>`
      : `<p>Not specified</p>`;

    const needs = artist.need.length
      ? `<div class="need-list">${artist.need
          .map((n) => `<span class="pill">${escapeHtml(shortNeed(n))}</span>`)
          .join("")}</div>`
      : `<p>None listed</p>`;

    const actions = [];
    if (artist.portfolio) {
      actions.push(
        `<a class="action" href="${escapeHtml(artist.portfolio)}" target="_blank" rel="noopener noreferrer">Open portfolio</a>`
      );
    }
    if (artist.instagramUrl) {
      actions.push(
        `<a class="action ghost" href="${escapeHtml(artist.instagramUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(artist.instagram || "Instagram")}</a>`
      );
    }
    if (artist.phone) {
      actions.push(
        `<a class="action ghost" href="tel:${escapeHtml(artist.phone.replace(/[^\d+]/g, ""))}">Call</a>`
      );
    }

    const media = mediaOf(artist);
    const galleryImgs = [media.profile, ...media.samples].filter(Boolean);
    const gallery = galleryImgs.length
      ? `<div class="drawer-gallery">${galleryImgs
          .map(
            (src, i) =>
              `<a href="${escapeHtml(src)}" target="_blank" rel="noopener noreferrer" class="drawer-shot${i === 0 ? " is-profile" : ""}"><img src="${escapeHtml(src)}" alt="" loading="lazy" /></a>`
          )
          .join("")}</div>`
      : `<p class="drawer-no-media">No scraped images yet.</p>`;

    els.drawerContent.innerHTML = `
      <h2 class="drawer-name" id="drawer-name">${escapeHtml(artist.name)}</h2>
      <div class="card-tags">${tags}</div>
      ${gallery}
      <div class="drawer-actions">${actions.join("")}</div>

      <div class="drawer-section">
        <h3>Practice</h3>
        <p>${escapeHtml(artist.description || "—")}</p>
      </div>

      <div class="drawer-section">
        <h3>Open house</h3>
        ${hours}
      </div>

      <div class="drawer-section">
        <h3>Bringing</h3>
        <p>${escapeHtml(artist.bring || "—")}</p>
      </div>

      <div class="drawer-section">
        <h3>Needs from studio</h3>
        ${needs}
      </div>

      <div class="drawer-section">
        <h3>Group show</h3>
        <p>${artist.groupShow ? "Yes" : "No / not specified"}</p>
      </div>

      <div class="drawer-section">
        <h3>Expects from Yituo</h3>
        <p>${escapeHtml(artist.expect || "—")}</p>
      </div>

      ${
        artist.phone
          ? `<div class="drawer-section"><h3>Phone</h3><p>${escapeHtml(artist.phone)}</p></div>`
          : ""
      }
    `;

    els.drawer.classList.add("is-open");
    els.drawer.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
  }

  function closeDrawer() {
    els.drawer.classList.remove("is-open");
    els.drawer.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }

  function clearFilters() {
    state.query = "";
    state.tags.clear();
    state.hours.clear();
    state.needs.clear();
    state.groupShow = false;
    state.hasPortfolio = false;
    state.hasIg = false;
    state.hasMedia = false;
    els.search.value = "";
    els.groupShow.checked = false;
    els.hasPortfolio.checked = false;
    els.hasIg.checked = false;
    if (els.hasMedia) els.hasMedia.checked = false;
    document.querySelectorAll(".chip.is-active").forEach((c) => c.classList.remove("is-active"));
    render();
  }

  // Events
  els.search.addEventListener("input", (e) => {
    state.query = e.target.value;
    render();
  });

  els.groupShow.addEventListener("change", (e) => {
    state.groupShow = e.target.checked;
    render();
  });
  els.hasPortfolio.addEventListener("change", (e) => {
    state.hasPortfolio = e.target.checked;
    render();
  });
  els.hasIg.addEventListener("change", (e) => {
    state.hasIg = e.target.checked;
    render();
  });
  if (els.hasMedia) {
    els.hasMedia.addEventListener("change", (e) => {
      state.hasMedia = e.target.checked;
      render();
    });
  }
  els.clear.addEventListener("click", clearFilters);

  document.querySelectorAll(".view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.view = btn.dataset.view;
      document.querySelectorAll(".view-btn").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      render();
    });
  });

  els.drawer.querySelectorAll("[data-close]").forEach((el) => {
    el.addEventListener("click", closeDrawer);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });

  renderStats();
  buildFilters();
  render();
})();
