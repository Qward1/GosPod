/* Контакт-центр — Господдержка СВО. SPA без сборки и внешних зависимостей. */
"use strict";

function appBaseFromPath(pathname) {
  let base = pathname.replace(/\/+$/, "");
  base = base.replace(/\/(index|login)\.html$/, "");
  base = base.replace(/\/usvo\/cards\/\d+$/, "");
  base = base.replace(/(\/application)+$/, "/application");
  return base;
}

const APP_BASE = appBaseFromPath(window.location.pathname);
const API = `${APP_BASE}/api/web`;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  meta: null,
  user: null,
  isAdmin: false,
  operator: localStorage.getItem("op") || "",
  view: "appeals",
  appeals: [],
  usvo: [],
  applications: [],
  measures: [],
  aiChats: [],
  aiMessages: [],
  activeAiChat: null,
  aiAnswerTypes: [],
  aiReady: false,
  appsTab: "list",
  activeUsvo: null,
  appealsSort: "newest",
};

function gotoLogin() {
  location.replace(`${APP_BASE}/login.html`);
}

/* ---------- утилиты ---------- */
async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...opts,
  });
  if (res.status === 401) { gotoLogin(); throw new Error("Требуется авторизация"); }
  if (!res.ok) {
    let msg = "Ошибка " + res.status;
    try { msg = (await res.json()).detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, kind = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3200);
}

function spinnerBtnHtml(text) {
  return `<span class="spinner"></span>${text}`;
}

/* Подтверждение действия — своя модалка в тон интерфейса (замена нативного confirm).
   Возвращает Promise<boolean>. По умолчанию — «опасное» (красное) подтверждение удаления. */
function confirmDialog(message, opts = {}) {
  const {
    title = "Подтвердите действие",
    confirmText = "Удалить",
    cancelText = "Отмена",
    danger = true,
    iconName = "trash",
  } = opts;
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "confirm-overlay";
    overlay.innerHTML = `
      <div class="confirm-box glass" role="alertdialog" aria-modal="true">
        <div class="confirm-box__ico${danger ? "" : " confirm-box__ico--info"}">${icon(iconName, 24)}</div>
        <h3>${esc(title)}</h3>
        <p>${esc(message)}</p>
        <div class="confirm-box__actions">
          <button class="btn btn--soft" data-act="cancel">${esc(cancelText)}</button>
          <button class="btn ${danger ? "btn--danger" : "btn--primary"}" data-act="ok">${esc(confirmText)}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("show"));

    let done = false;
    const close = (val) => {
      if (done) return;
      done = true;
      overlay.classList.remove("show");
      document.removeEventListener("keydown", onKey);
      setTimeout(() => overlay.remove(), 220);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); close(false); }
      else if (e.key === "Enter") { e.preventDefault(); close(true); }
    };
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(false); });
    overlay.addEventListener("click", (e) => {
      const act = e.target.closest("[data-act]");
      if (act) close(act.dataset.act === "ok");
    });
    document.addEventListener("keydown", onKey);
    const okBtn = overlay.querySelector('[data-act="ok"]');
    if (okBtn) okBtn.focus();
  });
}

function loadingBlock(text = "Загрузка…") {
  return `<div class="loading-block"><span class="spinner"></span><span>${esc(text)}</span></div>`;
}

const ICONS = {
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
  wand: '<path d="M15 4V2M15 10V8M12 5h2M16 5h2M6 20l12-12-4-4L2 16z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4 20-7z"/><path d="M22 2 11 13"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  card: '<path d="M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  work: '<path d="M20 7h-4V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zM10 5h4v2h-4z"/>',
  edu: '<path d="M22 10 12 5 2 10l10 5 10-5zM6 12v5c0 1 3 3 6 3s6-2 6-3v-5"/>',
  status: '<path d="M12 2 4 6v6c0 5 3.4 7.7 8 10 4.6-2.3 8-5 8-10V6z"/>',
  med: '<path d="M12 5v14M5 12h14"/>',
  family: '<circle cx="9" cy="7" r="3"/><circle cx="17" cy="9" r="2"/><path d="M3 20v-1a5 5 0 0 1 9-3M14 20v-1a4 4 0 0 1 7-2.6"/>',
  org: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9"/>',
  contact: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.6A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h6"/>',
  building: '<path d="M3 21h18"/><path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16"/><path d="M19 21V11a1 1 0 0 0-1-1h-3"/><path d="M9 8h2M9 12h2M9 16h2"/>',
  visit: '<path d="M3 21h18"/><path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16"/><path d="M19 21V11a1 1 0 0 0-1-1h-3"/><path d="M9 8h2M9 12h2M9 16h2"/>',
  appeal: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  chat: '<path d="M12 3a7 7 0 0 0-7 7v1a4 4 0 0 0-2 3.5A3.5 3.5 0 0 0 6.5 18H8l4 3v-3h5.5a3.5 3.5 0 0 0 3.5-3.5A4 4 0 0 0 19 11v-1a7 7 0 0 0-7-7z"/><path d="M9 10h.01M12 10h.01M15 10h.01"/>',
  pin: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  award: '<circle cx="12" cy="8" r="6"/><path d="M8.2 13.9 7 22l5-3 5 3-1.2-8.1"/>',
  gauge: '<path d="M12 14 16 10"/><path d="M3.4 18a9 9 0 1 1 17.2 0"/><circle cx="12" cy="14" r="1.5"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/>',
  filter: '<path d="M4 5h16M7 12h10M10 19h4"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
function icon(name, size = 18) {
  const p = ICONS[name] || ICONS.info;
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}
function suggIcon(kind) {
  return icon(kind, 20);
}

/* ---------- тема ---------- */
function applyTheme(t) {
  document.documentElement.dataset.theme = t;
  const label = $("#theme-toggle-label");
  if (label) label.textContent = t === "dark" ? "Светлая тема" : "Тёмная тема";
}
function initTheme() {
  applyTheme(document.documentElement.dataset.theme || "light");
  const btn = $("#theme-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("theme", next); } catch (_) {}
  });
}

/* ---------- помощники карточки ---------- */
function ageFrom(birth) {
  const m = /(\d{1,2})\.(\d{1,2})\.(\d{4})/.exec(birth || "");
  if (!m) return null;
  const b = new Date(+m[3], +m[2] - 1, +m[1]);
  if (isNaN(b.getTime())) return null;
  const now = new Date();
  let a = now.getFullYear() - b.getFullYear();
  const md = now.getMonth() - b.getMonth();
  if (md < 0 || (md === 0 && now.getDate() < b.getDate())) a--;
  return a >= 0 && a < 120 ? a : null;
}
function yearsWord(n) {
  const a = Math.abs(n) % 100, b = n % 10;
  if (a > 10 && a < 20) return "лет";
  if (b === 1) return "год";
  if (b >= 2 && b <= 4) return "года";
  return "лет";
}

// Группировка полей карточки по смыслу — для компактных аккордеонов.
const FIELD_GROUPS = [
  { title: "Личные данные", icon: "contact", keys: ["дата рождения", "семейное положение", "телефон", "адрес регистрац", "адрес факт"] },
  { title: "Семья", icon: "family", keys: ["родственник", "жена", "супруг", "дети", "контакты"] },
  { title: "Статус и здоровье", icon: "status", keys: ["статус", "инвалид", "состояни", "здоров", "примечание", "ик/", "чвк", "сизо", "ветеран боевых"] },
  { title: "Образование и работа", icon: "work", keys: ["образование", "специальн", "профессия", "трудоустр", "место работы", "должност", "переподготов", "квалификац", "тер.", "прежнее место"] },
  { title: "Организации и поддержка", icon: "org", keys: ["время героя", "герои подмосков", "ассоциация", "мерах поддержки", "ответственн", "должностное лицо"] },
];

// Каноничный список полей карточки: дедупликация по названию + гарантированные
// ключевые поля шапки (ФИО, статус, телефон…), даже если их нет среди полей. Тот
// же список лежит в основе инлайн-редактирования — значения правятся прямо в
// аккордеонах, а на сохранение уходит полный набор, поэтому ничего не теряется.
function usvoCanonFields(r) {
  const all = [...(r.primary || []), ...(r.secondary || []), ...(r.extra || [])];
  const seen = new Set();
  const fields = [];
  for (const f of all) {
    const key = (f.label || "").toLowerCase().trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    fields.push({ label: f.label || "", value: f.value || "" });
  }
  const hasKey = (needles) =>
    fields.some((f) => needles.some((n) => f.label.toLowerCase().includes(n)));
  const ensure = (needles, label, value) => {
    if (value && !hasKey(needles)) fields.unshift({ label, value });
  };
  ensure(["награды"], "Награды", r.awards);
  ensure(["дата обзвона"], "Дата обзвона", r.call_date);
  ensure(["адрес"], "Адрес регистрации", r.address);
  ensure(["телефон", "контакт"], "Телефон", r.phone);
  if (r.status && !fields.some((f) => {
    const l = f.label.toLowerCase();
    return l.includes("статус") && !l.includes("примечание");
  })) fields.unshift({ label: "Статус", value: r.status });
  ensure(["дата рождения"], "Дата рождения", r.birth_date);
  ensure(["фио", "ф.и.о", "участник"], "ФИО УСВО", r.name);
  return fields;
}

function groupFields(r) {
  const fields = usvoCanonFields(r);
  const buckets = FIELD_GROUPS.map((g) => ({ title: g.title, icon: g.icon, keys: g.keys, items: [] }));
  const other = [];
  for (const f of fields) {
    const ll = f.label.toLowerCase();
    let placed = false;
    for (const b of buckets) {
      if (b.keys.some((k) => ll.includes(k))) { b.items.push(f); placed = true; break; }
    }
    if (!placed) other.push(f);
  }
  const groups = buckets.filter((b) => b.items.length);
  // «Дополнительно» показываем всегда — это дом для несгруппированных и новых
  // (добавленных при инлайн-правке) полей.
  groups.push({ title: "Дополнительно", icon: "info", items: other });
  return groups;
}

function healthNote(r) {
  for (const f of [...(r.primary || []), ...(r.secondary || [])]) {
    if (/(инвалид|ампутац|увеч|тяжел)/i.test(f.value || "")) return f.value;
  }
  return "";
}

function statPills(r, age) {
  const flags = r.flags || {};
  const orgCovered = flags.org_vremya || flags.org_geroi_mo || flags.org_assoc;
  const medalCount = window.Medals ? window.Medals.resolve(r.awards).length : 0;
  const pills = [];
  if (age != null) pills.push({ v: age, l: yearsWord(age), cls: "" });
  pills.push(flags.vbd
    ? { v: "✓", l: "статус ВБД", cls: "stat-pill--ok" }
    : { v: "—", l: "ВБД не оформлен", cls: "stat-pill--warn" });
  pills.push(flags.unemployed
    ? { v: "!", l: "нужна работа", cls: "stat-pill--warn" }
    : { v: "✓", l: "трудоустроен", cls: "stat-pill--ok" });
  pills.push(orgCovered
    ? { v: "✓", l: "в организациях", cls: "stat-pill--ok" }
    : { v: "—", l: "вне организаций", cls: "" });
  pills.push(flags.stale_contact
    ? { v: "!", l: "давно без связи", cls: "stat-pill--danger" }
    : { v: "✓", l: "связь актуальна", cls: "stat-pill--ok" });
  pills.push({ v: medalCount, l: "наград", cls: medalCount ? "stat-pill--ok" : "" });
  return `<div class="stat-pills">${pills.map((p) =>
    `<div class="stat-pill ${p.cls}"><div class="stat-pill__val">${esc(String(p.v))}</div><div class="stat-pill__lbl">${esc(p.l)}</div></div>`).join("")}</div>`;
}

function renderMedals(awards) {
  const list = window.Medals ? window.Medals.resolve(awards) : [];
  if (!list.length) return "";
  return `<div class="block">
    <div class="block__head"><h3>Награды</h3><span class="block__hint">${list.length}</span></div>
    <div class="medals">${list.map((m) =>
      `<div class="medal"><div class="medal__img">${m.html}</div><div class="medal__name">${esc(m.name)}</div></div>`).join("")}</div>
  </div>`;
}

function renderMap(address) {
  if (!address) return "";
  const q = encodeURIComponent(address);
  const ya = `https://yandex.ru/maps/?text=${q}`;
  const gis = `https://2gis.ru/search/${q}`;
  return `<div class="block">
    <div class="block__head"><h3>Адрес и карта</h3></div>
    <a class="map-card" href="${ya}" target="_blank" rel="noopener">
      <div class="map-card__canvas"><span class="map-card__pin">${icon("pin", 30)}</span></div>
      <div class="map-card__bar">${icon("pin", 16)}<span>${esc(address)}</span><span class="map-card__open">Открыть ↗</span></div>
    </a>
    <div class="map-links">
      <a class="btn btn--soft" href="${ya}" target="_blank" rel="noopener">Яндекс.Карты</a>
      <a class="btn btn--soft" href="${gis}" target="_blank" rel="noopener">2ГИС</a>
    </div>
  </div>`;
}

function tlKindClass(status) {
  if (status === "выполнено") return "tl--done";
  if (status === "запланировано") return "tl--planned";
  return "tl--progress";
}
function tlStatusClass(status) {
  if (status === "выполнено") return "done";
  if (status === "запланировано") return "planned";
  return "progress";
}
// Акцент точки события: явный style из нормализованной истории, иначе по статусу.
function tlStyleClass(e) {
  const allowed = ["ok", "accent", "planned", "warn", "danger", "info"];
  if (e.style && allowed.includes(e.style)) return e.style;
  if (e.status === "выполнено") return "ok";
  if (e.status === "запланировано") return "planned";
  return "accent";
}
function renderTimeline(history) {
  if (!history || !history.length) return `<p class="muted">История пока пуста</p>`;
  return `<div class="timeline">${history.map((e) => `
    <div class="tl-item ${tlKindClass(e.status)} tl-style--${tlStyleClass(e)}">
      <div class="tl-item__dot">${icon(e.kind || "info", 15)}</div>
      <div class="tl-item__card">
        <div class="tl-item__top"><h4>${esc(e.title)}</h4><span class="tl-status tl-status--${tlStatusClass(e.status)}">${esc(e.status)}</span></div>
        ${e.detail ? `<p class="tl-item__detail">${esc(e.detail)}</p>` : ""}
        <div class="tl-item__meta">${e.date ? `<span class="date">${esc(e.date)}</span>` : ""}${e.org ? `<span class="org">${icon("building", 13)}${esc(e.org)}</span>` : ""}</div>
      </div>
    </div>`).join("")}</div>`;
}

function suggSkeleton() {
  return `<div class="skeleton sk-card"></div><div class="skeleton sk-card"></div><div class="skeleton sk-card"></div>`;
}

/* ---------- модалка ---------- */
function openModal(html) {
  $("#modal-body").innerHTML = html;
  $("#modal").hidden = false;
}
function closeModal() { $("#modal").hidden = true; }
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

/* ---------- offcanvas (боковая панель) ----------
   Карточка обращения/заявления выезжает справа на 60%, список остаётся слева —
   оператор переключается между задачами, не теряя контекст. */
const drawerHooks = { keydown: null };
function drawerOpen() { return !$("#drawer").hidden; }

function openDrawer({ title, subtitle = "", body = "", footer = "", onKeydown = null }) {
  $("#drawer-title").innerHTML = title;
  $("#drawer-subtitle").innerHTML = subtitle;
  $("#drawer-subtitle").hidden = !subtitle;
  $("#drawer-body").innerHTML = body;
  $("#drawer-foot").innerHTML = footer;
  $("#drawer-foot").hidden = !footer;
  const ov = $("#drawer-overlay"), dr = $("#drawer");
  ov.hidden = false; dr.hidden = false;
  // следующий кадр — запускаем переход (slide-in + затемнение)
  requestAnimationFrame(() => { ov.classList.add("open"); dr.classList.add("open"); });
  drawerHooks.keydown = typeof onKeydown === "function" ? onKeydown : null;
}
function closeDrawer() {
  const ov = $("#drawer-overlay"), dr = $("#drawer");
  if ($("#drawer").hidden) return;
  ov.classList.remove("open"); dr.classList.remove("open");
  drawerHooks.keydown = null;
  setTimeout(() => { ov.hidden = true; dr.hidden = true; $("#drawer-body").innerHTML = ""; }, 360);
}
$("#drawer-close").addEventListener("click", closeDrawer);
$("#drawer-overlay").addEventListener("click", closeDrawer);

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); closeDrawer(); return; }
  if (drawerOpen() && drawerHooks.keydown) drawerHooks.keydown(e);
});

/* ---------- скелетоны загрузки ---------- */
function tableSkeleton(rows = 6) {
  const row = `<div class="sk-row">
    <span class="skeleton sk-pill" style="width:88px"></span>
    <span class="skeleton sk-line" style="width:85%"></span>
    <span class="skeleton sk-line"></span>
    <span class="skeleton sk-pill"></span>
    <span class="skeleton sk-line" style="width:70%"></span>
    <span class="skeleton sk-pill"></span>
  </div>`;
  return `<div class="card appeals-card">${row.repeat(rows)}</div>`;
}
function listSkeleton(rows = 7) {
  const item = `<div class="sk-listitem">
    <span class="skeleton sk-ava"></span>
    <span class="sk-lines"><span class="skeleton sk-line" style="width:70%"></span>
    <span class="skeleton sk-line" style="width:45%"></span></span>
  </div>`;
  return item.repeat(rows);
}

