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
const files = (n) => `${n.toLocaleString("de-DE")} ${n === 1 ? "Datei" : "Dateien"}`;
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
  selectMode: false,
  hideCopies: loadSetting("hideCopies", false),
};

// Kleine Vorlieben pro Rechner; ohne Speicher (z. B. privates Fenster) gelten die Vorgaben
function loadSetting(key, fallback) {
  try { const v = localStorage.getItem("bk." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
function saveSetting(key, value) {
  try { localStorage.setItem("bk." + key, JSON.stringify(value)); } catch { /* egal */ }
}

// Datum mit der Genauigkeit, mit der es von Hand gesetzt wurde
function formatTaken(iso, precision) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (precision === "year") return `${d.getFullYear()} (ungefähr)`;
  if (precision === "month") return `${monthName(d.getMonth() + 1)} ${d.getFullYear()} (ungefähr)`;
  if (precision === "day") return d.toLocaleDateString("de-DE", { dateStyle: "medium" });
  return formatDate(iso);
}

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
$("#licenses-btn").addEventListener("click", async () => {
  $("#licenses-dlg").showModal();
  try {
    $("#licenses-text").textContent = await (await fetch("/api/licenses")).text();
  } catch (err) {
    $("#licenses-text").textContent = err.message;
  }
});
$("#licenses-close").addEventListener("click", () => $("#licenses-dlg").close());
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
      startPolling("enrich");
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
  nav.append(el("ul", {},
    el("li", {}, navItem("Alle Bilder", f.total, {}, "Alle Bilder")),
    el("li", {}, navItem("★ Favoriten", f.special.favorites, { favorite: true }, "Favoriten",
      { tooltip: "Bilder mit Stern – Stern in der Kachel, in der Großansicht oder Taste F" }))));

  // Alben
  nav.append(section("Alben", state.albums.length,
    el("ul", {}, state.albums.map((a) => el("li", {}, navItem(a.name, a.n, { album: a.id }, `Album: ${a.name}`)))),
    el("button", { class: "link new-album", onclick: newAlbum }, "+ Neues Album"),
    el("button", { class: "link new-album", onclick: openBackup, title: "Alben, Favoriten, eigene Daten und Orte sichern" }, "Sichern / Wiederherstellen …")));

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
    el("li", {}, navItem("Ohne GPS", f.special.no_gps, { no_gps: true }, "Ohne GPS-Position",
      { tooltip: "Bilder ohne Ort. Auswählen und „Ort …“ ergänzt ihn." })),
    el("li", {}, navItem("Videos", f.special.videos, { media: "video" }, "Videos")),
    f.special.dup_groups ? el("li", {}, navItem("Doppelte Dateien", f.special.dup_copies, { dups: true }, "Doppelte Dateien",
      { tooltip: `${f.special.dup_groups.toLocaleString("de-DE")} Dateien sind mehrfach vorhanden, zusammen ${f.special.dup_copies.toLocaleString("de-DE")} Kopien. ` +
        "Gleich groß und gleiche Aufnahmezeit oder gleicher Name. Gelöscht wird nur, was du selbst auswählst und bestätigst." })) : null,
    f.special.date_manual ? el("li", {}, navItem("Datum von Hand gesetzt", f.special.date_manual, { date_manual: true }, "Datum von Hand gesetzt")) : null,
    f.special.place_manual ? el("li", {}, navItem("Ort von Hand gesetzt", f.special.place_manual, { place_manual: true }, "Ort von Hand gesetzt")) : null,
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
  state.viewStale = false;  // wird ohnehin neu geladen
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
  $("#dup-copies-btn").hidden = !state.filter.dups;
  $("#hide-copies").checked = state.hideCopies;
  $("#hide-copies").disabled = Boolean(state.filter.dups);
  await loadMore();
}

function currentQuery() {
  const search = $("#search").value.trim();
  const unique = state.hideCopies && !state.filter.dups ? true : undefined;
  return { ...state.filter, q: search || undefined, unique };
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
    $("#view-count").textContent = files(data.total);
    state.viewPartial = data.partial;
    $("#view-enrich-btn").hidden = !data.partial;
    $("#view-enrich-btn").textContent = `Details nachladen (${data.partial.toLocaleString("de-DE")})`;
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
    if (state.filter.dups) {
      // Ansicht "Doppelte Dateien": jede Gruppe mit eigener Überschrift, Hauptexemplar zuerst
      if (i === 0 || state.items[i - 1].dup_group !== item.dup_group) {
        fragment.append(el("div", { class: "group-head" }, `${item.name} · ${formatDay(item.taken_at)} · ${formatSize(item.size)}`));
      }
      fragment.append(tile(item, i));
      continue;
    }
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
    item.online_only ? el("span", { class: "cloud", title: "Nur online – OneDrive liefert keine Vorschau (läuft der OneDrive-Client?)" }, "☁") : null,
    el("span", {}, item.name), el("span", {}, formatDay(item.taken_at)))), { once: true });
  const node = el("div", { class: "tile" + (state.selected.has(item.id) ? " selected" : ""), "data-index": index },
    img,
    el("span", { class: "check", role: "checkbox", "aria-label": "Auswählen" }),
    item.is_video ? el("span", { class: "badge" }, "▶ Video") : null,
    state.filter.dups ? el("span", { class: "badge left" + (item.dup_rank ? "" : " main") },
      item.dup_rank ? "Kopie" : "Hauptexemplar") : null,
    el("button", {
      class: "fav" + (item.favorite ? " on" : ""), title: item.favorite ? "Favorit entfernen" : "Als Favorit markieren",
      "aria-label": "Favorit", onclick: (event) => { event.stopPropagation(); toggleFavorite(item); },
    }, item.favorite ? "★" : "☆"),
  );
  if (state.filter.dups) node.title = item.parent_path || "";
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

