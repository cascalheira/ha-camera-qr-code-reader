/**
 * QR Code RTSP Reader — admin panel.
 *
 * A dependency-free custom element (no build step). Home Assistant assigns the
 * `hass` property; we talk to the backend over its WebSocket connection.
 */

const WEEKDAYS = [
  ["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"],
  ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class QrRtspPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._entries = [];
    this._entryId = null;
    this._rules = [];
    this._ready = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._ready) {
      this._ready = true;
      this._renderShell();
      this._init();
    }
  }
  get hass() {
    return this._hass;
  }

  async _init() {
    try {
      const { entries } = await this._hass.callWS({ type: "qr_rtsp/entries" });
      this._entries = entries || [];
      this._entryId = this._entries.length ? this._entries[0].entry_id : null;
      this._renderHeader();
      await this._loadRules();
    } catch (err) {
      this._toast(`Failed to load: ${err.message || err}`, true);
    }
  }

  async _loadRules() {
    if (!this._entryId) {
      this._rules = [];
      this._renderTable();
      return;
    }
    const { rules } = await this._hass.callWS({
      type: "qr_rtsp/rules/list",
      entry_id: this._entryId,
    });
    this._rules = rules || [];
    this._renderTable();
  }

  /* ---------- rendering ---------- */

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="wrap">
        <div class="bar">
          <h1>QR Codes</h1>
          <div id="header-right"></div>
        </div>
        <div class="card">
          <div id="table"></div>
        </div>
      </div>
      <div id="modal-root"></div>
      <div id="toast" class="toast"></div>
    `;
    this.shadowRoot
      .getElementById("table")
      .addEventListener("click", (e) => this._onTableClick(e));
  }

  _renderHeader() {
    const right = this.shadowRoot.getElementById("header-right");
    const selector =
      this._entries.length > 1
        ? `<select id="entry-sel">${this._entries
            .map(
              (e) =>
                `<option value="${esc(e.entry_id)}" ${
                  e.entry_id === this._entryId ? "selected" : ""
                }>${esc(e.name)}</option>`
            )
            .join("")}</select>`
        : "";
    right.innerHTML = `
      ${selector}
      <button class="btn ghost" id="btn-add">Add code</button>
      <button class="btn" id="btn-gen">Generate code</button>
    `;
    const sel = this.shadowRoot.getElementById("entry-sel");
    if (sel)
      sel.addEventListener("change", (e) => {
        this._entryId = e.target.value;
        this._loadRules();
      });
    this.shadowRoot.getElementById("btn-add").onclick = () =>
      this._openRuleDialog(null);
    this.shadowRoot.getElementById("btn-gen").onclick = () =>
      this._openGenerateDialog();
  }

  _renderTable() {
    const el = this.shadowRoot.getElementById("table");
    if (!this._entryId) {
      el.innerHTML = `<p class="empty">No QR Code RTSP Reader is configured yet.</p>`;
      return;
    }
    if (!this._rules.length) {
      el.innerHTML = `<p class="empty">No codes yet. Use “Add code” or “Generate code”.</p>`;
      return;
    }
    const rows = this._rules
      .map((r) => {
        const script = r.script_entity
          ? esc(this._friendly(r.script_entity))
          : `<span class="muted">—</span>`;
        return `<tr>
          <td><b>${esc(r.title || r.name || "")}</b>${
          r.title && r.name ? `<div class="muted small">${esc(r.name)}</div>` : ""
        }<div class="muted mono">${esc(this._shortPayload(r.payload))}</div></td>
          <td>${esc(this._validity(r))}</td>
          <td>${script}</td>
          <td class="actions">
            <button class="icon" data-act="qr" data-p="${esc(r.payload)}" title="Show QR">▦</button>
            <button class="icon" data-act="edit" data-p="${esc(r.payload)}" title="Edit">✎</button>
            <button class="icon danger" data-act="del" data-p="${esc(r.payload)}" title="Delete">🗑</button>
          </td>
        </tr>`;
      })
      .join("");
    el.innerHTML = `
      <table>
        <thead><tr><th>Code</th><th>Validity</th><th>Script on scan</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  _onTableClick(e) {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const rule = this._rules.find((r) => r.payload === btn.dataset.p);
    if (!rule) return;
    if (btn.dataset.act === "edit") this._openRuleDialog(rule);
    else if (btn.dataset.act === "qr") this._showQr(rule);
    else this._confirmDelete(rule);
  }

  async _showQr(rule) {
    try {
      const { image_b64 } = await this._hass.callWS({
        type: "qr_rtsp/image",
        entry_id: this._entryId,
        payload: rule.payload,
      });
      const url = `data:image/png;base64,${image_b64}`;
      this._modal(
        rule.title || rule.name || "QR code",
        `<div class="result">
           <img src="${url}" alt="QR code" />
           <div>
             <p class="mono small">${esc(this._shortPayload(rule.payload))}</p>
             <a class="btn" href="${url}" download="${esc(rule.name || "qr-code")}.png">Download PNG</a>
           </div>
         </div>`,
        `<button class="btn ghost" id="d-cancel">Close</button>`
      );
      this.shadowRoot.getElementById("d-cancel").onclick = () =>
        this._closeModal();
    } catch (err) {
      this._toast(err.message || String(err), true);
    }
  }

  /* ---------- helpers ---------- */

  _friendly(entityId) {
    const st = this._hass.states[entityId];
    return (st && st.attributes.friendly_name) || entityId;
  }
  _shortPayload(p) {
    return p && p.length > 42 ? p.slice(0, 39) + "…" : p || "";
  }
  _validity(r) {
    const parts = [];
    if (r.valid_from || r.valid_until)
      parts.push(`${r.valid_from || "…"} → ${r.valid_until || "…"}`);
    if (r.weekdays && r.weekdays.length)
      parts.push(
        r.weekdays
          .map((d) => (WEEKDAYS.find((w) => w[0] === d) || [d, d])[1])
          .join(" ")
      );
    if (r.start_time || r.end_time)
      parts.push(`${r.start_time || "00:00"}–${r.end_time || "23:59"}`);
    return parts.length ? parts.join(" · ") : "Always";
  }

  _scriptOptions(selected) {
    const opts = Object.keys(this._hass.states)
      .filter((s) => s.startsWith("script."))
      .sort()
      .map(
        (s) =>
          `<option value="${esc(s)}" ${
            s === selected ? "selected" : ""
          }>${esc(this._friendly(s))}</option>`
      )
      .join("");
    return `<option value="">— none —</option>${opts}`;
  }

  /* ---------- modals ---------- */

  _closeModal() {
    this.shadowRoot.getElementById("modal-root").innerHTML = "";
  }

  _modal(title, bodyHtml, footerHtml) {
    const root = this.shadowRoot.getElementById("modal-root");
    root.innerHTML = `
      <div class="overlay">
        <div class="dialog">
          <div class="dhead"><h2>${esc(title)}</h2><button class="icon" id="m-x">✕</button></div>
          <div class="dbody">${bodyHtml}</div>
          <div class="dfoot">${footerHtml}</div>
        </div>
      </div>`;
    root.querySelector(".overlay").addEventListener("click", (e) => {
      if (e.target.classList.contains("overlay")) this._closeModal();
    });
    root.querySelector("#m-x").onclick = () => this._closeModal();
    return root;
  }

  _ruleFormFields(r) {
    return `
      <label>Title (what this code is for)
        <input id="f-title" type="text" value="${esc(r.title || "")}" placeholder="e.g. Weekend guest access"></label>
      <label>Name (short identifier, embedded in the code)
        <input id="f-name" type="text" value="${esc(r.name || "")}"></label>
      <label>QR payload (exact text in the code)
        <input id="f-payload" type="text" value="${esc(r.payload || "")}"></label>
      ${this._validityFieldsHtml(r)}
    `;
  }

  /** Optional limits, each behind an off-by-default toggle. */
  _validityFieldsHtml(r) {
    const has = (k) =>
      r[k] != null && r[k] !== "" && !(Array.isArray(r[k]) && !r[k].length);
    const sec = (key, label, on, inner) => `
      <div class="toggle-sec">
        <label class="switch"><input type="checkbox" id="t-${key}" ${
      on ? "checked" : ""
    }> ${label}</label>
        <div class="sec" id="s-${key}" style="${on ? "" : "display:none"}">${inner}</div>
      </div>`;

    const dates = sec(
      "dates",
      "Limit to a date range",
      has("valid_from") || has("valid_until"),
      `<div class="row">
         <label>Valid from<input id="f-from" type="date" value="${esc(r.valid_from || "")}"></label>
         <label>Valid until<input id="f-until" type="date" value="${esc(r.valid_until || "")}"></label>
       </div>`
    );
    const days = sec(
      "days",
      "Limit to certain weekdays",
      (r.weekdays || []).length > 0,
      `<div class="days">${WEEKDAYS.map(
        ([v, l]) =>
          `<label class="day"><input type="checkbox" value="${v}" ${
            (r.weekdays || []).includes(v) ? "checked" : ""
          }>${l}</label>`
      ).join("")}</div>`
    );
    const time = sec(
      "time",
      "Limit to a time of day",
      has("start_time") || has("end_time"),
      `<div class="row">
         <label>Allowed from<input id="f-start" type="time" value="${esc(r.start_time || "")}"></label>
         <label>Allowed until<input id="f-end" type="time" value="${esc(r.end_time || "")}"></label>
       </div>`
    );
    return `${dates}${days}${time}
      <label>Script to run on authorized scan
        <select id="f-script">${this._scriptOptions(r.script_entity || "")}</select></label>`;
  }

  _wireToggles() {
    ["dates", "days", "time"].forEach((k) => {
      const t = this.shadowRoot.getElementById(`t-${k}`);
      const s = this.shadowRoot.getElementById(`s-${k}`);
      if (t && s)
        t.addEventListener("change", () => {
          s.style.display = t.checked ? "" : "none";
        });
    });
  }

  _collectRuleForm() {
    const sr = this.shadowRoot;
    const v = (id) => {
      const el = sr.getElementById(id);
      return el ? el.value.trim() : "";
    };
    const on = (id) => {
      const el = sr.getElementById(id);
      return !!(el && el.checked);
    };
    const rule = {};
    if (v("f-payload")) rule.payload = v("f-payload");
    if (v("f-title")) rule.title = v("f-title");
    if (v("f-name")) rule.name = v("f-name");
    if (on("t-dates")) {
      if (v("f-from")) rule.valid_from = v("f-from");
      if (v("f-until")) rule.valid_until = v("f-until");
    }
    if (on("t-days")) {
      const days = [...sr.querySelectorAll(".days input:checked")].map(
        (c) => c.value
      );
      if (days.length) rule.weekdays = days;
    }
    if (on("t-time")) {
      if (v("f-start")) rule.start_time = v("f-start");
      if (v("f-end")) rule.end_time = v("f-end");
    }
    if (v("f-script")) rule.script_entity = v("f-script");
    return rule;
  }

  _openRuleDialog(existing) {
    const r = existing || {};
    this._modal(
      existing ? "Edit code" : "Add code",
      `<form id="rule-form">${this._ruleFormFields(r)}</form>`,
      `<button class="btn ghost" id="d-cancel">Cancel</button>
       <button class="btn" id="d-save">Save</button>`
    );
    this._wireToggles();
    this.shadowRoot.getElementById("d-cancel").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("d-save").onclick = async () => {
      const rule = this._collectRuleForm();
      if (!rule.payload) {
        this._toast("Payload is required", true);
        return;
      }
      try {
        const msg = {
          type: "qr_rtsp/rules/save",
          entry_id: this._entryId,
          rule,
        };
        if (existing && existing.payload) msg.original_payload = existing.payload;
        const { rules } = await this._hass.callWS(msg);
        this._rules = rules;
        this._renderTable();
        this._closeModal();
        this._toast("Saved");
      } catch (err) {
        this._toast(err.message || String(err), true);
      }
    };
  }

  _openGenerateDialog() {
    this._modal(
      "Generate secure code",
      `<form id="gen-form">
         <label>Name<input id="g-name" type="text" placeholder="guest-weekend"></label>
         <label>Title (what this code is for)
           <input id="f-title" type="text" placeholder="e.g. Weekend guest access"></label>
         <label>Complexity (bytes of randomness): <span id="g-eval">16</span>
           <input id="g-bytes" type="range" min="8" max="64" value="16"></label>
         ${this._validityFieldsHtml({})}
       </form>
       <div id="gen-result"></div>`,
      `<button class="btn ghost" id="d-cancel">Close</button>
       <button class="btn" id="d-gen">Generate</button>`
    );
    this._wireToggles();
    const bytes = this.shadowRoot.getElementById("g-bytes");
    bytes.oninput = () =>
      (this.shadowRoot.getElementById("g-eval").textContent = bytes.value);
    this.shadowRoot.getElementById("d-cancel").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("d-gen").onclick = async () => {
      const sr = this.shadowRoot;
      const name = sr.getElementById("g-name").value.trim();
      if (!name) {
        this._toast("Name is required", true);
        return;
      }
      const rule = this._collectRuleForm(); // reuses f-* ids
      delete rule.payload;
      try {
        const res = await this._hass.callWS({
          type: "qr_rtsp/generate",
          entry_id: this._entryId,
          name,
          entropy_bytes: Number(bytes.value),
          rule,
        });
        this._rules = res.rules;
        this._renderTable();
        const url = `data:image/png;base64,${res.image_b64}`;
        sr.getElementById("gen-result").innerHTML = `
          <div class="result">
            <img src="${url}" alt="QR code" />
            <div>
              <p class="ok">Generated and registered.</p>
              <p class="mono small">${esc(res.payload)}</p>
              <a class="btn" href="${url}" download="${esc(name)}.png">Download PNG</a>
            </div>
          </div>`;
        this._toast("Generated");
      } catch (err) {
        this._toast(err.message || String(err), true);
      }
    };
  }

  _confirmDelete(rule) {
    this._modal(
      "Delete code",
      `<p>Delete <b>${esc(
        rule.title || rule.name || this._shortPayload(rule.payload)
      )}</b>?</p>`,
      `<button class="btn ghost" id="d-cancel">Cancel</button>
       <button class="btn danger" id="d-del">Delete</button>`
    );
    this.shadowRoot.getElementById("d-cancel").onclick = () => this._closeModal();
    this.shadowRoot.getElementById("d-del").onclick = async () => {
      try {
        const { rules } = await this._hass.callWS({
          type: "qr_rtsp/rules/delete",
          entry_id: this._entryId,
          payload: rule.payload,
        });
        this._rules = rules;
        this._renderTable();
        this._closeModal();
        this._toast("Deleted");
      } catch (err) {
        this._toast(err.message || String(err), true);
      }
    };
  }

  _toast(message, isError) {
    const t = this.shadowRoot.getElementById("toast");
    t.textContent = message;
    t.className = "toast show" + (isError ? " error" : "");
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => (t.className = "toast"), 3200);
  }
}