/* ---------- тональность обращения (смайл-индикатор) ---------- */
function sentiTone(s) { return (s && s.tone) || "info"; }
function sentiDot(s) {
  if (!s) return "";
  return `<span class="senti-dot senti-tone--${sentiTone(s)}" title="Тональность обращения: ${esc(s.label)}">
    <span class="senti-dot__mark">${s.emoji}</span>
    <small>${esc(s.label)}</small></span>`;
}
function sentiChip(s) {
  if (!s) return "";
  return `<span class="senti senti--${sentiTone(s)}"><span class="senti__emoji">${s.emoji}</span>${esc(s.label)}</span>`;
}

/* ============================================================
   РАЗДЕЛ 1 — ОБРАЩЕНИЯ (таск-трекер)
   ============================================================ */
function statusPill(status) {
  return status === "open"
    ? `<span class="pill pill--warn"><span class="pill__dot"></span>На рассмотрении</span>`
    : `<span class="pill pill--ok"><span class="pill__dot"></span>Отвечено</span>`;
}

/* Сортировка списка обращений (в т.ч. по просроченности и возрасту — SLA). */
const APPEAL_SORTS = {
  newest: { label: "Сначала новые", fn: (a, b) => (b.created_at || 0) - (a.created_at || 0) },
  overdue: {
    label: "Сначала просроченные",
    fn: (a, b) => (b.is_overdue - a.is_overdue) || ((b.age_days || 0) - (a.age_days || 0)),
  },
  oldest: { label: "Сначала старые (по возрасту)", fn: (a, b) => (b.age_days || 0) - (a.age_days || 0) },
};

/* Одна строка списка обращений. */
function appealRowHtml(a) {
  return `
    <tr data-id="${a.id}" class="${a.is_overdue ? "row--overdue" : ""}">
      <td class="senti-cell">${sentiDot(a.sentiment)}</td>
      <td class="q">${esc(a.question)}<small>${esc(a.summary || a.citizen.name || "Гражданин")}</small></td>
      <td>${esc(a.created_human)}${a.age ? `<small class="age-sub">возраст: ${esc(a.age)}</small>` : ""}</td>
      <td><span class="pill pill--info">${esc(a.topic)}</span></td>
      <td>${a.assignee
        ? `<span class="assignee-tag"><span class="dot"></span>${esc(a.assignee)}</span>`
        : '<span class="muted">не назначен</span>'}</td>
      <td>${a.is_overdue ? `<span class="pill pill--danger" title="Срок обработки истёк (регламент ${state.meta?.sla_business_days || 3} дн.)"><span class="pill__dot"></span>Просрочено</span> ` : ""}${statusPill(a.status)}</td>
    </tr>`;
}

/* Сигнатура значимого состояния списка — чтобы тихое автообновление не трогало
   DOM (и не «дёргало» интерфейс), пока реально ничего не изменилось. */
function appealsSignature(items, sort) {
  return sort + "|" + items.map((a) =>
    `${a.id}:${a.status}:${a.assignee || ""}:${a.is_overdue ? 1 : 0}`).join(",");
}

function bindAppealRows() {
  $$("#view-root tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => openAppeal(tr.dataset.id)));
}

/* `opts.silent` — фоновое автообновление: без скелетона-заглушки и без перестройки
   шапки; DOM меняется только если данные реально изменились (мягкая замена строк
   таблицы). Это убирает периодическое «дёрганье» списка каждые 15 секунд. */
async function renderAppeals(opts = {}) {
  const silent = opts && opts.silent === true;
  const root = $("#view-root");
  const sort = state.appealsSort && APPEAL_SORTS[state.appealsSort] ? state.appealsSort : "newest";
  if (!silent) {
    root.innerHTML = tableSkeleton();
    setTitle("Обращения", "Вопросы граждан, распределённые на оператора");
    const sortOpts = Object.entries(APPEAL_SORTS)
      .map(([k, v]) => `<option value="${k}" ${k === sort ? "selected" : ""}>${esc(v.label)}</option>`).join("");
    $("#topbar-actions").innerHTML = `
      <label class="topbar-sort"><span>Сортировка</span>
        <select id="appeals-sort">${sortOpts}</select></label>
      ${state.isAdmin ? `<button class="btn btn--soft" id="appeals-notify">${icon("send")}<span>Оповестить</span></button>` : ""}
      <a class="btn btn--soft" href="${API}/export/appeals" download>${icon("send")}<span>Экспорт</span></a>
      <button class="btn btn--ghost" id="reload-appeals">${icon("refresh")}<span>Обновить</span></button>`;
    $("#reload-appeals").onclick = () => renderAppeals();
    $("#appeals-sort").onchange = (e) => { state.appealsSort = e.target.value; renderAppeals(); };
    const notifyBtn = $("#appeals-notify");
    if (notifyBtn) notifyBtn.onclick = openBroadcastModal;
  }

  const { items } = await api("/appeals");
  state.appeals = items;
  const open = items.filter((a) => a.status === "open").length;
  const overdue = items.filter((a) => a.is_overdue).length;
  $("#nav-appeals-count").textContent = open || "";
  setTitle("Обращения", overdue
    ? `Вопросы граждан · просрочено: ${overdue} (регламент ${state.meta?.sla_business_days || 3} дн.)`
    : "Вопросы граждан, распределённые на оператора");

  if (!items.length) {
    if (silent && !$("#view-root .appeals-table")) return; // уже пусто — не трогаем DOM
    root.innerHTML = emptyState("Все обращения обработаны, ИИ отдыхает",
      "Новые вопросы граждан из бота MAX появятся здесь автоматически.", "robot");
    state._appealsSig = "";
    return;
  }

  const sig = appealsSignature(items, sort);
  const tbody = $("#view-root .appeals-table tbody");
  if (silent && tbody && sig === state._appealsSig) return; // ничего не изменилось

  const rowsHtml = [...items].sort(APPEAL_SORTS[sort].fn).map(appealRowHtml).join("");
  if (silent && tbody) {
    tbody.innerHTML = rowsHtml; // мягкая замена строк — раскладка/прокрутка сохраняются
  } else {
    root.innerHTML = `
      <div class="fade-in card appeals-card">
        <table class="appeals-table">
          <thead><tr>
            <th>Тон</th><th>Вопрос · суть</th><th>Дата · возраст</th><th>Тематика</th><th>Ответственный</th><th>Статус</th>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>`;
  }
  bindAppealRows();
  state._appealsSig = sig;
}

function confLabel(c) {
  if (c >= 0.75) return "высокая";
  if (c >= 0.5) return "средняя";
  if (c >= 0.3) return "ниже средней";
  return "низкая — проверьте вручную";
}
function renderConfidence(c) {
  const pct = Math.round((c || 0) * 100);
  const box = $("#ap-conf");
  if (!box) return;
  box.hidden = false;
  box.innerHTML = `
    <div class="conf__head">${icon("gauge", 15)}<span>Уверенность ИИ в черновике</span>
      <span class="ai-tag">${confLabel(c)}</span></div>
    <div class="conf__bar"><div class="conf__fill" id="ap-conf-fill"></div></div>
    <div class="conf__legend"><span>0%</span><span class="conf__val" id="ap-conf-val">0%</span><span>100%</span></div>`;
  // анимируем заполнение от 0 к целевому значению
  requestAnimationFrame(() => {
    const fill = $("#ap-conf-fill");
    if (fill) fill.style.width = pct + "%";
    let n = 0;
    const t = setInterval(() => {
      n += Math.max(1, Math.round(pct / 22));
      if (n >= pct) { n = pct; clearInterval(t); }
      const v = $("#ap-conf-val"); if (v) v.textContent = n + "%"; else clearInterval(t);
    }, 32);
  });
}

const btnLabel = (ico, text, kbd = "") =>
  `${icon(ico)}<span>${text}${kbd ? `<span class="kbd">${kbd}</span>` : ""}</span>`;

/* Связь обращения из MAX с карточкой(ами) УСВО по номеру телефона (задание 7).
   Один матч — ссылка в подвале карточки; несколько — показываем список, чтобы
   оператор выбрал нужную (случайно связь не выбираем). */
function usvoLinkBlock(a) {
  const matches = a.usvo_matches || [];
  if (a.usvo_ambiguous && matches.length > 1) {
    // Неоднозначность может возникнуть и по телефону, и по совпадению ФИО —
    // подписываем честно, по чему найдены совпадения.
    const byName = a.link_by === "name";
    const crit = byName ? "с совпадающим ФИО" : "с этим номером телефона";
    const list = matches.map((m) =>
      `<button type="button" class="usvo-link" data-usvo="${m.id}">${icon("card", 14)}<span>${esc(m.name || ("Карточка #" + m.id))}${m.phone ? ` · ${esc(m.phone)}` : ""}</span></button>`).join("");
    return `<div class="hero-banner hero-banner--warn usvo-link-block">${icon("info", 18)}
      <div><b>Несколько карточек УСВО ${crit}.</b>
      Связь не выбрана автоматически — выберите нужную карточку:
      <div class="usvo-link-list">${list}</div></div></div>`;
  }
  if (a.usvo_id && matches.length === 1) {
    // Честно показываем, ПО ЧЕМУ определена связь: телефон — надёжно, ФИО —
    // предположение (совпали фамилия и имя), которое оператор должен проверить.
    // Раньше любой матч подписывался «по номеру телефона», из-за чего карточка с
    // другим телефоном выглядела ложно связанной.
    const byName = a.link_by === "name";
    const how = byName
      ? `<span class="usvo-link-how">по совпадению ФИО — проверьте номер</span>`
      : `по номеру телефона`;
    const nm = matches[0].name ? `: <b>${esc(matches[0].name)}</b>` : "";
    return `<div class="usvo-link-note${byName ? " usvo-link-note--soft" : ""}">${icon("card", 14)} ${byName ? "Возможно, это карточка УСВО" : "Связано с карточкой УСВО"} ${how}${nm} <button type="button" class="usvo-link usvo-link--inline" data-usvo="${a.usvo_id}">открыть ↗</button></div>`;
  }
  return "";
}

function openAppeal(id) {
  const a = state.appeals.find((x) => x.id === id);
  if (!a) return;
  // Сотрудник отвечает только от своего имени — список ответственных схлопывается
  // до его учётной записи и блокируется. Администратор выбирает из всех сотрудников.
  const ops = state.isAdmin
    ? [...new Set([a.assignee, state.operator, ...(state.meta.operators || [])].filter(Boolean))]
    : [state.operator];
  const selectedOp = state.isAdmin ? (a.assignee || state.operator) : state.operator;
  const opOptions = ops.map((o) =>
    `<option ${o === selectedOp ? "selected" : ""}>${esc(o)}</option>`).join("");
  const assigneeLocked = state.isAdmin ? "" : "disabled";

  const sendText = a.status === "answered" ? "Отправить" : "Ответить";

  openDrawer({
    title: `Обращение ${sentiChip(a.sentiment)}`,
    subtitle: `${esc(a.citizen.name || "Гражданин")} · ${esc(a.created_human)} · <span class="pill pill--info">${esc(a.topic)}</span>`,
    body: `
      ${a.summary ? `<div class="gist"><span class="gist__ico">${icon("spark", 18)}</span>
        <div><span class="gist__tag">Суть кратко · ИИ</span><span class="gist__txt">${esc(a.summary)}</span></div></div>` : ""}

      <div class="q-box">
        <div class="lbl">Вопрос гражданина</div>
        <div class="txt">${esc(a.question)}</div>
      </div>

      ${usvoLinkBlock(a)}

      <div class="form-row">
        <label>Ответственный за ответ</label>
        <select id="ap-assignee" ${assigneeLocked}>${opOptions}</select>
      </div>

      <div class="form-row">
        <label>Ответ гражданину</label>
        <textarea id="ap-answer" placeholder="Нажмите «Сформировать черновик» (Alt+G), чтобы ИИ подставил известные данные и ответ из базы знаний…">${esc(a.answer || "")}</textarea>
      </div>

      <div class="conf" id="ap-conf" hidden></div>

      <div class="ap-history" id="ap-history">${loadingBlock("Загрузка истории обращений…")}</div>
    `,
    footer: `
      <button class="btn btn--danger" id="ap-delete">${icon("trash")}<span>Удалить</span></button>
      ${a.usvo_id ? `<button class="btn btn--soft" id="ap-card">${icon("card")}<span>Карточка УСВО</span></button>` : ""}
      <span class="hotkey-hint">${icon("keyboard", 14)} <span class="kbd">Alt+G</span> черновик · <span class="kbd">Ctrl+↵</span> отправить</span>
      <button class="btn btn--ghost" id="ap-draft">${btnLabel("wand", "Сформировать черновик", "Alt+G")}</button>
      <button class="btn btn--primary" id="ap-send">${btnLabel("send", sendText, "Ctrl+↵")}</button>
    `,
    onKeydown: (e) => {
      if (e.altKey && (e.key === "g" || e.key === "G" || e.code === "KeyG")) {
        e.preventDefault(); $("#ap-draft") && $("#ap-draft").click();
      } else if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault(); $("#ap-send") && $("#ap-send").click();
      }
    },
  });

  loadAppealHistory(a.id);

  // Переход к связанной карточке УСВО (одиночная ссылка или выбор из нескольких).
  $$("#drawer-body .usvo-link[data-usvo]").forEach((b) => {
    b.onclick = () => {
      const usvoId = +b.dataset.usvo;
      state.activeUsvo = usvoId;
      closeDrawer();
      switchView("cards");
      setTimeout(() => selectUsvo(usvoId), 80);
    };
  });

  $("#ap-delete").onclick = async (e) => {
    const btn = e.currentTarget;
    if (!(await confirmDialog("Обращение будет удалено из списка без возможности восстановления.", { title: "Удалить обращение?" }))) return;
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Удаление…");
    try {
      await api(`/appeals/${a.id}`, { method: "DELETE" });
      toast("Обращение удалено", "ok");
      closeDrawer();
      renderAppeals();
    } catch (err) {
      btn.disabled = false; btn.innerHTML = `${icon("trash")}<span>Удалить</span>`;
      toast(err.message, "err");
    }
  };

  $("#ap-assignee").addEventListener("change", async (e) => {
    const assignee = e.currentTarget.value;
    try {
      const updated = await api(`/appeals/${a.id}/assignee`, {
        method: "POST",
        body: JSON.stringify({ assignee }),
      });
      a.assignee = updated.assignee;
      state.appeals = state.appeals.map((x) => x.id === a.id ? updated : x);
      toast("Ответственный обновлён", "ok");
    } catch (err) {
      toast(err.message, "err");
    }
  });

  $("#ap-draft").onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true; btn.classList.add("is-thinking");
    btn.innerHTML = spinnerBtnHtml("ИИ думает…");
    try {
      const operator = $("#ap-assignee").value || state.operator;
      const res = await api(`/appeals/${a.id}/draft`, {
        method: "POST", body: JSON.stringify({ operator }),
      });
      $("#ap-answer").value = res.draft;
      renderConfidence(res.confidence);
      const sourceLabel = res.source === "dify"
        ? "Dify"
        : (res.source === "model" ? "модель" : "шаблон");
      toast(`Черновик сформирован (${sourceLabel})`, "ok");
    } catch (err) { toast(err.message, "err"); }
    finally {
      btn.disabled = false; btn.classList.remove("is-thinking");
      btn.innerHTML = btnLabel("wand", "Сформировать черновик", "Alt+G");
    }
  };

  if (a.usvo_id) $("#ap-card").onclick = () => {
    state.activeUsvo = a.usvo_id;
    closeDrawer();
    switchView("cards");
    setTimeout(() => selectUsvo(a.usvo_id), 80);
  };

  $("#ap-send").onclick = async (e) => {
    const answer = $("#ap-answer").value.trim();
    if (!answer) { toast("Введите текст ответа", "err"); return; }
    const btn = e.currentTarget;
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Отправка…");
    try {
      const res = await api(`/appeals/${a.id}/answer`, {
        method: "POST",
        body: JSON.stringify({ answer, assignee: $("#ap-assignee").value }),
      });
      let m = "Ответ сохранён";
      if (res.delivered_to_citizen) m += ", отправлен гражданину";
      if (res.saved_to_kb) m += ", записан в базу знаний";
      toast(m, "ok");
      closeDrawer();
      renderAppeals();
    } catch (err) {
      btn.disabled = false; btn.innerHTML = btnLabel("send", sendText, "Ctrl+↵");
      toast(err.message, "err");
    }
  };
}

/* История обращений гражданина (по его id в MAX): профиль из БД + прошлые вопросы. */
async function loadAppealHistory(appealId) {
  const box = $("#ap-history");
  if (!box) return;
  try {
    const res = await api(`/appeals/${encodeURIComponent(appealId)}/history`);
    const p = res.profile || {};
    const items = res.items || [];
    const others = items.filter((i) => !i.is_current);
    const profileLine = [
      p.user_id ? `MAX ID: <b>${esc(p.user_id)}</b>` : "",
      p.username ? `@${esc(p.username)}` : "",
      p.phone ? esc(p.phone) : "",
    ].filter(Boolean).join(" · ");

    const rows = others.length
      ? others.map((i) => `
        <div class="ahist-item ahist--${i.status === "answered" ? "ok" : "open"}">
          <div class="ahist-item__top">
            <span class="ahist-item__q">${esc(i.question)}</span>
            <span class="ahist-item__date">${esc(i.created_human)}</span>
          </div>
          ${i.answer ? `<div class="ahist-item__a">${icon("send", 12)} ${esc(i.answer)}</div>`
            : `<div class="ahist-item__a muted">Ответ ещё не дан</div>`}
        </div>`).join("")
      : `<p class="muted">Это первое обращение гражданина.</p>`;

    // Хронология текущего обращения: создание, ответ, отправленные уведомления.
    const EV_LABELS = {
      created: "Обращение создано", answered: "Ответ оператора",
      notification: "Уведомление гражданину", sla_reminder: "Напоминание о просрочке",
    };
    const events = res.events || [];
    const evHtml = events.length ? `
      <div class="ap-events">
        <div class="ap-events__head">${icon("gauge", 13)} Хронология обращения</div>
        ${events.map((e) => `<div class="ap-event ap-event--${esc(e.kind)}">
          <span class="ap-event__dot"></span>
          <span class="ap-event__t">${esc(EV_LABELS[e.kind] || e.kind)}${e.detail ? ` — ${esc(e.detail)}` : ""}</span>
          <span class="ap-event__d">${esc(e.created_human)}</span>
        </div>`).join("")}
      </div>` : "";

    box.innerHTML = `
      <div class="ap-history__head">
        ${icon("appeal", 15)}
        <b>История обращений гражданина</b>
        <span class="ap-history__count">${others.length}</span>
      </div>
      ${profileLine ? `<div class="ap-history__profile">${profileLine}</div>` : ""}
      ${evHtml}
      <div class="ahist-list">${rows}</div>`;
  } catch (err) {
    box.innerHTML = `<p class="muted">Не удалось загрузить историю: ${esc(err.message)}</p>`;
  }
}