$("#hide-copies").addEventListener("change", (event) => {
  state.hideCopies = event.target.checked;
  saveSetting("hideCopies", state.hideCopies);
  reload();
});

function updateTile(item) {
  const index = state.items.indexOf(item);
  document.querySelector(`.tile[data-index="${index}"]`)?.replaceWith(tile(item, index));
}

async function toggleFavorite(item) {
  const value = !item.favorite;
  try {
    await api("/api/favorites", { method: "POST", body: { ids: [item.id], value } });
  } catch (err) {
    return toast(err.message);
  }
  item.favorite = value ? 1 : 0;
  updateTile(item);
  // Ein Favorit wird Hauptexemplar – die Duplikat-Ansicht danach neu ordnen
  if (state.filter.dups) { if ($("#lightbox").open) state.reloadOnClose = true; else reload(); }
  if (state.items[state.lightboxIndex] === item && $("#lightbox").open) renderLightbox();
  loadSidebar();
}

$("#sel-fav").addEventListener("click", async () => {
  const ids = [...state.selected];
  // Sind schon alle Favoriten, wird der Stern entfernt
  const value = !state.items.filter((i) => state.selected.has(i.id)).every((i) => i.favorite);
  try {
    const res = await api("/api/favorites", { method: "POST", body: { ids, value } });
    toast(value ? `${files(res.changed)} als Favorit markiert.` : `Stern bei ${files(res.changed)} entfernt.`);
  } catch (err) {
    return toast(err.message);
  }
  for (const item of state.items) if (state.selected.has(item.id)) item.favorite = value ? 1 : 0;
  state.items.forEach((item) => { if (ids.includes(item.id)) updateTile(item); });
  loadSidebar();
});

$("#sel-date").addEventListener("click", () => openDate({ ids: [...state.selected] }, `${state.selected.size} ausgewählte Dateien`));

function openDate(selection, info, current = "") {
  const dlg = $("#date-dlg");
  const form = $("#date-form");
  $("#date-info").textContent = info;
  $("#date-error").textContent = "";
  form.date.value = current;
  form.onsubmit = async (event) => {
    const action = event.submitter?.value;
    if (action === "cancel") return;
    event.preventDefault();
    try {
      const res = action === "reset"
        ? await api("/api/dates/reset", { method: "POST", body: selection })
        : await api("/api/dates", { method: "POST", body: { ...selection, date: form.date.value } });
      dlg.close();
      toast((action === "reset" ? `Automatisches Datum für ${files(res.changed)} wiederhergestellt.`
        : `Datum für ${files(res.changed)} gesetzt.`) + keepHint());
      await afterEdit();
    } catch (err) {
      $("#date-error").textContent = err.message;
    }
  };
  dlg.showModal();
  form.date.focus();
}

