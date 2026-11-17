// Scove — scheda mappa per le dashboard: stanze, zone vietate e passaggi del robot.
// Uso:  type: custom:scove-map-card   (opzioni: title, history: true|false)
// Mostra il giro in corso (o l'ultimo). «Storico» apre gli ultimi 10 giri: toccandone uno
// si apre la sua mappa con tempo di pulizia e superficie.
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtShort = (iso) => (iso ? new Date(iso).toLocaleString("it-IT", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "—");

const CSS = `
:host{display:block}
.box{border-radius:22px;padding:12px 14px;background:rgba(40,90,170,.14);border:1px solid rgba(120,180,255,.28);color:#e8f1ff;box-shadow:0 0 18px rgba(60,140,255,.12) inset}
.head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:6px;font-size:14px;font-weight:600}
.state{font-weight:400;font-size:12px;color:#9fc0f0}
svg{width:100%;height:auto;max-height:60vh;display:block;border-radius:14px;background:rgba(4,12,28,.55)}
.legend{display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:11px;color:#9fc0f0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:-1px}
.sum{font-size:12px;color:#cfe0ff;margin:0 2px 8px}
button{font:inherit;font-size:12px;color:#e8f1ff;background:transparent;border:1px solid rgba(140,185,255,.35);border-radius:99px;padding:4px 11px;cursor:pointer}
.empty{padding:26px 8px;text-align:center;color:#9fc0f0;font-size:13px}
.modal{position:fixed;inset:0;z-index:9999;background:rgba(2,8,20,.78);backdrop-filter:blur(6px);display:flex;align-items:flex-start;justify-content:center;padding:14px;overflow-y:auto}
.sheet{width:100%;max-width:640px;background:linear-gradient(180deg,#10284d,#0a1a33);border:1px solid rgba(140,185,255,.3);border-radius:20px;padding:14px;color:#e8f1ff}
.bar{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px}
.ttl{font-size:15px;font-weight:600}
.run{display:flex;justify-content:space-between;align-items:center;gap:10px;width:100%;text-align:left;margin:0 0 8px;padding:11px 13px;border-radius:14px;background:rgba(110,160,255,.08)}
.run b{display:block;font-size:14px}
.muted{color:#9fc0f0;font-size:12px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:2px 0 10px}
.stats div{background:rgba(110,160,255,.10);border:1px solid rgba(140,185,255,.2);border-radius:14px;padding:9px 11px}
.stats b{display:block;font-size:20px}
`;

class ScoveMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hist = null;   // null = chiuso, "list" = elenco, altrimenti il giro aperto
    this._tracks = {};
    this._asked = {};
  }

  setConfig(config) { this._config = config || {}; }

  getCardSize() { return 6; }

  set hass(h) {
    this._hass = h;
    if (!this._loaded) { this._loaded = true; this._load(); }
  }

  connectedCallback() {
    this._timer = setInterval(() => { if (this.offsetParent !== null && !this._hist) this._load(); }, 3000);
  }

  disconnectedCallback() { clearInterval(this._timer); }

  async _load() {
    if (!this._hass) return;
    try { this._s = await this._hass.callWS({ type: "scove/get" }); this._err = null; }
    catch (e) { this._err = e.message || String(e); }
    if (!this._hist) this._render();
  }

  _track(h) {
    if (this._tracks[h.started]) return this._tracks[h.started];
    if (h.has_track && !this._asked[h.started]) {
      this._asked[h.started] = true;
      this._hass.callWS({ type: "scove/track", started: h.started })
        .then((r) => { this._tracks[h.started] = r.points || []; })
        .catch(() => { this._tracks[h.started] = []; })
        .finally(() => this._render());
    }
    return [];
  }

  _svg(s, trail, { position = false, coverage = null } = {}) {
    const rooms = (s.map?.rooms || []).filter((r) => r.polygon.length > 2);
    const keepouts = s.map?.keepouts || [];
    const pts = [[0, 0]];
    rooms.forEach((r) => pts.push(...r.polygon));
    keepouts.forEach((k) => pts.push(...k));
    s.zones.forEach((z) => pts.push([z.x1, z.y1], [z.x2, z.y2]));
    trail.forEach((p) => pts.push(p));
    if (position && s.position) pts.push([s.position.x, s.position.y]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => -p[1]);
    const x0 = Math.min(...xs) - 350, y0 = Math.min(...ys) - 350;
    const w = Math.max(...xs) - x0 + 350, hgt = Math.max(...ys) - y0 + 350;
    const poly = (p) => p.map(([x, y]) => `${x},${-y}`).join(" ");
    const font = Math.max(150, w / 34);
    const labels = rooms.map((r) => {
      const cx = r.polygon.reduce((a, p) => a + p[0], 0) / r.polygon.length;
      const cy = r.polygon.reduce((a, p) => a + p[1], 0) / r.polygon.length;
      const name = r.label || r.name;
      const pct = coverage?.[name];
      return `<text x="${cx}" y="${-cy}" fill="#e3eeff" font-size="${font}" text-anchor="middle" dominant-baseline="middle" style="paint-order:stroke;stroke:#06122a;stroke-width:${font / 5}px">${esc(name)}${pct !== undefined ? ` · ${pct}%` : ""}</text>`;
    }).join("");
    if (!rooms.length && !trail.length) return `<div class="empty">Mappa non ancora disponibile.</div>`;
    return `<svg viewBox="${x0} ${y0} ${w} ${hgt}">
        <defs><pattern id="h" width="160" height="160" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="60" height="160" fill="#ff5d5d" opacity=".6"/></pattern>
        <filter id="g"><feGaussianBlur stdDeviation="40" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
        ${rooms.map((r) => `<polygon points="${poly(r.polygon)}" fill="rgba(70,135,255,.15)" stroke="#6fb0ff" stroke-width="34" stroke-linejoin="round" filter="url(#g)"/>`).join("")}
        ${trail.length > 1 ? `<polyline points="${poly(trail)}" fill="none" stroke="#4fd8ff" stroke-opacity=".4" stroke-width="330" stroke-linecap="round" stroke-linejoin="round"/>` : ""}
        ${keepouts.map((k) => `<polygon points="${poly(k)}" fill="url(#h)" stroke="#ff6b6b" stroke-width="28"/>`).join("")}
        ${s.zones.map((z) => `<rect x="${Math.min(z.x1, z.x2)}" y="${-Math.max(z.y1, z.y2)}" width="${Math.abs(z.x2 - z.x1)}" height="${Math.abs(z.y2 - z.y1)}" fill="rgba(255,170,60,.25)" stroke="#ffb14a" stroke-width="30" stroke-dasharray="120 80"/>`).join("")}
        ${labels}
        <circle cx="0" cy="0" r="110" fill="#ffd27a"/>
        ${position && s.position ? `<circle cx="${s.position.x}" cy="${-s.position.y}" r="165" fill="#fff" stroke="#2f74e6" stroke-width="60" filter="url(#g)"/>` : ""}
      </svg>`;
  }

  _modal(s) {
    if (!this._hist) return "";
    const runs = s.history || [];
    if (this._hist === "list") {
      return `<div class="modal" id="bg"><div class="sheet">
        <div class="bar"><span class="ttl">Storico · ultimi ${runs.length} giri</span><button id="close">✕ Chiudi</button></div>
        ${runs.length ? runs.map((h) => `<button class="run" data-run="${esc(h.started)}"><span><b>${esc(fmtShort(h.started))}</b><span class="muted">${esc(h.result || "")}${h.recharges ? " · con ricarica" : ""}</span></span><span style="text-align:right"><b>${h.minutes ?? "?"} min</b><span class="muted">${h.area_m2 ?? "?"} m²</span></span></button>`).join("")
          : `<div class="empty">Ancora nessun giro registrato.</div>`}
      </div></div>`;
    }
    const h = runs.find((x) => x.started === this._hist);
    if (!h) { this._hist = "list"; return this._modal(s); }
    const trail = this._track(h);
    return `<div class="modal" id="bg"><div class="sheet">
      <div class="bar"><button id="back">‹ Elenco</button><button id="close">✕ Chiudi</button></div>
      <div class="ttl" style="margin-bottom:8px">${esc(fmtShort(h.started))} · ${esc(h.result || "")}</div>
      <div class="stats">
        <div><span class="muted">Tempo di pulizia</span><b>${h.minutes ?? "?"} min</b>${h.recharges ? `<span class="muted">ricarica esclusa</span>` : ""}</div>
        <div><span class="muted">Superficie</span><b>${h.area_m2 ?? "?"} m²</b></div>
      </div>
      ${this._svg(s, trail, { coverage: h.coverage })}
      ${!h.has_track ? `<p class="muted">Il percorso di questo giro non è stato conservato: lo storico dei percorsi parte dalla versione 1.0.16.</p>` : ""}
      ${h.has_track && !this._tracks[h.started] ? `<p class="muted">Carico il percorso…</p>` : ""}
      ${h.partial_track ? `<p class="muted">L'inizio di questo giro manca dal percorso: metri quadri sottostimati.</p>` : ""}
    </div></div>`;
  }

  _render() {
    const s = this._s;
    const title = esc(this._config.title || "Mappa");
    if (!s) { this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="box"><div class="head">${title}</div><div class="empty">${esc(this._err || "Caricamento…")}</div></div>`; return; }
    const live = !!(s.mission && !s.mission.ended);
    const last = s.history?.[0];
    const sum = live ? `In corso da ${fmtTime(s.mission.started)} · ${s.run_minutes || 0} min di lavoro`
      : last ? `Ultimo giro: ${fmtShort(last.started)} · ${last.area_m2 ?? "?"} m² · ${last.minutes ?? "?"} min` : "";
    const withHistory = this._config.history !== false;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><div class="box">
      <div class="head"><span>${title} <span class="state">· ${esc(s.phase_label)}</span></span>
        ${withHistory ? `<button id="open">Storico</button>` : ""}</div>
      ${sum ? `<div class="sum">${esc(sum)}</div>` : ""}
      ${this._svg(s, s.trail, { position: live })}
      <div class="legend"><span><i style="background:#4fd8ff"></i>${live ? "Giro in corso" : "Ultimo giro"}</span><span><i style="background:#ff5d5d"></i>Zone vietate</span><span><i style="background:#ffd27a"></i>Base</span></div>
    </div>${this._modal(s)}`;
    const one = (q) => this.shadowRoot.querySelector(q);
    const open = one("#open");
    if (open) open.onclick = () => { this._hist = "list"; this._render(); };
    this.shadowRoot.querySelectorAll("[data-run]").forEach((b) => (b.onclick = () => { this._hist = b.dataset.run; this._render(); }));
    const back = one("#back");
    if (back) back.onclick = () => { this._hist = "list"; this._render(); };
    const close = () => { this._hist = null; this._load(); };
    const cl = one("#close");
    if (cl) cl.onclick = close;
    const bg = one("#bg");
    if (bg) bg.onclick = (ev) => { if (ev.target === bg) close(); };
  }
}

if (!customElements.get("scove-map-card")) customElements.define("scove-map-card", ScoveMapCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "scove-map-card")) {
  window.customCards.push({ type: "scove-map-card", name: "Scove – Mappa", description: "Stanze, zone vietate e passaggi del robot, con lo storico degli ultimi 10 giri." });
}