/* ============================================================
   РАЗДЕЛ 2 — КАРТОЧКИ УСВО
   ============================================================ */
// Текущее состояние фильтров карточек УСВО.
const usvoFilters = {
  query: "", status: "", vbd: "", employment: "", contact: "",
  org: "", awards: "", directive: "", source: "",
};
let usvoFiltersCollapsed = false;
try { usvoFiltersCollapsed = localStorage.getItem("usvoFiltersCollapsed") === "1"; } catch (_) {}

function usvoQuery() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(usvoFilters)) if (v) p.set(k, v);
  return p.toString();
}

function usvoFiltersActive() {
  return Object.entries(usvoFilters).some(([k, v]) => k !== "query" && v);
}

function usvoActiveFilterCount() {
  return Object.entries(usvoFilters).filter(([k, v]) => k !== "query" && v).length;
}

function syncUsvoFiltersUi() {
  const box = $("#usvo-filters");
  const panel = $("#usvo-filters-panel");
  const toggle = $("#usvo-filters-toggle");
  const label = $("#usvo-filters-toggle-label");
  const active = $("#usvo-filters-active");
  const activeCount = usvoActiveFilterCount();
  if (!box || !panel || !toggle || !label || !active) return;

  box.classList.toggle("is-collapsed", usvoFiltersCollapsed);
  panel.hidden = usvoFiltersCollapsed;
  panel.inert = usvoFiltersCollapsed;
  panel.setAttribute("aria-hidden", String(usvoFiltersCollapsed));
  toggle.setAttribute("aria-expanded", String(!usvoFiltersCollapsed));
  label.textContent = usvoFiltersCollapsed ? "Показать" : "Скрыть";
  active.textContent = `${activeCount} ${plural(activeCount, "фильтр", "фильтра", "фильтров")}`;
  active.hidden = activeCount === 0;
}

const TRI = [["", "любые"], ["yes", "да"], ["no", "нет"]];
function selectFilter(key, label, options) {
  const opts = options.map(([v, t]) =>
    `<option value="${v}" ${usvoFilters[key] === v ? "selected" : ""}>${esc(t)}</option>`).join("");
  return `<label class="flt"><span>${esc(label)}</span><select data-filter="${key}">${opts}</select></label>`;
}

async function renderCards() {
  const root = $("#view-root");
  setTitle("Персональные карточки УСВО", "Данные участников СВО, фильтрация, загрузка и выгрузка");
  $("#topbar-actions").innerHTML = `
    <div class="search">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="usvo-search" type="search" placeholder="Поиск по ФИО, телефону, статусу…" value="${esc(usvoFilters.query)}" />
    </div>
    <button class="btn btn--ghost" id="usvo-upload-btn">${icon("doc")}<span>Загрузить</span></button>
    <a class="btn btn--soft" id="usvo-export-btn" href="#">${icon("send")}<span>Экспорт</span></a>`;

  const statuses = (state.meta && state.meta.usvo_statuses) || [];
  const statusOpts = [["", "любой"], ...statuses.map((s) => [s, s])];

  root.innerHTML = `
    <div class="usvo-filters card ${usvoFiltersCollapsed ? "is-collapsed" : ""}" id="usvo-filters">
      <div class="usvo-filters__head">
        <div class="usvo-filters__heading">
          <span class="usvo-filters__icon">${icon("filter", 19)}</span>
          <span>
            <b>Фильтры карточек</b>
            <small>Можно выбрать сразу несколько условий</small>
          </span>
        </div>
        <div class="usvo-filters__summary">
          <span class="usvo-filters__active" id="usvo-filters-active" ${usvoFiltersActive() ? "" : "hidden"}></span>
          <span class="usvo-filters__count" id="usvo-count">Загрузка…</span>
          <button class="usvo-filters__toggle" id="usvo-filters-toggle" type="button"
                  aria-controls="usvo-filters-panel" aria-expanded="${String(!usvoFiltersCollapsed)}">
            <span id="usvo-filters-toggle-label">${usvoFiltersCollapsed ? "Показать" : "Скрыть"}</span>
            ${icon("chevron", 17)}
          </button>
        </div>
      </div>
      <div class="usvo-filters__panel" id="usvo-filters-panel"
           aria-hidden="${String(usvoFiltersCollapsed)}" ${usvoFiltersCollapsed ? "hidden inert" : ""}>
        <div class="usvo-filters__panel-inner">
          <div class="usvo-filters__grid">
            ${selectFilter("status", "Статус", statusOpts)}
            ${selectFilter("vbd", "Ветеран БД", TRI)}
            ${selectFilter("employment", "Нужна работа", TRI)}
            ${selectFilter("contact", "Давно без связи", TRI)}
            ${selectFilter("org", "В организациях", TRI)}
            ${selectFilter("awards", "С наградами", TRI)}
            ${selectFilter("directive", "Поручение Главы", TRI)}
            ${selectFilter("source", "Источник", [["", "любой"], ["uploaded", "загружено"], ["table", "таблица"]])}
          </div>
          <div class="usvo-filters__actions">
            <span>Фильтры применяются автоматически</span>
            <button class="btn btn--ghost btn--sm" id="usvo-filters-reset" ${usvoFiltersActive() ? "" : "disabled"}>
              ${icon("refresh", 15)}<span>Сбросить фильтры</span>
            </button>
          </div>
        </div>
      </div>
    </div>
    <div class="cards-layout fade-in">
      <div class="card cards-list" id="usvo-list">${listSkeleton()}</div>
      <div class="card card-detail" id="usvo-detail">${emptyState("Выберите карточку", "Слева список участников СВО. Выберите запись, чтобы открыть данные.", "search")}</div>
    </div>`;

  const search = $("#usvo-search");
  search.addEventListener("input", debounce(() => { usvoFilters.query = search.value; loadUsvoList(); }, 250));
  $("#usvo-filters-toggle").addEventListener("click", () => {
    usvoFiltersCollapsed = !usvoFiltersCollapsed;
    try { localStorage.setItem("usvoFiltersCollapsed", usvoFiltersCollapsed ? "1" : "0"); } catch (_) {}
    syncUsvoFiltersUi();
  });
  $$("#usvo-filters select[data-filter]").forEach((sel) =>
    sel.addEventListener("change", () => {
      usvoFilters[sel.dataset.filter] = sel.value;
      $("#usvo-filters-reset").disabled = !usvoFiltersActive();
      syncUsvoFiltersUi();
      loadUsvoList();
    }));
  $("#usvo-filters-reset").addEventListener("click", () => {
    Object.keys(usvoFilters).forEach((k) => { if (k !== "query") usvoFilters[k] = ""; });
    renderCards();
  });
  $("#usvo-upload-btn").addEventListener("click", openUsvoUpload);
  syncUsvoFiltersUi();
  syncUsvoExportHref();
  await loadUsvoList();
}

function syncUsvoExportHref() {
  const a = $("#usvo-export-btn");
  if (a) { const qs = usvoQuery(); a.href = `${API}/export/usvo${qs ? "?" + qs : ""}`; }
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function loadUsvoList() {
  const list = $("#usvo-list");
  if (!list) return;
  syncUsvoExportHref();
  const qs = usvoQuery();
  const { items } = await api("/usvo" + (qs ? "?" + qs : ""));
  state.usvo = items;
  const cnt = $("#usvo-count");
  if (cnt) cnt.textContent = `${items.length} ${plural(items.length, "карточка", "карточки", "карточек")}`;
  if (!items.length) {
    list.innerHTML = emptyState("Ничего не найдено",
      "Уточните запрос или сбросьте фильтры — поиск идёт по ФИО, телефону, статусу и адресу.", "search");
    $("#usvo-detail").innerHTML = emptyState("Нет карточек", "Под текущие фильтры ничего не подошло.", "search");
    return;
  }
  if (!items.some((r) => r.id === state.activeUsvo)) {
    state.activeUsvo = items[0].id;
  }
  list.innerHTML = items.map((r) => `
    <div class="usvo-item ${state.activeUsvo === r.id ? "active" : ""}" data-id="${r.id}">
      <div class="avatar">${esc(r.initials)}</div>
      <div class="usvo-item__main">
        <div class="usvo-item__name">${esc(r.name)}${r.head_directive ? `<span class="usvo-item__star" title="Поручение Главы округа">${icon("award", 13)}</span>` : ""}${r.source === "uploaded" ? `<span class="usvo-item__tag" title="Загружено из кабинета">загружено</span>` : ""}</div>
        <div class="usvo-item__sub">${esc(r.status || "—")} · обзвон ${esc(r.call_date || "—")}</div>
      </div>
    </div>`).join("");
  $$("#usvo-list .usvo-item").forEach((el) =>
    el.addEventListener("click", () => selectUsvo(+el.dataset.id)));

  if (state.activeUsvo != null) selectUsvo(state.activeUsvo);
}

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = n % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

/* Загрузка карточек УСВО из Excel. */
function openUsvoUpload() {
  openModal(`
    <h3 class="modal__title">Загрузка карточек УСВО</h3>
    <p class="muted">Загрузите Excel-таблицу с карточками участников СВО. Столбец
      «История взаимодействия» (свободный текст) ИИ нормализует и оформит автоматически.</p>
    <div class="upload-drop" id="upload-drop">
      <input type="file" id="usvo-file" accept=".xlsx,.xls" hidden />
      <div class="upload-drop__ico">${icon("doc", 30)}</div>
      <div class="upload-drop__txt" id="upload-name">Нажмите, чтобы выбрать .xlsx</div>
    </div>
    <label class="chk"><input type="checkbox" id="usvo-replace" /> <span>Заменить ранее загруженные карточки</span></label>
    <div class="modal__actions">
      <a class="btn btn--soft" href="${API}/usvo/template" download>${icon("doc")}<span>Скачать пример таблицы</span></a>
      <button class="btn btn--primary" id="usvo-import-go" disabled>${icon("send")}<span>Загрузить</span></button>
    </div>`);

  const drop = $("#upload-drop"), input = $("#usvo-file"), go = $("#usvo-import-go");
  drop.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const f = input.files[0];
    $("#upload-name").textContent = f ? f.name : "Нажмите, чтобы выбрать .xlsx";
    go.disabled = !f;
  });
  go.addEventListener("click", async () => {
    const f = input.files[0];
    if (!f) return;
    go.disabled = true; go.innerHTML = spinnerBtnHtml("Загрузка и обработка ИИ…");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const replace = $("#usvo-replace").checked;
      const res = await fetch(`${API}/usvo/import?replace=${replace}`, {
        method: "POST", credentials: "same-origin", body: fd,
      });
      if (!res.ok) {
        let m = "Ошибка " + res.status;
        try { m = (await res.json()).detail || m; } catch (_) {}
        throw new Error(m);
      }
      const data = await res.json();
      toast(`Загружено карточек: ${data.saved}`, "ok");
      state.meta = await api("/meta");
      state.activeUsvo = null;
      // Обновляем список под модалкой и показываем итог импорта (с дублями).
      renderCards();
      showImportResult(data);
    } catch (err) {
      go.disabled = false; go.innerHTML = `${icon("send")}<span>Загрузить</span>`;
      toast(err.message, "err");
    }
  });
}

/* Итог импорта карточек УСВО: сколько загружено, сколько пропущено как дубли и
   какие именно записи пропущены (с причиной распознавания дубля). */
function showImportResult(data) {
  const skipped = data.skipped_details || [];
  const skippedHtml = skipped.length ? `
    <div class="import-skip">
      <div class="import-skip__head">${icon("info", 15)} Пропущены как дубли (${data.skipped})</div>
      <ul class="import-skip__list">
        ${skipped.map((s) => `<li>
          <b>${esc(s.name || "(без ФИО)")}</b>
          ${s.birth_date ? ` · ${esc(s.birth_date)}` : ""}${s.phone ? ` · ${esc(s.phone)}` : ""}
          <small>${esc(s.reason || "дубликат")}${(s.matched_by && s.matched_by.length) ? ` (по: ${esc(s.matched_by.join(", "))})` : ""}</small>
        </li>`).join("")}
      </ul>
    </div>` : (data.skipped
      ? `<p class="muted">Пропущено дублей: ${data.skipped}.</p>`
      : `<p class="muted">Дублей не найдено — все записи новые.</p>`);

  openModal(`
    <h3 class="modal__title">Импорт завершён</h3>
    <div class="import-stats">
      <div class="import-stat import-stat--ok"><b>${data.saved}</b><span>импортировано</span></div>
      <div class="import-stat ${data.skipped ? "import-stat--warn" : ""}"><b>${data.skipped}</b><span>пропущено дублей</span></div>
      <div class="import-stat"><b>${data.total_uploaded ?? "—"}</b><span>всего в базе</span></div>
    </div>
    ${skippedHtml}
    <div class="modal__actions">
      <button class="btn btn--primary" id="import-done">${icon("send")}<span>Готово</span></button>
    </div>`);
  $("#import-done").onclick = closeModal;
}

async function selectUsvo(id) {
  state.activeUsvo = id;
  $$("#usvo-list .usvo-item").forEach((el) =>
    el.classList.toggle("active", +el.dataset.id === id));
  const detail = $("#usvo-detail");
  detail.innerHTML = loadingBlock();
  const r = await api("/usvo/" + id);
  if (state.activeUsvo !== id) return; // пользователь уже переключился

  // Поле карточки редактируется прямо на месте: название и значение —
  // contenteditable. Полный набор полей собирается из DOM при сохранении, так что
  // скрытые/служебные поля (награды и пр.) не теряются.
  const fieldHtml = (f = { label: "", value: "" }) => `
    <div class="field field--edit">
      <div class="field__label" contenteditable="true" spellcheck="false" data-ph="Название поля">${esc(f.label)}</div>
      <div class="field__value" contenteditable="true" data-ph="Значение">${esc(f.value)}</div>
      <button type="button" class="field__del" title="Удалить поле" aria-label="Удалить поле">${icon("trash", 13)}</button>
    </div>`;

  const age = ageFrom(r.birth_date);
  const flags = r.flags || {};
  const groups = groupFields(r);
  const accHtml = groups.map((g, i) => `
    <div class="acc ${i === 0 ? "open" : ""}">
      <button class="acc__head" type="button">
        <span class="acc__ico">${icon(g.icon || "info", 18)}</span>
        <span>${esc(g.title)}</span>
        <span class="acc__count">${g.items.length}</span>
        <svg class="acc__chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
      </button>
      <div class="acc__body"><div class="acc__inner"><div class="fields-grid">${g.items.map(fieldHtml).join("")}</div></div></div>
    </div>`).join("");

  const note = healthNote(r);
  const banner = note
    ? `<div class="hero-banner hero-banner--danger">${icon("med", 18)}<span>${esc(note)}</span></div>`
    : "";

  const dir = r.head_directive;
  const directiveBanner = dir
    ? `<div class="directive-banner directive-banner--${dir.type === "meeting" ? "meeting" : "program"}">
        <span class="directive-banner__ico">${icon(dir.type === "meeting" ? "visit" : "award", 18)}</span>
        <div><span class="directive-banner__tag">Поручение Главы округа</span>${esc(dir.text)}</div>
      </div>`
    : "";

  detail.innerHTML = `
    <div class="detail-scroll fade-in">
      <div class="usvo-hero">
        <div class="usvo-hero__top">
          <div class="avatar avatar--lg">${esc(r.initials)}</div>
          <div class="usvo-hero__id">
            <h2>${esc(r.name)}${age != null ? `<span class="usvo-hero__age">, ${age} ${yearsWord(age)}</span>` : ""}</h2>
            <div class="usvo-hero__chips">
              <span class="chip">${esc(r.status || "—")}</span>
              ${flags.vbd ? `<span class="chip chip--ok">Ветеран БД</span>` : `<span class="chip chip--warn">ВБД не оформлен</span>`}
              ${flags.unemployed ? `<span class="chip chip--warn">Ищет работу</span>` : ""}
              ${flags.stale_contact ? `<span class="chip chip--danger">Давно без связи</span>` : ""}
            </div>
          </div>
          <div class="usvo-hero__contacts">
            ${r.phone ? `<div>${icon("contact", 14)} <b>${esc(r.phone)}</b></div>` : ""}
            ${r.call_date ? `<div>Обзвон: <b>${esc(r.call_date)}</b></div>` : ""}
            ${r.birth_date ? `<div>Род.: <b>${esc(r.birth_date)}</b></div>` : ""}
            <div class="usvo-hero__btns">
              <a class="btn btn--soft btn--sm" href="${API}/usvo/${id}/docx" download title="Скачать карточку в Word для передачи в ведомства">${icon("doc", 14)}<span>Экспорт в DOCX</span></a>
              ${r.source === "uploaded" ? `<button class="btn btn--danger btn--sm" id="usvo-del" title="Удалить загруженную карточку">${icon("trash", 14)}<span>Удалить</span></button>` : ""}
            </div>
          </div>
        </div>
        ${directiveBanner}
        ${banner}
      </div>

      <section class="block block--ai">
        <div class="block__head">
          <h3>Предложения ИИ по направлениям</h3>
          <span class="ai-badge">${icon("wand", 13)} персональный подбор</span>
        </div>
        <div id="suggest-box">${suggSkeleton()}</div>
      </section>

      <div class="block">${statPills(r, age)}</div>

      ${renderMedals(r.awards)}
      ${renderMap(r.address)}

      <div class="block">
        <div class="block__head">
          <h3>Данные участника</h3>
          <span class="muted usvo-edit-hint">правьте значения прямо в полях</span>
          <div class="usvo-edit-bar">
            <button class="btn btn--ghost btn--sm" id="usvo-add-field" type="button">${icon("plus", 14)}<span>Поле</span></button>
            <button class="btn btn--soft btn--sm" id="usvo-edit-cancel" type="button" disabled>Отменить</button>
            <button class="btn btn--primary btn--sm" id="usvo-edit-save" type="button" disabled>${icon("send", 14)}<span>Сохранить</span></button>
          </div>
        </div>
        <div id="usvo-data-acc">${accHtml || `<p class="muted">Нет данных</p>`}</div>
      </div>

      <div class="block">
        <div class="block__head">
          <h3>История взаимодействия</h3>
          <span class="block__hint">${(r.history || []).length} событий</span>
          <button class="btn btn--ghost btn--sm usvo-hist-toggle" id="usvo-hist-toggle" type="button">${icon("edit", 13)}<span>Изменить текст</span></button>
        </div>
        <div id="usvo-history-edit-wrap" hidden>
          <p class="muted usvo-hist-hint">Свободный текст — ИИ оформит его в события ленты. Изменения применятся при сохранении карточки.</p>
          <textarea id="usvo-history-edit" class="usvo-hist-area" rows="4" placeholder="Хронология взаимодействия…">${esc(r.history_raw || "")}</textarea>
        </div>
        ${renderTimeline(r.history)}
      </div>
    </div>`;

  $$("#usvo-detail .acc__head").forEach((h) =>
    h.addEventListener("click", () => h.parentElement.classList.toggle("open")));

  bindUsvoInlineEdit(r, id);

  const delBtn = $("#usvo-del");
  if (delBtn) delBtn.addEventListener("click", async () => {
    if (!(await confirmDialog("Загруженная карточка УСВО будет удалена из базы.", { title: "Удалить карточку?" }))) return;
    delBtn.disabled = true; delBtn.innerHTML = spinnerBtnHtml("Удаление…");
    try {
      await api(`/usvo/${id}`, { method: "DELETE" });
      toast("Карточка удалена", "ok");
      state.activeUsvo = null;
      state.meta = await api("/meta");
      loadUsvoList();
    } catch (err) { delBtn.disabled = false; toast(err.message, "err"); }
  });

  loadSuggestions(id); // авто-загрузка предложений — фишка наверху
}