// ---------- Ort setzen ----------

$("#sel-place").addEventListener("click", () => openPlace({ ids: [...state.selected] }, `${files(state.selected.size)} ausgewählt`));

function openPlace(selection, info) {
  const dlg = $("#place-dlg");
  let chosen;
  let timer;
  let generation = 0;
  api("/api/places/prepare", { method: "POST" }).catch(() => {});  // Ortsverzeichnis vorab laden
  $("#place-info").textContent = info;
  $("#place-q").value = "";
  $("#place-results").replaceChildren();
  $("#place-error").textContent = "";
  $("#place-save").disabled = true;

  const choose = (place, node) => {
    chosen = place;
    $("#place-results").querySelectorAll("button").forEach((b) => b.classList.toggle("chosen", b === node));
    $("#place-save").disabled = false;
  };
  $("#place-q").oninput = () => {
    clearTimeout(timer);
    chosen = undefined;
    $("#place-save").disabled = true;
    timer = setTimeout(async () => {
      const mine = ++generation;
      const q = $("#place-q").value.trim();
      if (q.length < 2) return $("#place-results").replaceChildren();
      // Die erste Suche nach dem Start lädt das Ortsverzeichnis (einige Sekunden)
      const wait = setTimeout(() => { if (mine === generation) $("#place-results").replaceChildren(el("li", { class: "muted" }, "Suche …")); }, 300);
      try {
        const results = await api(`/api/places/search?${qs({ q })}`);
        clearTimeout(wait);
        if (mine !== generation) return;
        $("#place-results").replaceChildren(...(results.length ? results.map((p) => {
          const label = [p.city, p.region, countryName(p.country_code)].filter(Boolean).join(", ");
          const extra = p.source === "catalog" ? ` · ${p.n.toLocaleString("de-DE")} Bilder im Katalog`
            : p.source === "coords" ? ` · ${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}` : "";
          const button = el("button", { type: "button", onclick: () => choose(p, button), ondblclick: save },
            el("span", {}, label || "Unbekannter Ort"), el("span", { class: "muted" }, extra));
          return el("li", {}, button);
        }) : [el("li", { class: "muted" }, "Nichts gefunden. Größere Orte heißen oft englisch (z. B. „Rome“), es hilft auch der Anfang des Namens.")]));
        if (results.length === 1) choose(results[0], $("#place-results button"));
      } catch (err) {
        clearTimeout(wait);
        $("#place-results").replaceChildren();
        $("#place-error").textContent = err.message;
      }
    }, 250);
  };

  async function send(path, body, message) {
    try {
      const res = await api(path, { method: "POST", body });
      dlg.close();
      toast(message(res.changed) + keepHint());
      await afterEdit();
    } catch (err) {
      $("#place-error").textContent = err.message;
    }
  }
  function save() {
    if (chosen) send("/api/places", { ...selection, place: chosen }, (n) => `Ort für ${files(n)} gesetzt.`);
  }
  $("#place-save").onclick = save;
  $("#place-q").onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); save(); } };
  $("#place-remove").onclick = () => send("/api/places", { ...selection, place: null }, (n) => `Ort bei ${files(n)} entfernt.`);
  $("#place-reset").onclick = () => send("/api/places/reset", selection, (n) => `GPS-Position aus der Datei für ${files(n)} wiederhergestellt.`);
  $("#place-cancel").onclick = () => dlg.close();
  dlg.showModal();
  $("#place-q").focus();
}

// Nach Datum, Ort, Favorit oder Album bleibt die Auswahl erhalten, damit man gleich weitermachen
// kann (z. B. erst Datum, dann Ort). Die Ansicht wird erst nach dem Auswählen neu geladen –
// sonst verschwänden Bilder etwa aus "Ohne GPS", während sie noch ausgewählt sind.
async function afterEdit() {
  loadSidebar();
  if ($("#lightbox").open) return refreshItem(state.items[state.lightboxIndex]);
  if (state.selected.size) state.viewStale = true;
  else reload();
}

const keepHint = () => (state.selected.size && !$("#lightbox").open ? " Die Auswahl bleibt für weitere Schritte erhalten." : "");

