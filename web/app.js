"use strict";
/* Laya demo: builds Jev-shaped {state, questions} payloads, renders typed answers. */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const FALLBACK_BASE = "https://jev.x1nx3r.dev";
// Served from the API itself -> same origin (no CORS involved).
// Opened as a file or from another host -> fall back to the public API.
const DEFAULT_BASE = window.location.protocol.startsWith("http") ? window.location.origin : FALLBACK_BASE;
const store = {
  get base() { return localStorage.getItem("laya.base") || DEFAULT_BASE; },
  set base(v) { localStorage.setItem("laya.base", v); },
  get key() { return localStorage.getItem("laya.key") || ""; },
  set key(v) { localStorage.setItem("laya.key", v); },
};

/* ---------------- presets: one click per capability ---------------- */
const PRESETS = {
  "Email triage": {
    state: { from: "user@acme.com", subject: "Duplicate charge on invoice #4411",
      body: "Hi, we were billed twice for March. Please refund the duplicate today or we will cancel our plan." },
    questions: {
      category: { type: "choice", instructions: "Which team should handle the email in `body`?",
        criteria: { billing: "invoices, payments, refunds", technical: "bugs, outages, integrations",
          sales: "pricing, demos, new purchases", other: "none of the above" } },
      is_phishing: { type: "noul", instructions: "Is this email a phishing or scam attempt?",
        criteria: { true: "phishing, scam, or fraud", false: "a legitimate email" } },
      urgency: { type: "score", instructions: "How urgent is the issue described in `body`?",
        criteria: ["no time pressure", "needs attention soon", "blocking issue or hard deadline"] },
      churn_risk: { type: "noul", instructions: "Does the sender threaten to cancel or leave?" },
    },
  },
  "Moderation": {
    state: "You are an idiot and I will find out where you live. This is the third time, I swear.",
    questions: {
      is_toxic: { type: "noul", instructions: "Does this message contain abuse or harassment?" },
      violation: { type: "choice", instructions: "What kind of violation is this?",
        criteria: { harassment: "insults, demeaning language", threat: "harm to person or property",
          hate: "protected-group slur", none: "no violation" } },
      severity: { type: "score", instructions: "How severe is the violation?",
        criteria: ["mild / borderline", "clear violation", "severe or threatening"] },
    },
  },
  "Intent router": {
    state: "The export button crashes the settings page in Safari. It works in Chrome, but a few of our customers only use Safari.",
    questions: {
      department: { type: "choice", instructions: "Which team should handle this?",
        criteria: { billing: "charges, invoices", technical: "bugs, outages", sales: "pricing, upgrades" } },
      bug_severity: { type: "score", instructions: "How severe is the reported issue?",
        criteria: ["cosmetic; no impact", "broken, but a workaround exists", "blocking; no workaround"] },
      wants_human: { type: "noul", instructions: "Is the customer asking for a human agent?" },
    },
  },
  "Hindi (routing)": {
    state: { body: "मुझसे दो बार शुल्क लिया गया, कृपया पैसे वापस करें।" },
    questions: {
      department: { type: "choice", instructions: "Which team should handle this request?",
        criteria: { billing: "invoices, payments, refunds", technical: "bugs, outages", other: "everything else" } },
      refund_requested: { type: "noul", instructions: "Does the user explicitly request a refund?" },
    },
  },
  "German (routing)": {
    state: { body: "Der Kunde wurde zweimal belastet. Bitte erstatten Sie die doppelte Abbuchung." },
    questions: {
      department: { type: "choice", instructions: "Which team should handle this request?",
        criteria: { billing: "invoices, payments, refunds", technical: "bugs, outages", other: "everything else" } },
    },
  },
};