async function loadSuggestions(id) {
  const box = $("#suggest-box");
  if (!box) return;
  try {
    const res = await api(`/usvo/${id}/suggestions`);
    if (state.activeUsvo !== id) return; // карточка сменилась, не подменяем
    const cards = (res.items || []).map((s) => {
      const prio = s.priority || "base";
      const prioLbl = prio === "high" ? "Высокий" : prio === "medium" ? "Средний" : "Базовый";
      const docs = (s.docs || []).map((d) => `<span class="doc-chip">${icon("doc", 13)}${esc(d)}</span>`).join("");
      return `<div class="sugg-card sugg--${prio}">
        <div class="sugg-card__ico">${icon(s.kind || "info", 20)}</div>
        <div class="sugg-card__body">
          <div class="sugg-card__top"><h4>${esc(s.title)}</h4><span class="prio prio--${prio}">${prioLbl}</span></div>
          <p class="sugg-card__detail">${esc(s.detail)}</p>
          ${s.action ? `<div class="sugg-card__action"><b>Следующий шаг:</b> ${esc(s.action)}</div>` : ""}
          ${docs ? `<div class="sugg-card__chips">${docs}</div>` : ""}
          ${s.where ? `<div class="sugg-card__where">${icon("building", 14)}${esc(s.where)}</div>` : ""}
        </div>
      </div>`;
    }).join("");
    const note = res.ai_note
      ? `<div class="ai-note"><span class="ai-tag">Заметка ИИ · база знаний</span><br/>${esc(res.ai_note)}</div>`
      : "";
    box.innerHTML = (cards + note) || `<p class="muted">Нет предложений</p>`;
  } catch (err) {
    box.innerHTML = `<p class="muted">Не удалось загрузить предложения: ${esc(err.message)}</p>`;
  }
}

/* ---------- инлайн-редактирование карточки УСВО ----------
   Поля правятся прямо во вкладках со значениями (contenteditable). Карточка
   хранится как список «поле → значение»; ключевые данные (ФИО, статус, телефон…)
   пересчитываются на бэкенде из полного набора полей. Табличная карточка при
   сохранении становится загруженной и перекрывает исходную (без дублей). */
function bindUsvoInlineEdit(r, id) {
  // Все слушатели вешаем на свежесозданные при этом рендере узлы (#usvo-data-acc,
  // кнопки, textarea), а не на постоянный #usvo-detail, — иначе при каждом открытии
  // карточки они бы накапливались.
  const acc = $("#usvo-data-acc");
  const saveBtn = $("#usvo-edit-save");
  const cancelBtn = $("#usvo-edit-cancel");
  const addBtn = $("#usvo-add-field");
  const histToggle = $("#usvo-hist-toggle");
  const histWrap = $("#usvo-history-edit-wrap");
  const histArea = $("#usvo-history-edit");
  if (!acc || !saveBtn) return;

  let dirty = false;
  const markDirty = () => {
    if (dirty) return;
    dirty = true;
    saveBtn.disabled = false;
    cancelBtn.disabled = false;
  };

  // Любая правка названия/значения поля — карточка «грязная».
  acc.addEventListener("input", (e) => {
    if (e.target.closest(".field--edit")) markDirty();
  });
  // Enter в названии/значении не плодит переносы — поля однострочные.
  acc.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.closest(".field__label, .field__value")) {
      e.preventDefault();
      e.target.blur();
    }
  });
  if (histArea && histWrap) histArea.addEventListener("input", () => {
    histWrap.dataset.touched = "1";
    markDirty();
  });

  // Удаление поля (кнопка внутри плитки).
  acc.addEventListener("click", (e) => {
    const del = e.target.closest(".field__del");
    if (!del) return;
    del.closest(".field--edit").remove();
    markDirty();
  });

  // Добавление поля — в последний (всегда присутствующий) аккордеон «Дополнительно».
  if (addBtn) addBtn.addEventListener("click", () => {
    const accs = $$("#usvo-data-acc .acc");
    const last = accs[accs.length - 1] || acc;
    last.classList.add("open");
    const grid = $(".fields-grid", last);
    if (!grid) return;
    grid.insertAdjacentHTML("beforeend", `
      <div class="field field--edit">
        <div class="field__label" contenteditable="true" spellcheck="false" data-ph="Название поля"></div>
        <div class="field__value" contenteditable="true" data-ph="Значение"></div>
        <button type="button" class="field__del" title="Удалить поле" aria-label="Удалить поле">${icon("trash", 13)}</button>
      </div>`);
    grid.lastElementChild.querySelector(".field__label").focus();
    markDirty();
  });

  if (histToggle && histWrap) histToggle.addEventListener("click", () => {
    histWrap.hidden = !histWrap.hidden;
    histToggle.classList.toggle("active", !histWrap.hidden);
    if (!histWrap.hidden) $("#usvo-history-edit").focus();
  });

  cancelBtn.addEventListener("click", () => selectUsvo(id));

  saveBtn.addEventListener("click", async () => {
    const out = $$("#usvo-data-acc .field--edit").map((el) => ({
      label: ($(".field__label", el).textContent || "").replace(/\s+/g, " ").trim(),
      value: ($(".field__value", el).textContent || "").trim(),
    })).filter((f) => f.label && f.value);
    if (!out.length) { toast("Карточка не может быть пустой", "err"); return; }
    const body = { fields: out };
    // history_raw шлём, только если оператор реально открыл и менял текст истории —
    // иначе бэкенд оставит уже нормализованные события без перегенерации.
    if (histWrap && histWrap.dataset.touched === "1") {
      body.history_raw = $("#usvo-history-edit").value;
    }
    saveBtn.disabled = true; saveBtn.innerHTML = spinnerBtnHtml("Сохранение…");
    cancelBtn.disabled = true;
    try {
      const res = await api(`/usvo/${id}`, { method: "PUT", body: JSON.stringify(body) });
      toast("Карточка сохранена", "ok");
      state.activeUsvo = res.id || id;
      state.meta = await api("/meta");
      await loadUsvoList();
      selectUsvo(state.activeUsvo);
    } catch (err) {
      saveBtn.disabled = false; cancelBtn.disabled = false;
      saveBtn.innerHTML = `${icon("send", 14)}<span>Сохранить</span>`;
      toast(err.message, "err");
    }
  });
}

/* ============================================================
   ЧАТ С ИИ ПО КАРТОЧКАМ УСВО
   ============================================================ */
function aiChatTime(ts) {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
      timeZone: "Europe/Moscow",
    });
  } catch (_) { return ""; }
}

function renderAiText(text) {
  const links = [];
  let source = String(text || "").replace(
    /\[([^\]]+)\]\(\/usvo\/cards\/(\d+)\)/g,
    (_, label, id) => {
      links.push(`<a class="ai-person-link" href="${APP_BASE}/usvo/cards/${id}" data-usvo-id="${id}">${esc(label)}</a>`);
      return `\u0001${links.length - 1}\u0002`;
    },
  );
  // Справки приходят как Markdown-таблицы `| № | Показатель | Значение |` —
  // рендерим их настоящими таблицами, остальной текст экранируем как раньше.
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const isRow = (l) => {
    const s = l.trim();
    return s.startsWith("|") && (s.match(/\|/g) || []).length >= 2;
  };
  const cells = (l) => {
    let s = l.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|")) s = s.slice(0, -1);
    return s.split("|").map((c) => c.trim());
  };
  const isSep = (cs) => cs.length && cs.every((c) => c && /^[-:\s]+$/.test(c) && c.includes("-"));
  const html = [];
  let textBuf = [];
  const flushText = () => {
    if (textBuf.length) html.push(textBuf.map((l) => esc(l)).join("<br>"));
    textBuf = [];
  };
  let i = 0;
  while (i < lines.length) {
    if (isRow(lines[i])) {
      flushText();
      const rows = [];
      while (i < lines.length && isRow(lines[i])) {
        const cs = cells(lines[i]);
        if (!isSep(cs)) rows.push(cs);
        i += 1;
      }
      if (rows.length) {
        const head = rows[0].map((c) => `<th>${esc(c)}</th>`).join("");
        const body = rows.slice(1)
          .map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`)
          .join("");
        html.push(`<table class="ai-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`);
      }
      continue;
    }
    textBuf.push(lines[i]);
    i += 1;
  }
  flushText();
  source = html.join("");
  return source.replace(/\u0001(\d+)\u0002/g, (_, i) => links[+i] || "");
}

function renderAiChatList() {
  const list = $("#ai-chat-list");
  if (!list) return;
  if (!state.aiChats.length) {
    list.innerHTML = `<p class="ai-chat-list__empty">Диалогов пока нет</p>`;
    return;
  }
  list.innerHTML = state.aiChats.map((chat) => `
    <button class="ai-chat-item ${chat.id === state.activeAiChat ? "active" : ""}" data-chat-id="${chat.id}">
      <span class="ai-chat-item__icon">${icon("chat", 16)}</span>
      <span class="ai-chat-item__body">
        <b>${esc(chat.title || "Новый чат")}</b>
        <small>${esc(aiChatTime(chat.updatedAt))} · ${chat.messageCount || 0} сообщ.</small>
      </span>
    </button>`).join("");
  $$("#ai-chat-list .ai-chat-item").forEach((button) =>
    button.addEventListener("click", () => selectAiChat(+button.dataset.chatId)));
}

function bindAiPersonLinks() {
  $$("#ai-chat-messages .ai-person-link").forEach((link) =>
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const id = +link.dataset.usvoId;
      Object.keys(usvoFilters).forEach((key) => { usvoFilters[key] = ""; });
      state.activeUsvo = id;
      history.pushState({}, "", `${APP_BASE}/usvo/cards/${id}`);
      switchView("cards");
    }));
}

function renderAiMessages(loading = false) {
  const box = $("#ai-chat-messages");
  if (!box) return;
  if (!state.aiMessages.length && !loading) {
    box.innerHTML = `
      <div class="ai-chat-welcome">
        <span>${icon("spark", 28)}</span>
        <h3>Задайте вопрос по карточкам УСВО</h3>
        <p>Можно запросить данные по человеку, статистику по базе или сформировать справку.</p>
      </div>`;
    return;
  }
  box.innerHTML = state.aiMessages.map((message) => `
    <article class="ai-message ai-message--${message.role === "user" ? "user" : "assistant"}">
      <div class="ai-message__avatar">${message.role === "user" ? esc(initials(state.user?.name || "П")) : icon("spark", 17)}</div>
      <div class="ai-message__body">
        <div class="ai-message__content">${renderAiText(message.content)}</div>
        ${message.role !== "user" && message.metadata?.hasDocx && message.id
          ? `<a class="ai-message__docx" href="${API}/ai-chats/${state.activeAiChat}/messages/${message.id}/docx" download>${icon("doc", 15)}<span>Скачать справку (.docx)</span></a>`
          : ""}
        ${message.createdAt ? `<time>${esc(aiChatTime(message.createdAt))}</time>` : ""}
      </div>
    </article>`).join("") + (loading ? `
      <article class="ai-message ai-message--assistant">
        <div class="ai-message__avatar">${icon("spark", 17)}</div>
        <div class="ai-message__body"><div class="ai-thinking"><i></i><i></i><i></i></div></div>
      </article>` : "");
  bindAiPersonLinks();
  box.scrollTop = box.scrollHeight;
}

function currentAiChat() {
  return state.aiChats.find((chat) => chat.id === state.activeAiChat) || null;
}

function syncAiChatHeader() {
  const chat = currentAiChat();
  const title = $("#ai-chat-title");
  const select = $("#ai-answer-type");
  const del = $("#ai-chat-delete");
  if (title) title.textContent = chat ? chat.title : "Новый диалог";
  if (select) {
    select.innerHTML = state.aiAnswerTypes.map((item) =>
      `<option value="${esc(item.value)}">${esc(item.label)}</option>`).join("");
    select.value = chat?.answerType || "text";
    select.disabled = !chat;
  }
  if (del) del.disabled = !chat;
}

async function loadAiChats(preferredId = null) {
  const data = await api("/ai-chats");
  state.aiChats = data.items || [];
  state.aiAnswerTypes = data.answerTypes || [
    { value: "text", label: "Текст" },
    { value: "detailed_reference", label: "Развернутая справка" },
    { value: "brief_reference", label: "Краткая справка" },
  ];
  state.aiReady = !!data.ready;
  const preferred = preferredId || state.activeAiChat;
  state.activeAiChat = state.aiChats.some((chat) => chat.id === preferred)
    ? preferred
    : (state.aiChats[0]?.id || null);
  renderAiChatList();
  syncAiChatHeader();
}

async function createAiChat() {
  const data = await api("/ai-chats", {
    method: "POST",
    body: JSON.stringify({ answerType: "text" }),
  });
  await loadAiChats(data.chat.id);
  await selectAiChat(data.chat.id);
  $("#ai-chat-input")?.focus();
}

async function selectAiChat(chatId) {
  state.activeAiChat = chatId;
  renderAiChatList();
  syncAiChatHeader();
  const box = $("#ai-chat-messages");
  if (box) box.innerHTML = loadingBlock("Загрузка истории…");
  const data = await api(`/ai-chats/${chatId}/messages`);
  if (state.activeAiChat !== chatId || state.view !== "ai-chat") return;
  state.aiMessages = data.items || [];
  renderAiMessages();
}

async function deleteCurrentAiChat() {
  const chat = currentAiChat();
  if (!chat) return;
  if (!(await confirmDialog(`Диалог «${chat.title}» будет удалён вместе с историей переписки.`, { title: "Удалить диалог?" }))) return;
  await api(`/ai-chats/${chat.id}`, { method: "DELETE" });
  state.activeAiChat = null;
  state.aiMessages = [];
  await loadAiChats();
  if (state.activeAiChat) await selectAiChat(state.activeAiChat);
  else renderAiMessages();
  toast("Диалог удалён", "ok");
}