async function refreshItem(item) {
  const updated = await api(`/api/photos/${encodeURIComponent(item.id)}`);
  Object.assign(item, updated);
  updateTile(item);
  renderLightbox();
}

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
  $("#grid").classList.toggle("selecting", n > 0 || Boolean(state.selectMode));
  $("#selbar").hidden = n === 0 && !state.selectMode;
  $("#sel-count").textContent = `${n} ausgewählt`;
  $("#sel-hint").hidden = n > 0;
  for (const id of ["#sel-fav", "#sel-album", "#sel-date", "#sel-place", "#sel-delete", "#sel-zip", "#sel-enrich", "#sel-remove", "#sel-clear"]) $(id).disabled = n === 0;
  $("#select-mode-btn").textContent = state.selectMode ? "Auswahl beenden" : "Auswählen";
  $("#select-mode-btn").classList.toggle("primary", Boolean(state.selectMode));
}

function setSelectMode(on) {
  state.selectMode = on;
  if (on) refreshSelection();
  else clearSelection();
}

$("#select-mode-btn").addEventListener("click", () => setSelectMode(!state.selectMode));

function clearSelection() {
  state.selected.clear();
  state.lastIndex = null;
  refreshSelection();
  if (state.viewStale) {  // Änderungen während der Auswahl jetzt anzeigen
    state.viewStale = false;
    reload();
  }
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
  `${state.viewPartial.toLocaleString("de-DE")} Bilder in „${state.title}“ liegen nur online. Ort und Kamera werden nachgeladen.`));
$("#sel-enrich").addEventListener("click", () => {
  const ids = [...state.selected];
  const partial = state.items.filter((i) => state.selected.has(i.id) && i.meta_level === "partial").length;
  if (!partial) return toast("Für die ausgewählten Bilder sind die Details schon vorhanden.");
  openEnrich({ ids }, `${partial} der ausgewählten Bilder liegen nur online. Ort und Kamera werden nachgeladen.`);
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
      toast(`${files(res.added)} zum Album hinzugefügt.` + keepHint());
      await loadSidebar();
    } catch (err) {
      toast(err.message);
    }
  };
  dlg.showModal();
}

// ---------- Löschen (Papierkorb) ----------

$("#sel-delete").addEventListener("click", () => openDelete([...state.selected]));

$("#dup-copies-btn").addEventListener("click", async () => {
  // alle Kopien der Duplikat-Ansicht, auch die noch nicht geladenen
  const ids = [];
  for (let offset = 0; ; offset += 1000) {
    const page = await api(`/api/photos?${qs({ dups: true, limit: 1000, offset })}`);
    page.items.filter((i) => i.dup_rank > 0).forEach((i) => ids.push(i.id));
    if (page.items.length < 1000) break;
  }
  setSelectMode(true);
  ids.forEach((id) => state.selected.add(id));
  refreshSelection();
  toast(`${files(ids.length)} ausgewählt – nur Kopien, die Hauptexemplare nicht.`);
});

async function openDelete(ids) {
  let info;
  try {
    info = await api("/api/delete/preview", { method: "POST", body: { ids } });
  } catch (err) {
    return toast(err.message);
  }
  if (!info.count) return toast("Löschen geht nur bei Dateien aus lokal erfassten Ordnern.");
  const dlg = $("#delete-dlg");
  $("#delete-summary").textContent = `${files(info.count)} (${formatSize(info.bytes)}) werden in den Papierkorb verschoben.`;
  $("#delete-examples").replaceChildren(...[
    ...info.examples.map((e) => el("li", {}, `${e.folder}/${e.name}`)),
    info.count > info.examples.length ? el("li", { class: "muted" }, `… und ${files(info.count - info.examples.length)} mehr`) : null,
  ].filter(Boolean));  // replaceChildren würde null als Text einfügen
  const warnings = [
    info.onedrive ? `${files(info.onedrive)} liegen in OneDrive und werden dadurch auch in der Cloud und auf allen anderen Geräten gelöscht. ` +
      "Wiederherstellen kannst du sie aus dem Papierkorb von Windows oder bis zu 30 Tage lang aus dem OneDrive-Papierkorb im Browser." : null,
    info.favorites ? `${files(info.favorites)} davon sind Favoriten.` : null,
    info.in_album ? `${files(info.in_album)} davon liegen in Alben.` : null,
    info.not_local ? `${files(info.not_local)} aus dem Online-Zugang werden nicht gelöscht.` : null,
  ].filter(Boolean);
  $("#delete-warnings").replaceChildren(...warnings.map((w) => el("p", {}, w)));
  $("#delete-confirm-text").textContent = `Ja, ${info.count === 1 ? "diese Datei" : `diese ${files(info.count)}`} löschen.`;
  const check = $("#delete-confirm");
  check.checked = false;
  $("#delete-ok").disabled = true;
  check.onchange = () => { $("#delete-ok").disabled = !check.checked; };
  $("#delete-cancel").onclick = () => dlg.close();
  $("#delete-ok").onclick = async () => {
    $("#delete-ok").disabled = true;
    try {
      await api("/api/delete", { method: "POST", body: { ids, confirm_count: info.count } });
      dlg.close();
      if ($("#lightbox").open) $("#lightbox").close();
      setSelectMode(false);
      startPolling("delete");
    } catch (err) {
      dlg.close();
      toast(err.message);
    }
  };
  dlg.showModal();
  $("#delete-cancel").focus();  // Enter bricht ab, löscht nie versehentlich
}