/* ---------------- question builder cards ---------------- */
let qseq = 0;
function cardHTML(id, type, q) {
  q = q || {};
  const ins = typeof q.instructions === "string" ? q.instructions : q.instructions ? JSON.stringify(q.instructions) : "";
  let crit = "";
  if (type === "choice") {
    const c = q.criteria && !Array.isArray(q.criteria) ? q.criteria : {};
    crit = `<label>options, one per line — <code>key: description</code> (description optional)\n<textarea data-f="criteria" rows="4">${Object.entries(c).map(([k, v]) => v ? `${k}: ${v}` : k).join("\n")}</textarea></label>`;
  } else if (type === "score") {
    const c = Array.isArray(q.criteria) ? q.criteria : [];
    crit = `<label>levels in order, one per line (2–10)\n<textarea data-f="criteria" rows="4">${c.join("\n")}</textarea></label>`;
  } else {
    const c = q.criteria || {};
    const t = typeof c.true === "string" ? c.true : "";
    const f = typeof c.false === "string" ? c.false : "";
    crit = `<div class="row"><label>yes means (optional)<input data-f="ctrue" value="${t.replace(/"/g, "&quot;")}" /></label>
      <label>no means (optional)<input data-f="cfalse" value="${f.replace(/"/g, "&quot;")}" /></label></div>`;
  }
  return `<div class="q" data-id="${id}" data-type="${type}">
    <span class="q-type">${type}</span>
    <div class="q-top">
      <input data-f="qid" value="${id}" title="question id (answers come back under this key)" />
      <button type="button" class="danger" data-act="del">remove</button>
    </div>
    <label>instructions<textarea data-f="instructions" rows="2">${ins.replace(/</g, "&lt;")}</textarea></label>
    <div style="margin-top:10px">${crit}</div>
  </div>`;
}
function addCard(type, q) {
  const id = (q && q._id) || `${type}_${++qseq}`;
  $("#questions").insertAdjacentHTML("beforeend", cardHTML(id, type, q));
}
function readCards() {
  const out = {};
  for (const el of $$("#questions .q")) {
    const type = el.dataset.type;
    const qid = el.querySelector('[data-f="qid"]').value.trim() || `q${++qseq}`;
    const ins = el.querySelector('[data-f="instructions"]').value;
    let instructions = ins;
    try { const p = JSON.parse(ins); if (p && typeof p === "object") instructions = p; } catch { /* plain string */ }
    const q = { type, instructions };
    if (type === "choice") {
      const criteria = {};
      for (const line of el.querySelector('[data-f="criteria"]').value.split("\n")) {
        const t = line.trim(); if (!t) continue;
        const i = t.indexOf(":");
        if (i < 0) criteria[t] = null; else criteria[t.slice(0, i).trim()] = t.slice(i + 1).trim() || null;
      }
      q.criteria = criteria;
    } else if (type === "score") {
      q.criteria = el.querySelector('[data-f="criteria"]').value.split("\n").map((s) => s.trim()).filter(Boolean);
    } else {
      const t = el.querySelector('[data-f="ctrue"]').value.trim();
      const f = el.querySelector('[data-f="cfalse"]').value.trim();
      if (t || f) q.criteria = { ...(t && { true: t }), ...(f && { false: f }) };
    }
    out[qid] = q;
  }
  return out;
}

