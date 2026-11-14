// Scove — scheda mappa per le dashboard: stanze, zone vietate e passaggi del robot.
// Uso:  type: custom:scove-map-card   (opzioni: title, history: true|false)
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const CSS = `
:host{display:block}
.box{border-radius:22px;padding:12px 14px;background:rgba(40,90,170,.14);border:1px solid rgba(120,180,255,.28);color:#e8f1ff;box-shadow:0 0 18px rgba(60,140,255,.12) inset}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px;font-size:14px;font-weight:600}
.state{font-weight:400;font-size:12px;color:#9fc0f0}
svg{width:100%;height:auto;max-height:60vh;display:block;border-radius:14px;background:rgba(4,12,28,.55)}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:11px;color:#9fc0f0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.chips{display:flex;gap:6px}
button{font:inherit;font-size:11px;color:#e8f1ff;background:transparent;border:1px solid rgba(140,185,255,.35);border-radius:99px;padding:3px 9px;cursor:pointer}
button.on{background:#2f74e6;border-color:#7db1ff}
.empty{padding:26px 8px;text-align:center;color:#9fc0f0;font-size:13px}
`;

class ScoveMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._layers = { trail: true, history: true };
  }

  setConfig(config) {
    this._config = config || {};
    if (this._config.history === false) this._layers.history = false;
  }

  getCardSize() { return 6; }

  set hass(h) {
    this._hass = h;
    if (!this._loaded) { this._loaded = true; this._load(); }
  }

  connectedCallback() {
    this._timer = setInterval(() => { if (this.offsetParent !== null) this._load(); }, 3000);
  }

  disconnectedCallback() { clearInterval(this._timer); }

  async _load() {
    if (!this._hass) return;
    try { this._s = await this._hass.callWS({ type: "scove/get" }); this._err = null; }
    catch (e) { this._err = e.message || String(e); }
    this._render();
  }

  _roomName(s, room) {
    return room.label || room.name;
  }

  _render() {
    const s = this._s;
    const title = esc(this._config.title || "Mappa");
    if (!s) { this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="box"><div class="head">${title}</div><div class="empty">${esc(this._err || "Caricamento…")}</div></div>`; return; }
    const rooms = (s.map?.rooms || []).filter((r) => r.polygon.length > 2);
    const keepouts = s.map?.keepouts || [];
    const cell = s.cell_mm;
    const pts = [[0, 0]];
    rooms.forEach((r) => pts.push(...r.polygon));
    keepouts.forEach((k) => pts.push(...k));
    s.zones.forEach((z) => pts.push([z.x1, z.y1], [z.x2, z.y2]));
    if (this._layers.trail) s.trail.forEach((p) => pts.push(p));
    if (s.position) pts.push([s.position.x, s.position.y]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => -p[1]);
    const x0 = Math.min(...xs) - 350, y0 = Math.min(...ys) - 350;
    const w = Math.max(...xs) - x0 + 350, hgt = Math.max(...ys) - y0 + 350;
    const poly = (p) => p.map(([x, y]) => `${x},${-y}`).join(" ");
    const maxVisits = Math.max(1, ...Object.values(s.coverage));
    const cells = this._layers.history ? Object.entries(s.coverage).map(([c, n]) => {
      const [cx, cy] = c.split(",").map(Number);
      return `<rect x="${cx * cell}" y="${-(cy + 1) * cell}" width="${cell}" height="${cell}" fill="#35d0a5" opacity="${(0.14 + 0.42 * n / maxVisits).toFixed(2)}"/>`;
    }).join("") : "";
    const font = Math.max(150, w / 34);
    const labels = rooms.map((r) => {
      const cx = r.polygon.reduce((a, p) => a + p[0], 0) / r.polygon.length;
      const cy = r.polygon.reduce((a, p) => a + p[1], 0) / r.polygon.length;
      return `<text x="${cx}" y="${-cy}" fill="#e3eeff" font-size="${font}" text-anchor="middle" dominant-baseline="middle" style="paint-order:stroke;stroke:#06122a;stroke-width:${font / 5}px">${esc(this._roomName(s, r))}</text>`;
    }).join("");
    const body = rooms.length || s.trail.length || Object.keys(s.coverage).length ? `
      <svg viewBox="${x0} ${y0} ${w} ${hgt}">
        <defs><pattern id="h" width="160" height="160" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="60" height="160" fill="#ff5d5d" opacity=".6"/></pattern>
        <filter id="g"><feGaussianBlur stdDeviation="40" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
        ${rooms.map((r) => `<polygon points="${poly(r.polygon)}" fill="rgba(70,135,255,.15)" stroke="#6fb0ff" stroke-width="34" stroke-linejoin="round" filter="url(#g)"/>`).join("")}
        ${cells}
        ${this._layers.trail && s.trail.length > 1 ? `<polyline points="${poly(s.trail)}" fill="none" stroke="#4fd8ff" stroke-opacity=".4" stroke-width="330" stroke-linecap="round" stroke-linejoin="round"/><polyline points="${poly(s.trail)}" fill="none" stroke="#bff3ff" stroke-opacity=".8" stroke-width="22" stroke-linejoin="round"/>` : ""}
        ${keepouts.map((k) => `<polygon points="${poly(k)}" fill="url(#h)" stroke="#ff6b6b" stroke-width="28"/>`).join("")}
        ${s.zones.map((z) => `<rect x="${Math.min(z.x1, z.x2)}" y="${-Math.max(z.y1, z.y2)}" width="${Math.abs(z.x2 - z.x1)}" height="${Math.abs(z.y2 - z.y1)}" fill="rgba(255,170,60,.25)" stroke="#ffb14a" stroke-width="30" stroke-dasharray="120 80"/>`).join("")}
        ${labels}
        <circle cx="0" cy="0" r="110" fill="#ffd27a"/>
        ${s.position ? `<circle cx="${s.position.x}" cy="${-s.position.y}" r="165" fill="#fff" stroke="#2f74e6" stroke-width="60" filter="url(#g)"/>` : ""}
      </svg>
      <div class="legend"><span><i style="background:#4fd8ff"></i>Ultima pulizia</span><span><i style="background:#35d0a5"></i>Passaggi accumulati</span><span><i style="background:#ff5d5d"></i>Zone vietate</span><span><i style="background:#ffd27a"></i>Base</span></div>`
      : `<div class="empty">Mappa non ancora disponibile.</div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="box">
      <div class="head"><span>${title} <span class="state">· ${esc(s.phase_label)}</span></span>
        <span class="chips"><button class="${this._layers.trail ? "on" : ""}" data-l="trail">Percorso</button><button class="${this._layers.history ? "on" : ""}" data-l="history">Storico</button></span></div>
      ${body}</div>`;
    this.shadowRoot.querySelectorAll("[data-l]").forEach((b) => (b.onclick = () => { this._layers[b.dataset.l] = !this._layers[b.dataset.l]; this._render(); }));
  }
}

if (!customElements.get("scove-map-card")) customElements.define("scove-map-card", ScoveMapCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "scove-map-card")) {
  window.customCards.push({ type: "scove-map-card", name: "Scove – Mappa", description: "Stanze, zone vietate e passaggi del robot." });
}