// ---------- Sicherung ----------

async function openBackup() {
  const form = $("#backup-form");
  form.target_dir.value = form.target_dir.value || state.exportDir || "";
  $("#backup-msg").textContent = "";
  $("#restore-msg").textContent = "";
  $("#restore-file").value = "";
  $("#backup-dlg").showModal();
  const list = $("#auto-backups");
  list.replaceChildren(el("li", { class: "muted" }, "Lade …"));
  try {
    const data = await api("/api/backups");
    list.replaceChildren(...(data.auto.length ? data.auto.map((b) => el("li", {},
      el("span", {}, formatDate(b.created), el("span", { class: "muted" },
        `  · ${b.albums} Alben, ${b.favorites} Favoriten, ${b.dates} Daten, ${b.places} Orte`)),
      el("button", { class: "link", onclick: () => restoreBackup({ auto: b.file }) }, "Wiederherstellen"))) :
      [el("li", { class: "muted" }, "Noch keine.")]));
  } catch (err) {
    list.replaceChildren(el("li", { class: "error" }, err.message));
  }
}

$("#backup-close").addEventListener("click", () => $("#backup-dlg").close());
$("#backup-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const r = await api("/api/backup", { method: "POST", body: { target_dir: event.target.target_dir.value || null } });
    $("#backup-msg").textContent = `Gespeichert (${r.albums} Alben, ${r.favorites} Favoriten, ${r.dates} Daten, ${r.places} Orte): ${r.path}`;
  } catch (err) {
    $("#backup-msg").textContent = err.message;
  }
});
$("#restore-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  let data;
  try {
    data = JSON.parse(await file.text());
  } catch {
    $("#restore-msg").textContent = "Die Datei ist keine gültige Sicherung.";
    return;
  }
  restoreBackup({ data });
});