const STYLES = `
  :host { display:block; background: var(--primary-background-color); color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); min-height:100%; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 16px; }
  .bar { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
  h1 { font-size: 22px; margin: 8px 0 16px; }
  #header-right { display:flex; gap:8px; align-items:center; }
  .card { background: var(--card-background-color, #fff); border-radius: 12px;
          box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12)); padding: 8px; }
  table { width:100%; border-collapse: collapse; }
  th, td { text-align:left; padding: 12px; border-bottom: 1px solid var(--divider-color, #e0e0e0); vertical-align: top; }
  th { font-size: 12px; text-transform: uppercase; color: var(--secondary-text-color); }
  td.actions, th:last-child { text-align: right; white-space: nowrap; }
  .muted { color: var(--secondary-text-color); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  .small { font-size: 12px; }
  .empty { text-align:center; color: var(--secondary-text-color); padding: 32px; }
  .btn { background: var(--primary-color); color: var(--text-primary-color, #fff); border: none;
         padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 14px; text-decoration:none; display:inline-block; }
  .btn.ghost { background: transparent; color: var(--primary-color); border: 1px solid var(--primary-color); }
  .btn.danger { background: var(--error-color, #db4437); }
  .icon { background:none; border:none; cursor:pointer; font-size:16px; padding:4px 6px; border-radius:6px; color: var(--primary-text-color); }
  .icon.danger { color: var(--error-color, #db4437); }
  select, input[type=text], input[type=date], input[type=time] {
    width:100%; box-sizing:border-box; padding:8px; border-radius:8px;
    border:1px solid var(--divider-color,#ccc); background: var(--card-background-color,#fff); color: inherit; font-size:14px; }
  .overlay { position:fixed; inset:0; background: rgba(0,0,0,.45); display:flex; align-items:center; justify-content:center; z-index:9999; }
  .dialog { background: var(--card-background-color,#fff); width: min(560px, 94vw); max-height: 90vh; overflow:auto;
            border-radius: 14px; box-shadow: 0 10px 40px rgba(0,0,0,.3); }
  .dhead { display:flex; justify-content:space-between; align-items:center; padding: 14px 18px; border-bottom:1px solid var(--divider-color,#eee); }
  .dhead h2 { margin:0; font-size:18px; }
  .dbody { padding: 18px; display:flex; flex-direction:column; gap:14px; }
  .dfoot { padding: 14px 18px; display:flex; justify-content:flex-end; gap:10px; border-top:1px solid var(--divider-color,#eee); }
  label { display:flex; flex-direction:column; gap:6px; font-size:13px; color: var(--secondary-text-color); }
  .row { display:flex; gap:12px; } .row > label { flex:1; }
  .toggle-sec { border-top:1px solid var(--divider-color,#eee); padding-top:12px; }
  .switch { flex-direction:row; align-items:center; gap:8px; font-size:14px; color: var(--primary-text-color); cursor:pointer; }
  .switch input { width:auto; }
  .sec { margin-top:12px; display:flex; flex-direction:column; gap:12px; }
  .days { display:flex; gap:6px; flex-wrap:wrap; }
  .day { flex-direction:row; align-items:center; gap:4px; background: var(--secondary-background-color,#f1f1f1);
         padding:6px 10px; border-radius:8px; color: var(--primary-text-color); }
  .result { display:flex; gap:16px; align-items:center; margin-top:8px; }
  .result img { width:140px; height:140px; border-radius:8px; background:#fff; padding:6px; }
  .ok { color: var(--success-color, #43a047); margin:0 0 6px; }
  .toast { position:fixed; bottom:24px; left:50%; transform:translateX(-50%) translateY(20px);
           background:#323232; color:#fff; padding:10px 16px; border-radius:8px; opacity:0; transition:.25s; pointer-events:none; z-index:10000; }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.error { background: var(--error-color,#db4437); }
`;

if (!customElements.get("qr-rtsp-panel")) {
  customElements.define("qr-rtsp-panel", QrRtspPanel);
}
