// QuoteBuilder AI – front end (no framework, no build step)
"use strict";

const $ = (s) => document.querySelector(s);
const gbp = (x) => "£" + Number(x).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const EXAMPLES = [
  "Hi, I'd like a 5x4m sandstone patio at the back of the house and about 12 metres of new fencing, 1.8m high, down the side of the garden.",
  "Can you build a garden wall 8m long and 1m high, double skin? Also need a concrete base for a new shed, 3m by 2.5m, 150mm thick.",
  "We're doing up the lounge (5m by 4m): paint the walls with two coats and re-skim the ceiling, about 20 sqm. Plus laminate flooring in the bedroom, 4 x 3.5 m.",
];

const FIELD_LABELS = {
  length_m: "Length (m)", width_m: "Width (m)", height_m: "Height (m)", area_m2: "Area (m²)",
  depth_mm: "Depth (mm)", material: "Material", thickness: "Thickness", coats: "Coats",
};

let CONFIG = null;
let items = [];
let lastQuote = null;
let recalcTimer = null;
let recalcSeq = 0;

async function api(path, body, asBlob = false) {
  const res = await fetch(path, body ? {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  } : undefined);
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : (j.detail?.[0]?.msg || msg);
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return asBlob ? res.blob() : res.json();
}

// ------------------------------------------------------------- init
async function init() {
  CONFIG = await api("/api/config");
  $("#biz-name").textContent = `${CONFIG.business.name} · demo`;
  const badge = $("#ai-badge");
  badge.textContent = CONFIG.ai_enabled ? "AI: Gemini connected" : "AI: offline – rule parser";
  badge.classList.add(CONFIG.ai_enabled ? "on" : "off");

  const sel = $("#add-type");
  sel.innerHTML = Object.entries(CONFIG.job_types)
    .map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");

  document.querySelectorAll(".chip[data-ex]").forEach((b) =>
    b.addEventListener("click", () => { $("#description").value = EXAMPLES[b.dataset.ex]; analyse(); }));
  $("#analyse").addEventListener("click", analyse);
  $("#description").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) analyse();
  });
  $("#add-item").addEventListener("click", () => {
    const type = $("#add-type").value;
    items.push({ type, ...CONFIG.job_types[type].defaults });
    renderItems(); scheduleRecalc();
  });
  ["#c-name", "#c-email", "#c-address"].forEach((id) => $(id).addEventListener("input", scheduleRecalc));
}

// ------------------------------------------------------------- step 1
async function analyse() {
  const description = $("#description").value.trim();
  if (description.length < 3) { $("#description").focus(); return; }
  const btn = $("#analyse");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span><span>Reading the request…</span>`;
  try {
    const res = await api("/api/extract", { description });
    items = res.items.map((i) => ({ ...CONFIG.job_types[i.type].defaults, ...stripNulls(i) }));
    const sb = $("#source-badge");
    sb.textContent = res.source === "ai" ? "✦ Extracted by AI" : "Extracted by rule parser";
    sb.classList.remove("hidden");
    showNotice(res.warning, res.questions);
    renderItems();
    scheduleRecalc(0);
    if (window.innerWidth < 900) $("#review-card").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    showNotice(e.message, []);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l1.8 5.4L19 9l-5.2 1.6L12 16l-1.8-5.4L5 9l5.2-1.6zM19 14l.9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9z"/></svg><span>Analyse with AI</span>`;
  }
}

function stripNulls(o) {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== ""));
}

function showNotice(warning, questions) {
  const n = $("#notice");
  const parts = [];
  if (warning) parts.push(`<div>${esc(warning)}</div>`);
  if (questions?.length) {
    parts.push(`<b>Worth checking with the customer:</b><ul>${questions.map((q) => `<li>${esc(q)}</li>`).join("")}</ul>`);
  }
  n.innerHTML = parts.join("");
  n.classList.toggle("hidden", parts.length === 0);
}