async function sendAiMessage() {
  const input = $("#ai-chat-input");
  const button = $("#ai-chat-send");
  const chat = currentAiChat();
  const content = (input?.value || "").trim();
  if (!chat || !content || button.disabled) return;
  const answerType = $("#ai-answer-type")?.value || chat.answerType || "text";
  input.value = "";
  button.disabled = true;
  $("#ai-answer-type").disabled = true;
  state.aiMessages.push({ role: "user", content, createdAt: Date.now() / 1000 });
  renderAiMessages(true);
  try {
    const data = await api(`/ai-chats/${chat.id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content, answerType }),
    });
    state.aiMessages.push(data.message);
    await loadAiChats(chat.id);
    renderAiMessages();
  } catch (error) {
    toast(error.message, "err");
    try { await selectAiChat(chat.id); } catch (_) { renderAiMessages(); }
    const box = $("#ai-chat-messages");
    if (box) box.insertAdjacentHTML("beforeend",
      `<div class="ai-chat-error">${icon("info", 16)}<span>${esc(error.message)}</span></div>`);
  } finally {
    button.disabled = false;
    $("#ai-answer-type").disabled = false;
    input.focus();
  }
}

async function rebuildUsvoKnowledge() {
  const button = $("#ai-kb-rebuild");
  if (!button) return;
  button.disabled = true;
  button.innerHTML = spinnerBtnHtml("Пересборка…");
  try {
    const result = await api("/ai-knowledge/usvo/rebuild", { method: "POST" });
    toast(`База знаний пересобрана: ${result.count} карточек`, "ok");
  } catch (error) {
    toast(error.message, "err");
  } finally {
    button.disabled = false;
    button.innerHTML = `${icon("refresh", 15)}<span>Пересобрать базу знаний</span>`;
  }
}

async function renderAiChat() {
  const root = $("#view-root");
  setTitle("Вопрос-ответ по нормативке", "Вопросы, статистика и справки по текущим карточкам УСВО");
  $("#topbar-actions").innerHTML = state.isAdmin
    ? `<button class="btn btn--soft btn--sm" id="ai-kb-rebuild">${icon("refresh", 15)}<span>Пересобрать базу знаний</span></button>`
    : "";
  root.innerHTML = `
    <div class="ai-chat-layout fade-in">
      <aside class="card ai-chat-sidebar">
        <button class="btn btn--primary ai-chat-new" id="ai-chat-new">${icon("plus", 16)}<span>Новый чат</span></button>
        <div class="ai-chat-list" id="ai-chat-list">${loadingBlock()}</div>
      </aside>
      <section class="card ai-chat-main">
        <header class="ai-chat-head">
          <div><h2 id="ai-chat-title">Новый диалог</h2><small>Ответы формируются по базе карточек УСВО</small></div>
          <div class="ai-chat-controls">
            <label><span>Тип ответа</span><select id="ai-answer-type"></select></label>
            <button class="icon-btn ai-chat-delete" id="ai-chat-delete" title="Удалить чат">${icon("trash", 16)}</button>
          </div>
        </header>
        <div class="ai-chat-warning" id="ai-chat-warning" hidden>
          ${icon("info", 16)}<span>Dify-ассистент чата не настроен. Укажите <b>dify.usvo_ai.app_key</b> в config.yaml.</span>
        </div>
        <div class="ai-chat-messages" id="ai-chat-messages">${loadingBlock()}</div>
        <footer class="ai-chat-composer">
          <textarea id="ai-chat-input" rows="2" maxlength="12000" placeholder="Спросите о карточках УСВО…"></textarea>
          <button class="btn btn--primary" id="ai-chat-send" title="Отправить">${icon("send", 18)}<span>Отправить</span></button>
        </footer>
      </section>
    </div>`;

  $("#ai-chat-new").addEventListener("click", () => createAiChat().catch((e) => toast(e.message, "err")));
  $("#ai-chat-delete").addEventListener("click", () => deleteCurrentAiChat().catch((e) => toast(e.message, "err")));
  $("#ai-chat-send").addEventListener("click", sendAiMessage);
  $("#ai-chat-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendAiMessage();
    }
  });
  $("#ai-answer-type").addEventListener("change", async (event) => {
    const chat = currentAiChat();
    if (!chat) return;
    try {
      const data = await api(`/ai-chats/${chat.id}`, {
        method: "PATCH",
        body: JSON.stringify({ answerType: event.target.value }),
      });
      Object.assign(chat, data.chat);
      renderAiChatList();
    } catch (error) {
      event.target.value = chat.answerType;
      toast(error.message, "err");
    }
  });
  if (state.isAdmin) $("#ai-kb-rebuild")?.addEventListener("click", rebuildUsvoKnowledge);

  await loadAiChats();
  $("#ai-chat-warning").hidden = state.aiReady;
  if (!state.activeAiChat) await createAiChat();
  else await selectAiChat(state.activeAiChat);
}

/* ============================================================
   РАЗДЕЛ 3 — ЗАЯВЛЕНИЯ (меры поддержки, оформленные ботом по фото)
   ============================================================ */
const APP_STATUS = {
  submitted: { label: "На рассмотрении", cls: "open" },
  approved: { label: "Одобрено", cls: "answered" },
  rejected: { label: "Отклонено", cls: "rejected" },
};

const APP_PILL = {
  submitted: "pill--warn", approved: "pill--ok", rejected: "pill--danger",
};

async function renderApplications(opts = {}) {
  const silent = opts && opts.silent === true;
  const root = $("#view-root");
  // Подраздел «Меры поддержки» — только для администратора.
  if (!state.isAdmin && state.appsTab === "measures") state.appsTab = "list";
  const tab = state.appsTab || "list";
  // Фоновое автообновление трогает только список поданных заявлений — вкладку
  // «Меры поддержки» (CRUD админа) не перерисовываем, чтобы ничего не «дёргалось».
  if (silent) {
    if (tab !== "list") return;
    return renderApplicationsListPane({ silent: true });
  }
  setTitle("Заявления", "Заявления граждан и доступные меры поддержки");

  const tabsHtml = state.isAdmin ? `
    <div class="subtabs">
      <button class="subtab ${tab === "list" ? "active" : ""}" data-tab="list">${icon("doc", 16)}<span>Поданные заявления</span></button>
      <button class="subtab ${tab === "measures" ? "active" : ""}" data-tab="measures">${icon("book", 16)}<span>Доступные меры поддержки</span></button>
    </div>` : "";
  root.innerHTML = `${tabsHtml}<div id="apps-content">${loadingBlock()}</div>`;
  $$("#view-root .subtab").forEach((b) => b.addEventListener("click", () => {
    state.appsTab = b.dataset.tab;
    renderApplications();
  }));

  if (tab === "measures") return renderMeasuresPane();
  return renderApplicationsListPane();
}

/* Одна строка списка поданных заявлений. */
function applicationRowHtml(a) {
  const st = APP_STATUS[a.status] || { label: a.status_label || a.status, cls: "open" };
  const pcls = APP_PILL[a.status] || "pill--warn";
  const who = a.applicant.fio || a.citizen.name || "Заявитель";
  const basis = a.is_measure ? "Мера поддержки" : (a.category || "—");
  const docsCell = a.is_measure
    ? `<span class="pill pill--info">полей: ${a.measure_fields.length}</span>`
    : (a.missing && a.missing.length
      ? `<span class="pill pill--warn">уточнить: ${a.missing.length}</span>`
      : '<span class="muted">полный пакет</span>');
  return `<tr data-id="${a.id}">
    <td class="q">${esc(a.measure_title)}<small>${esc(who)}</small></td>
    <td>${esc(a.created_human)}</td>
    <td><span class="pill pill--info">${esc(basis)}</span></td>
    <td>${docsCell}</td>
    <td><span class="pill ${pcls}"><span class="pill__dot"></span>${esc(st.label)}</span></td>
  </tr>`;
}

async function renderApplicationsListPane(opts = {}) {
  const silent = opts && opts.silent === true;
  if (!silent) {
    $("#topbar-actions").innerHTML = `
      ${state.isAdmin ? `<button class="btn btn--soft" id="apps-notify">${icon("send")}<span>Оповестить</span></button>` : ""}
      <a class="btn btn--soft" href="${API}/export/applications" download>${icon("send")}<span>Экспорт</span></a>
      <button class="btn btn--ghost" id="reload-apps">${icon("refresh")}<span>Обновить</span></button>`;
    $("#reload-apps").onclick = () => renderApplications();
    const notify = $("#apps-notify");
    if (notify) notify.onclick = openBroadcastModal;
  }

  const c = $("#apps-content");
  if (!c) return; // вкладка сменилась во время запроса — обновлять нечего
  let items = [];
  try {
    ({ items } = await api("/applications"));
  } catch (e) {
    if (!silent) c.innerHTML = emptyState("Не удалось загрузить", e.message);
    return;
  }
  state.applications = items;
  const pending = items.filter((a) => a.status === "submitted").length;
  $("#nav-apps-count").textContent = pending || "";

  if (!items.length) {
    if (silent && !$("#apps-content .appeals-table")) return; // уже пусто — не трогаем DOM
    c.innerHTML = emptyState("Заявлений пока нет",
      "Гражданин оформляет меру поддержки в боте MAX — после подтверждения заявление появится здесь.", "doc");
    state._appsSig = "";
    return;
  }

  const sig = items.map((a) => `${a.id}:${a.status}`).join(",");
  const tbody = $("#apps-content .appeals-table tbody");
  if (silent && tbody && sig === state._appsSig) return; // ничего не изменилось

  const rows = items.map(applicationRowHtml).join("");
  if (silent && tbody) {
    tbody.innerHTML = rows; // мягкая замена строк — без мигания и скачка прокрутки
    $$("#apps-content tbody tr").forEach((tr) =>
      tr.addEventListener("click", () => openApplication(+tr.dataset.id)));
    state._appsSig = sig;
    return;
  }

  c.innerHTML = `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr>
          <th>Мера поддержки</th><th>Поступило</th><th>Основание</th><th>Документы</th><th>Статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  $$("#apps-content tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => openApplication(+tr.dataset.id)));
  state._appsSig = sig;
}

/* ---------- подраздел «Меры поддержки» (CRUD, только админ) ---------- */
async function renderMeasuresPane() {
  const kbReady = !!(state.meta && state.meta.kb_ready);
  $("#topbar-actions").innerHTML = `
    <button class="btn btn--ghost" id="reload-sm">${icon("refresh")}<span>Обновить</span></button>
    ${kbReady ? `<button class="btn btn--soft" id="sync-sm">${icon("book")}<span>Синхронизировать с ИИ</span></button>` : ""}
    <button class="btn btn--primary" id="add-sm">${icon("plus")}<span>Новая мера</span></button>`;
  $("#reload-sm").onclick = renderApplications;
  $("#add-sm").onclick = () => openMeasureForm(null);
  const syncBtn = $("#sync-sm");
  if (syncBtn) syncBtn.onclick = syncMeasuresKb;

  const c = $("#apps-content");
  let items = [];
  try {
    ({ items } = await api("/settings/support-measures"));
  } catch (e) {
    c.innerHTML = emptyState("Не удалось загрузить", e.message);
    return;
  }
  state.measures = items;

  if (!items.length) {
    c.innerHTML = emptyState("Мер поддержки пока нет",
      "Создайте первую меру — она появится в боте MAX и станет доступна ИИ.", "doc");
    return;
  }

  const body = items.map((m) => `
    <tr data-id="${m.id}">
      <td class="q">${esc(m.title)}<small>${m.documents.length} док. · ${m.placeholders.length} полей${m.has_template ? " · шаблон ✓" : ""}</small></td>
      <td>${esc(m.description || "—")}</td>
      <td>${m.active
        ? '<span class="pill pill--ok"><span class="pill__dot"></span>Активна</span>'
        : '<span class="pill pill--warn"><span class="pill__dot"></span>Отключена</span>'}</td>
      <td class="row-actions">
        <button class="btn btn--soft btn--sm" data-act="edit">${icon("edit")}<span>Изменить</span></button>
        <button class="btn btn--danger btn--sm" data-act="del">${icon("trash")}<span>Удалить</span></button>
      </td>
    </tr>`).join("");

  c.innerHTML = `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr><th>Мера поддержки</th><th>Описание</th><th>Статус</th><th></th></tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>`;
  $$("#apps-content tbody tr").forEach((tr) => {
    const id = +tr.dataset.id;
    const m = items.find((x) => x.id === id);
    tr.querySelector('[data-act="edit"]').onclick = () => openMeasureForm(m);
    tr.querySelector('[data-act="del"]').onclick = () => deleteMeasure(m);
  });
}

async function uploadMeasureTemplate(measureId, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${API}/settings/support-measures/${measureId}/template`, {
    method: "POST", credentials: "same-origin", body: fd,
  });
  if (!res.ok) {
    let m = "Ошибка " + res.status;
    try { m = (await res.json()).detail || m; } catch (_) {}
    throw new Error(m);
  }
  return res.json();
}

function openMeasureForm(measure) {
  const editing = !!measure;
  const docs = editing && measure.documents.length
    ? measure.documents.map((d) => d.title) : [""];
  const phs = editing ? measure.placeholders : [];

  const docRow = (val = "") => `
    <div class="edit-row edit-row--single">
      <input class="doc-name" placeholder="Название документа (напр. Паспорт)" value="${esc(val)}" />
      <button type="button" class="icon-btn edit-row__del" title="Удалить">${icon("trash", 14)}</button>
    </div>`;
  const phRow = (p = { key: "", label: "" }) => `
    <div class="edit-row">
      <input class="ph-key" placeholder="ключ ({{snake_case}})" value="${esc(p.key)}" />
      <input class="ph-label" placeholder="Понятная подпись" value="${esc(p.label)}" />
      <button type="button" class="icon-btn edit-row__del" title="Удалить">${icon("trash", 14)}</button>
    </div>`;

  openModal(`
    <h3 class="modal__title">${editing ? "Изменить меру поддержки" : "Новая мера поддержки"}</h3>
    <div class="edit-scroll">
      <div class="form-row"><label>Название меры *</label>
        <input id="sm-title" type="text" value="${esc(measure?.title || "")}" placeholder="Единовременная выплата" required /></div>
      <div class="form-row"><label>Описание</label>
        <textarea id="sm-desc" rows="2" placeholder="Кратко: кому и при каких условиях положена">${esc(measure?.description || "")}</textarea></div>
      <div class="form-row"><label>Подсказка для ИИ (когда подходит мера)</label>
        <textarea id="sm-hint" rows="2" placeholder="Ключевые слова и ситуации, по которым ИИ выберет эту меру">${esc(measure?.llm_hint || "")}</textarea></div>

      <div class="section-title">${icon("doc", 14)} Требуемые документы</div>
      <div id="sm-docs">${docs.map(docRow).join("")}</div>
      <button type="button" class="btn btn--ghost btn--sm" id="sm-add-doc">${icon("plus", 14)}<span>Добавить документ</span></button>

      <div class="section-title">${icon("book", 14)} Поля шаблона</div>
      <p class="muted" style="margin:-6px 0 8px">Разметка в .docx — <code>{{snake_case}}</code>. При загрузке шаблона поля распознаются автоматически; здесь можно поправить подписи.</p>
      <div id="sm-phs">${phs.map(phRow).join("")}</div>
      <button type="button" class="btn btn--ghost btn--sm" id="sm-add-ph">${icon("plus", 14)}<span>Добавить поле</span></button>

      <div class="form-row" style="margin-top:16px"><label>Шаблон заявления (.docx)</label>
        <div class="upload-drop" id="sm-tpl-drop">
          <input type="file" id="sm-tpl-file" accept=".docx" hidden />
          <div class="upload-drop__ico">${icon("doc", 30)}</div>
          <div class="upload-drop__txt" id="sm-tpl-name">${editing && measure.has_template
            ? "Шаблон загружен — выберите файл, чтобы заменить" : "Нажмите, чтобы выбрать .docx"}</div>
        </div>
      </div>
      <label class="emp-active"><input id="sm-active" type="checkbox" ${(!editing || measure.active) ? "checked" : ""} /> <span>Активна (видна гражданам в боте)</span></label>
    </div>
    <div class="login-error" id="sm-error" hidden></div>
    <div class="modal__actions">
      <button class="btn btn--ghost" type="button" id="sm-cancel">Отмена</button>
      <button class="btn btn--primary" type="button" id="sm-save">${editing ? "Сохранить" : "Создать"}</button>
    </div>`);

  const docsBox = $("#sm-docs"), phsBox = $("#sm-phs");
  $("#sm-add-doc").onclick = () => {
    docsBox.insertAdjacentHTML("beforeend", docRow());
    docsBox.lastElementChild.querySelector(".doc-name").focus();
  };
  $("#sm-add-ph").onclick = () => {
    phsBox.insertAdjacentHTML("beforeend", phRow());
    phsBox.lastElementChild.querySelector(".ph-key").focus();
  };
  docsBox.addEventListener("click", (e) => {
    const b = e.target.closest(".edit-row__del");
    if (b) b.closest(".edit-row").remove();
  });
  phsBox.addEventListener("click", (e) => {
    const b = e.target.closest(".edit-row__del");
    if (b) b.closest(".edit-row").remove();
  });

  const tplInput = $("#sm-tpl-file");
  $("#sm-tpl-drop").onclick = () => tplInput.click();
  tplInput.addEventListener("change", () => {
    $("#sm-tpl-name").textContent = tplInput.files[0]
      ? tplInput.files[0].name : "Нажмите, чтобы выбрать .docx";
  });

  $("#sm-cancel").onclick = closeModal;
  $("#sm-save").onclick = async () => {
    const err = $("#sm-error"); err.hidden = true;
    const title = $("#sm-title").value.trim();
    if (!title) { err.textContent = "Укажите название меры"; err.hidden = false; return; }
    const documents = $$("#sm-docs .doc-name").map((i) => i.value.trim()).filter(Boolean);
    const placeholders = $$("#sm-phs .edit-row").map((el) => ({
      key: $(".ph-key", el).value.trim(),
      label: $(".ph-label", el).value.trim(),
    })).filter((p) => p.key);
    const payload = {
      title,
      description: $("#sm-desc").value.trim(),
      llm_hint: $("#sm-hint").value.trim(),
      documents, placeholders,
      active: $("#sm-active").checked,
    };
    const btn = $("#sm-save");
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Сохранение…");
    try {
      let id = measure?.id;
      if (editing) {
        await api(`/settings/support-measures/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        const r = await api("/settings/support-measures", { method: "POST", body: JSON.stringify(payload) });
        id = r.measure.id;
      }
      const f = tplInput.files[0];
      if (f) await uploadMeasureTemplate(id, f);
      toast(editing ? "Мера обновлена" : "Мера создана", "ok");
      closeModal();
      renderApplications();
    } catch (e2) {
      err.textContent = e2.message; err.hidden = false;
      btn.disabled = false; btn.innerHTML = editing ? "Сохранить" : "Создать";
    }
  };
}