async function restoreBackup(body) {
  const msg = $("#restore-msg");
  msg.textContent = "Spiele Sicherung ein …";
  try {
    const r = await api("/api/restore", { method: "POST", body });
    msg.textContent = `Fertig: ${r.albums_new} neue Alben, ${r.assigned} Albumzuordnungen, ${r.favorites} Favoriten, ${r.dates} Daten, ${r.places} Orte.` +
      (r.not_found ? ` ${r.not_found} Bilder wurden im Katalog nicht gefunden (Ordner noch nicht eingelesen?).` : "");
    await loadSidebar();
    reload();
  } catch (err) {
    msg.textContent = err.message;
  }
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
      startPolling("export");
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
  const sourceLabel = { exif: "Kamera (EXIF)", filename: "aus Dateiname", file: "Dateizeit", manual: "von Hand gesetzt" }[detail.date_source] || "–";
  const rows = [
    ["Datei", detail.name],
    ["Aufgenommen", formatTaken(detail.taken_at, detail.date_precision)],
    ["Datumsquelle", sourceLabel],
    ["Ort", (detail.country_code ? place : "–") + (detail.place_manual ? " (von Hand)" : "")],
    ["GPS", detail.lat != null ? `${detail.lat.toFixed(5)}, ${detail.lon.toFixed(5)}` : "–"],
    ["Kamera", (detail.camera_model?.startsWith(detail.camera_make || "\u0000") ? detail.camera_model
      : [detail.camera_make, detail.camera_model].filter(Boolean).join(" ")) || "–"],
    ["Größe", `${detail.width && detail.height ? `${detail.width} × ${detail.height} · ` : ""}${formatSize(detail.size)}`],
    ["Ordner", detail.parent_path || "–"],
    ["Alben", detail.albums.map((a) => a.name).join(", ") || "–"],
  ];
  const copies = detail.copies.length ? el("div", { class: "copies" },
    el("p", {}, `${detail.copies.length} Exemplare dieser Datei:`),
    el("ul", {}, detail.copies.map((c) => el("li", { class: c.id === detail.id ? "current" : null },
      el("span", { title: `${c.parent_path}/${c.name}` }, `${c.dup_rank ? "Kopie" : "Hauptexemplar"}: ${c.parent_path}`),
      el("button", { class: "link", title: "Im Explorer zeigen",
        onclick: () => api(`/api/photos/${encodeURIComponent(c.id)}/reveal`, { method: "POST" }).catch((e) => toast(e.message)) }, "Zeigen")))),
    el("p", { class: "muted" }, "Als Hauptexemplar gilt bevorzugt ein Favorit oder Albumbild, nicht eines aus Kopie- oder WhatsApp-Ordnern.")) : null;
  const dateText = detail.date_source === "manual" ? formatTaken(detail.taken_at, detail.date_precision).replace(" (ungefähr)", "") : "";
  $("#lb-meta").replaceChildren(...[
    el("p", {}, el("button", { class: "fav-big" + (detail.favorite ? " on" : ""), onclick: () => toggleFavorite(item), title: "Taste F" },
      detail.favorite ? "★ Favorit" : "☆ Als Favorit markieren")),
    el("dl", {}, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
    detail.meta_level === "partial" ? el("p", {}, "Nur online: Ort und Kamera fehlen noch. ",
      el("button", { onclick: (e) => enrichOne(item, e.currentTarget) }, "Details laden")) : null,
    copies,
    detail.source === "local"
      ? el("p", {}, el("button", { onclick: () => api(`/api/photos/${encodeURIComponent(item.id)}/reveal`, { method: "POST" }).catch((e) => toast(e.message)) }, "Im Explorer zeigen"))
      : detail.web_url ? el("a", { href: detail.web_url, target: "_blank", rel: "noopener" }, "In OneDrive öffnen ↗") : null,
    el("p", {}, el("button", { onclick: () => { $("#lightbox").close(); chooseAlbum({ ids: [item.id] }); } }, "Zu Album …"), " ",
      el("button", { onclick: () => openDate({ ids: [item.id] }, item.name, dateText) }, "Datum ändern …"), " ",
      el("button", { onclick: () => openPlace({ ids: [item.id] }, item.name) }, detail.country_code ? "Ort ändern …" : "Ort hinzufügen …")),
    detail.source === "local" ? el("p", {}, el("button", { class: "danger", onclick: () => openDelete([item.id]) }, "Löschen …")) : null,
  ].filter(Boolean));  // replaceChildren würde null als Text "null" einfügen
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
    button.textContent = "Details laden";
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
$("#lightbox").addEventListener("close", () => {
  if (state.reloadOnClose) { state.reloadOnClose = false; reload(); }
});
$("#lightbox").addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") stepLightbox(-1);
  if (event.key === "ArrowRight") stepLightbox(1);
  if (event.key.toLowerCase() === "f" && !event.ctrlKey && !event.altKey && state.items[state.lightboxIndex]) {
    toggleFavorite(state.items[state.lightboxIndex]);
  }
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
    startPolling("sync");
  } catch (err) {
    toast(err.message);
  }
});

let activeJob = null;
$("#job-cancel").addEventListener("click", () => activeJob && api(`/api/jobs/${activeJob}/cancel`, { method: "POST" }));
$("#job-close").addEventListener("click", () => { $("#jobbar").hidden = true; });

// started: gerade gestarteter Job – auch wenn er schon vor der ersten Abfrage fertig ist,
// soll die Oberfläche das Ende bemerken (Ansicht neu laden, Meldung zeigen)
function startPolling(started) {
  if (started) state.lastJobStates[started] = "running";
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
  const kinds = ["sync", "enrich", "export", "delete"];
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