// ------------------------------------------------------------- step 2
function renderItems() {
  const box = $("#items");
  if (!items.length) {
    box.innerHTML = `<p class="empty">No items yet – describe a job above, or add one manually.</p>`;
    return;
  }
  box.innerHTML = items.map((it, idx) => {
    const meta = CONFIG.job_types[it.type];
    const typeSel = Object.entries(CONFIG.job_types)
      .map(([k, v]) => `<option value="${k}" ${k === it.type ? "selected" : ""}>${esc(v.label)}</option>`).join("");
    const fields = meta.fields.map((f) => {
      const opts = meta.options[f];
      if (opts) {
        return `<label>${FIELD_LABELS[f]}<select data-i="${idx}" data-f="${f}">${opts
          .map((o) => `<option ${it[f] === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>`;
      }
      const step = f === "coats" ? "1" : "0.1";
      return `<label>${FIELD_LABELS[f]}<input type="number" min="0" step="${step}" inputmode="decimal"
               data-i="${idx}" data-f="${f}" value="${it[f] ?? ""}" placeholder="–"></label>`;
    }).join("");
    return `<div class="item" id="item-${idx}">
      <div class="item-head">
        <label>Job type<select data-i="${idx}" data-f="type">${typeSel}</select></label>
        <label class="lbl-input">Label<input data-i="${idx}" data-f="label" value="${esc(it.label)}" placeholder="${esc(meta.label)}"></label>
        <button class="remove" data-remove="${idx}" title="Remove item" aria-label="Remove item">×</button>
      </div>
      <div class="fields">${fields}</div>
      ${it.notes ? `<div class="note">ⓘ ${esc(it.notes)}</div>` : ""}
      <div class="hint">${esc(meta.hint)}</div>
    </div>`;
  }).join("");

  box.querySelectorAll("[data-f]").forEach((el) => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", onEdit));
  box.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
    items.splice(Number(b.dataset.remove), 1); renderItems(); scheduleRecalc(0);
  }));
}

function onEdit(e) {
  const el = e.target;
  const i = Number(el.dataset.i), f = el.dataset.f;
  if (f === "type") {
    const keep = { label: items[i].label };
    items[i] = { type: el.value, ...CONFIG.job_types[el.value].defaults, ...stripNulls(keep) };
    renderItems();
  } else if (el.type === "number") {
    const v = parseFloat(el.value);
    if (Number.isFinite(v) && v > 0) items[i][f] = f === "coats" ? Math.round(v) : v;
    else delete items[i][f];
    // An explicit area overrides length x width, so clear it when a dimension is edited.
    if (["length_m", "width_m", "height_m"].includes(f) && items[i].area_m2) {
      delete items[i].area_m2;
      const a = document.querySelector(`[data-i="${i}"][data-f="area_m2"]`);
      if (a) a.value = "";
    }
  } else {
    items[i][f] = el.value;
  }
  scheduleRecalc();
}

// ------------------------------------------------------------- step 3
function scheduleRecalc(delay = 350) {
  clearTimeout(recalcTimer);
  recalcTimer = setTimeout(recalc, delay);
}

function payload() {
  return {
    items: items.map(stripNulls),
    reference: lastQuote?.reference || null,
    description: $("#description").value.trim(),
    customer: { name: $("#c-name").value.trim(), email: $("#c-email").value.trim(), address: $("#c-address").value.trim() },
  };
}

async function recalc() {
  const box = $("#quote");
  document.querySelectorAll(".item.invalid").forEach((e) => e.classList.remove("invalid"));
  if (!items.length) {
    lastQuote = null;
    box.innerHTML = `<p class="empty">Your priced quote will appear here.</p>`;
    return;
  }
  const seq = ++recalcSeq;
  box.classList.add("updating");
  try {
    const q = await api("/api/quote", payload());
    if (seq !== recalcSeq) return;
    lastQuote = q;
    renderQuote(q);
  } catch (e) {
    if (seq !== recalcSeq) return;
    lastQuote = null;
    box.innerHTML = `<div class="error">⚠ ${esc(e.message)}</div><p class="muted small">Fill in the highlighted item to see the price.</p>`;
    const label = e.message.split(":")[0];
    items.forEach((it, idx) => {
      if (CONFIG.job_types[it.type].label === label) document.getElementById(`item-${idx}`)?.classList.add("invalid");
    });
  } finally {
    if (seq === recalcSeq) box.classList.remove("updating");
  }
}

function renderQuote(q) {
  const works = q.materials_total + q.labour_total + q.plant_total;
  const pct = (x) => (works ? (100 * x) / works : 0).toFixed(1);
  const s = q.settings;
  $("#quote").innerHTML = `
    <div class="total-box">
      <div class="lbl">Total incl. VAT · ${esc(q.reference)}</div>
      <div class="amt">${gbp(q.grand_total)}</div>
      <div class="sub">${gbp(q.net_total)} + VAT · valid until ${new Date(q.valid_until).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</div>
    </div>
    <div class="split">
      <div><span><i class="dot c-mat"></i>Materials</span><b>${gbp(q.materials_total)}</b></div>
      <div><span><i class="dot c-lab"></i>Labour</span><b>${gbp(q.labour_total)}</b></div>
      <div><span><i class="dot c-pla"></i>Plant & skips</span><b>${gbp(q.plant_total)}</b></div>
    </div>
    <div class="bar" title="Cost mix">
      <i class="c-mat" style="width:${pct(q.materials_total)}%"></i><i class="c-lab" style="width:${pct(q.labour_total)}%"></i><i class="c-pla" style="width:${pct(q.plant_total)}%"></i>
    </div>
    ${q.items.map((it, n) => `
      <details class="qitem" ${q.items.length <= 2 ? "open" : ""}>
        <summary><span class="t">${n + 1}. ${esc(it.title)}</span><span>${gbp(it.subtotal)}</span></summary>
        <table class="lines">${it.lines.map((l) => `
          <tr><td>${esc(l.description)}</td><td class="q">${l.quantity} ${esc(l.unit)} × ${gbp(l.unit_price)}</td><td class="n">${gbp(l.total)}</td></tr>`).join("")}
        </table>
        <ul class="assume">${it.assumptions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
      </details>`).join("")}
    <table class="totals">
      <tr><td>Works total</td><td>${gbp(works)}</td></tr>
      <tr><td>Contingency (${s.contingency_pct}%)</td><td>${gbp(q.contingency)}</td></tr>
      <tr><td>VAT (${s.vat_pct}%)</td><td>${gbp(q.vat)}</td></tr>
      <tr class="grand"><td>Total</td><td>${gbp(q.grand_total)}</td></tr>
    </table>
    <div class="internal">🔒 Internal: includes ${gbp(q.materials_markup)} materials margin (${s.materials_markup_pct}% markup). Not shown to the customer.</div>
    <button id="pdf" class="btn accent">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11.2l4.1-4.1 1.4 1.4L12 17l-5.5-5.5 1.4-1.4 4.1 4.1V3zM5 19h14v2H5z"/></svg>
      Download PDF quote
    </button>`;
  $("#pdf").addEventListener("click", downloadPdf);
}

async function downloadPdf() {
  const btn = $("#pdf");
  btn.disabled = true;
  try {
    const blob = await api("/api/quote/pdf", payload(), true);
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: `${lastQuote?.reference || "quote"}.pdf` });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
}

init().catch((e) => { $("#quote").innerHTML = `<div class="error">Could not load the app: ${esc(e.message)}</div>`; });