async function deleteMeasure(m) {
  if (!(await confirmDialog(`Мера поддержки «${m.title}» будет удалена.`, { title: "Удалить меру?" }))) return;
  try {
    await api(`/settings/support-measures/${m.id}`, { method: "DELETE" });
    toast("Мера удалена", "ok");
    renderApplications();
  } catch (e) {
    toast(e.message, "err");
  }
}

async function syncMeasuresKb() {
  const btn = $("#sync-sm");
  if (btn) { btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Синхронизация…"); }
  try {
    const r = await api("/settings/support-measures/sync-kb", { method: "POST" });
    toast(`В базу знаний выгружено мер: ${r.count}`, "ok");
  } catch (e) {
    toast(e.message, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = `${icon("book")}<span>Синхронизировать с ИИ</span>`; }
  }
}

function kv(label, value) {
  return `<div class="field"><div class="field__label">${esc(label)}</div><div class="field__value">${esc(value || "—")}</div></div>`;
}

function openApplication(id) {
  const a = state.applications.find((x) => x.id === id);
  if (!a) return;
  const ap = a.applicant;
  const st = APP_STATUS[a.status] || { label: a.status_label || a.status, cls: "open" };
  const docxUrl = `${API}/applications/${id}/docx`;
  const decided = a.status !== "submitted";
  const pcls = APP_PILL[a.status] || "pill--warn";

  let body;
  if (a.is_measure) {
    // Заявление сценария «Меры поддержки» — поля произвольные (по шаблону меры).
    const fields = (a.measure_fields || []).map((f) => kv(f.label, f.value)).join("");
    const docs = (a.documents || []).map((d) => `<li>${esc(d)}</li>`).join("");
    const files = (a.user_files || []).map((u, i) =>
      `<li><a href="${esc(u)}" target="_blank" rel="noopener">Документ ${i + 1}</a></li>`).join("");
    body = `
      <div class="section-title">${icon("contact", 14)} Заявитель</div>
      <div class="fields-grid">
        ${kv("Гражданин", a.citizen.name)}
        ${a.citizen.username ? kv("Username", "@" + a.citizen.username) : ""}
      </div>
      ${fields ? `<div class="section-title">${icon("doc", 14)} Данные заявления</div><div class="fields-grid">${fields}</div>` : ""}
      ${docs ? `<div class="section-title">${icon("book", 14)} Требуемые документы</div><ul class="app-list">${docs}</ul>` : ""}
      ${files ? `<div class="section-title">${icon("doc", 14)} Загруженные документы</div><ul class="app-list">${files}</ul>` : ""}
    `;
  } else {
    const family = (a.family || []).map((m) =>
      `<li>${esc(m.fio)} — ${esc(m.relation)}${m.birth_date ? `, ${esc(m.birth_date)}` : ""}</li>`).join("");
    const providers = (a.providers || []).map((p) =>
      `<li>${esc(p.name)} — л/с ${esc(p.account || "—")}</li>`).join("");
    body = `
      ${a.missing && a.missing.length
        ? `<div class="hero-banner hero-banner--warn">${icon("info", 18)}<span>Требуется уточнить: ${esc(a.missing.join(", "))}</span></div>` : ""}

      <div class="section-title">${icon("contact", 14)} Заявитель</div>
      <div class="fields-grid">
        ${kv("ФИО", ap.fio)}
        ${kv("Дата рождения", ap.birth_date)}
        ${kv("Паспорт", `${ap.passport_series || ""} ${ap.passport_number || ""}`.trim())}
        ${kv("Кем выдан", ap.passport_issued)}
        ${kv("Адрес", ap.address)}
        ${kv("Телефон", ap.phone)}
        ${kv("Категория льготы", `${a.category} (код ${a.category_code})`)}
        ${kv("Жильё", `${a.ownership}, комнат: ${a.rooms || "—"}`)}
      </div>

      ${family ? `<div class="section-title">${icon("family", 14)} Члены семьи</div><ul class="app-list">${family}</ul>` : ""}
      ${providers ? `<div class="section-title">${icon("building", 14)} Поставщики ЖКУ</div><ul class="app-list">${providers}</ul>` : ""}
      <div class="section-title">${icon("med", 14)} Способ выплаты</div>
      <div class="fields-grid">
        ${a.payment.method === "post"
          ? kv("Доставка", "Почтой по адресу")
          : kv("Банк / счёт", `${a.payment.bank || "—"} · ${a.payment.account || "—"}`)}
      </div>
    `;
  }

  openDrawer({
    title: `Заявление #${id}`,
    subtitle: `${esc(a.measure_title)} · <span class="pill ${pcls}">${esc(st.label)}</span> · ${esc(a.created_human)}`,
    body,
    footer: `
      <button class="btn btn--danger" id="app-delete">${icon("trash")}<span>Удалить</span></button>
      <a class="btn btn--soft" href="${docxUrl}" download>${icon("doc")}<span>Скачать .docx</span></a>
      <button class="btn btn--ghost" id="app-reject" ${decided ? "disabled" : ""}>${icon("status")}<span>Отклонить</span></button>
      <button class="btn btn--primary" id="app-approve" ${decided ? "disabled" : ""}>${icon("send")}<span>Одобрить</span></button>
    `,
  });

  const decide = async (decision, btn) => {
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Сохранение…");
    try {
      await api(`/applications/${id}/${decision}`, {
        method: "POST", body: JSON.stringify({ operator: state.operator }),
      });
      toast(decision === "approve" ? "Заявление одобрено" : "Заявление отклонено", "ok");
      closeDrawer();
      renderApplications();
    } catch (err) { btn.disabled = false; toast(err.message, "err"); }
  };
  if (!decided) {
    $("#app-approve").onclick = (e) => decide("approve", e.currentTarget);
    $("#app-reject").onclick = (e) => decide("reject", e.currentTarget);
  }
  $("#app-delete").onclick = async (e) => {
    const btn = e.currentTarget;
    if (!(await confirmDialog("Заявление будет удалено из списка.", { title: "Удалить заявление?" }))) return;
    btn.disabled = true;
    try {
      await api(`/applications/${id}`, { method: "DELETE" });
      toast("Заявление удалено", "ok"); closeDrawer(); renderApplications();
    } catch (err) { toast(err.message, "err"); }
  };
}

/* ============================================================
   РАЗДЕЛ 4 — АНАЛИТИКА
   ============================================================ */
/* ---------- загрузка материалов в базу знаний Dify ----------
   Тот же датасет, из которого бот берёт ответы и куда падают ответы операторов. */
function openKbUpload() {
  const ready = !!(state.meta && state.meta.kb_ready);
  openModal(`
    <h3 class="modal__title">Загрузка в базу знаний</h3>
    <p class="muted">Материалы попадут в ту же базу знаний Dify, из которой бот берёт ответы
      гражданам и куда сохраняются ответы операторов.</p>
    ${ready ? "" : `<div class="kb-warn">${icon("info", 16)}<span>База знаний Dify не настроена (раздел <b>dify.dataset</b> в конфиге). Загрузка недоступна.</span></div>`}
    <div class="kb-tabs">
      <button class="chart-tab active" data-tab="text" type="button">Текст</button>
      <button class="chart-tab" data-tab="file" type="button">Файл</button>
    </div>
    <div id="kb-pane-text" class="kb-pane">
      <input id="kb-title" class="kb-input" placeholder="Заголовок документа (необязательно)" />
      <textarea id="kb-text" class="kb-input" rows="7" placeholder="Вставьте текст материала для базы знаний…"></textarea>
      <div class="modal__actions">
        <button class="btn btn--primary" id="kb-text-go" ${ready ? "" : "disabled"}>${icon("send")}<span>Загрузить текст</span></button>
      </div>
    </div>
    <div id="kb-pane-file" class="kb-pane" hidden>
      <div class="upload-drop" id="kb-drop">
        <input type="file" id="kb-file" accept=".txt,.md,.markdown,.pdf,.docx,.doc,.csv,.xlsx,.html,.htm" hidden />
        <div class="upload-drop__ico">${icon("doc", 30)}</div>
        <div class="upload-drop__txt" id="kb-fname">Нажмите, чтобы выбрать файл (txt, md, pdf, docx, csv…)</div>
      </div>
      <div class="modal__actions">
        <button class="btn btn--primary" id="kb-file-go" disabled>${icon("send")}<span>Загрузить файл</span></button>
      </div>
    </div>`);

  $$("#modal-body .kb-tabs .chart-tab").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#modal-body .kb-tabs .chart-tab").forEach((x) => x.classList.toggle("active", x === b));
      $("#kb-pane-text").hidden = b.dataset.tab !== "text";
      $("#kb-pane-file").hidden = b.dataset.tab !== "file";
    }));

  const textGo = $("#kb-text-go");
  if (textGo) textGo.addEventListener("click", async () => {
    const text = $("#kb-text").value.trim();
    if (!text) { toast("Введите текст материала", "err"); return; }
    textGo.disabled = true; textGo.innerHTML = spinnerBtnHtml("Загрузка…");
    try {
      await api("/kb/upload-text", {
        method: "POST", body: JSON.stringify({ title: $("#kb-title").value, text }),
      });
      toast("Материал добавлен в базу знаний", "ok");
      closeModal();
    } catch (err) {
      textGo.disabled = false; textGo.innerHTML = `${icon("send")}<span>Загрузить текст</span>`;
      toast(err.message, "err");
    }
  });

  if (ready) {
    const drop = $("#kb-drop"), input = $("#kb-file"), go = $("#kb-file-go");
    drop.addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      const f = input.files[0];
      $("#kb-fname").textContent = f ? f.name : "Нажмите, чтобы выбрать файл (txt, md, pdf, docx, csv…)";
      go.disabled = !f;
    });
    go.addEventListener("click", async () => {
      const f = input.files[0];
      if (!f) return;
      go.disabled = true; go.innerHTML = spinnerBtnHtml("Загрузка…");
      try {
        const fd = new FormData();
        fd.append("file", f);
        const res = await fetch(`${API}/kb/upload-file`, {
          method: "POST", credentials: "same-origin", body: fd,
        });
        if (!res.ok) {
          let m = "Ошибка " + res.status;
          try { m = (await res.json()).detail || m; } catch (_) {}
          throw new Error(m);
        }
        toast("Файл добавлен в базу знаний", "ok");
        closeModal();
      } catch (err) {
        go.disabled = false; go.innerHTML = `${icon("send")}<span>Загрузить файл</span>`;
        toast(err.message, "err");
      }
    });
  }
}

async function renderAnalytics() {
  const root = $("#view-root");
  root.innerHTML = loadingBlock();
  setTitle("Аналитика", "Сводка по обращениям и участникам СВО");
  $("#topbar-actions").innerHTML = `
    <button class="btn btn--ghost" id="kb-upload-btn">${icon("book")}<span>В базу знаний</span></button>
    <a class="btn btn--soft" href="${API}/export/analytics" download>${icon("send")}<span>Экспорт</span></a>`;
  $("#kb-upload-btn").onclick = openKbUpload;

  const d = await api("/analytics");

  const metric = (val, label, cls = "", ico = "info") => `
    <div class="metric card ${cls}">
      <div class="metric__ico"><svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[ico] || ICONS.info}</svg></div>
      <div class="metric__val">${val}</div>
      <div class="metric__label">${label}</div>
    </div>`;

  // Бары/спарклайн рендерятся «пустыми» (data-* с целью), затем плавно
  // заполняются после отрисовки — см. animateCharts() ниже.
  const maxTopic = Math.max(1, ...d.topics.map((t) => t.count));
  const barRows = (arr, max) => arr.length
    ? arr.map((t) => `
      <div class="bar animate">
        <div class="bar__label" title="${esc(t.label)}">${esc(t.label)}</div>
        <div class="bar__track"><div class="bar__fill" data-w="${Math.round(t.count / max * 100)}"></div></div>
        <div class="bar__val">${t.count}</div>
      </div>`).join("")
    : `<p class="muted">Нет данных</p>`;

  const maxMeasure = Math.max(1, ...d.support_measures.map((t) => t.count));

  const cov = d.orgs.coverage_pct;
  const donut = `
    <div class="donut-wrap">
      <div class="donut" style="background:conic-gradient(var(--blue) ${cov * 3.6}deg, rgba(var(--accent-rgb),.12) 0deg)">
        <div class="donut__hole"><div><div class="donut__pct">${cov}%</div><div class="donut__sub">охват</div></div></div>
      </div>
      <div class="legend">
        <div class="legend__row"><span class="legend__dot" style="background:var(--blue)"></span>«Время Героя»: <b>&nbsp;${d.orgs.vremya_geroev}</b></div>
        <div class="legend__row"><span class="legend__dot" style="background:var(--blue-400)"></span>«Герои Подмосковья»: <b>&nbsp;${d.orgs.geroi_podmoskovya}</b></div>
        <div class="legend__row"><span class="legend__dot" style="background:#8fb3ff"></span>Ассоциация ветеранов: <b>&nbsp;${d.orgs.associaciya}</b></div>
        <div class="legend__row muted">Охвачено ${d.orgs.covered} из ${d.orgs.total}</div>
      </div>
    </div>`;

  root.innerHTML = `
    <div class="fade-in">
      <div class="metrics-grid">
        ${metric(d.appeals.day, "Обращений за день", "", "info")}
        ${metric(d.appeals.week, "За неделю", "", "info")}
        ${metric(d.appeals.month, "За месяц", "", "info")}
        ${metric(d.in_person, "Очных обращений в администрацию", "ok", "family")}
        ${metric(d.applications ?? 0, "Заявлений на меры поддержки", "ok", "doc")}
        ${metric(d.total_usvo, "Всего УСВО на учёте", "", "org")}
        ${metric(d.unemployed, "Нуждаются в трудоустройстве", "warn", "work")}
        ${metric(d.stale_contacts, `Без контакта > ${Math.round(d.stale_days / 30)} мес.`, "accent", "contact")}
        ${metric(d.orgs.coverage_pct + "%", "Охват ветеранскими организациями", "ok", "status")}
      </div>

      ${renderDynamics(d.series)}

      <div class="charts-grid">
        <div class="chart-card card">
          <h3>Тематики обращений</h3>
          <div class="bar-row">${barRows(d.topics, maxTopic)}</div>
        </div>
        <div class="chart-card card">
          <h3>Наиболее востребованные меры поддержки</h3>
          <div class="bar-row">${barRows(d.support_measures, maxMeasure)}</div>
        </div>
        <div class="chart-card card" style="grid-column:1/-1">
          <h3>Охват участием в организациях ветеранов</h3>
          ${donut}
        </div>
      </div>

      ${renderHeatmap(d.heatmap)}
    </div>`;

  // Карта Leaflet инициализируется после вставки разметки в DOM.
  initHeatmapMap(d.heatmap);
  animateCharts();
  setupDynamics(d.series);
}

/* ----- Интерактивный график «Динамика по показателям» -----
   x — дата, y — выбранный показатель (день/неделя/месяц/очные/заявления).
   Показатель переключается кнопками; график перерисовывается с анимацией. */
function renderDynamics(series) {
  if (!series || !series.length) return "";
  const tabs = series.map((s, i) =>
    `<button class="chart-tab ${i === 0 ? "active" : ""}" data-key="${s.key}">${esc(s.label)}</button>`
  ).join("");
  return `
    <div class="chart-card card dyn-card">
      <div class="dyn-head">
        <h3>${icon("spark", 16)} Динамика обращений · 14 дней</h3>
        <div class="chart-tabs">${tabs}</div>
      </div>
      <div class="linechart-wrap" id="dyn-chart"></div>
    </div>`;
}

function setupDynamics(series) {
  if (!series || !series.length) return;
  state.dyn = { series, key: series[0].key };
  $$("#view-root .chart-tab").forEach((b) =>
    b.addEventListener("click", () => {
      state.dyn.key = b.dataset.key;
      $$("#view-root .chart-tab").forEach((x) => x.classList.toggle("active", x === b));
      drawDyn();
    }));
  drawDyn();
}

function niceMax(v) {
  v = Math.max(1, v);
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return Math.ceil(v / p) * p;
}