/* ---------------- run ---------------- */
function parseState() {
  const t = $("#state").value;
  try { const p = JSON.parse(t); if (p && typeof p === "object") return p; } catch { /* plain text */ }
  return t;
}
async function run() {
  const err = $("#error"), lat = $("#latency"), btn = $("#run");
  err.textContent = ""; $("#answers").innerHTML = ""; $("#routing-card").classList.add("hidden");
  const base = $("#base").value.trim().replace(/\/+$/, "");
  const key = $("#key").value;
  store.base = base; store.key = key;
  const body = { state: parseState(), questions: readCards() };
  const model = $("#model").value;
  if (model) body.model = model;
  if (!Object.keys(body.questions).length) { err.textContent = "Add at least one question."; return; }
  $("#raw-req").textContent = JSON.stringify(body, null, 2);
  btn.disabled = true;
  const t0 = performance.now();
  try {
    const r = await fetch(`${base}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(key && { Authorization: `Bearer ${key}` }) },
      body: JSON.stringify(body),
    });
    const ms = Math.round(performance.now() - t0);
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : `HTTP ${r.status}`);
    lat.textContent = `${ms} ms end-to-end · ${data.usage?.input_tokens ?? "?"} input tokens`;
    $("#raw-res").textContent = JSON.stringify(data, null, 2);
    renderRouting(data.routing);
    renderAnswers(data.answers);
  } catch (e) {
    err.textContent = String(e.message || e);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------- render ---------------- */
function pct(x) { return `${(x * 100).toFixed(1)}%`; }
function bars(entries) {
  return entries.map(([k, v]) => `<div class="bar-row"><span class="lbl" title="${k}">${k}</span>
    <span class="bar"><i style="width:${(v * 100).toFixed(1)}%"></i></span>
    <span class="val">${v.toFixed(3)}</span></div>`).join("");
}
function renderRouting(r) {
  if (!r) return;
  $("#routing-card").classList.remove("hidden");
  $("#routing").innerHTML = `<dl class="kv"><dt>checkpoint</dt><dd><b>${r.model}</b> (${r.repo})</dd>
    <dt>why</dt><dd>${r.reason}</dd></dl>`;
}
function renderAnswers(answers) {
  $("#answers").innerHTML = Object.entries(answers).map(([qid, a]) => {
    if (a.type === "choice") {
      const probs = Object.entries(a.probabilities).sort((x, y) => y[1] - x[1]);
      return `<div class="card"><div class="ans-head"><h2>${qid}</h2>
        <span class="conf">confidence <b>${a.confidence.toFixed(3)}</b></span></div>
        <div class="winner">${a.choice}</div>${bars(probs)}</div>`;
    }
    if (a.type === "score") {
      const probs = Object.entries(a.probabilities).sort((x, y) => +x[0] - +y[0]);
      const leg = Object.entries(a.legend).map(([k, v]) =>
        `<div class="bar-row"><span class="lbl">${k}</span><span>${typeof v === "string" ? v : JSON.stringify(v)}</span><span></span></div>`).join("");
      return `<div class="card"><div class="ans-head"><h2>${qid}</h2>
        <span class="conf">confidence <b>${a.confidence.toFixed(3)}</b></span></div>
        <div class="big">${a.score.toFixed(2)}</div>${bars(probs)}${leg}</div>`;
    }
    const v = a.noul, cls = v > 0.7 ? "yes" : v < 0.3 ? "no" : "mid";
    const lbl = v > 0.7 ? "yes" : v < 0.3 ? "no" : "unsure";
    return `<div class="card"><div class="ans-head"><h2>${qid}</h2>
      <span class="pill ${cls}">${lbl} · ${pct(v)}</span></div>
      <div class="meter"><i style="width:${(v * 100).toFixed(1)}%"></i></div>
      <span class="muted">P(yes) = ${v.toFixed(4)}</span></div>`;
  }).join("");
}

/* ---------------- wire up ---------------- */
function loadPreset(name) {
  const p = PRESETS[name];
  $("#state").value = typeof p.state === "string" ? p.state : JSON.stringify(p.state, null, 2);
  $("#questions").innerHTML = "";
  for (const [qid, q] of Object.entries(p.questions)) addCard(q.type, { ...q, _id: qid });
}
$("#presets").innerHTML = Object.keys(PRESETS).map((n) => `<button type="button" data-p="${n}">${n}</button>`).join("");
$("#presets").addEventListener("click", (e) => { const b = e.target.closest("[data-p]"); if (b) loadPreset(b.dataset.p); });
$("#questions").addEventListener("click", (e) => {
  if (e.target.dataset.act === "del") e.target.closest(".q").remove();
});
$("#add-choice").onclick = () => addCard("choice");
$("#add-score").onclick = () => addCard("score");
$("#add-noul").onclick = () => addCard("noul");
$("#run").onclick = run;
$("#base").value = store.base;
$("#key").value = store.key;
$("#docs-link").href = store.base + "/docs";
$("#base").addEventListener("change", (e) => {
  store.base = e.target.value.trim(); $("#docs-link").href = store.base + "/docs";
});
$("#key").addEventListener("change", (e) => { store.key = e.target.value; });
loadPreset("Email triage");
