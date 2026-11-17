// Scove — pannello per Home Assistant. Nessuna dipendenza, nessuna build.
const DAYS = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"];
const TABS = [["cmd", "Comandi"], ["prog", "Programma"], ["health", "Salute"], ["map", "Mappa"], ["alerts", "Avvisi"]];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtShort = (iso) => (iso ? new Date(iso).toLocaleString("it-IT", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "—");
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("it-IT", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

const CSS = `
:host{display:block;min-height:100vh;background:radial-gradient(1200px 600px at 20% -10%,#12305c 0%,#0a1a33 45%,#060e1d 100%);color:#e8f1ff;font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
*{box-sizing:border-box}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:4px 0 14px;font-weight:600;display:flex;align-items:center;gap:10px}
h2{font-size:15px;margin:0 0 10px;font-weight:600;color:#a9c6f5;text-transform:uppercase;letter-spacing:.06em}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px}
.tab,button{font:inherit;color:#e8f1ff;background:rgba(120,170,255,.10);border:1px solid rgba(140,185,255,.25);border-radius:12px;padding:9px 14px;cursor:pointer;backdrop-filter:blur(8px)}
.tab.on,button.primary{background:linear-gradient(180deg,#3d8bff,#2466d8);border-color:#6aa6ff}
button.danger{border-color:#ff7a7a;color:#ffb3b3}
button.small{padding:5px 10px;border-radius:9px;font-size:13px}
button:disabled{opacity:.45;cursor:default}
.card{background:rgba(110,160,255,.08);border:1px solid rgba(140,185,255,.2);border-radius:18px;padding:16px;margin-bottom:14px;backdrop-filter:blur(10px)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.sp{justify-content:space-between}
.big{font-size:30px;font-weight:600}
.muted{color:#93abd3;font-size:13px}
.pill{display:inline-block;padding:3px 10px;border-radius:99px;font-size:12px;border:1px solid rgba(140,185,255,.3)}
.pill.ok{color:#7be3a4;border-color:#3f9d68}.pill.soon{color:#ffd27a;border-color:#b98a2d}.pill.due{color:#ff9c9c;border-color:#c25555}
.chip{padding:7px 12px;border-radius:99px;border:1px solid rgba(140,185,255,.3);background:transparent;cursor:pointer}
.chip.on{background:#2f74e6;border-color:#7db1ff}
.bar{height:8px;border-radius:9px;background:rgba(255,255,255,.1);overflow:hidden;margin:6px 0}
.bar i{display:block;height:100%;background:#4f9bff}.bar i.soon{background:#f0b440}.bar i.due{background:#f06a6a}
input,select{font:inherit;color:#e8f1ff;background:rgba(10,25,50,.6);border:1px solid rgba(140,185,255,.3);border-radius:9px;padding:7px 9px}
input[type=number]{width:90px}
.err{color:#ffb3b3}
svg.map{width:100%;height:auto;max-height:70vh;background:rgba(5,14,30,.6);border-radius:14px;touch-action:none}
.sched{border-top:1px solid rgba(140,185,255,.15);padding-top:12px;margin-top:12px}
.list div{padding:8px 0;border-bottom:1px solid rgba(140,185,255,.12)}
`;

class ScovePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "cmd";
    this._sel = new Set();
    this._draw = false;
    this._drag = null;
    this._session = null;   // giro mostrato sulla mappa: null = quello in corso o l'ultimo
    this._tracks = {};      // percorsi dei giri passati già scaricati
    this._asked = {};
  }

  set hass(h) {
    this._hass = h;
    if (!this._started) { this._started = true; this._load(); }
  }

  connectedCallback() { this._timer = setInterval(() => this._load(), 3000); }
  disconnectedCallback() { clearInterval(this._timer); }

  async _load() {
    if (!this._hass) return;
    try { this._s = await this._hass.callWS({ type: "scove/get" }); this._err = null; }
    catch (e) { this._err = e.message || String(e); }
    const active = this.shadowRoot.activeElement;
    if (this._drag || (active && ["INPUT", "SELECT"].includes(active.tagName))) return;
    this._render();
  }

  async _cmd(action, extra = {}) {
    try { this._s = await this._hass.callWS({ type: "scove/command", action, ...extra }); this._err = null; }
    catch (e) { this._err = e.message || String(e); }
    this._render();
  }

  async _save(key, value) {
    try { this._s = await this._hass.callWS({ type: "scove/save", key, value }); this._err = null; }
    catch (e) { this._err = e.message || String(e); }
    this._render();
  }

  _render() {
    const s = this._s;
    const root = this.shadowRoot;
    if (!s) { root.innerHTML = `<style>${CSS}</style><div class="wrap"><h1>Scove</h1><div class="card">${esc(this._err || "Caricamento…")}</div></div>`; return; }
    const body = { cmd: () => this._tabCmd(s), prog: () => this._tabProg(s), health: () => this._tabHealth(s), map: () => this._tabMap(s), alerts: () => this._tabAlerts(s) }[this._tab]();
    root.innerHTML = `<style>${CSS}</style><div class="wrap">
      <h1>${esc(s.name)} <span class="pill ${s.connected ? "ok" : "due"}">${s.connected ? "online" : "offline"}</span></h1>
      <div class="tabs">${TABS.map(([k, n]) => `<button class="tab ${k === this._tab ? "on" : ""}" data-tab="${k}">${n}</button>`).join("")}</div>
      ${this._err ? `<div class="card err">${esc(this._err)}</div>` : ""}${body}</div>`;
    root.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { this._tab = b.dataset.tab; this._render(); }));
    this._bind(s);
  }

  // ── Comandi ───────────────────────────────────────────────────────
  _tabCmd(s) {
    const rooms = [...(s.map?.rooms || []), ...(s.map?.zones || [])];
    return `<div class="grid">
      <div class="card"><h2>Stato</h2>
        <div class="big">${esc(s.phase_label)}</div>
        <div class="muted">Batteria ${s.battery ?? "?"}% · Salute ${s.health}% · Prossima pulizia: ${fmtDate(s.next_run)}</div>
        ${s.error ? `<p class="err">⚠ ${esc(s.error_text)} (codice ${s.error})</p>` : ""}
        ${s.bin_full ? `<p class="err">⚠ Contenitore pieno</p>` : ""}
        <div class="row" style="margin-top:14px">
          <button class="primary" data-cmd="start">▶ Avvia</button><button data-cmd="pause">⏸ Pausa</button>
          <button data-cmd="stop">⏹ Stop</button><button data-cmd="dock">⌂ Base</button>
          <button data-cmd="find">🔔 Trova</button><button data-cmd="evac">🗑 Svuota</button>
        </div></div>
      <div class="card"><h2>Pulisci stanze</h2>
        ${rooms.length ? `<div class="row">${rooms.map((r) => `<button class="chip ${this._sel.has(r.id + r.type) ? "on" : ""}" data-room="${esc(r.id + r.type)}">${esc(r.label || r.name)}</button>`).join("")}</div>
        <div class="row" style="margin-top:14px"><button class="primary" id="go-rooms" ${this._sel.size ? "" : "disabled"}>Manda nelle stanze scelte</button></div>`
        : `<p class="muted">Mappa non ancora scaricata dal cloud iRobot. ${esc(s.cloud_error || "")}</p><button data-cmd="refresh_map">Riprova</button>`}
      </div>
      ${(s.room_days || []).length ? `<div class="card"><h2>Ultima pulizia per stanza</h2><div class="list">${s.room_days.map((d) => `<div class="row sp"><span>${esc(d.label)}</span><span class="muted">${d.never ? "non ancora registrata" : d.days === 0 ? "oggi" : d.days === 1 ? "ieri" : d.days + " giorni fa"}</span></div>`).join("")}</div></div>` : ""}
      </div>`;
  }

  // ── Programma ─────────────────────────────────────────────────────
  _tabProg(s) {
    const rooms = s.map?.rooms || [];
    return `<div class="card"><div class="row sp"><h2>Programmi</h2><button class="primary small" id="add-sched">+ Nuovo</button></div>
      <p class="muted">Gli orari sono gestiti da Home Assistant. Un programma viene saltato (con avviso) se il robot è già al lavoro, ha il contenitore pieno o la batteria sotto il ${s.settings.min_battery}%.</p>
      ${s.schedules.length ? "" : `<p class="muted">Nessun programma.</p>`}
      ${s.schedules.map((p, i) => `<div class="sched">
        <div class="row sp"><div class="row"><input data-s="${i}" data-f="name" value="${esc(p.name || "")}" placeholder="Nome">
          <input type="time" data-s="${i}" data-f="time" value="${esc(p.time || "09:00")}"></div>
          <div class="row"><button class="chip ${p.enabled !== false ? "on" : ""}" data-s="${i}" data-toggle="enabled">${p.enabled !== false ? "Attivo" : "Spento"}</button>
          <button class="small danger" data-s="${i}" data-del="1">Elimina</button></div></div>
        <div class="row" style="margin-top:8px">${DAYS.map((d, n) => `<button class="chip ${(p.days || []).includes(n) ? "on" : ""}" data-s="${i}" data-day="${n}">${d}</button>`).join("")}</div>
        <div class="row" style="margin-top:8px"><span class="muted">Stanze:</span>
          <button class="chip ${(p.rooms || []).length ? "" : "on"}" data-s="${i}" data-all="1">Tutta la casa</button>
          ${rooms.map((r) => `<button class="chip ${(p.rooms || []).includes(r.id) ? "on" : ""}" data-s="${i}" data-sroom="${esc(r.id)}">${esc(r.label || r.name)}</button>`).join("")}</div>
      </div>`).join("")}</div>`;
  }

  // ── Salute ────────────────────────────────────────────────────────
  _tabHealth(s) {
    const m = s.missions || {};
    return `<div class="grid"><div class="card"><h2>Salute generale</h2><div class="big">${s.health}%</div>
        <div class="muted">Ore di lavoro totali: ${s.runtime_hours} · Wi-Fi: ${s.rssi ?? "?"} dBm</div>
        <div class="muted">Batteria: ${s.battery_retention != null ? s.battery_retention + "% della capacità migliore registrata" : "capacità in rilevamento"} · ${s.battery_cycles ?? "?"} cicli</div>
        <div class="muted">Missioni: ${m.nMssn ?? "?"} totali, ${m.nMssnOk ?? "?"} riuscite, ${m.nMssnF ?? "?"} fallite, ${m.nMssnC ?? "?"} annullate</div>
        <div class="muted">Modello ${esc(s.sku || "?")} · firmware ${esc((s.firmware || "?").split("+").slice(0, 2).join(" "))}</div></div>
      <div class="card"><h2>Componenti</h2>${Object.entries(s.parts).map(([k, p]) => `<div style="margin-bottom:12px">
        <div class="row sp"><span>${esc(p.name)} <span class="pill ${p.level}">${p.level === "ok" ? "ok" : p.level === "soon" ? "a breve" : "da fare"}</span></span>
          <button class="small" data-part="${k}">Fatto</button></div>
        <div class="bar"><i class="${p.level}" style="width:${Math.min(100, p.used_pct)}%"></i></div>
        <div class="muted">${p.by_sensor ? "pieno secondo il robot: da svuotare adesso" : p.remaining > 0 ? `ancora ${p.remaining} h di lavoro` : `superato di ${Math.abs(p.remaining)} h`} ${p.learned_from ? `(si riempie in circa ${p.interval} h, da ${p.learned_from} ${p.learned_from > 1 ? "riempimenti osservati" : "riempimento osservato"})` : `(ogni ${p.interval} h)`}</div></div>`).join("")}</div></div>
      <div class="card"><h2>Ultime pulizie</h2><div class="list">${s.history.length ? s.history.map((h) => `<div data-sess="${esc(h.started)}" style="cursor:pointer">${fmtDate(h.started)} — <b>${esc(h.result)}</b> · ${h.area_m2 ?? "?"} m² · ${h.minutes ?? "?"} min di lavoro${(h.rooms || []).length ? " · " + esc(h.rooms.join(", ")) : ""}${h.stuck ? ` · bloccato ${h.stuck}×` : ""}${h.recharges ? ` · ${h.recharges} ricarica${h.recharges > 1 ? "he" : ""} a metà` : ""}${(h.missed || []).length ? ` · poco pulite: ${esc(h.missed.join(", "))}` : ""}${h.coverage ? `<br><span class="muted">${Object.entries(h.coverage).map(([n, v]) => `${esc(n)} ${v}%`).join(" · ")}</span>` : ""}</div>`).join("") : `<div class="muted">Ancora nessuna pulizia registrata da Scove.</div>`}</div>${s.history.length ? `<p class="muted">Tocca un giro per vederne il percorso sulla mappa.</p>` : ""}</div>`;
  }

  // ── Mappa ─────────────────────────────────────────────────────────
  _sessions(s) {
    const list = [];
    if (s.mission && !s.mission.ended) list.push({ key: "live", live: true });
    (s.history || []).forEach((h) => list.push({ key: h.started, h }));
    return list;
  }

  _currentSession(s) {
    const list = this._sessions(s);
    return list.find((x) => x.key === this._session) || list[0] || null;
  }

  _sessionPoints(s, cur) {
    if (!cur) return [];
    if (cur.live) return s.trail;
    if (this._tracks[cur.key]) return this._tracks[cur.key];
    if (cur.h.has_track && !this._asked[cur.key]) {
      this._asked[cur.key] = true;
      this._hass.callWS({ type: "scove/track", started: cur.key })
        .then((r) => { this._tracks[cur.key] = r.points || []; })
        .catch(() => { this._tracks[cur.key] = []; })
        .finally(() => this._render());
    }
    return [];
  }

  _sessionText(s, cur) {
    if (!cur) return "Ancora nessuna pulizia registrata da Scove.";
    if (cur.live) return `In corso da ${fmtTime(s.mission.started)} · ${s.run_minutes || 0} min di lavoro finora${s.recharging ? " · in ricarica" : ""}`;
    const h = cur.h;
    const bits = [fmtShort(h.started), h.result, `${h.area_m2 ?? "?"} m²`, `${h.minutes ?? "?"} min di lavoro`];
    if (h.recharges) bits.push(`${h.recharges} ricarica${h.recharges > 1 ? "he" : ""} esclus${h.recharges > 1 ? "e" : "a"} dal tempo`);
    if (h.stuck) bits.push(`bloccato ${h.stuck}×`);
    return bits.join(" · ");
  }

  _bounds(s, points, withPosition) {
    const pts = [[0, 0]];
    (s.map?.rooms || []).forEach((r) => pts.push(...r.polygon));
    (s.map?.keepouts || []).forEach((k) => pts.push(...k));
    s.zones.forEach((z) => pts.push([z.x1, z.y1], [z.x2, z.y2]));
    points.forEach((p) => pts.push(p));
    if (withPosition && s.position) pts.push([s.position.x, s.position.y]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => -p[1]);
    const x = Math.min(...xs) - 400, y = Math.min(...ys) - 400;
    return { x, y, w: Math.max(...xs) - x + 400, h: Math.max(...ys) - y + 400 };
  }

  _tabMap(s) {
    const sessions = this._sessions(s);
    const cur = this._currentSession(s);
    const points = this._sessionPoints(s, cur);
    const live = !!cur?.live;
    const b = this._bounds(s, points, live);
    const poly = (p) => p.map(([x, y]) => `${x},${-y}`).join(" ");
    const cal = s.calibration;
    const missing = cur && !live && !cur.h.has_track;
    const loading = cur && !live && cur.h.has_track && !this._tracks[cur.key];
    const h = cur && !live ? cur.h : null;
    return `<div class="card"><div class="row sp"><h2>Dove è passato</h2><div class="row">
        <button class="chip ${this._draw ? "on" : ""}" id="draw">${this._draw ? "Trascina sulla mappa…" : "+ Zona vietata"}</button></div></div>
      ${sessions.length ? `<div class="row" style="margin-bottom:10px"><select id="session" style="flex:1;min-width:0">${sessions.map((x) => `<option value="${esc(x.key)}" ${cur && x.key === cur.key ? "selected" : ""}>${x.live ? "In corso adesso" : `${esc(fmtShort(x.h.started))} · ${x.h.area_m2 ?? "?"} m² · ${x.h.minutes ?? "?"} min`}</option>`).join("")}</select></div>` : ""}
      <div class="muted" style="margin-bottom:10px">${esc(this._sessionText(s, cur))}</div>
      <svg class="map" viewBox="${b.x} ${b.y} ${b.w} ${b.h}">
        <defs><pattern id="hatch" width="160" height="160" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="60" height="160" fill="#ff5d5d" opacity=".55"/></pattern></defs>
        ${(s.map?.rooms || []).map((r) => r.polygon.length > 2 ? `<polygon points="${poly(r.polygon)}" fill="rgba(80,140,255,.16)" stroke="#7fb2ff" stroke-width="35"/>` : "").join("")}
        ${points.length > 1 ? `<polyline points="${poly(points)}" fill="none" stroke="#4fd8ff" stroke-opacity=".38" stroke-width="340" stroke-linecap="round" stroke-linejoin="round"/><polyline points="${poly(points)}" fill="none" stroke="#bff3ff" stroke-opacity=".75" stroke-width="22" stroke-linejoin="round"/>` : ""}
        ${(s.map?.keepouts || []).map((k) => `<polygon points="${poly(k)}" fill="url(#hatch)" stroke="#ff6b6b" stroke-width="30"/>`).join("")}
        ${s.zones.map((z, i) => `<rect data-zone="${i}" x="${Math.min(z.x1, z.x2)}" y="${-Math.max(z.y1, z.y2)}" width="${Math.abs(z.x2 - z.x1)}" height="${Math.abs(z.y2 - z.y1)}" fill="rgba(255,170,60,.28)" stroke="#ffb14a" stroke-width="35" stroke-dasharray="120 80" style="cursor:pointer"/>`).join("")}
        ${(s.map?.rooms || []).map((r) => { if (r.polygon.length < 3) return ""; const cx = r.polygon.reduce((a, p) => a + p[0], 0) / r.polygon.length, cy = r.polygon.reduce((a, p) => a + p[1], 0) / r.polygon.length; const pct = h?.coverage?.[r.label || r.name]; return `<text x="${cx}" y="${-cy}" fill="#dbe9ff" font-size="${Math.max(170, b.w / 45)}" text-anchor="middle">${esc(r.label || r.name)}${pct !== undefined ? ` · ${pct}%` : ""}</text>`; }).join("")}
        <circle cx="0" cy="0" r="120" fill="#ffd27a"/>
        ${live && s.position ? `<circle cx="${s.position.x}" cy="${-s.position.y}" r="170" fill="#fff" stroke="#2f74e6" stroke-width="60"/>` : ""}
        <rect id="ghost" fill="rgba(255,170,60,.3)" stroke="#ffb14a" stroke-width="35" visibility="hidden"/>
      </svg>
      ${missing ? `<p class="muted">Per questo giro il percorso non è stato conservato: lo storico dei percorsi parte dalla versione 1.0.16.</p>` : ""}
      ${loading ? `<p class="muted">Carico il percorso…</p>` : ""}
      ${h?.partial_track ? `<p class="muted">L'inizio di questo giro manca dal percorso: metri quadri sottostimati.</p>` : ""}
      <p class="muted">Azzurro: stanze del robot, con la parte coperta in quel giro · ciano: percorso del giro scelto · bianco: robot adesso · rosso tratteggiato: zone vietate impostate nell'app iRobot (il robot le rispetta) · arancione: zone segnate in Scove (tocca per eliminarle) · giallo: base. Il tempo non comprende le soste di ricarica.</p>
      ${s.zones.length ? `<p class="muted">Le zone arancioni per ora sono un promemoria visivo: il robot rispetta solo quelle rosse.</p>` : ""}
      <div class="row"><button class="small" data-cmd="refresh_map">Aggiorna mappa dal cloud</button></div></div>
      <div class="card"><h2>Allineamento percorso ↔ mappa</h2>
        <p class="muted">Se il percorso appare spostato o ruotato rispetto alle stanze, correggi qui. Vale per i punti registrati da ora in poi.</p>
        <div class="row">X (mm) <input type="number" data-cal="dx" value="${cal.dx || 0}"> Y (mm) <input type="number" data-cal="dy" value="${cal.dy || 0}">
          Rotazione (°) <input type="number" data-cal="rotation" value="${cal.rotation || 0}">
          <button class="chip ${cal.mirror ? "on" : ""}" id="mirror">Specchia</button></div></div>`;
  }

  // ── Avvisi ────────────────────────────────────────────────────────
  _tabAlerts(s) {
    return `<div class="card"><h2>Impostazioni avvisi</h2>
        <p class="muted">Gli avvisi compaiono sempre nelle notifiche di Home Assistant e come evento «scove_alert». Per riceverli anche sul telefono indica un servizio di notifica.</p>
        <div class="row">Servizio <input data-set="notify_service" value="${esc(s.settings.notify_service || "")}" placeholder="notify.famiglia" style="width:220px">
          Batteria minima per partire (%) <input type="number" data-set="min_battery" value="${s.settings.min_battery}">
          <button class="chip ${s.settings.notify_start ? "on" : ""}" id="notify-start">Avviso alla partenza</button>
          <button class="chip ${s.settings.notify_done ? "on" : ""}" id="notify-done">Avviso a fine pulizia</button></div></div>
      <div class="card"><h2>Funzioni aggiuntive</h2>
        <p class="muted">Tutte spente finché non le accendi. Toccale per attivarle o disattivarle.</p>
        ${[["action_buttons", "Pulsanti negli avvisi", "Dieci minuti prima di un programma arriva un avviso con «Rimanda di un'ora» e «Salta oggi»; alla partenza, «Torna alla base»."],
           ["retry", "Nuovo tentativo", "Se un programma non può partire (robot occupato, contenitore pieno, batteria scarica) riprova ogni 30 minuti, fino a 4 volte."],
           ["stuck_details", "Bloccato: stanza e mappa", "L'avviso di robot bloccato dice in quale stanza si trova e allega la mappa con il punto."],
           ["map_backup", "Copia della mappa", `Salva una copia di stanze e zone a ogni modifica della mappa (cartella scove_backups di Home Assistant).${s.backup_version ? " Ultima copia: versione " + esc(s.backup_version) + "." : ""}`],
          ].map(([k, n, d]) => `<div class="row sp" style="margin:10px 0;align-items:flex-start"><span style="flex:1"><b>${n}</b><br><span class="muted">${d}</span></span><button class="chip ${s.settings[k] ? "on" : ""}" data-toggle="${k}">${s.settings[k] ? "Accesa" : "Spenta"}</button></div>`).join("")}
        <div class="row sp" style="margin:10px 0;align-items:flex-start"><span style="flex:1"><b>Stanze trascurate</b><br><span class="muted">Avviso alle 9:00 se una stanza non viene pulita da questo numero di giorni. 0 = spento.</span></span><input type="number" min="0" data-set="stale_days" value="${s.settings.stale_days || 0}"></div></div>
      <div class="card"><h2>Avvisi recenti</h2><div class="list">${s.alerts.length ? s.alerts.map((a) => `<div><span class="pill ${a.kind === "problema" ? "due" : a.kind === "info" ? "ok" : "soon"}">${esc(a.kind)}</span> ${esc(a.message)} <span class="muted">· ${fmtDate(a.time)}</span></div>`).join("") : `<div class="muted">Nessun avviso.</div>`}</div></div>`;
  }

  // ── Eventi ────────────────────────────────────────────────────────
  _bind(s) {
    const q = (sel) => this.shadowRoot.querySelectorAll(sel);
    const one = (sel) => this.shadowRoot.querySelector(sel);
    q("[data-cmd]").forEach((b) => (b.onclick = () => this._cmd(b.dataset.cmd)));
    q("[data-room]").forEach((b) => (b.onclick = () => { const k = b.dataset.room; this._sel.has(k) ? this._sel.delete(k) : this._sel.add(k); this._render(); }));
    const go = one("#go-rooms");
    if (go) go.onclick = () => {
      const all = [...(s.map?.rooms || []), ...(s.map?.zones || [])];
      const ids = all.filter((r) => this._sel.has(r.id + r.type)).map((r) => `${r.type}:${r.id}`);
      this._sel.clear();
      this._cmd("clean_rooms", { rooms: ids });
    };
    q("[data-part]").forEach((b) => (b.onclick = () => this._cmd("reset_part", { part: b.dataset.part })));

    const sched = () => JSON.parse(JSON.stringify(s.schedules));
    const add = one("#add-sched");
    if (add) add.onclick = () => this._save("schedules", [...sched(), { name: "Nuovo programma", time: "10:00", days: [0, 1, 2, 3, 4], rooms: [], enabled: true }]);
    q("[data-s]").forEach((el) => {
      const i = Number(el.dataset.s);
      const apply = (fn) => { const list = sched(); fn(list[i], list); this._save("schedules", list); };
      if (el.dataset.f) el.onchange = () => apply((p) => (p[el.dataset.f] = el.value));
      else if (el.dataset.toggle) el.onclick = () => apply((p) => (p.enabled = p.enabled === false));
      else if (el.dataset.del) el.onclick = () => apply((_p, list) => list.splice(i, 1));
      else if (el.dataset.day !== undefined) el.onclick = () => apply((p) => { const d = Number(el.dataset.day); p.days = (p.days || []).includes(d) ? p.days.filter((x) => x !== d) : [...(p.days || []), d].sort(); });
      else if (el.dataset.all) el.onclick = () => apply((p) => (p.rooms = []));
      else if (el.dataset.sroom) el.onclick = () => apply((p) => { const r = el.dataset.sroom; p.rooms = (p.rooms || []).includes(r) ? p.rooms.filter((x) => x !== r) : [...(p.rooms || []), r]; });
    });

    const sess = one("#session");
    if (sess) sess.onchange = () => { this._session = sess.value; this._render(); };
    q("[data-sess]").forEach((el) => (el.onclick = () => { this._session = el.dataset.sess; this._tab = "map"; this._render(); }));
    const draw = one("#draw");
    if (draw) draw.onclick = () => { this._draw = !this._draw; this._render(); };
    q("[data-cal]").forEach((el) => (el.onchange = () => this._save("calibration", { ...s.calibration, [el.dataset.cal]: Number(el.value) || 0 })));
    const mirror = one("#mirror");
    if (mirror) mirror.onclick = () => this._save("calibration", { ...s.calibration, mirror: !s.calibration.mirror });
    q("[data-zone]").forEach((el) => (el.onclick = () => { if (!this._draw && confirm("Eliminare questa zona?")) this._save("zones", s.zones.filter((_z, i) => i !== Number(el.dataset.zone))); }));

    const svg = one("svg.map");
    if (svg && this._draw) {
      const ghost = one("#ghost");
      const pt = (ev) => { const p = svg.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
      svg.onpointerdown = (ev) => { this._drag = pt(ev); svg.setPointerCapture(ev.pointerId); };
      svg.onpointermove = (ev) => {
        if (!this._drag) return;
        const p = pt(ev), a = this._drag;
        ghost.setAttribute("x", Math.min(a.x, p.x)); ghost.setAttribute("y", Math.min(a.y, p.y));
        ghost.setAttribute("width", Math.abs(p.x - a.x)); ghost.setAttribute("height", Math.abs(p.y - a.y));
        ghost.setAttribute("visibility", "visible");
      };
      svg.onpointerup = (ev) => {
        const a = this._drag, p = pt(ev);
        this._drag = null; this._draw = false;
        if (!a || Math.abs(p.x - a.x) < 150 || Math.abs(p.y - a.y) < 150) { this._render(); return; }
        this._save("zones", [...s.zones, { id: Date.now().toString(36), x1: Math.round(a.x), y1: Math.round(-a.y), x2: Math.round(p.x), y2: Math.round(-p.y) }]);
      };
    }

    q("[data-set]").forEach((el) => (el.onchange = () => this._save("settings", { ...s.settings, [el.dataset.set]: el.type === "number" ? Number(el.value) : el.value })));
    q("[data-toggle]").forEach((el) => (el.onclick = () => this._save("settings", { ...s.settings, [el.dataset.toggle]: !s.settings[el.dataset.toggle] })));
    const ns = one("#notify-start");
    if (ns) ns.onclick = () => this._save("settings", { ...s.settings, notify_start: !s.settings.notify_start });
    const nd = one("#notify-done");
    if (nd) nd.onclick = () => this._save("settings", { ...s.settings, notify_done: !s.settings.notify_done });
  }
}

customElements.define("scove-panel", ScovePanel);