function drawDyn() {
  const wrap = $("#dyn-chart");
  if (!wrap || !state.dyn) return;
  const s = state.dyn.series.find((x) => x.key === state.dyn.key) || state.dyn.series[0];
  const pts = s.points, n = pts.length;
  if (!n) { wrap.innerHTML = `<p class="muted">Нет данных</p>`; return; }

  const W = 820, H = 260, l = 46, r = 18, t = 20, b = 34;
  const pw = W - l - r, ph = H - t - b;
  const mx = niceMax(Math.max(...pts.map((p) => p.count)) * 1.12);
  const X = (k) => l + (n > 1 ? k * pw / (n - 1) : pw / 2);
  const Y = (v) => t + ph * (1 - v / mx);

  let grid = "";
  for (let g = 0; g <= 4; g++) {
    const val = mx * g / 4, y = Y(val);
    grid += `<line class="lc-grid" x1="${l}" y1="${y}" x2="${W - r}" y2="${y}" vector-effect="non-scaling-stroke"/>`;
    grid += `<text class="lc-ylab" x="${l - 8}" y="${y + 3}" text-anchor="end">${Math.round(val)}</text>`;
  }
  const step = Math.max(1, Math.ceil(n / 7));
  let xlab = "";
  for (let k = 0; k < n; k += step)
    xlab += `<text class="lc-xlab" x="${X(k)}" y="${H - 10}" text-anchor="middle">${esc(pts[k].date)}</text>`;

  const lineD = pts.map((p, k) => `${k ? "L" : "M"}${X(k).toFixed(1)},${Y(p.count).toFixed(1)}`).join(" ");
  const areaD = `M${X(0).toFixed(1)},${(t + ph).toFixed(1)} `
    + pts.map((p, k) => `L${X(k).toFixed(1)},${Y(p.count).toFixed(1)}`).join(" ")
    + ` L${X(n - 1).toFixed(1)},${(t + ph).toFixed(1)} Z`;
  const dots = pts.map((p, k) =>
    `<circle class="lc-dot" cx="${X(k).toFixed(1)}" cy="${Y(p.count).toFixed(1)}" r="3" data-k="${k}" vector-effect="non-scaling-stroke"/>`).join("");

  wrap.innerHTML = `
    <svg class="linechart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(s.label)}">
      <defs><linearGradient id="lcfill" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="var(--blue)" stop-opacity=".34"/>
        <stop offset="1" stop-color="var(--blue)" stop-opacity="0"/></linearGradient></defs>
      ${grid}
      <path class="lc-area" d="${areaD}" fill="url(#lcfill)"/>
      <path class="lc-line" d="${lineD}" vector-effect="non-scaling-stroke"/>
      ${dots}
      ${xlab}
      <line class="lc-cursor" x1="0" y1="${t}" x2="0" y2="${t + ph}" vector-effect="non-scaling-stroke" style="opacity:0"/>
    </svg>
    <div class="lc-tip" hidden></div>`;

  const svgEl = wrap.querySelector(".linechart");
  const tip = wrap.querySelector(".lc-tip");
  const cursor = wrap.querySelector(".lc-cursor");
  const lineEl = wrap.querySelector(".lc-line");
  const dotEls = [...wrap.querySelectorAll(".lc-dot")];

  // Анимация «прорисовки» линии: меряем реальную длину пути и гоним dashoffset → 0.
  // Длину умножаем на масштаб отрисовки, т.к. на линии vector-effect:non-scaling-stroke
  // (штрих считается в экранных пикселях) — так линия гарантированно скрыта в начале и
  // полностью сплошная в конце независимо от ширины контейнера.
  if (lineEl && lineEl.getTotalLength) {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const scale = (svgEl.getBoundingClientRect().width || W) / W;
    const len = lineEl.getTotalLength() * (scale || 1);
    lineEl.style.strokeDasharray = String(len);
    if (reduce) {
      lineEl.style.strokeDashoffset = "0";
    } else {
      lineEl.style.strokeDashoffset = String(len);
      void lineEl.getBoundingClientRect(); // принудительный reflow до перехода
      requestAnimationFrame(() => { lineEl.style.strokeDashoffset = "0"; });
    }
  }

  const denom = n > 1 ? pw / (n - 1) : pw; // защита от деления на ноль при n === 1
  const move = (e) => {
    const rect = svgEl.getBoundingClientRect();
    if (!rect.width) return;
    const relX = (e.clientX - rect.left) / rect.width * W;
    let k = Math.round((relX - l) / denom);
    k = Math.max(0, Math.min(n - 1, k));
    const px = X(k), py = Y(pts[k].count);
    cursor.setAttribute("x1", px); cursor.setAttribute("x2", px); cursor.style.opacity = "1";
    dotEls.forEach((d2, i) => d2.classList.toggle("on", i === k));
    tip.innerHTML = `<b>${esc(pts[k].date)}</b><span>${pts[k].count} <i>${esc(s.unit || "")}</i></span>`;
    tip.hidden = false;
    // Подсказку держим в пределах контейнера, чтобы не обрезалась у краёв.
    const wrapW = wrap.clientWidth || W;
    const half = tip.offsetWidth / 2;
    const leftPx = Math.max(half + 2, Math.min(wrapW - half - 2, px / W * wrapW));
    tip.style.left = leftPx + "px";
    tip.style.top = (py / H * 100) + "%";
  };
  const leave = () => {
    tip.hidden = true; cursor.style.opacity = "0";
    dotEls.forEach((d2) => d2.classList.remove("on"));
  };
  svgEl.addEventListener("mousemove", move);
  svgEl.addEventListener("mouseleave", leave);
}

/* Плавное заполнение баров при загрузке страницы. */
function animateCharts() {
  setTimeout(() => {
    $$("#view-root .bar__fill").forEach((el) => { el.style.width = (el.dataset.w || 0) + "%"; });
  }, 60);
}

/* Тепловая карта Ленинского городского округа: очаги обращений.
   Каркас: реальная интерактивная карта Leaflet инициализируется в initHeatmapMap()
   уже после вставки разметки в DOM (карте нужен существующий контейнер с размерами). */
function renderHeatmap(h) {
  if (!h) return "";
  const max = Math.max(1, ...h.hotspots.map((p) => p.count));
  const legend = h.hotspots
    .slice().sort((a, b) => b.count - a.count).slice(0, 5)
    .map((p) => `<div class="legend__row"><span class="legend__dot" style="background:hsl(${18 + Math.round((1 - p.intensity) * 36)},90%,52%)"></span>${esc(p.name)}: <b>&nbsp;${p.count}</b></div>`)
    .join("");

  return `
    <div class="heatmap-card card">
      <div class="heatmap-head">
        <h3>${icon("pin", 16)} Тепловая карта обращений · ${esc(h.area)}</h3>
        <span class="ai-badge">${icon("wand", 13)} анализ ИИ</span>
      </div>
      <div class="heatmap-grid">
        <div class="heatmap-canvas">
          <div id="heatmap-map" class="heatmap-map"></div>
          <div class="heatmap-scale">Очаги: <b>${h.hotspots.length}</b> · макс. ${max} обращений</div>
        </div>
        <div class="heatmap-side">
          <div class="legend">${legend}</div>
          <div class="ai-note ai-note--alert">
            <span class="ai-tag ai-tag--alert">${icon("info", 12)} Заметка ИИ · новая тематика</span><br/>
            ${esc(h.ai_insight)}
          </div>
        </div>
      </div>
    </div>`;
}

// Активный экземпляр карты — чтобы корректно пересоздавать при перерисовке раздела.
let _heatMap = null;

/* Инициализация реальной карты Leaflet с тепловым слоем и метками-очагами. */
function initHeatmapMap(h) {
  const el = document.getElementById("heatmap-map");
  if (!el || !h || typeof L === "undefined") return;

  // Пересоздаём при повторном входе в раздел (старый контейнер уже удалён из DOM).
  if (_heatMap) { try { _heatMap.remove(); } catch (_) {} _heatMap = null; }

  const c = h.center || { lat: 55.556, lng: 37.718, zoom: 12 };
  // Колёсико мыши приближает карту; бейдж атрибуции скрыт по просьбе.
  const map = L.map(el, { scrollWheelZoom: true, attributionControl: false })
    .setView([c.lat, c.lng], c.zoom || 12);
  _heatMap = map;

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
  }).addTo(map);

  const max = Math.max(1, ...h.hotspots.map((p) => p.count));

  // Тепловой слой (если плагин leaflet.heat загружен).
  if (typeof L.heatLayer === "function") {
    const points = h.hotspots.map((p) => [p.lat, p.lng, Math.max(0.15, p.count / max)]);
    L.heatLayer(points, {
      radius: 38, blur: 28, maxZoom: 14, minOpacity: 0.35,
      gradient: { 0.2: "#2b8cff", 0.4: "#ffd14d", 0.65: "#ff8c1a", 1.0: "#e4002b" },
    }).addTo(map);
  }

  // Метки-очаги с радиусом по числу обращений и всплывающей карточкой.
  const bounds = [];
  h.hotspots.forEach((p) => {
    bounds.push([p.lat, p.lng]);
    const hue = 18 + Math.round((1 - p.intensity) * 36);
    L.circleMarker([p.lat, p.lng], {
      radius: 6 + Math.round(p.intensity * 14),
      color: `hsl(${hue},90%,45%)`,
      fillColor: `hsl(${hue},92%,52%)`,
      fillOpacity: 0.55, weight: 2,
    })
      .addTo(map)
      .bindPopup(
        `<b>${esc(p.name)}</b><br/>Обращений: <b>${p.count}</b><br/>Плотность очага: ${Math.round(p.intensity * 100)}%`,
        { className: "heat-popup" }
      )
      .bindTooltip(`${esc(p.name)} · ${p.count}`, { direction: "top", offset: [0, -4] });
  });

  if (bounds.length) map.fitBounds(bounds, { padding: [40, 40] });
  // Карта инициализирована в скрытом/изменяемом контейнере — пересчитать размеры.
  setTimeout(() => { try { map.invalidateSize(); } catch (_) {} }, 120);
}

/* ============================================================
   НАСТРОЙКИ — учётные записи сотрудников (только админ)
   ============================================================ */
async function renderSettings() {
  const root = $("#view-root");
  if (!state.isAdmin) { switchView("appeals"); return; }
  root.innerHTML = tableSkeleton();
  setTitle("Настройки", "Регламент обращений и учётные записи сотрудников");
  $("#topbar-actions").innerHTML = `
    <button class="btn btn--ghost" id="reload-emp">${icon("refresh")}<span>Обновить</span></button>
    <button class="btn btn--primary" id="add-emp">${icon("contact")}<span>Добавить сотрудника</span></button>`;
  $("#reload-emp").onclick = renderSettings;
  $("#add-emp").onclick = () => openEmployeeForm(null);

  let items = [];
  let sla = null;
  try {
    ({ items } = await api("/settings/employees"));
    sla = await api("/settings/sla").catch(() => null);
  } catch (e) {
    root.innerHTML = emptyState("Не удалось загрузить", e.message);
    return;
  }

  const body = items.length ? items.map((e) => `
    <tr data-id="${e.id}">
      <td class="q">${esc(e.name)}<small>${esc(e.position || "—")}</small></td>
      <td>${esc(e.login)}</td>
      <td>${esc(e.phone || "—")}</td>
      <td>${e.active
        ? '<span class="pill pill--ok"><span class="pill__dot"></span>Активен</span>'
        : '<span class="pill pill--warn"><span class="pill__dot"></span>Отключён</span>'}</td>
      <td class="row-actions">
        <button class="btn btn--soft btn--sm" data-act="edit">${icon("wand")}<span>Изменить</span></button>
        <button class="btn btn--danger btn--sm" data-act="del">${icon("trash")}<span>Удалить</span></button>
      </td>
    </tr>`).join("") : "";

  root.innerHTML = `
    ${slaSettingsCard(sla)}
    <div class="fade-in card hero-banner hero-banner--info" style="margin-bottom:14px">
      ${icon("info", 18)}
      <span>Сотрудник входит в кабинет по своему логину и паролю и отвечает на обращения
      только под своей учётной записью. Администратор может назначить ответственным любого сотрудника.</span>
    </div>
    ${items.length ? `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr>
          <th>Сотрудник</th><th>Логин</th><th>Телефон</th><th>Статус</th><th></th>
        </tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>` : emptyState("Сотрудников пока нет",
      "Создайте первую учётную запись — сотрудник сможет войти и отвечать на обращения.", "doc")}`;

  $$("#view-root tbody tr").forEach((tr) => {
    const id = +tr.dataset.id;
    const emp = items.find((x) => x.id === id);
    tr.querySelector('[data-act="edit"]').onclick = () => openEmployeeForm(emp);
    tr.querySelector('[data-act="del"]').onclick = () => deleteEmployee(emp);
  });

  bindSlaSettings(sla);
}

/* Регламент времени ответа на обращения (SLA). Раньше жил только в config.yaml —
   теперь администратор меняет его прямо в кабинете (значение хранится в БД и
   имеет приоритет над дефолтом из конфига). */
function slaSettingsCard(sla) {
  if (!sla) return "";
  const days = sla.sla_business_days ?? sla.default_business_days ?? 3;
  const min = sla.min ?? 1, max = sla.max ?? 30;
  const def = sla.default_business_days;
  return `
    <div class="fade-in card sla-card" style="margin-bottom:14px">
      <div class="sla-card__title">${icon("clock", 16)} Регламент времени ответа на обращения</div>
      <div class="sla-card__sub">Обращение считается просроченным, если ответ не дан за указанное число
        <b>календарных</b> дней (считаются все дни подряд, включая выходные, с момента поступления
        обращения). Влияет на бейдж «Просрочено», сортировку и напоминания операторам.</div>
      <form id="sla-form" class="sla-form">
        <label for="sla-days" class="sla-form__label">Срок ответа</label>
        <div class="sla-field">
          <input id="sla-days" type="number" min="${min}" max="${max}" step="1" value="${days}" inputmode="numeric" />
          <span class="sla-field__unit">дн.</span>
        </div>
        <button class="btn btn--primary" type="submit" id="sla-save">Сохранить</button>
        ${def != null ? `<span class="sla-form__hint">По умолчанию — ${def} дн. Допустимо от ${min} до ${max}.</span>` : ""}
      </form>
      <div class="login-error" id="sla-error" hidden></div>
    </div>`;
}

function bindSlaSettings(sla) {
  const form = $("#sla-form");
  if (!form) return;
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const err = $("#sla-error"); err.hidden = true;
    const btn = $("#sla-save");
    const val = parseInt($("#sla-days").value, 10);
    if (!Number.isFinite(val)) { err.textContent = "Укажите число дней"; err.hidden = false; return; }
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Сохранение…");
    try {
      const res = await api("/settings/sla", { method: "PUT", body: JSON.stringify({ sla_business_days: val }) });
      // Обновляем мету, чтобы бейджи/подписи в «Обращениях» сразу учли новый регламент.
      state.meta = await api("/meta");
      toast(`Регламент обновлён: ${res.sla_business_days} дн.`, "ok");
      renderSettings();
    } catch (e) {
      err.textContent = e.message; err.hidden = false;
      btn.disabled = false; btn.innerHTML = "Сохранить";
    }
  };
}

function openEmployeeForm(emp) {
  const editing = !!emp;
  openModal(`
    <h3 class="modal__title">${editing ? "Изменить сотрудника" : "Новый сотрудник"}</h3>
    <form id="emp-form" class="emp-form" autocomplete="off">
      <div class="form-row"><label>ФИО *</label>
        <input id="emp-name" type="text" value="${esc(emp?.name || "")}" placeholder="Иванова О. П." required /></div>
      <div class="form-row"><label>Должность</label>
        <input id="emp-position" type="text" value="${esc(emp?.position || "")}" placeholder="Специалист контакт-центра" /></div>
      <div class="form-row"><label>Логин *</label>
        <input id="emp-login" type="text" value="${esc(emp?.login || "")}" placeholder="operator2@mosreg.ru" required /></div>
      <div class="form-row"><label>Телефон</label>
        <input id="emp-phone" type="text" value="${esc(emp?.phone || "")}" placeholder="+7 (___) ___-__-__" /></div>
      <div class="form-row"><label>Пароль ${editing ? "<small>(оставьте пустым, чтобы не менять)</small>" : "*"}</label>
        <input id="emp-password" type="text" placeholder="${editing ? "•••••• без изменений" : "Задайте пароль"}" ${editing ? "" : "required"} /></div>
      ${editing ? `<label class="emp-active"><input id="emp-active" type="checkbox" ${emp.active ? "checked" : ""} /> <span>Активна (может входить)</span></label>` : ""}
      <div class="login-error" id="emp-error" hidden></div>
      <div class="modal__actions">
        <button class="btn btn--ghost" type="button" id="emp-cancel">Отмена</button>
        <button class="btn btn--primary" type="submit" id="emp-save">${editing ? "Сохранить" : "Создать"}</button>
      </div>
    </form>`);

  $("#emp-cancel").onclick = closeModal;
  $("#emp-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const err = $("#emp-error"); err.hidden = true;
    const payload = {
      name: $("#emp-name").value.trim(),
      login: $("#emp-login").value.trim(),
      position: $("#emp-position").value.trim(),
      phone: $("#emp-phone").value.trim(),
    };
    const pw = $("#emp-password").value;
    if (pw) payload.password = pw;
    if (editing) payload.active = $("#emp-active").checked;
    const btn = $("#emp-save");
    btn.disabled = true; btn.innerHTML = spinnerBtnHtml("Сохранение…");
    try {
      if (editing) {
        await api(`/settings/employees/${emp.id}`, { method: "PUT", body: JSON.stringify(payload) });
        toast("Сотрудник обновлён", "ok");
      } else {
        await api("/settings/employees", { method: "POST", body: JSON.stringify(payload) });
        toast("Сотрудник создан", "ok");
      }
      closeModal();
      renderSettings();
    } catch (e2) {
      err.textContent = e2.message; err.hidden = false;
      btn.disabled = false; btn.innerHTML = editing ? "Сохранить" : "Создать";
    }
  };
}

