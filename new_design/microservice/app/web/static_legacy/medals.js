/* Медали для карточки УСВО: реальное фото из static/medals/, иначе SVG-заглушка.
   Экспорт: window.Medals.resolve(text) -> [{ name, html }]. */
"use strict";

(function () {
  /* Базовый шаблон медали: лента (две полосы) + медальон с фигурой.
     cfg: { r1, r2 — цвета ленты; disc — цвет медальона; edge — окантовка;
            shape — 'star' | 'cross' | 'disc'; glyph — цвет фигуры } */
  function medal(cfg) {
    const { r1, r2, disc, edge, shape = "star", glyph = "#fff3c4" } = cfg;
    const id = "g" + Math.random().toString(36).slice(2, 8);
    const shapeSvg = {
      star:
        `<path d="M32 30l3.4 6.9 7.6 1.1-5.5 5.3 1.3 7.5L32 54.3l-6.8 3.6 1.3-7.5-5.5-5.3 7.6-1.1z" ` +
        `fill="${glyph}" stroke="${edge}" stroke-width="1"/>`,
      cross:
        `<path d="M28 33h8v6h6v8h-6v6h-8v-6h-6v-8h6z" fill="${glyph}" stroke="${edge}" stroke-width="1"/>`,
      disc:
        `<circle cx="32" cy="44" r="8" fill="${glyph}" stroke="${edge}" stroke-width="1"/>` +
        `<circle cx="32" cy="44" r="3.2" fill="${disc}"/>`,
    }[shape];

    return (
      `<svg viewBox="0 0 64 80" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">` +
      `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" stop-color="${_lighten(disc)}"/><stop offset="1" stop-color="${disc}"/>` +
      `</linearGradient></defs>` +
      // лента
      `<path d="M22 4h8l-3 26h-8z" fill="${r1}"/>` +
      `<path d="M34 4h8l3 26h-8z" fill="${r2}"/>` +
      `<path d="M30 4h4l1 26h-6z" fill="${_lighten(r1)}"/>` +
      // медальон
      `<circle cx="32" cy="44" r="18" fill="url(#${id})" stroke="${edge}" stroke-width="2"/>` +
      `<circle cx="32" cy="44" r="18" fill="none" stroke="rgba(255,255,255,.35)" stroke-width="1"/>` +
      shapeSvg +
      // блик
      `<path d="M20 34a18 18 0 0 1 12-9" fill="none" stroke="rgba(255,255,255,.5)" stroke-width="2" stroke-linecap="round"/>` +
      `</svg>`
    );
  }

  function _lighten(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    const r = Math.min(255, ((n >> 16) & 255) + 50);
    const g = Math.min(255, ((n >> 8) & 255) + 50);
    const b = Math.min(255, (n & 255) + 50);
    return `rgb(${r},${g},${b})`;
  }

  // абсолютный префикс статики: карточка открывается по deep-link
  // /usvo/cards/<id>, и относительный путь к фото давал бы 404
  function _photoBase() {
    let base = (window.location.pathname || "/").replace(/\/+$/, "");
    base = base.replace(/\/(index|login)\.html$/, "");
    base = base.replace(/\/usvo\/cards\/\d+$/, "");
    base = base.replace(/(\/application)+$/, "/application");
    return `${base}/medals/`;
  }

  // Реальное фото награды (приоритет над SVG-стилизацией).
  function photo(src, alt) {
    return (
      `<img class="medal__photo" src="${_photoBase()}${src}" alt="${_esc(alt)}" loading="lazy" ` +
      `decoding="async" />`
    );
  }

  function _esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // Узнаваемые награды. match — подстроки (нижний регистр) для сопоставления.
  // img — имя файла реального фото в static/medals/ (если есть); иначе рисуется SVG.
  const MEDALS = [
    { match: ["орден мужества"], img: "orden-muzhestva.png", cfg: { r1: "#a8132b", r2: "#7d0e20", disc: "#c0392b", edge: "#7d0e20", shape: "cross", glyph: "#f6d365" } },
    { match: ["благодарность командования", "благодарность"], img: "blagodarnost-komandovaniya.png", cfg: { r1: "#3a4761", r2: "#7d0e20", disc: "#b8860b", edge: "#7a5901", shape: "star", glyph: "#f6d365" } },
    { match: ["за заслуги перед отечеством", "за заслуги"], cfg: { r1: "#9b1b2e", r2: "#6c1320", disc: "#b03050", edge: "#6c1320", shape: "cross", glyph: "#ffe08a" } },
    { match: ["за отвагу"], img: "za-otvagu.png", cfg: { r1: "#c0392b", r2: "#7f8c8d", disc: "#bdc3c7", edge: "#7f8c8d", shape: "disc", glyph: "#ecf0f1" } },
    { match: ["за боевые отличия", "боевые отличия"], img: "za-boevye-otlichiya.jpg", cfg: { r1: "#1f6f3e", r2: "#0f4023", disc: "#27ae60", edge: "#0f4023", shape: "star", glyph: "#f6d365" } },
    { match: ["суворова"], cfg: { r1: "#b8860b", r2: "#7a5901", disc: "#d4a017", edge: "#7a5901", shape: "disc", glyph: "#fff3c4" } },
    { match: ["жукова"], cfg: { r1: "#8e0e1a", r2: "#5a0910", disc: "#c0392b", edge: "#5a0910", shape: "disc", glyph: "#f6d365" } },
    { match: ["георгиевский крест", "георгиевск"], cfg: { r1: "#e8a300", r2: "#2b2b2b", disc: "#d4a017", edge: "#2b2b2b", shape: "cross", glyph: "#fff3c4" } },
    { match: ["за спасение погибавших", "за спасение"], cfg: { r1: "#c0392b", r2: "#8a1f12", disc: "#cf5e3a", edge: "#8a1f12", shape: "disc", glyph: "#ffe08a" } },
    { match: ["за воинскую доблесть", "воинскую доблесть", "доблесть"], cfg: { r1: "#1f5fa8", r2: "#123c6b", disc: "#2e6fc0", edge: "#123c6b", shape: "star", glyph: "#ffe08a" } },
    { match: ["орден"], cfg: { r1: "#9b1b2e", r2: "#6c1320", disc: "#b22f44", edge: "#6c1320", shape: "cross", glyph: "#ffe08a" } },
    { match: ["медаль"], cfg: { r1: "#1f6f8b", r2: "#143f50", disc: "#2e8bc0", edge: "#143f50", shape: "disc", glyph: "#ffe08a" } },
  ];

  const GENERIC = { r1: "#5a6b86", r2: "#3a4761", disc: "#5b6b86", edge: "#3a4761", shape: "star", glyph: "#e6ecf5" };

  const SKIP = ["нет сведений", "нет данных", "нет", "-", "—", "не имеет", "отсутству"];

  function _isEmpty(token) {
    const t = token.trim().toLowerCase();
    if (!t) return true;
    return SKIP.some((s) => t === s || t.startsWith(s));
  }

  function _find(token) {
    const t = token.toLowerCase();
    for (const m of MEDALS) {
      if (m.match.some((needle) => t.includes(needle))) return m;
    }
    return { cfg: GENERIC };
  }

  // HTML награды: реальное фото при наличии, иначе стилизованный SVG.
  function _render(entry, name) {
    return entry.img ? photo(entry.img, name) : medal(entry.cfg);
  }

  // Разбор строки наград в список { name, html }.
  function resolve(text) {
    if (!text) return [];
    const tokens = String(text)
      .split(/[,;\n•·]+|(?:\s\/\s)/)
      .map((s) => s.trim())
      .filter((s) => s && !_isEmpty(s));
    const seen = new Set();
    const out = [];
    for (const tok of tokens) {
      const key = tok.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ name: tok, html: _render(_find(tok), tok) });
    }
    return out;
  }

  window.Medals = { resolve };
})();
