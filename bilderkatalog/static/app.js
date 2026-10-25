"use strict";

// ---------- Hilfsfunktionen ----------

const $ = (sel) => document.querySelector(sel);
const PAGE = 200;

async function api(path, { method = "GET", body } = {}) {
  const opts = { method, headers: {} };
  if (method !== "GET") opts.headers["X-Bilderkatalog"] = "1";
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) throw new Error(data?.detail || `Fehler ${res.status}`);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

const qs = (obj) => new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false)).toString();

let regionNames = null;
try { regionNames = new Intl.DisplayNames(["de"], { type: "region" }); } catch { /* ältere Browser */ }
const countryName = (cc) => { try { return (cc && regionNames?.of(cc)) || cc || "Unbekannt"; } catch { return cc; } };
const monthName = (m) => new Date(2000, m - 1, 1).toLocaleString("de-DE", { month: "long" });
const formatDate = (iso) => iso ? new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" }) : "–";
const formatDay = (iso) => iso ? new Date(iso).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "numeric" }) : "";
const formatSize = (bytes) => bytes == null ? "–" : bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1e3)} kB`;
const thumbUrl = (id, size = "medium") => `/api/thumb/${encodeURIComponent(id)}?size=${size}`;

function toast(message) {
  $("#job-msg").textContent = message;
  $("#job-progress").hidden = true;
  $("#job-cancel").hidden = true;
  $("#job-close").hidden = false;
  $("#jobbar").hidden = false;
}

// ---------- Zustand ----------

const state = {
  filter: {},
  title: "Alle Bilder",
  sort: "date_desc",
  items: [],
  total: 0,
  loading: false,
  generation: 0,
  selected: new Set(),
  lastIndex: null,
  albums: [],
  facets: null,
  mode: "grid",
  lightboxIndex: null,
  pollTimer: null,
  lastJobStates: {},
  open: new Set(),
};

// ---------- Start ----------

async function boot() {
  const status = await api("/api/status");
  state.exportDir = status.export_dir;
  applyStatus(status);
  handleJobs(status.jobs, true);
  await Promise.all([loadSidebar(), reload()]);
  if (!status.scopes.length) openFolders();
}

function applyStatus(status) {
  state.status = status;
  $("#version-btn").textContent = `v${status.version}`;
  $("#online-btn").textContent = status.account ? `Online: ${status.account}` : "Online-Zugang";
  const partial = status.partial.count;
  $("#enrich-btn").hidden = partial === 0;
  $("#enrich-btn").textContent = `Details nachladen (${partial.toLocaleString("de-DE")})`;
}

// ---------- Version und Updates ----------

$("#version-btn").addEventListener("click", () => {
  $("#version-nr").textContent = state.status.version;
  $("#version-daten").textContent = state.status.data_dir;
  $("#update-msg").replaceChildren();
  $("#version-dlg").showModal();
});
$("#version-close").addEventListener("click", () => $("#version-dlg").close());
$("#releases-btn").addEventListener("click", () => api("/api/open-releases", { method: "POST" }));
$("#update-btn").addEventListener("click", async () => {
  const msg = $("#update-msg");
  msg.textContent = "Frage GitHub …";
  try {
    const r = await api("/api/update");
    if (r.error) msg.textContent = r.error;
    else if (r.newer) msg.replaceChildren(`Neue Version ${r.latest} verfügbar. `,
      el("button", { class: "primary", onclick: () => api("/api/open-releases", { method: "POST" }) }, "Herunterladen"));
    else msg.textContent = `Du hast die neueste Version (${r.current}).`;
  } catch (err) {
    msg.textContent = err.message;
  }
});

// ---------- Optionaler Online-Zugang ----------

$("#online-btn").addEventListener("click", () => { renderOnline(); $("#online-dlg").showModal(); });
$("#online-close").addEventListener("click", () => $("#online-dlg").close());

function renderOnline() {
  const s = state.status;
  $("#online-account").hidden = !s.account;
  $("#online-name").textContent = s.account || "";
  $("#setup-form").hidden = Boolean(s.account);
  $("#login-btn").hidden = !s.online_configured || Boolean(s.account);
}

$("#setup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-msg").textContent = "";
  try {
    await api("/api/setup", { method: "POST", body: { client_id: event.target.client_id.value } });
    applyStatus(await api("/api/status"));
    renderOnline();
    $("#login-msg").textContent = "Gespeichert. Jetzt anmelden.";
  } catch (err) {
    $("#login-msg").textContent = err.message;
  }
});

$("#login-btn").addEventListener("click", async () => {
  try {
    await api("/api/login", { method: "POST" });
    pollLogin();
  } catch (err) {
    $("#login-msg").textContent = err.message;
  }
});

async function pollLogin() {
  $("#login-msg").textContent = "Bitte im geöffneten Browserfenster anmelden …";
  const status = await api("/api/status");
  applyStatus(status);
  if (status.account) { $("#login-msg").textContent = ""; return renderOnline(); }
  if (status.jobs.login.state === "running") return setTimeout(pollLogin, 1500);
  $("#login-msg").textContent = status.jobs.login.message || "Anmeldung nicht abgeschlossen.";
}

$("#logout-btn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  applyStatus(await api("/api/status"));
  renderOnline();
});

// ---------- Details nachladen ----------

$("#enrich-btn").addEventListener("click", () => {
  const p = state.status.partial;
  openEnrich(null,
    `${p.count.toLocaleString("de-DE")} Bilder liegen nur online (zusammen ${formatSize(p.bytes)}). ` +
    "Von ihnen ist bisher nur das Aufnahmedatum bekannt.");
});

// selection: null = alle, sonst { ids } oder { filter }
function openEnrich(selection, info) {
  $("#enrich-info").textContent = info;
  const dlg = $("#enrich-dlg");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    try {
      await api("/api/enrich", { method: "POST", body: selection ?? undefined });
      clearSelection();
      startPolling();
    } catch (err) {
      toast(err.message);
    }
  };
  dlg.showModal();
}

// ---------- Seitenleiste ----------

async function loadSidebar() {
  const [facets, albums] = await Promise.all([api("/api/facets"), api("/api/albums")]);
  state.facets = facets;
  state.albums = albums;
  renderSidebar();
}

function navItem(label, count, filter, title, extra = {}) {
  const key = JSON.stringify(filter);
  return el("div", {
    class: "item" + (JSON.stringify(state.filter) === key ? " active" : ""),
    title: extra.tooltip || label,
    onclick: (event) => {
      event.preventDefault();
      // Gruppen (Jahr, Land) beim Anklicken aufklappen und offen halten
      if (event.currentTarget.parentElement.tagName === "SUMMARY") state.open.add(key);
      setView(filter, title || label);
      document.body.classList.remove("sidebar-open");
    },
  }, el("span", { class: "label" }, label), count != null ? el("span", { class: "count" }, String(count)) : null);
}

function group(filter, summary, children) {
  const key = JSON.stringify(filter);
  const details = el("details", { open: state.open.has(key) }, el("summary", {}, summary), el("ul", {}, children));
  details.addEventListener("toggle", () => details.open ? state.open.add(key) : state.open.delete(key));
  return details;
}

// Hauptgruppe der Seitenleiste; beim Start eingeklappt, Zustand bleibt während der Sitzung erhalten
function section(title, count, ...content) {
  const key = "section:" + title;
  const details = el("details", { class: "section", open: state.open.has(key) },
    el("summary", {}, el("h4", {}, title), count != null ? el("span", { class: "count" }, String(count)) : null),
    ...content);
  details.addEventListener("toggle", () => details.open ? state.open.add(key) : state.open.delete(key));
  return details;
}

function renderSidebar() {
  const f = state.facets;
  const nav = $("#sidebar");
  nav.replaceChildren();
  nav.append(el("ul", {}, el("li", {}, navItem("Alle Bilder", f.total, {}, "Alle Bilder"))));

  // Alben
  nav.append(section("Alben", state.albums.length,
    el("ul", {}, state.albums.map((a) => el("li", {}, navItem(a.name, a.n, { album: a.id }, `Album: ${a.name}`)))),
    el("button", { class: "link new-album", onclick: newAlbum }, "+ Neues Album")));

  // Zeitleiste: Jahr → Monat
  const years = new Map();
  for (const row of f.years) {
    if (!years.has(row.year)) years.set(row.year, []);
    years.get(row.year).push(row);
  }
  if (years.size) {
    nav.append(section("Zeitleiste", years.size, el("ul", {}, [...years].map(([year, months]) => el("li", {},
      group({ year }, navItem(String(year), months.reduce((s, m) => s + m.n, 0), { year }, `Jahr ${year}`),
        months.map((m) => el("li", {},
          navItem(monthName(m.month), m.n, { year, month: m.month }, `${monthName(m.month)} ${year}`)))),
    )))));
  }

  // Orte: Land → Stadt
  const countries = new Map();
  for (const row of f.places) {
    if (!countries.has(row.country_code)) countries.set(row.country_code, []);
    countries.get(row.country_code).push(row);
  }
  if (countries.size) {
    const sorted = [...countries].sort((a, b) => countryName(a[0]).localeCompare(countryName(b[0]), "de"));
    nav.append(section("Orte", countries.size, el("ul", {}, sorted.map(([cc, cities]) => el("li", {},
      group({ country: cc }, navItem(countryName(cc), cities.reduce((s, c) => s + c.n, 0), { country: cc }, countryName(cc)),
        cities.map((c) => el("li", {},
          navItem(c.city || "Unbekannt", c.n, { country: cc, city: c.city }, `${c.city}, ${countryName(cc)}`)))),
    )))));
  }

  // Ordner
  if (f.folders.length) {
    nav.append(section("Ordner", f.folders.length, el("ul", {}, f.folders.map((d) => el("li", {},
      navItem(d.path.split("/").filter(Boolean).slice(-2).join(" / ") || "/", d.n, { folder: d.path }, `Ordner: ${d.path}`, { tooltip: d.path }))))));
  }

  // Kameras
  if (f.cameras.length) {
    nav.append(section("Kameras", f.cameras.length,
      el("ul", {}, f.cameras.slice(0, 30).map((c) => el("li", {}, navItem(c.camera, c.n, { camera: c.camera }, `Kamera: ${c.camera}`))))));
  }

  // Sonderfilter
  nav.append(section("Prüfen", null, el("ul", {},
    el("li", {}, navItem("Ohne Aufnahmedatum", f.special.no_date, { no_date: true }, "Ohne EXIF-Aufnahmedatum",
      { tooltip: "Kein EXIF-Datum: Datum stammt aus Dateiname oder Dateizeit" })),
    el("li", {}, navItem("Ohne GPS", f.special.no_gps, { no_gps: true }, "Ohne GPS-Position")),
    el("li", {}, navItem("Videos", f.special.videos, { media: "video" }, "Videos")),
    f.special.partial ? el("li", {}, navItem("Nur online, Details fehlen", f.special.partial, { partial: true }, "Nur online – Details fehlen",
      { tooltip: "Nur das Aufnahmedatum ist bekannt. „Details nachladen“ ergänzt Ort, Kamera und Vorschau." })) : null,
  )));
}

async function newAlbum() {
  const name = prompt("Name des neuen Albums:");
  if (!name?.trim()) return;
  const album = await api("/api/albums", { method: "POST", body: { name } });
  await loadSidebar();
  setView({ album: album.id }, `Album: ${album.name}`);
}

// ---------- Raster ----------

function setView(filter, title) {
  state.filter = filter;
  state.title = title;
  state.mode = "grid";
  clearSelection();
  renderSidebar();
  reload();
}

async function reload() {
  state.generation++;
  state.items = [];
  state.total = 0;
  state.complete = false;
  state.loading = false;
  $("#grid").replaceChildren();
  $("#events").hidden = true;
  $("#grid").hidden = false;
  $("#view-enrich-btn").hidden = true;
  $("#view-title").textContent = state.title;
  $("#sel-remove").hidden = state.filter.album === undefined;
  await loadMore();
}

function currentQuery() {
  const search = $("#search").value.trim();
  return { ...state.filter, q: search || undefined };
}

async function loadMore() {
  if (state.loading || state.mode !== "grid") return;
  if (state.complete) return;
  state.loading = true;
  const generation = state.generation;
  try {
    const data = await api(`/api/photos?${qs({ ...currentQuery(), sort: state.sort, limit: PAGE, offset: state.items.length })}`);
    if (generation !== state.generation) return;
    const start = state.items.length;
    state.items.push(...data.items);
    state.total = data.total;
    state.complete = state.items.length >= data.total || !data.items.length;
    renderTiles(start);
    $("#view-count").textContent = `${data.total.toLocaleString("de-DE")} Dateien`;
    state.viewPartial = data.partial;
    $("#view-enrich-btn").hidden = !data.partial;
    $("#view-enrich-btn").textContent = `Vorschau nachladen (${data.partial.toLocaleString("de-DE")})`;
    $("#empty").hidden = data.total > 0;
  } finally {
    if (generation === state.generation) state.loading = false;
  }
  // Wenn der Bildschirm noch nicht gefüllt ist, direkt weiterladen
  if (state.complete || generation !== state.generation) return;
  const sentinel = $("#sentinel").getBoundingClientRect();
  if (sentinel.top < window.innerHeight + 400) loadMore();
}

function groupKey(item) {
  if (state.sort.startsWith("date")) return item.year ? `${monthName(item.month)} ${item.year}` : "Ohne Datum";
  if (state.sort === "place") return item.country_code ? `${item.city || "Unbekannt"}, ${countryName(item.country_code)}` : "Ohne Ort";
  return null;
}

function renderTiles(start) {
  const grid = $("#grid");
  const fragment = document.createDocumentFragment();
  for (let i = start; i < state.items.length; i++) {
    const item = state.items[i];
    const key = groupKey(item);
    if (key && (i === 0 || groupKey(state.items[i - 1]) !== key)) {
      fragment.append(el("div", { class: "group-head" }, key));
    }
    fragment.append(tile(item, i));
  }
  grid.append(fragment);
}

function tile(item, index) {
  const img = el("img", { loading: "lazy", src: thumbUrl(item.id), alt: item.name, title: `${item.name}\n${formatDate(item.taken_at)}` });
  img.addEventListener("error", () => img.replaceWith(el("div", { class: "noimg" },
    item.online_only ? el("span", { class: "cloud", title: "Nur online – Vorschau nach „Details nachladen“" }, "☁") : null,
    el("span", {}, item.name), el("span", {}, formatDay(item.taken_at)))), { once: true });
  const node = el("div", { class: "tile" + (state.selected.has(item.id) ? " selected" : ""), "data-index": index },
    img,
    el("span", { class: "check", role: "checkbox", "aria-label": "Auswählen" }),
    item.is_video ? el("span", { class: "badge" }, "▶ Video") : null,
  );
  node.addEventListener("click", (event) => {
    if (state.selectMode || event.target.classList.contains("check") || event.shiftKey || event.ctrlKey || event.metaKey || state.selected.size) {
      toggleSelect(index, event.shiftKey);
    } else {
      openLightbox(index);
    }
  });
  return node;
}

new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) loadMore();
}, { rootMargin: "600px" }).observe($("#sentinel"));

let searchTimer;
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(reload, 300);
});
$("#sort").addEventListener("change", (e) => { state.sort = e.target.value; reload(); });
$("#menu-btn").addEventListener("click", () => document.body.classList.toggle("sidebar-open"));

// ---------- Auswahl ----------

function toggleSelect(index, range) {
  const ids = [];
  if (range && state.lastIndex !== null) {
    const [a, b] = [Math.min(state.lastIndex, index), Math.max(state.lastIndex, index)];
    for (let i = a; i <= b; i++) ids.push(state.items[i].id);
    ids.forEach((id) => state.selected.add(id));
  } else {
    const id = state.items[index].id;
    state.selected.has(id) ? state.selected.delete(id) : state.selected.add(id);
  }
  state.lastIndex = index;
  refreshSelection();
}

function refreshSelection() {
  document.querySelectorAll(".tile").forEach((node) => {
    const item = state.items[Number(node.dataset.index)];
    node.classList.toggle("selected", state.selected.has(item.id));
  });
  const n = state.selected.size;
  $("#grid").classList.toggle("selecting", n > 0 || state.selectMode);
  $("#selbar").hidden = n === 0 && !state.selectMode;
  $("#sel-count").textContent = `${n} ausgewählt`;
  $("#sel-hint").hidden = n > 0;
  for (const id of ["#sel-album", "#sel-zip", "#sel-enrich", "#sel-remove", "#sel-clear"]) $(id).disabled = n === 0;
  $("#select-mode-btn").textContent = state.selectMode ? "Auswahl beenden" : "Auswählen";
  $("#select-mode-btn").classList.toggle("primary", state.selectMode);
}

function setSelectMode(on) {
  state.selectMode = on;
  if (!on) state.selected.clear();
  refreshSelection();
}

$("#select-mode-btn").addEventListener("click", () => setSelectMode(!state.selectMode));

function clearSelection() {
  state.selected.clear();
  state.lastIndex = null;
  refreshSelection();
}

$("#sel-clear").addEventListener("click", clearSelection);
$("#sel-all").addEventListener("click", () => { state.items.forEach((i) => state.selected.add(i.id)); refreshSelection(); });
$("#sel-album").addEventListener("click", () => chooseAlbum({ ids: [...state.selected] }));
$("#sel-zip").addEventListener("click", () => openExport({ ids: [...state.selected] }, `${state.selected.size} ausgewählte Dateien`, "Auswahl"));
$("#sel-remove").addEventListener("click", async () => {
  const albumId = state.filter.album;
  const res = await api(`/api/albums/${albumId}/photos/remove`, { method: "POST", body: { ids: [...state.selected] } });
  toast(`${res.removed} Dateien aus dem Album entfernt (in OneDrive bleiben sie erhalten).`);
  clearSelection();
  await loadSidebar();
  reload();
});

$("#view-album-btn").addEventListener("click", () => chooseAlbum({ filter: currentQuery() }, state.title));
$("#view-enrich-btn").addEventListener("click", () => openEnrich({ filter: currentQuery() },
  `${state.viewPartial.toLocaleString("de-DE")} Bilder in „${state.title}“ liegen nur online. Ihre Vorschau und Details werden nachgeladen.`));
$("#sel-enrich").addEventListener("click", () => {
  const ids = [...state.selected];
  const partial = state.items.filter((i) => state.selected.has(i.id) && i.meta_level === "partial").length;
  if (!partial) return toast("Für die ausgewählten Bilder ist die Vorschau schon vorhanden.");
  openEnrich({ ids }, `${partial} der ausgewählten Bilder liegen nur online. Ihre Vorschau und Details werden nachgeladen.`);
});
$("#view-zip-btn").addEventListener("click", () => openExport({ filter: currentQuery() }, `${state.total} Dateien dieser Ansicht`, state.title));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (state.selected.size || state.selectMode) && !document.querySelector("dialog[open]")) setSelectMode(false);
});

// ---------- Alben-Dialog ----------

function chooseAlbum(selection, suggestedName = "") {
  const dlg = $("#album-dlg");
  const form = $("#album-form");
  form.album.replaceChildren(
    el("option", { value: "" }, "– neues Album –"),
    ...state.albums.map((a) => el("option", { value: a.id }, `${a.name} (${a.n})`)),
  );
  form.new_name.value = suggestedName.replace(/^Album: /, "");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    try {
      let albumId = form.album.value;
      if (!albumId) {
        const name = form.new_name.value.trim();
        if (!name) return toast("Bitte einen Namen für das neue Album eingeben.");
        albumId = (await api("/api/albums", { method: "POST", body: { name } })).id;
      }
      const res = await api(`/api/albums/${albumId}/photos`, { method: "POST", body: selection });
      toast(`${res.added} Dateien zum Album hinzugefügt.`);
      clearSelection();
      await loadSidebar();
    } catch (err) {
      toast(err.message);
    }
  };
  dlg.showModal();
}

// ---------- Export ----------

function openExport(selection, info, name) {
  const form = $("#export-form");
  $("#export-info").textContent = info;
  form.name.value = (name || "Export").replace(/^(Album|Ordner|Kamera): /, "");
  form.target_dir.value = form.target_dir.value || state.exportDir || "";
  const dlg = $("#export-dlg");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "ok") return;
    try {
      await api("/api/export", {
        method: "POST",
        body: { ...selection, name: form.name.value, structure: form.structure.value, target_dir: form.target_dir.value || null },
      });
      clearSelection();
      startPolling();
    } catch (err) {
      toast(err.message);
    }
  };
  dlg.showModal();
}

// ---------- Ereignisse ----------

$("#events-btn").addEventListener("click", showEvents);

async function showEvents() {
  state.mode = "events";
  clearSelection();
  $("#grid").hidden = true;
  $("#empty").hidden = true;
  const box = $("#events");
  box.hidden = false;
  box.replaceChildren(el("p", { class: "muted" }, "Suche Ereignisse …"));
  $("#view-title").textContent = `Ereignisse – ${state.title}`;
  const events = await api(`/api/events?${qs(currentQuery())}`);
  $("#view-count").textContent = `${events.length} Ereignisse`;
  box.replaceChildren(...events.map(eventCard));
  if (!events.length) box.append(el("p", { class: "muted" }, "Keine Ereignisse gefunden (mindestens 5 Bilder mit weniger als 6 Stunden Abstand)."));
}

function eventCard(ev) {
  const place = ev.city ? `${ev.city}, ${countryName(ev.country_code)}` : "Ort unbekannt";
  const sameDay = ev.start.slice(0, 10) === ev.end.slice(0, 10);
  const when = sameDay ? formatDay(ev.start) : `${formatDay(ev.start)} – ${formatDay(ev.end)}`;
  const title = `${place}, ${when}`;
  const filter = { ...currentQuery(), date_from: ev.start, date_to: ev.end };
  return el("div", { class: "event" },
    el("div", { class: "thumbs", title: "Anzeigen", onclick: () => setView(filter, title) },
      ev.samples.map((id) => el("img", { loading: "lazy", src: thumbUrl(id, "small"), alt: "" }))),
    el("div", { class: "info" },
      el("div", {}, el("div", { class: "title" }, place), el("div", { class: "muted" }, `${when} · ${ev.count} Dateien`)),
      el("button", { onclick: () => chooseAlbum({ filter }, `${ev.city || "Ereignis"} ${formatDay(ev.start)}`) }, "Als Album"),
    ));
}

// ---------- Großansicht ----------

function openLightbox(index) {
  state.lightboxIndex = index;
  renderLightbox();
  if (!$("#lightbox").open) $("#lightbox").showModal();
}

async function renderLightbox() {
  const item = state.items[state.lightboxIndex];
  if (!item) return;
  const img = $("#lb-img");
  img.src = thumbUrl(item.id, "medium");
  const large = new Image();
  large.onload = () => { if (state.items[state.lightboxIndex] === item) img.src = large.src; };
  large.src = thumbUrl(item.id, "large");
  img.alt = item.name;

  const detail = await api(`/api/photos/${encodeURIComponent(item.id)}`);
  if (state.items[state.lightboxIndex] !== item) return;
  const place = [detail.city, detail.region, countryName(detail.country_code)].filter(Boolean).join(", ");
  const sourceLabel = { exif: "Kamera (EXIF)", filename: "aus Dateiname", file: "Dateizeit" }[detail.date_source] || "–";
  const rows = [
    ["Datei", detail.name],
    ["Aufgenommen", formatDate(detail.taken_at)],
    ["Datumsquelle", sourceLabel],
    ["Ort", detail.country_code ? place : "–"],
    ["GPS", detail.lat != null ? `${detail.lat.toFixed(5)}, ${detail.lon.toFixed(5)}` : "–"],
    ["Kamera", (detail.camera_model?.startsWith(detail.camera_make || "\u0000") ? detail.camera_model
      : [detail.camera_make, detail.camera_model].filter(Boolean).join(" ")) || "–"],
    ["Größe", `${detail.width && detail.height ? `${detail.width} × ${detail.height} · ` : ""}${formatSize(detail.size)}`],
    ["Ordner", detail.parent_path || "–"],
    ["Alben", detail.albums.map((a) => a.name).join(", ") || "–"],
  ];
  $("#lb-meta").replaceChildren(
    el("dl", {}, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
    detail.meta_level === "partial" ? el("p", {}, "Nur online: Ort, Kamera und Vorschau fehlen noch. ",
      el("button", { onclick: (e) => enrichOne(item, e.currentTarget) }, "Vorschau laden")) : null,
    detail.source === "local"
      ? el("p", {}, el("button", { onclick: () => api(`/api/photos/${encodeURIComponent(item.id)}/reveal`, { method: "POST" }).catch((e) => toast(e.message)) }, "Im Explorer zeigen"))
      : detail.web_url ? el("a", { href: detail.web_url, target: "_blank", rel: "noopener" }, "In OneDrive öffnen ↗") : null,
    el("p", {}, el("button", { onclick: () => { $("#lightbox").close(); chooseAlbum({ ids: [item.id] }); } }, "Zu Album …")),
  );
}

async function enrichOne(item, button) {
  button.disabled = true;
  button.textContent = "Lädt …";
  try {
    const updated = await api(`/api/photos/${encodeURIComponent(item.id)}/enrich`, { method: "POST" });
    Object.assign(item, updated);
    // Kachel im Raster aktualisieren
    const index = state.items.indexOf(item);
    document.querySelector(`.tile[data-index="${index}"]`)?.replaceWith(tile(item, index));
    if (state.items[state.lightboxIndex] === item) renderLightbox();
    loadSidebar();
    applyStatus(await api("/api/status"));
  } catch (err) {
    button.disabled = false;
    button.textContent = "Vorschau laden";
    toast(err.message);
  }
}

function stepLightbox(delta) {
  const next = state.lightboxIndex + delta;
  if (next < 0) return;
  if (next >= state.items.length) { loadMore(); return; }
  state.lightboxIndex = next;
  renderLightbox();
}

$("#lb-prev").addEventListener("click", () => stepLightbox(-1));
$("#lb-next").addEventListener("click", () => stepLightbox(1));
$("#lb-close").addEventListener("click", () => $("#lightbox").close());
$("#lightbox").addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") stepLightbox(-1);
  if (event.key === "ArrowRight") stepLightbox(1);
});

// ---------- Ordnerauswahl ----------

$("#folders-btn").addEventListener("click", openFolders);
$("#folders-close").addEventListener("click", () => $("#folders-dlg").close());
$("#add-root").addEventListener("click", () => addScope({ kind: "graph", item_id: "root" }));
$("#path-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const path = event.target.path.value.trim();
  if (path) addScope({ kind: "local", path }).then(() => { event.target.path.value = ""; });
});

async function openFolders() {
  $("#folders-dlg").showModal();
  await renderScopes();
  const local = $("#local-tree");
  local.replaceChildren(el("li", { class: "muted" }, "Lade Ordner …"));
  try {
    const roots = await api("/api/local/folders");
    local.replaceChildren(...(roots.length ? roots.map((f) => treeNode(f, localChildren, { kind: "local", path: f.path }))
      : [el("li", { class: "muted" }, "Kein OneDrive-Ordner gefunden – Pfad unten eingeben.")]));
  } catch (err) {
    local.replaceChildren(el("li", { class: "error" }, err.message));
  }
  const graph = $("#graph-section");
  graph.hidden = !state.status?.account;
  if (!graph.hidden) {
    const tree = $("#folder-tree");
    tree.replaceChildren(el("li", { class: "muted" }, "Lade Ordner …"));
    try {
      const folders = await api("/api/folders?parent=root");
      tree.replaceChildren(...folders.map((f) => treeNode({ ...f, has_children: f.children > 0 }, graphChildren, { kind: "graph", item_id: f.id })));
    } catch (err) {
      tree.replaceChildren(el("li", { class: "error" }, err.message));
    }
  }
}

async function localChildren(folder) {
  const subs = await api(`/api/local/folders?${qs({ path: folder.path })}`);
  return subs.map((f) => treeNode(f, localChildren, { kind: "local", path: f.path }));
}

async function graphChildren(folder) {
  const subs = await api(`/api/folders?parent=${encodeURIComponent(folder.id)}`);
  return subs.map((f) => treeNode({ ...f, has_children: f.children > 0 }, graphChildren, { kind: "graph", item_id: f.id }));
}

async function renderScopes() {
  const status = await api("/api/status");
  applyStatus(status);
  $("#scope-list").replaceChildren(...(status.scopes.length ? status.scopes.map((s) => el("li", {},
    el("span", {},
      el("span", { class: "kind" }, s.kind === "local" ? "lokal" : "online"),
      s.path === "/" ? "Ganzes OneDrive" : s.path,
      el("span", { class: "muted" }, s.last_sync ? `  · zuletzt ${formatDate(s.last_sync)}` : "  · noch nicht eingelesen")),
    el("button", { class: "link", onclick: () => removeScope(s) }, "Entfernen"),
  )) : [el("li", { class: "muted" }, "Noch keine Ordner gewählt.")]));
}

function treeNode(folder, loadChildren, scopeBody) {
  const children = el("ul", { hidden: true });
  let loaded = false;
  const toggle = el("button", { class: "toggle", "aria-label": "Aufklappen" }, folder.has_children ? "▸" : "");
  toggle.addEventListener("click", async () => {
    if (!folder.has_children) return;
    children.hidden = !children.hidden;
    toggle.textContent = children.hidden ? "▸" : "▾";
    if (!loaded) {
      loaded = true;
      children.replaceChildren(el("li", { class: "muted" }, "…"));
      try {
        const nodes = await loadChildren(folder);
        children.replaceChildren(...(nodes.length ? nodes : [el("li", { class: "muted" }, "keine Unterordner")]));
      } catch (err) {
        children.replaceChildren(el("li", { class: "error" }, err.message));
      }
    }
  });
  return el("li", {},
    el("div", { class: "row" }, toggle, el("span", { class: "name", title: folder.path || folder.name }, folder.name),
      el("button", { class: "add", onclick: () => addScope(scopeBody) }, "Hinzufügen")),
    children);
}

async function addScope(body) {
  try {
    await api("/api/scopes", { method: "POST", body });
    await renderScopes();
  } catch (err) {
    toast(err.message);
  }
}

async function removeScope(scope) {
  if (!confirm(`„${scope.path}“ aus dem Katalog entfernen?\nDie Dateien selbst bleiben unverändert.`)) return;
  try {
    await api(`/api/scopes?${qs({ item_id: scope.item_id })}`, { method: "DELETE" });
  } catch (err) {
    return toast(err.message);
  }
  await renderScopes();
  await loadSidebar();
  reload();
}

// ---------- Hintergrundaufgaben ----------

$("#sync-btn").addEventListener("click", async () => {
  try {
    await api("/api/sync", { method: "POST" });
    startPolling();
  } catch (err) {
    toast(err.message);
  }
});

let activeJob = null;
$("#job-cancel").addEventListener("click", () => activeJob && api(`/api/jobs/${activeJob}/cancel`, { method: "POST" }));
$("#job-close").addEventListener("click", () => { $("#jobbar").hidden = true; });

function startPolling() {
  if (state.pollTimer) return;
  const tick = async () => {
    try {
      const status = await api("/api/status");
      applyStatus(status);
      const running = handleJobs(status.jobs, false);
      state.pollTimer = running ? setTimeout(tick, 1000) : null;
    } catch {
      state.pollTimer = null;
    }
  };
  state.pollTimer = setTimeout(tick, 300);
}

function handleJobs(jobs, initial) {
  const kinds = ["sync", "enrich", "export"];
  const running = kinds.find((k) => jobs[k].state === "running");
  for (const kind of kinds) {
    const job = jobs[kind];
    const previous = state.lastJobStates[kind];
    state.lastJobStates[kind] = job.state;
    if (!initial && previous === "running" && job.state !== "running") {
      if (kind !== "export") { loadSidebar(); reload(); }
      const text = job.state === "error" ? `Fehler: ${job.message}` : job.message || "Fertig.";
      const errors = job.result?.errors?.length ? ` (${job.result.errors.length} Dateien übersprungen)` : "";
      if (!running) toast(text + errors);
    }
  }
  if (running) {
    activeJob = running;
    const job = jobs[running];
    $("#jobbar").hidden = false;
    $("#job-msg").textContent = job.message || "Läuft …";
    const bar = $("#job-progress");
    bar.hidden = false;
    if (job.total) { bar.max = job.total; bar.value = job.done; } else { bar.removeAttribute("value"); }
    if (job.done) {
      const of = job.total ? ` von ${job.total.toLocaleString("de-DE")}` : "";
      $("#job-msg").textContent = `${job.message} ${job.done.toLocaleString("de-DE")}${of}`;
    }
    $("#job-cancel").hidden = false;
    $("#job-close").hidden = true;
    if (initial) startPolling();
  }
  return Boolean(running);
}

boot().catch((err) => {
  document.body.prepend(el("p", { class: "error", style: "padding:16px" }, `Start fehlgeschlagen: ${err.message}`));
});