async function deleteEmployee(emp) {
  if (!(await confirmDialog(`Учётная запись «${emp.name}» будет удалена — сотрудник больше не сможет войти в кабинет.`, { title: "Удалить учётную запись?" }))) return;
  try {
    await api(`/settings/employees/${emp.id}`, { method: "DELETE" });
    toast("Сотрудник удалён", "ok");
    renderSettings();
  } catch (e) {
    toast(e.message, "err");
  }
}

/* ============================================================
   РАЗДЕЛ — РАССЫЛКИ (push-уведомления пользователям MAX, только админ)
   ============================================================ */
function broadcastFormHtml(audience) {
  const ready = !!(audience && audience.max_ready);
  const total = audience ? audience.total : 0;
  const subs = audience ? audience.subscribers : 0;
  return `
    ${ready ? "" : `<div class="kb-warn">${icon("info", 16)}<span>MAX-бот не настроен (<b>max.bot_token</b> в конфиге). Рассылка недоступна.</span></div>`}
    <div class="bc-targets">
      <label class="bc-target"><input type="radio" name="bc-target" value="all" checked />
        <span><b>Все пользователи MAX</b><small><b id="bc-count-all">${total}</b> чел. — все, кто когда-либо писал боту</small></span></label>
      <label class="bc-target"><input type="radio" name="bc-target" value="subscribers" />
        <span><b>Только подписчики</b><small><b id="bc-count-subs">${subs}</b> чел. — подписаны на уведомления</small></span></label>
    </div>
    <div class="form-row"><label>Текст сообщения</label>
      <textarea id="bc-text" rows="5" placeholder="Например: Открыт приём заявлений на новую меру поддержки для семей участников СВО…"></textarea></div>
    <div class="modal__actions">
      <button class="btn btn--primary" id="bc-send" ${ready ? "" : "disabled"}>${icon("send")}<span>Отправить рассылку</span></button>
    </div>
    <div id="bc-result" class="bc-result" hidden></div>`;
}

function bindBroadcastForm(scope) {
  const send = $("#bc-send", scope);
  if (!send) return;
  send.onclick = async () => {
    const text = ($("#bc-text", scope).value || "").trim();
    if (!text) { toast("Введите текст сообщения", "err"); return; }
    const checked = $$('input[name="bc-target"]', scope).find((r) => r.checked);
    const target = (checked && checked.value) || "all";
    if (!(await confirmDialog(
      target === "subscribers"
        ? "Сообщение получат все подписанные пользователи бота MAX."
        : "Сообщение получат ВСЕ пользователи бота MAX.",
      { title: "Отправить рассылку?", confirmText: "Отправить", danger: false, iconName: "send" }
    ))) return;
    const rbox = $("#bc-result", scope);
    rbox.hidden = false; rbox.className = "bc-result"; rbox.innerHTML = loadingBlock("Отправка сообщений…");
    send.disabled = true; send.innerHTML = spinnerBtnHtml("Отправка…");
    try {
      const r = await api("/broadcast", { method: "POST", body: JSON.stringify({ text, target }) });
      rbox.className = "bc-result " + (r.failed ? "bc-result--warn" : "bc-result--ok");
      rbox.innerHTML = `${icon("send", 16)} Доставлено: <b>${r.sent}</b> из ${r.total}. Ошибок: <b>${r.failed}</b>.`
        + (r.failed ? `<div class="bc-errors">${(r.errors || []).slice(0, 12).map((e) =>
          `<div>ID ${esc(e.user_id || "—")}: ${esc(e.error)}</div>`).join("")}</div>` : "");
      // Обновляем счётчики аудитории по факту рассылки: если пользователь отписался,
      // но админ не рефрешил страницу, число подписчиков теперь станет актуальным.
      if (r.audience) {
        const ca = $("#bc-count-all", scope), cs = $("#bc-count-subs", scope);
        if (ca) ca.textContent = r.audience.total;
        if (cs) cs.textContent = r.audience.subscribers;
      }
      toast(`Рассылка: доставлено ${r.sent}/${r.total}`, r.failed ? "" : "ok");
    } catch (err) {
      rbox.className = "bc-result bc-result--err";
      rbox.innerHTML = `${icon("info", 16)} ${esc(err.message)}`;
      toast(err.message, "err");
    } finally {
      send.disabled = false; send.innerHTML = `${icon("send")}<span>Отправить рассылку</span>`;
    }
  };
}

async function renderBroadcast() {
  const root = $("#view-root");
  if (!state.isAdmin) { switchView("appeals"); return; }
  setTitle("Рассылки", "Push-уведомления пользователям бота MAX");
  $("#topbar-actions").innerHTML =
    `<button class="btn btn--ghost" id="reload-bc">${icon("refresh")}<span>Обновить</span></button>`;
  root.innerHTML = loadingBlock();
  let audience = { total: 0, subscribers: 0, max_ready: false };
  try { audience = await api("/broadcast/audience"); } catch (_) {}
  $("#reload-bc").onclick = renderBroadcast;
  root.innerHTML = `<div class="fade-in card bc-card">
    <div class="hero-banner hero-banner--info">${icon("info", 18)}<span>Сообщение придёт пользователям прямо в чат бота MAX.
    Ошибки доставки отдельным получателям не останавливают рассылку — итог показан внизу.</span></div>
    ${broadcastFormHtml(audience)}</div>`;
  bindBroadcastForm(root);
}

function openBroadcastModal() {
  openModal(`<h3 class="modal__title">Оповестить пользователей MAX</h3>
    <div id="bc-modal-body">${loadingBlock()}</div>`);
  const fill = (a) => {
    $("#bc-modal-body").innerHTML = broadcastFormHtml(a);
    bindBroadcastForm($("#modal-body"));
  };
  api("/broadcast/audience").then(fill).catch(() =>
    fill({ total: 0, subscribers: 0, max_ready: false }));
}

/* ============================================================
   РАЗДЕЛ — ЖУРНАЛ ДЕЙСТВИЙ (аудит-лог, только админ)
   ============================================================ */
const AUDIT_ACTIONS = {
  answer_appeal: "Ответ на обращение",
  delete_appeal: "Удаление обращения",
  update_usvo: "Правка карточки УСВО",
  delete_usvo: "Удаление карточки УСВО",
  clear_usvo: "Очистка карточек УСВО",
  import_usvo: "Импорт карточек УСВО",
  decide_application: "Решение по заявлению",
  delete_application: "Удаление заявления",
  broadcast: "Массовая рассылка",
};

async function renderAudit() {
  const root = $("#view-root");
  if (!state.isAdmin) { switchView("appeals"); return; }
  root.innerHTML = tableSkeleton();
  setTitle("Журнал действий", "Аудит действий сотрудников кабинета");
  $("#topbar-actions").innerHTML =
    `<button class="btn btn--ghost" id="reload-audit">${icon("refresh")}<span>Обновить</span></button>`;
  $("#reload-audit").onclick = renderAudit;
  let items = [];
  try {
    ({ items } = await api("/audit"));
  } catch (e) {
    root.innerHTML = emptyState("Не удалось загрузить", e.message);
    return;
  }
  if (!items.length) {
    root.innerHTML = emptyState("Журнал пуст",
      "Действия операторов (ответы, правки, удаления, рассылки) появятся здесь.", "doc");
    return;
  }
  const rows = items.map((x) => `
    <tr>
      <td>${esc(x.at_human)}</td>
      <td>${esc(x.user_name || x.user_sub || "—")}</td>
      <td><span class="pill pill--info">${esc(AUDIT_ACTIONS[x.action] || x.action)}</span></td>
      <td>${esc(x.entity || "")}${x.entity_id ? ` #${esc(x.entity_id)}` : ""}</td>
      <td class="q">${esc(x.details || "")}</td>
    </tr>`).join("");
  root.innerHTML = `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Объект</th><th>Детали</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

/* ============================================================
   КАРКАС / РОУТИНГ
   ============================================================ */
const VIEWS = {
  appeals: { title: "Обращения", render: renderAppeals },
  cards: { title: "Карточки УСВО", render: renderCards },
  "ai-chat": { title: "Вопрос-ответ по нормативке", render: renderAiChat },
  applications: { title: "Заявления", render: renderApplications },
  analytics: { title: "Аналитика", render: renderAnalytics },
  broadcast: { title: "Рассылки", render: renderBroadcast, adminOnly: true },
  audit: { title: "Журнал действий", render: renderAudit, adminOnly: true },
  settings: { title: "Настройки", render: renderSettings, adminOnly: true },
};

setInterval(() => {
  if (document.hidden) return; // вкладка не видна — незачем дёргать сеть/DOM
  if ($("#modal") && !$("#modal").hidden) return;
  if (drawerOpen()) return; // не перерисовываем список под открытой панелью
  // Тихое автообновление: без скелетона и без перестройки DOM, если данные не
  // изменились (см. renderAppeals/renderApplications) — интерфейс не «дёргается».
  if (state.view === "appeals") renderAppeals({ silent: true }).catch((e) => toast(e.message, "err"));
  else if (state.view === "applications") renderApplications({ silent: true }).catch((e) => toast(e.message, "err"));
}, 15000);

function setTitle(t, sub = "") {
  $("#view-title").textContent = t;
  $("#view-subtitle").textContent = sub;
}

function switchView(name) {
  if (!VIEWS[name]) name = "appeals";
  // Админский раздел недоступен сотруднику — молча уводим на «Обращения».
  if (VIEWS[name].adminOnly && !state.isAdmin) name = "appeals";
  state.view = name;
  $$(".nav__item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  VIEWS[name].render().catch((e) => {
    $("#view-root").innerHTML = emptyState("Не удалось загрузить", e.message);
  });
}

/* Аккуратные минималистичные иллюстрации для пустых экранов. */
const EMPTY_ART = {
  // Спящий робот — «всё обработано, ИИ отдыхает».
  robot: `<svg class="empty__art" viewBox="0 0 200 160" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="ea-body" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#3b6fe0"/></linearGradient>
      <linearGradient id="ea-screen" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#101a33"/><stop offset="1" stop-color="#1b294d"/></linearGradient>
    </defs>
    <ellipse cx="100" cy="142" rx="58" ry="9" fill="#000" opacity=".18"/>
    <rect x="52" y="58" width="96" height="74" rx="20" fill="url(#ea-body)"/>
    <rect x="52" y="58" width="96" height="74" rx="20" fill="#fff" opacity=".06"/>
    <rect x="62" y="70" width="76" height="50" rx="14" fill="url(#ea-screen)"/>
    <path d="M78 96c4 5 8 5 12 0" stroke="#6ee7ff" stroke-width="3.2" stroke-linecap="round"/>
    <path d="M110 96c4 5 8 5 12 0" stroke="#6ee7ff" stroke-width="3.2" stroke-linecap="round"/>
    <line x1="100" y1="44" x2="100" y2="58" stroke="#7c5cff" stroke-width="4" stroke-linecap="round"/>
    <circle cx="100" cy="40" r="6" fill="#22b8e6"/>
    <rect x="40" y="84" width="12" height="26" rx="6" fill="#3b6fe0"/>
    <rect x="148" y="84" width="12" height="26" rx="6" fill="#3b6fe0"/>
    <text class="zzz" x="150" y="56" font-size="16" font-weight="700" fill="#9b7bff">z</text>
    <text class="zzz" x="160" y="44" font-size="20" font-weight="700" fill="#9b7bff">Z</text>
    <text class="zzz" x="172" y="30" font-size="26" font-weight="800" fill="#7c5cff">Z</text>
  </svg>`,
  // Папка с лупой — «ничего не найдено».
  search: `<svg class="empty__art" viewBox="0 0 200 160" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="ea-folder" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#3b6fe0"/><stop offset="1" stop-color="#5b93ff"/></linearGradient>
    </defs>
    <ellipse cx="100" cy="144" rx="56" ry="8" fill="#000" opacity=".18"/>
    <path d="M44 56h34l10 12h68a8 8 0 0 1 8 8v52a8 8 0 0 1-8 8H44a8 8 0 0 1-8-8V64a8 8 0 0 1 8-8z" fill="url(#ea-folder)"/>
    <path d="M44 78h120v50a8 8 0 0 1-8 8H52a8 8 0 0 1-8-8z" fill="#fff" opacity=".10"/>
    <circle cx="92" cy="100" r="22" fill="none" stroke="#e8eef9" stroke-width="6"/>
    <line x1="108" y1="116" x2="124" y2="132" stroke="#e8eef9" stroke-width="7" stroke-linecap="round"/>
    <circle cx="92" cy="100" r="22" fill="#22b8e6" opacity=".14"/>
  </svg>`,
  // Документ-галочка — нейтральный «пока пусто».
  doc: `<svg class="empty__art" viewBox="0 0 200 160" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    <defs>
      <linearGradient id="ea-doc" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#3b6fe0"/></linearGradient>
    </defs>
    <ellipse cx="100" cy="144" rx="50" ry="8" fill="#000" opacity=".18"/>
    <rect x="64" y="30" width="72" height="96" rx="12" fill="url(#ea-doc)"/>
    <rect x="64" y="30" width="72" height="96" rx="12" fill="#fff" opacity=".07"/>
    <path d="M118 30v18h18" fill="#fff" opacity=".25"/>
    <line x1="78" y1="64" x2="122" y2="64" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".7"/>
    <line x1="78" y1="78" x2="122" y2="78" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".5"/>
    <line x1="78" y1="92" x2="104" y2="92" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".4"/>
    <circle cx="128" cy="118" r="18" fill="#1f9d57"/>
    <path d="M120 118l6 6 10-12" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
  </svg>`,
};

function emptyState(title, sub, kind = "doc") {
  return `<div class="empty">
    ${EMPTY_ART[kind] || EMPTY_ART.doc}
    <h3>${esc(title)}</h3><p>${esc(sub)}</p></div>`;
}

$$(".nav__item").forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view)));

function initials(name) {
  const p = (name || "").split(/\s+/).filter(Boolean);
  return ((p[0] || "")[0] || "") + ((p[1] || "")[0] || "") || "—";
}

async function initUserBox() {
  try {
    const { user } = await fetch(`${API}/auth/whoami`, { credentials: "same-origin" })
      .then((r) => { if (r.status === 401) { gotoLogin(); throw new Error("401"); } return r.json(); });
    state.user = user;
    state.isAdmin = !!user.is_admin;
    const box = $("#user-box");
    if (box) {
      box.hidden = false;
      $("#user-name").textContent = user.name || "Сотрудник";
      $("#user-role").textContent = user.role || "";
      $("#user-avatar").textContent = initials(user.name);
    }
    // Админские разделы (Настройки, Рассылки, Журнал действий) видны только админу.
    ["#nav-settings", "#nav-broadcast", "#nav-audit"].forEach((sel) => {
      const el = $(sel);
      if (el) el.hidden = !state.isAdmin;
    });
    // Сотрудник отвечает только от своего имени; администратор может выбирать,
    // от чьего имени отвечать. Имя берём из учётной записи.
    if (user.name && (!state.isAdmin || !state.operator)) state.operator = user.name;
    const lo = $("#logout-btn");
    if (lo) lo.onclick = async () => {
      try { await fetch(`${API}/auth/logout`, { method: "POST", credentials: "same-origin" }); } catch (_) {}
      gotoLogin();
    };
  } catch (_) { /* gotoLogin уже вызван при 401 */ }
}

/* ---------- инициализация ---------- */
async function init() {
  initTheme();
  await initUserBox();
  try {
    state.meta = await api("/meta");
  } catch (e) {
    document.body.innerHTML = `<div class="empty" style="margin:80px auto">
      <h3>Сервис недоступен</h3><p>${esc(e.message)}</p></div>`;
    return;
  }
  document.title = state.meta.title || document.title;

  // селектор сотрудника
  const sel = $("#operator-select");
  const opLabel = sel.closest(".op-select")?.querySelector("span");
  if (state.isAdmin) {
    // Администратор выбирает, от чьего имени отвечать (любой сотрудник).
    if (opLabel) opLabel.textContent = "Отвечает";
    const ops = [...new Set([state.operator, ...(state.meta.operators || [])].filter(Boolean))];
    if (!ops.length) ops.push("Оператор администрации");
    sel.disabled = false;
    sel.innerHTML = ops.map((o) => `<option ${o === state.operator ? "selected" : ""}>${esc(o)}</option>`).join("");
    if (!state.operator && ops.length) state.operator = ops[0];
    sel.value = state.operator;
    sel.addEventListener("change", () => {
      state.operator = sel.value;
      localStorage.setItem("op", state.operator);
      toast("Отвечаем от имени: " + state.operator);
    });
  } else {
    // Сотрудник «привязан» к своей учётной записи — выбор недоступен.
    if (opLabel) opLabel.textContent = "Сотрудник";
    const me = (state.user && state.user.name) || state.operator || "Сотрудник";
    state.operator = me;
    localStorage.removeItem("op");
    sel.innerHTML = `<option selected>${esc(me)}</option>`;
    sel.value = me;
    sel.disabled = true;
    sel.title = "Вы отвечаете под своей учётной записью";
  }

  if (state.meta.usvo_error) toast("Таблица УСВО: " + state.meta.usvo_error, "err");

  // OAuth платформы возвращает callback в hash:
  // #state=...&session_state=...&iss=...&code=...
  // Не затираем этот фрагмент и не принимаем его за маршрут SPA.
  const cardPath = /\/usvo\/cards\/(\d+)\/?$/.exec(location.pathname);
  if (cardPath) {
    state.activeUsvo = +cardPath[1];
    Object.keys(usvoFilters).forEach((key) => { usvoFilters[key] = ""; });
  }
  const hashView = (location.hash || "").slice(1);
  const start = cardPath ? "cards" : (VIEWS[hashView] ? hashView : "appeals");
  switchView(start);
}

init();
