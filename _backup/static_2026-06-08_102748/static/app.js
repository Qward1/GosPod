/* Контакт-центр — Господдержка СВО. SPA без сборки и внешних зависимостей. */
"use strict";

const APP_BASE = window.location.pathname.replace(/\/$/, "");
const API = `${APP_BASE}/api/web`;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  meta: null,
  user: null,
  operator: localStorage.getItem("op") || "",
  view: "appeals",
  appeals: [],
  usvo: [],
  applications: [],
  activeUsvo: null,
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

function loadingBlock(text = "Загрузка…") {
  return `<div class="loading-block"><span class="spinner"></span><span>${esc(text)}</span></div>`;
}

const ICONS = {
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>',
  wand: '<path d="M15 4V2M15 10V8M12 5h2M16 5h2M6 20l12-12-4-4L2 16z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4 20-7z"/><path d="M22 2 11 13"/>',
  trash: '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/>',
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
  pin: '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  award: '<circle cx="12" cy="8" r="6"/><path d="M8.2 13.9 7 22l5-3 5 3-1.2-8.1"/>',
  gauge: '<path d="M12 14 16 10"/><path d="M3.4 18a9 9 0 1 1 17.2 0"/><circle cx="12" cy="14" r="1.5"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/>',
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

function groupFields(r) {
  const all = [...(r.primary || []), ...(r.secondary || []), ...(r.extra || [])];
  const seen = new Set();
  const buckets = FIELD_GROUPS.map((g) => ({ title: g.title, icon: g.icon, keys: g.keys, items: [] }));
  const other = [];
  for (const f of all) {
    const sig = (f.label + "=" + f.value).toLowerCase();
    if (seen.has(sig)) continue;
    seen.add(sig);
    const ll = f.label.toLowerCase();
    if (ll.includes("награды")) continue; // показаны медалями
    let placed = false;
    for (const b of buckets) {
      if (b.keys.some((k) => ll.includes(k))) { b.items.push(f); placed = true; break; }
    }
    if (!placed) other.push(f);
  }
  const groups = buckets.filter((b) => b.items.length);
  if (other.length) groups.push({ title: "Дополнительно", icon: "info", items: other });
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
function renderTimeline(history) {
  if (!history || !history.length) return `<p class="muted">История пока пуста</p>`;
  return `<div class="timeline">${history.map((e) => `
    <div class="tl-item ${tlKindClass(e.status)}">
      <div class="tl-item__dot">${icon(e.kind || "info", 15)}</div>
      <div class="tl-item__card">
        <div class="tl-item__top"><h4>${esc(e.title)}</h4><span class="tl-status tl-status--${tlStatusClass(e.status)}">${esc(e.status)}</span></div>
        ${e.detail ? `<p class="tl-item__detail">${esc(e.detail)}</p>` : ""}
        <div class="tl-item__meta"><span class="date">${esc(e.date)}</span>${e.org ? `<span class="org">${icon("building", 13)}${esc(e.org)}</span>` : ""}</div>
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

async function renderAppeals() {
  const root = $("#view-root");
  root.innerHTML = tableSkeleton();
  setTitle("Обращения", "Вопросы граждан, распределённые на оператора");
  $("#topbar-actions").innerHTML = `<button class="btn btn--ghost" id="reload-appeals">${icon("refresh")}<span>Обновить</span></button>`;
  $("#reload-appeals").onclick = renderAppeals;

  const { items } = await api("/appeals");
  state.appeals = items;
  const open = items.filter((a) => a.status === "open").length;
  $("#nav-appeals-count").textContent = open || "";

  if (!items.length) {
    root.innerHTML = emptyState("Все обращения обработаны, ИИ отдыхает",
      "Новые вопросы граждан из бота MAX появятся здесь автоматически.", "robot");
    return;
  }

  const rows = items.map((a) => `
    <tr data-id="${a.id}">
      <td class="senti-cell">${sentiDot(a.sentiment)}</td>
      <td class="q">${esc(a.question)}<small>${esc(a.summary || a.citizen.name || "Гражданин")}</small></td>
      <td>${esc(a.created_human)}</td>
      <td><span class="pill pill--info">${esc(a.topic)}</span></td>
      <td>${a.assignee
        ? `<span class="assignee-tag"><span class="dot"></span>${esc(a.assignee)}</span>`
        : '<span class="muted">не назначен</span>'}</td>
      <td>${statusPill(a.status)}</td>
    </tr>`).join("");

  root.innerHTML = `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr>
          <th>Тон</th><th>Вопрос · суть</th><th>Дата и время</th><th>Тематика</th><th>Ответственный</th><th>Статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  $$("#view-root tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => openAppeal(tr.dataset.id)));
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

function openAppeal(id) {
  const a = state.appeals.find((x) => x.id === id);
  if (!a) return;
  const ops = [...new Set([a.assignee, state.operator, ...(state.meta.operators || [])].filter(Boolean))];
  const opOptions = ops.map((o) =>
    `<option ${o === (a.assignee || state.operator) ? "selected" : ""}>${esc(o)}</option>`).join("");

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

      <div class="form-row">
        <label>Ответственный за ответ</label>
        <select id="ap-assignee">${opOptions}</select>
      </div>

      <div class="form-row">
        <label>Ответ гражданину</label>
        <textarea id="ap-answer" placeholder="Нажмите «Сформировать черновик» (Alt+G), чтобы ИИ подставил известные данные и ответ из базы знаний…">${esc(a.answer || "")}</textarea>
      </div>

      <div class="conf" id="ap-conf" hidden></div>
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

  $("#ap-delete").onclick = async (e) => {
    if (!confirm("Удалить обращение из списка?")) return;
    const btn = e.currentTarget;
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

/* ============================================================
   РАЗДЕЛ 2 — КАРТОЧКИ УСВО
   ============================================================ */
async function renderCards() {
  const root = $("#view-root");
  setTitle("Персональные карточки УСВО", "Данные участников СВО и предложения по мерам поддержки");
  $("#topbar-actions").innerHTML = `
    <div class="search">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
      <input id="usvo-search" type="search" placeholder="Поиск по ФИО, телефону, статусу…" />
    </div>`;

  root.innerHTML = `
    <div class="cards-layout fade-in">
      <div class="card cards-list" id="usvo-list">${listSkeleton()}</div>
      <div class="card card-detail" id="usvo-detail">${emptyState("Выберите карточку", "Слева список участников СВО. Выберите запись, чтобы открыть данные.", "search")}</div>
    </div>`;

  const search = $("#usvo-search");
  search.addEventListener("input", debounce(() => loadUsvoList(search.value), 250));
  await loadUsvoList("");
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

async function loadUsvoList(query) {
  const list = $("#usvo-list");
  if (!list) return;
  const { items } = await api("/usvo?query=" + encodeURIComponent(query || ""));
  state.usvo = items;
  if (!items.length) {
    list.innerHTML = emptyState("Ничего не найдено",
      "Уточните запрос — поиск идёт по ФИО, телефону, статусу и адресу.", "search");
    return;
  }
  if (!items.some((r) => r.id === state.activeUsvo)) {
    state.activeUsvo = items[0].id;
  }
  list.innerHTML = items.map((r) => `
    <div class="usvo-item ${state.activeUsvo === r.id ? "active" : ""}" data-id="${r.id}">
      <div class="avatar">${esc(r.initials)}</div>
      <div class="usvo-item__main">
        <div class="usvo-item__name">${esc(r.name)}${r.head_directive ? `<span class="usvo-item__star" title="Поручение Главы округа">${icon("award", 13)}</span>` : ""}</div>
        <div class="usvo-item__sub">${esc(r.status || "—")} · обзвон ${esc(r.call_date || "—")}</div>
      </div>
    </div>`).join("");
  $$("#usvo-list .usvo-item").forEach((el) =>
    el.addEventListener("click", () => selectUsvo(+el.dataset.id)));

  if (state.activeUsvo != null) selectUsvo(state.activeUsvo);
}

async function selectUsvo(id) {
  state.activeUsvo = id;
  $$("#usvo-list .usvo-item").forEach((el) =>
    el.classList.toggle("active", +el.dataset.id === id));
  const detail = $("#usvo-detail");
  detail.innerHTML = loadingBlock();
  const r = await api("/usvo/" + id);
  if (state.activeUsvo !== id) return; // пользователь уже переключился

  const fieldHtml = (f) => `
    <div class="field"><div class="field__label">${esc(f.label)}</div><div class="field__value">${esc(f.value)}</div></div>`;

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
        <div class="block__head"><h3>Данные участника</h3><span class="block__hint">нажмите, чтобы развернуть</span></div>
        ${accHtml || `<p class="muted">Нет данных</p>`}
      </div>

      <div class="block">
        <div class="block__head"><h3>История взаимодействия</h3><span class="block__hint">${(r.history || []).length} событий</span></div>
        ${renderTimeline(r.history)}
      </div>
    </div>`;

  $$("#usvo-detail .acc__head").forEach((h) =>
    h.addEventListener("click", () => h.parentElement.classList.toggle("open")));

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

async function renderApplications() {
  const root = $("#view-root");
  root.innerHTML = tableSkeleton();
  setTitle("Заявления", "Меры поддержки, оформленные ботом MAX по фотографиям документов");
  $("#topbar-actions").innerHTML = `<button class="btn btn--ghost" id="reload-apps">${icon("refresh")}<span>Обновить</span></button>`;
  $("#reload-apps").onclick = renderApplications;

  const { items } = await api("/applications");
  state.applications = items;
  const pending = items.filter((a) => a.status === "submitted").length;
  $("#nav-apps-count").textContent = pending || "";

  if (!items.length) {
    root.innerHTML = emptyState("Заявлений пока нет",
      "Гражданин присылает боту MAX фото документов, ИИ заполняет заявление — после подтверждения оно появится здесь.", "doc");
    return;
  }

  const rows = items.map((a) => {
    const st = APP_STATUS[a.status] || { label: a.status_label || a.status, cls: "open" };
    const pcls = APP_PILL[a.status] || "pill--warn";
    return `<tr data-id="${a.id}">
      <td class="q">${esc(a.measure_title)}<small>${esc(a.applicant.fio || a.citizen.name || "Заявитель")}</small></td>
      <td>${esc(a.created_human)}</td>
      <td><span class="pill pill--info">${esc(a.category)}</span></td>
      <td>${a.missing && a.missing.length ? `<span class="pill pill--warn">уточнить: ${a.missing.length}</span>` : '<span class="muted">полный пакет</span>'}</td>
      <td><span class="pill ${pcls}"><span class="pill__dot"></span>${esc(st.label)}</span></td>
    </tr>`;
  }).join("");

  root.innerHTML = `
    <div class="fade-in card appeals-card">
      <table class="appeals-table">
        <thead><tr>
          <th>Мера поддержки</th><th>Поступило</th><th>Основание</th><th>Документы</th><th>Статус</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  $$("#view-root tbody tr").forEach((tr) =>
    tr.addEventListener("click", () => openApplication(+tr.dataset.id)));
}

function kv(label, value) {
  return `<div class="field"><div class="field__label">${esc(label)}</div><div class="field__value">${esc(value || "—")}</div></div>`;
}

function openApplication(id) {
  const a = state.applications.find((x) => x.id === id);
  if (!a) return;
  const ap = a.applicant;
  const st = APP_STATUS[a.status] || { label: a.status_label || a.status, cls: "open" };
  const family = (a.family || []).map((m) =>
    `<li>${esc(m.fio)} — ${esc(m.relation)}${m.birth_date ? `, ${esc(m.birth_date)}` : ""}</li>`).join("");
  const providers = (a.providers || []).map((p) =>
    `<li>${esc(p.name)} — л/с ${esc(p.account || "—")}</li>`).join("");
  const docxUrl = `${API}/applications/${id}/docx`;
  const decided = a.status !== "submitted";
  const pcls = APP_PILL[a.status] || "pill--warn";

  openDrawer({
    title: `Заявление #${id}`,
    subtitle: `${esc(a.measure_title)} · <span class="pill ${pcls}">${esc(st.label)}</span> · ${esc(a.created_human)}`,
    body: `
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
    `,
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
    if (!confirm("Удалить заявление?")) return;
    e.currentTarget.disabled = true;
    try {
      await api(`/applications/${id}`, { method: "DELETE" });
      toast("Заявление удалено", "ok"); closeDrawer(); renderApplications();
    } catch (err) { toast(err.message, "err"); }
  };
}

/* ============================================================
   РАЗДЕЛ 4 — АНАЛИТИКА
   ============================================================ */
async function renderAnalytics() {
  const root = $("#view-root");
  root.innerHTML = loadingBlock();
  setTitle("Аналитика", "Сводка по обращениям и участникам СВО");
  $("#topbar-actions").innerHTML = "";

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
      <div class="donut" style="background:conic-gradient(var(--blue) ${cov * 3.6}deg, rgba(13,76,211,.12) 0deg)">
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
   КАРКАС / РОУТИНГ
   ============================================================ */
const VIEWS = {
  appeals: { title: "Обращения", render: renderAppeals },
  cards: { title: "Карточки УСВО", render: renderCards },
  applications: { title: "Заявления", render: renderApplications },
  analytics: { title: "Аналитика", render: renderAnalytics },
};

setInterval(() => {
  if ($("#modal") && !$("#modal").hidden) return;
  if (drawerOpen()) return; // не перерисовываем список под открытой панелью
  if (state.view === "appeals") renderAppeals().catch((e) => toast(e.message, "err"));
  else if (state.view === "applications") renderApplications().catch((e) => toast(e.message, "err"));
}, 15000);

function setTitle(t, sub = "") {
  $("#view-title").textContent = t;
  $("#view-subtitle").textContent = sub;
}

function switchView(name) {
  if (!VIEWS[name]) name = "appeals";
  state.view = name;
  $$(".nav__item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  location.hash = name;
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
    const box = $("#user-box");
    if (box) {
      box.hidden = false;
      $("#user-name").textContent = user.name || "Сотрудник";
      $("#user-role").textContent = user.role || "";
      $("#user-avatar").textContent = initials(user.name);
    }
    if (user.name && !state.operator) state.operator = user.name;
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
  const ops = [...new Set([state.operator, ...(state.meta.operators || [])].filter(Boolean))];
  if (!ops.length) ops.push("Оператор администрации");
  sel.innerHTML = ops.map((o) => `<option ${o === state.operator ? "selected" : ""}>${esc(o)}</option>`).join("");
  if (!state.operator && ops.length) state.operator = ops[0];
  sel.value = state.operator;
  sel.addEventListener("change", () => {
    state.operator = sel.value;
    localStorage.setItem("op", state.operator);
    toast("Текущий сотрудник: " + state.operator);
  });

  // индикатор режима данных
  const pill = $("#mode-pill");
  if (state.meta.seeded_appeals) {
    pill.className = "status-pill seeded";
    pill.textContent = "Данные таблицы";
    pill.title = "Реальных обращений ещё нет — обращения заполнены по таблице УСВО";
  } else {
    pill.textContent = "Данные MAX";
  }
  if (state.meta.usvo_error) toast("Таблица УСВО: " + state.meta.usvo_error, "err");

  const start = (location.hash || "").replace("#", "") || "appeals";
  switchView(start);
}

init();
