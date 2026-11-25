/* ForgeAI frontend — vanilla JS, hash routing. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const TYPE_LABEL = { answerable: "Answerable", multi_doc: "Multi-doc", ambiguous: "Ambiguous", out_of_scope: "Out of scope" };
const CAT_LABEL = { retrieval_miss: "Retrieval miss", prompt_defect: "Prompt defect", hallucination: "Hallucination", wrong_refusal: "Wrong refusal" };
const TOOL_LABEL = { csv_lookup: "CSV lookup", calculator: "Calculator", current_date: "Current date", weather: "Weather" };
const STAGES = ["Documents", "Intake", "Groundability", "Agent", "Evals", "Judge + optimize", "Report"];

const state = { runs: [], cache: {}, chats: {}, poll: null };

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

/* ------------------------------------------------------------------ rail */
async function loadRuns() {
  try { state.runs = await api("/api/runs"); } catch { state.runs = []; }
  renderRail();
}
function renderRail() {
  const cur = location.hash.split("/")[2];
  $("#run-list").innerHTML = state.runs.map((r) => {
    const score = r.running ? `<span class="dot live"></span>` : r.score != null ? pct(r.score) : "";
    const sub = r.running ? "building…" : `${r.passed ?? "?"}/${r.total ?? "?"} evals · ${r.tools.length ? r.tools.map((t) => TOOL_LABEL[t] || t).join(", ") : "no tools"}`;
    return `<a class="run-item ${r.name === cur ? "active" : ""}" href="#/run/${r.name}">
      <span class="nm">${esc(pretty(r.name))}</span><span class="sc">${score}</span><span class="sub">${esc(sub)}</span></a>`;
  }).join("") || `<div class="muted" style="padding:8px">No agents yet.</div>`;
}
const ACRONYMS = new Set(["hr", "it", "ai", "api", "csv", "qa", "faq", "ui", "id"]);
const pretty = (n) => n.replace(/^selftest_/, "").split(/[_\s]+/).filter(Boolean)
  .map((w, i) => ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : i === 0 ? w[0].toUpperCase() + w.slice(1) : w).join(" ");

async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#health").innerHTML = h.models_ready
      ? `<span class="dot ok"></span>Ollama ready<br><span class="mono">${esc(h.gen_model)} · judge ${esc(h.judge_model)}</span>`
      : `<span class="dot bad"></span>${h.ollama ? "Models missing" : "Ollama offline"}<br><span class="mono">ollama serve</span>`;
  } catch { $("#health").innerHTML = `<span class="dot bad"></span>Server offline`; }
}

/* ---------------------------------------------------------------- router */
async function route() {
  clearInterval(state.poll);
  const [, page, name, tab = "overview"] = location.hash.split("/");
  renderRail();
  const main = $("#main");
  if (page === "new") return renderNew(main);
  if (page === "build") return renderBuild(main, name);
  if (page === "run" && name) {
    main.innerHTML = `<div class="skeleton"></div>`;
    try {
      const run = await getRun(name);
      return renderRun(main, run, tab);
    } catch (e) { main.innerHTML = `<div class="notice">Could not load run: ${esc(e.message)}</div>`; return; }
  }
  renderHome(main);
}
async function getRun(name, fresh) {
  if (fresh || !state.cache[name]) state.cache[name] = await api(`/api/runs/${name}`);
  return state.cache[name];
}

/* ------------------------------------------------------------------ home */
function constellation(w = 1200, h = 520, n = 46, seed = 7) {
  let x = seed; const rnd = () => ((x = (x * 9301 + 49297) % 233280) / 233280);
  const pts = Array.from({ length: n }, () => [rnd() * w, rnd() * h]);
  const lines = [];
  pts.forEach((p, i) => pts.slice(i + 1).forEach((q) => { if (Math.hypot(p[0] - q[0], p[1] - q[1]) < 170) lines.push([p, q]); }));
  return `<svg class="net" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
    ${lines.map(([p, q]) => `<line x1="${p[0]}" y1="${p[1]}" x2="${q[0]}" y2="${q[1]}"/>`).join("")}
    ${pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="2.2"/>`).join("")}</svg>`;
}
const ICONS = {
  ask: '<path d="M4 5h12v8H9l-4 3v-3H4z"/>', shield: '<path d="M10 2l6 3v5c0 4-3 6.5-6 8-3-1.5-6-4-6-8V5z"/><path d="M7.5 10l2 2 3.5-4"/>',
  build: '<path d="M3 17h14M5 17V8l5-4 5 4v9M8 17v-5h4v5"/>', exam: '<path d="M5 3h10v14H5z"/><path d="M8 7h4M8 10h4M8 13h2"/>',
  judge: '<path d="M10 3v14M4 7h12M4 7l-2 5h4zM16 7l-2 5h4z"/>', report: '<path d="M4 16V9M9 16V4M14 16v-6"/>',
};
const icon = (k) => `<svg viewBox="0 0 20 20">${ICONS[k]}</svg>`;

function renderHome(main) {
  const done = state.runs.filter((r) => r.complete);
  const avg = done.length ? done.reduce((a, r) => a + r.score, 0) / done.length : null;
  const cases = done.reduce((a, r) => a + (r.total || 0), 0);
  const gaps = done.length;
  main.innerHTML = `
    <section class="hero">${constellation()}
      <svg class="mark" viewBox="0 0 32 32"><defs><linearGradient id="hg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2558f0"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs>
        <rect width="32" height="32" rx="9" fill="url(#hg)" stroke="none"/><path d="M10 23V9h12M10 16h9" stroke="#fff" stroke-width="3"/></svg>
      <h1>Brief. Build. Prove.</h1>
      <p class="lead">Turn one paragraph and a folder of documents into a working assistant — tested on an exam it writes itself, repaired where it fails, and shipped with an honest score.</p>
      <div class="cta"><a class="btn" href="#/new">Build an agent</a>${done[0] ? `<a class="btn ghost" href="#/run/${done[0].name}">See a finished run</a>` : ""}</div>
    </section>
    <section class="band">
      <div><div class="v">${done.length}</div><div class="k">Agents built and tested</div></div>
      <div><div class="v">${cases}</div><div class="k">Self-generated exam questions</div></div>
      <div><div class="v">${pct(avg)}</div><div class="k">Average final score</div></div>
      <div><div class="v">0</div><div class="k">Paid APIs — runs on a laptop</div></div>
    </section>
    <div class="section" style="margin-top:44px">
      <div class="eyebrow">Built so far</div><h1 style="font-size:30px;margin-bottom:18px">Agents and their report cards</h1>
      <div class="grid g3">${done.map((r) => `
        <a class="card run-card" href="#/run/${r.name}">
          <div class="muted" style="font-size:12.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em">${esc(r.tools.length ? r.tools.map((t) => TOOL_LABEL[t] || t).join(" · ") : "Documents only")}</div>
          <h2 style="margin:6px 0 14px;font-size:22px">${esc(pretty(r.name))}</h2>
          <div style="display:flex;align-items:baseline;gap:12px"><span class="big">${pct(r.score)}</span><span class="muted">held-out ${pct(r.holdout)}</span></div>
          <p class="muted" style="font-size:14px;margin:12px 0 0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden">${esc(r.brief)}</p>
        </a>`).join("")}</div>
    </div>
    <div class="section" style="margin-top:48px">
      <div class="eyebrow">How it works</div><h1 style="font-size:30px;margin-bottom:18px">The loop a good engineer runs by hand — automated</h1>
      <div class="grid g3">
        ${[["ask", "Intake", "Up to five clarifying questions — answered by you, or by ForgeAI with every assumption logged."],
           ["shield", "Groundability guard", "Each requested topic is checked against the documents. Gaps are refused, never improvised."],
           ["build", "Agent definition", "Prompt, retrieval settings, output contract — and only the tools the brief actually justifies."],
           ["exam", "Self-written exam", "20+ questions from the documents, each confirmed by a second model before it counts."],
           ["judge", "Judge and repair", "Failures are classified and patched one at a time. Patches that don’t help are reverted."],
           ["report", "Honest report", "A score you can defend, a held-out score it never trained on, and what still fails."]]
          .map(([i, t, d]) => `<div class="card feature"><div class="ic">${icon(i)}</div><h2>${t}</h2><p>${d}</p></div>`).join("")}
      </div>
    </div>`;
}

/* ------------------------------------------------------------- run page */
function finalOf(run) {
  const its = run.iterations || [];
  const kept = its.filter((i) => i.kept !== false);
  return (kept[kept.length - 1] || its[0] || {}).summary;
}
function renderRun(main, run, tab) {
  const fin = finalOf(run);
  const tabs = [["overview", "Overview"], ["evals", "Exam", run.evals.length], ["optimizer", "Repairs", Math.max(0, run.iterations.length - 1)],
    ["agent", "Agent"], ["chat", "Try it"], ["report", "Report"]];
  const agentName = run.agent?.name || run.requirements?.agent_name || "Agent";
  main.innerHTML = `
    <div class="crumbs">${esc(agentName)} · <span class="mono">${esc(run.name)}</span></div>
    <h1>${esc(pretty(run.name))}</h1>
    <div class="brief">“${esc(run.brief)}”</div>
    <nav class="tabs">${tabs.map(([k, l, c]) => `<a class="tab ${k === tab ? "active" : ""}" href="#/run/${run.name}/${k}">${l}${c != null ? `<span class="ct">${c}</span>` : ""}</a>`).join("")}</nav>
    <div id="tab"></div>`;
  const el = $("#tab");
  if (!fin && tab !== "report") { el.innerHTML = `<div class="notice">This run has no results yet.</div>`; return; }
  ({ overview: tOverview, evals: tEvals, optimizer: tOptimizer, agent: tAgent, chat: tChat, report: tReport }[tab] || tOverview)(el, run, fin);
}

/* overview */
function tOverview(el, run, fin) {
  const base = run.iterations[0].summary;
  const req = run.requirements || {};
  const gaps = req.unsupported_topics || [];
  const u = run.usage || {};
  const cal = run.calibration || {};
  const d = (fin.overall - base.overall) * 100;
  const agent = run.agent || {};
  const stagesOut = [
    `${countDocs(run)} files`,
    `${(req.clarifications || []).length} questions · ${(req.assumptions || []).length} assumptions`,
    gaps.length ? `<b class="down">${gaps.length} gap${gaps.length > 1 ? "s" : ""} flagged</b>` : "all topics supported",
    `${agent.tools?.length ? agent.tools.map((t) => TOOL_LABEL[t] || t).join(", ") : "no tools"}`,
    `${run.evals.length} cases · ${run.eval_stats?.dropped_roundtrip_failed ?? 0} rejected by round-trip`,
    `${run.iterations.length - 1} patches · ${run.iterations.filter((i) => i.kept === false).length} reverted`,
    `${pct(fin.overall)} final`,
  ];
  el.innerHTML = `
    <div class="grid g4">
      ${stat("Final score", `${pct(fin.overall)}<small>${fin.passed}/${fin.total}</small>`, `${Math.round(d) > 0 ? `<span class="up">+${d.toFixed(0)} pts</span>` : Math.round(d) < 0 ? `<span class="down">${d.toFixed(0)} pts</span>` : `<span class="muted">no change</span>`} vs. baseline ${pct(base.overall)}`)}
      ${stat("Held-out score", pct(fin.holdout), "cases the optimizer never saw")}
      ${stat("Judge agreement", pct(cal.agreement), `${cal.n || 0} known-answer checks · ${cal.false_positives ?? 0} false passes`)}
      ${stat("Cost", `${((u.total_tokens || 0) / 1000).toFixed(0)}k<small>tokens</small>`, `${u.calls || 0} local model calls · ${Math.round((u.seconds || 0) / 60)} min`)}
    </div>
    ${Math.abs((fin.dev || 0) - (fin.holdout || 0)) > 0.15 && fin.dev > fin.holdout ? `<div class="notice" style="margin-top:14px">Dev score (${pct(fin.dev)}) is well above held-out (${pct(fin.holdout)}). Some repairs may fit the exam rather than the domain — see Repairs.</div>` : ""}
    <div class="section"><h3>Pipeline</h3>
      <div class="pipe">${STAGES.map((s, i) => `<div class="st done ${i === 2 && gaps.length ? "flag" : ""}"><div class="n">0${i + 1}</div><div class="t">${s}</div><div class="o">${stagesOut[i]}</div></div>`).join("")}</div>
    </div>
    <div class="grid g2 section">
      <div class="card"><div class="section-head"><h2>Can the documents support the brief?</h2></div>
        <div class="rows">${(req.groundability || []).map((v) => `
          <div class="row" style="grid-template-columns:1fr auto">
            <div><div style="font-weight:600">${esc(v.topic)}</div>
            <div class="why">${v.evidence ? `“${esc(v.evidence.slice(0, 140))}${v.evidence.length > 140 ? "…" : ""}” <span class="muted mono">${esc(v.evidence_source)}</span>` : esc(guardReason(v))}</div></div>
            <span class="chip ${v.status}">${v.status === "unsupported" ? "Gap — will refuse" : v.status}</span>
          </div>`).join("")}</div></div>
      <div class="card"><div class="section-head"><h2>Tools</h2><span class="muted" style="font-size:12.5px">attached only with evidence</span></div>
        <div class="rows">${Object.entries(agent.tool_rationale || {}).map(([k, v]) => {
          const on = (agent.tools || []).includes(k);
          return `<div class="row" style="grid-template-columns:1fr auto"><div><div style="font-weight:600">${esc(TOOL_LABEL[k] || k)}</div>
          ${toolWhy(v)}</div>
          <span class="chip ${on ? "attached" : v.startsWith("REJECTED") ? "rejected" : "neutral"}">${on ? "Attached" : v.startsWith("REJECTED") ? "Rejected" : "Not needed"}</span></div>`;
        }).join("")}</div></div>
    </div>
    <div class="grid g2 section">
      <div class="card"><h2>Results by question type</h2><div class="rows" style="margin-top:8px">${Object.entries(fin.by_type).map(([t, v]) => `
        <div class="row" style="grid-template-columns:130px 1fr 60px"><span>${TYPE_LABEL[t] || t}</span>
        <div class="bar"><i style="width:${v.rate * 100}%;background:${v.rate >= 0.8 ? "var(--pass)" : v.rate >= 0.6 ? "var(--warn)" : "var(--fail)"}"></i></div>
        <span class="mono" style="text-align:right">${v.pass}/${v.total}</span></div>`).join("")}</div></div>
      <div class="card"><h2>What still fails</h2>${failMix(run, fin)}</div>
    </div>
    ${(req.assumptions || []).length ? `<div class="section"><h3>Assumptions made without the requester</h3><div class="card"><div class="rows">${req.assumptions.map((a) => {
      const [q, ans] = a.split(" -> ");
      return `<div class="row" style="grid-template-columns:1fr"><div><div style="font-weight:550">${esc(q)}</div><div class="why">${esc(ans || "")}</div></div></div>`;
    }).join("")}</div></div></div>` : ""}`;
}
function guardReason(v) {
  const r = (v.reason || "").replace(/\s*[\[(]?(evidence quote NOT found in docs - ignored|the checker's supporting quote[^)]*)[\])]?/i, "").trim();
  const miss = r.match(/documents never mention: ([^.]+)\./);
  if (miss) return `The documents never mention “${miss[1]}”.`;
  if (v.status === "supported") return `Relevant passages found (relevance ${v.relevance.toFixed(2)}).`;
  if (v.status === "partial") return `Related text exists but the checker couldn’t point to a passage that covers it (relevance ${v.relevance.toFixed(2)}).`;
  return r.length > 12 ? r : "No passage in the documents addresses this.";
}
function toolWhy(v) {
  const ev = (v.match(/mention: ([^;)]+?)(?: \+ CSV present in documents)?(?:\)|;|$)/) || [])[1];
  const csv = /CSV present/.test(v);
  const model = (v.match(/model: (.*?);/) || [])[1];
  if (v.startsWith("REJECTED")) return `<div class="why">The model asked for it, but nothing in the brief needs it.</div>`;
  if (v.startsWith("not attached - no evidence")) return `<div class="why">Nothing in the brief calls for it.</div>`;
  if (v.startsWith("not attached")) return `<div class="why">Only weak evidence${ev ? ` (“${esc(ev)}”)` : ""} and the model didn’t ask for it.</div>`;
  return `<div class="why">${model && model !== "n/a" ? esc(model.replace(/\.$/, "")) + ". " : ""}${ev ? `Brief mentions <i>${esc(ev)}</i>` : ""}${csv ? " and a CSV is in the documents" : ""}.</div>`;
}
function countDocs(run) {
  const s = new Set();
  (run.evals || []).forEach((e) => (e.expected_sources || []).forEach((x) => s.add(x)));
  return s.size || "—";
}
function stat(k, v, d) { return `<div class="card stat"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`; }
function failMix(run, fin) {
  const fails = run.transcripts.filter((t) => !t.verdict.passed);
  if (!fails.length) return `<p class="muted">Nothing failed in the final run. On a self-generated exam that deserves suspicion — read the Report.</p>`;
  return `<div class="rows" style="margin-top:8px">${fails.map((t) => `
    <a class="row" style="grid-template-columns:auto 1fr;text-decoration:none;cursor:pointer" href="#/run/${run.name}/evals" data-case="${t.case}">
      <span class="chip fail">${CAT_LABEL[t.verdict.category] || t.verdict.category}</span>
      <div><div style="font-weight:550">${esc(t.question)}</div><div class="why">${esc(t.verdict.reason)}</div></div></a>`).join("")}</div>`;
}

/* evals */
function tEvals(el, run) {
  const tmap = Object.fromEntries(run.transcripts.map((t) => [t.case, t]));
  const f = { type: "all", result: "all", split: "all" };
  const seg = (key, opts) => `<div class="seg" data-k="${key}">${opts.map(([v, l]) => `<button data-v="${v}" class="${f[key] === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
  const draw = () => {
    const rows = run.evals.filter((c) => {
      const v = tmap[c.id]?.verdict;
      return (f.type === "all" || c.type === f.type) && (f.split === "all" || c.split === f.split) &&
        (f.result === "all" || (f.result === "pass") === !!v?.passed);
    });
    el.innerHTML = `
      <p class="muted" style="margin-top:-8px;max-width:760px">Every question below was written by the factory from the documents, then answered independently by a second model;
      cases whose expected facts it couldn’t reproduce were thrown out. Held-out cases are never shown to the repair loop.</p>
      <div class="filters">${seg("result", [["all", "All"], ["pass", "Passed"], ["fail", "Failed"]])}
      ${seg("type", [["all", "Every type"], ...Object.entries(TYPE_LABEL)])}${seg("split", [["all", "Dev + held-out"], ["dev", "Dev"], ["holdout", "Held-out"]])}
      <span class="muted" style="margin-left:auto">${rows.length} of ${run.evals.length}</span></div>
      <div class="card" style="padding:4px 8px"><table><thead><tr><th style="width:52px">ID</th><th>Question</th><th style="width:120px">Type</th><th style="width:150px">Result</th></tr></thead>
      <tbody>${rows.map((c) => {
        const v = tmap[c.id]?.verdict;
        return `<tr class="clickable" data-id="${c.id}"><td class="mono muted">${c.id}</td><td>${esc(c.question)}${c.split === "holdout" ? ` <span class="chip outline">held-out</span>` : ""}</td>
        <td>${TYPE_LABEL[c.type]}</td><td>${v?.passed ? `<span class="chip pass">Pass</span>` : `<span class="chip fail">${CAT_LABEL[v?.category] || "Fail"}</span>`}</td></tr>`;
      }).join("")}</tbody></table></div>`;
    el.querySelectorAll(".seg").forEach((s) => s.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return; f[s.dataset.k] = b.dataset.v; draw();
    }));
    el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openCase(run, tr.dataset.id)));
  };
  draw();
  const pending = sessionStorage.getItem("openCase");
  if (pending) { sessionStorage.removeItem("openCase"); openCase(run, pending); }
}
function openCase(run, id) {
  const c = run.evals.find((x) => x.id === id);
  const t = run.transcripts.find((x) => x.case === id) || {};
  const v = t.verdict || {};
  const ch = v.checks || {};
  openDrawer(`
    <div class="crumbs mono">${c.id} · ${TYPE_LABEL[c.type]} · ${c.split}</div>
    <h2 style="font-size:22px;margin:6px 40px 14px 0">${esc(c.question)}</h2>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px">
      ${v.passed ? `<span class="chip pass">Passed</span>` : `<span class="chip fail">${CAT_LABEL[v.category] || "Failed"}</span>`}
      ${c.should_refuse ? `<span class="chip neutral">Must refuse</span>` : ""}
      ${ch.coverage != null ? `<span class="chip neutral">fact coverage ${pct(ch.coverage)}</span>` : ""}
      ${ch.grounded === false ? `<span class="chip fail">ungrounded</span>` : ""}
      ${(ch.unsupported_numbers || []).length ? `<span class="chip fail">invented figures: ${ch.unsupported_numbers.join(", ")}</span>` : ""}
    </div>
    <h3>Agent’s answer</h3>
    <div class="answer">${esc(t.answer || "—")}</div>
    <div class="trace">${(t.citations || []).length ? `cited <b>${t.citations.map(esc).join(", ")}</b>` : "no citations"}${t.gate ? ` · abstained: ${esc(t.gate)}` : ""}</div>
    ${(t.tool_calls || []).map((x) => `<div class="trace">tool <b>${esc(x.tool)}</b>(${esc(JSON.stringify(x.args))}) → ${esc(x.result.slice(0, 160))}</div>`).join("")}
    <div class="section"><h3>Judge’s reasoning</h3><p style="margin:0 0 10px">${esc(v.reason || "")}</p>
    ${(v.facts || []).length ? `<div class="card" style="padding:6px 16px">${v.facts.map((f) => `<div class="fact"><span>${f.stated ? "✅" : "❌"}</span><span>${esc(f.fact)}</span><span class="muted mono" style="font-size:11.5px">${esc(f.method)}</span></div>`).join("")}</div>` : ""}</div>
    ${c.expected_sources.length ? `<div class="section"><h3>Expected source</h3><div class="mono">${c.expected_sources.map(esc).join("<br>")}</div></div>` : ""}
    <div class="section"><h3>What retrieval surfaced</h3><div class="rows">${(t.retrieved || []).map((r) => `
      <div class="row" style="grid-template-columns:1fr 110px 44px"><span class="mono" style="font-size:12.5px">${esc(r.id)}</span>
      <div class="bar"><i style="width:${Math.min(100, r.relevance * 100)}%;background:${c.expected_sources.includes(r.doc) ? "var(--accent)" : "var(--rule-2)"}"></i></div>
      <span class="mono muted" style="text-align:right">${r.relevance.toFixed(2)}</span></div>`).join("")}</div>
      <div class="hint">Orange = from the expected document.</div></div>`);
}

/* optimizer */
function tOptimizer(el, run) {
  const its = run.iterations;
  el.innerHTML = `
    <div class="card"><div class="section-head"><h2>Score across repair iterations</h2>
      <div class="legend"><span><i style="background:var(--ink)"></i>Overall</span><span><i style="background:var(--accent)"></i>Dev</span><span><i style="background:var(--info)"></i>Held-out</span></div></div>
      ${chart(its)}</div>
    <div class="section"><h3>Repair log</h3><div class="card" style="padding:4px 22px">${its.map((it, i) => i === 0 ? `
      <div class="iter"><div class="num">0</div><div><div class="chg">Baseline — the agent as first generated</div>
      <div class="muted">${it.summary.passed}/${it.summary.total} passed · failures: ${fmtFails(it.summary.failures)}</div></div></div>` : `
      <div class="iter"><div class="num">${it.iteration}</div><div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><span class="chip ${it.kept ? "kept" : "reverted"}">${it.kept ? "Kept" : "Reverted"}</span>
        <span class="chip outline">${CAT_LABEL[it.category] || it.category}</span>
        <span class="delta ${it.score_after.overall > it.score_before.overall ? "up" : it.score_after.overall < it.score_before.overall ? "down" : "muted"}">${pct(it.score_before.overall)} → ${pct(it.score_after.overall)}</span>
        <span class="muted mono" style="font-size:12px">held-out ${pct(it.score_before.holdout)} → ${pct(it.score_after.holdout)}</span></div>
        <div class="chg">${esc(it.change)}</div>
        <div class="muted" style="font-size:13px">Why: ${esc(it.why)}. Targeted ${it.targeted_cases.join(", ") || "—"}.
          ${it.fixed_cases.length ? `<span class="up">Fixed ${it.fixed_cases.join(", ")}.</span>` : "Fixed nothing."}
          ${it.newly_broken_cases.length ? `<span class="down">Broke ${it.newly_broken_cases.join(", ")}.</span>` : ""}</div>
      </div></div>`).join("")}</div></div>
    <div class="section"><h3>Final prompt vs. first draft</h3>${promptDiff(run)}</div>`;
}
function fmtFails(f) { const e = Object.entries(f).filter(([, n]) => n); return e.length ? e.map(([k, n]) => `${CAT_LABEL[k]} ×${n}`).join(", ") : "none"; }
function chart(its) {
  const W = 760, H = 220, P = { l: 40, r: 16, t: 14, b: 30 };
  const n = Math.max(its.length - 1, 1);
  const x = (i) => P.l + (i / n) * (W - P.l - P.r);
  const y = (v) => P.t + (1 - v) * (H - P.t - P.b);
  const line = (key, color, dash) => {
    const pts = its.map((it, i) => [x(i), y(it.summary[key] ?? 0)]);
    return `<polyline points="${pts.map((p) => p.join(",")).join(" ")}" stroke="${color}" stroke-width="2.2" ${dash ? 'stroke-dasharray="5 4"' : ""}/>` +
      pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="${its[i].kept === false ? 4 : 4.5}" fill="${its[i].kept === false ? "var(--panel)" : color}" stroke="${color}" stroke-width="2"><title>${key} ${pct(its[i].summary[key])}</title></circle>`).join("");
  };
  const grid = [0.25, 0.5, 0.75, 1].map((g) => `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${y(g)}" y2="${y(g)}"/><text x="${P.l - 8}" y="${y(g) + 4}" text-anchor="end">${g * 100}%</text>`).join("");
  const xs = its.map((it, i) => `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${i === 0 ? "baseline" : `#${i}${it.kept === false ? " ↺" : ""}`}</text>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="score per iteration">${grid}${xs}
    ${line("holdout", "var(--info)", true)}${line("dev", "var(--accent)")}${line("overall", "var(--ink)")}</svg>
    <div class="hint">Hollow points are patches that were reverted — the next iteration starts from the last kept version.</div>`;
}
function promptDiff(run) {
  const a = run.agent_v0?.prompt_sections || {}, b = run.agent?.prompt_sections || {};
  const ra = run.agent_v0?.retrieval || {}, rb = run.agent?.retrieval || {};
  const out = [];
  for (const k of Object.keys(b)) {
    const al = (a[k] || "").split("\n"), bl = (b[k] || "").split("\n");
    const added = bl.filter((l) => !al.includes(l)), removed = al.filter((l) => !bl.includes(l));
    if (added.length || removed.length) out.push(`<div class="diff"><b>${k.toUpperCase()}</b>\n${removed.map((l) => `<span class="del">- ${esc(l)}</span>`).join("")}${added.map((l) => `<span class="add">+ ${esc(l)}</span>`).join("")}</div>`);
  }
  const rch = Object.keys(rb).filter((k) => JSON.stringify(ra[k]) !== JSON.stringify(rb[k]));
  if (rch.length) out.push(`<div class="diff"><b>RETRIEVAL</b>\n${rch.map((k) => `<span class="add">${k}: ${esc(ra[k])} → ${esc(rb[k])}</span>`).join("")}</div>`);
  if ((run.agent?.output_contract?.max_words) !== (run.agent_v0?.output_contract?.max_words))
    out.push(`<div class="diff"><b>OUTPUT CONTRACT</b>\n<span class="add">max_words: ${run.agent_v0.output_contract.max_words} → ${run.agent.output_contract.max_words}</span></div>`);
  return out.join("") || `<p class="muted">No repair was kept — the shipped agent is the first draft. That is a valid outcome: every attempted patch made things worse or no better.</p>`;
}

/* agent */
function tAgent(el, run) {
  const a = run.agent;
  const prompt = Object.entries(a.prompt_sections).map(([k, v]) => `<span class="sec">## ${k.toUpperCase()}</span>\n${esc(v)}`).join("\n\n");
  const blob = URL.createObjectURL(new Blob([JSON.stringify(a, null, 2)], { type: "application/json" }));
  el.innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,2fr) minmax(0,1fr)">
    <div><div class="section-head"><h2>System prompt</h2><a class="btn ghost" download="${run.name}_agent.json" href="${blob}">Download agent.json</a></div>
      <div class="prompt">${prompt}</div></div>
    <div class="grid" style="align-content:start">
      <div class="card"><h3>Retrieval</h3><dl class="kv">${Object.entries(a.retrieval).map(([k, v]) => `<dt>${k.replace(/_/g, " ")}</dt><dd class="mono">${esc(v)}</dd>`).join("")}</dl></div>
      <div class="card"><h3>Output contract</h3><dl class="kv">${Object.entries(a.output_contract).map(([k, v]) => `<dt>${k.replace(/_/g, " ")}</dt><dd>${esc(v)}</dd>`).join("")}</dl></div>
      <div class="card"><h3>Tools</h3>${a.tools.length ? a.tools.map((t) => `<span class="chip attached" style="margin:0 6px 6px 0">${TOOL_LABEL[t] || t}</span>`).join("") : `<span class="muted">None — the documents answer everything in scope.</span>`}</div>
      <div class="card"><h3>Version history</h3>${a.changelog.map((c) => `<div style="font-size:13px;padding:4px 0" class="${c.startsWith("(reverted") ? "muted" : ""}">${esc(c)}</div>`).join("")}</div>
    </div></div>`;
}

/* chat */
function tChat(el, run) {
  const hist = (state.chats[run.name] ||= []);
  const answerable = run.evals.filter((e) => !e.should_refuse).slice(0, 3).map((e) => e.question);
  const oos = run.evals.find((e) => e.should_refuse && !/cake/i.test(e.question))?.question;
  const suggestions = [...answerable, ...(oos ? [oos] : [])];
  el.innerHTML = `<div class="chat"><div class="msgs" id="msgs"></div>
    <form class="composer" id="composer"><textarea id="q" rows="1" placeholder="Ask ${esc(run.agent?.name || "the agent")} something…"></textarea>
    <button class="btn" id="send" type="submit">Ask <svg viewBox="0 0 16 16" width="14" height="14"><path d="M3 8h10M9 4l4 4-4 4"/></svg></button></form></div>`;
  const msgs = $("#msgs"), q = $("#q");
  const paint = () => {
    msgs.innerHTML = hist.length ? hist.map(renderMsg).join("") : `<div class="empty-chat"><h2>Talk to the agent this run produced</h2>
      <p>It answers only from the documents and cites what it used. Try a question from its exam — or one it should refuse.</p>
      <div class="suggest" style="justify-content:center">${suggestions.map((s) => `<button type="button" data-q="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
    msgs.scrollTop = msgs.scrollHeight;
    msgs.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => send(b.dataset.q)));
    msgs.querySelectorAll(".src").forEach((b) => b.addEventListener("click", () => {
      const box = b.closest(".msg").querySelector(".src-body");
      box.hidden = box.dataset.id === b.dataset.id && !box.hidden;
      box.dataset.id = b.dataset.id; box.textContent = b.dataset.text || "(no excerpt text)";
    }));
  };
  const send = async (text) => {
    text = (text || "").trim(); if (!text) return;
    hist.push({ role: "user", text }, { role: "bot", pending: true }); paint(); q.value = ""; $("#send").disabled = true;
    try {
      const r = await api(`/api/runs/${run.name}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) });
      hist[hist.length - 1] = { role: "bot", r };
    } catch (e) { hist[hist.length - 1] = { role: "bot", error: e.message }; }
    $("#send").disabled = false; paint(); q.focus();
  };
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(q.value); });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(q.value); } });
  q.addEventListener("input", () => { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 140) + "px"; });
  paint();
}
function renderMsg(m) {
  if (m.role === "user") return `<div class="msg user">${esc(m.text)}</div>`;
  if (m.pending) return `<div class="msg bot"><div class="typing"><span></span><span></span><span></span></div></div>`;
  if (m.error) return `<div class="msg bot"><div class="notice">${esc(m.error)}</div></div>`;
  const r = m.r;
  return `<div class="msg bot"><div class="bubble">${esc(r.answer)}</div>
    <div class="meta">${r.refused ? `<span class="chip neutral">Declined</span>` : ""}
      ${r.sources.map((s) => `<button class="src" data-id="${esc(s.id)}" data-text="${esc(s.text)}">${esc(s.id)}</button>`).join("")}
      <span class="muted mono" style="font-size:11.5px;margin-left:auto">${(r.latency_ms / 1000).toFixed(1)}s</span></div>
    <div class="src-body" hidden></div>
    ${r.tool_calls.map((t) => `<div class="trace">↳ <b>${esc(TOOL_LABEL[t.tool] || t.tool)}</b> ${esc(JSON.stringify(t.args))} → ${esc(t.result.slice(0, 140))}</div>`).join("")}
    ${r.gate ? `<div class="trace">↳ abstained before generation: ${esc(r.gate)}</div>` : ""}</div>`;
}

/* report */
function tReport(el, run) {
  el.innerHTML = `<div class="md">${window.marked ? marked.parse(run.report_md || "No report.") : `<pre>${esc(run.report_md)}</pre>`}</div>`;
}

/* ----------------------------------------------------------- new agent */
function renderNew(main) {
  let files = [];
  main.innerHTML = `<div style="max-width:780px">
    <div class="crumbs">New agent</div><h1>What should the agent do?</h1>
    <p class="muted" style="margin:0 0 26px">Write it the way you would brief a colleague. ForgeAI will ask itself the clarifying questions and log every assumption it makes.</p>
    <form id="nf" class="grid" style="gap:22px">
      <div><label class="f" for="brief">Brief</label>
        <textarea class="big" id="brief" required placeholder="I need an assistant that answers HR policy questions for our shore staff — leave, travel expenses, hybrid working. Answers must be short and cite the policy."></textarea></div>
      <div><label class="f">Documents</label>
        <label class="drop" id="drop"><input type="file" id="fi" multiple accept=".pdf,.txt,.md,.csv,.docx,.json" hidden>
          <div style="font-weight:600">Drop files here or click to choose</div><div class="hint">PDF, DOCX, TXT, Markdown, CSV, JSON</div>
          <div class="files" id="files"></div></label></div>
      <div class="grid g2"><div><label class="f" for="nm">Run name</label><input type="text" id="nm" value="my_agent" pattern="[A-Za-z0-9_\\- ]+">
        <div class="hint">Becomes the folder under runs/.</div></div>
        <div><label class="f" for="it">Repair iterations: <span id="itv">3</span></label><input type="range" id="it" min="0" max="3" value="3" style="width:100%">
        <div class="hint">Each iteration re-runs the full exam (~2–6 min on a laptop).</div></div></div>
      <div style="display:flex;gap:12px;align-items:center"><button class="btn" id="go" type="submit" disabled>Build and test the agent</button><span class="muted" id="why">Add a brief and at least one document.</span></div>
    </form></div>`;
  const fi = $("#fi"), drop = $("#drop"), go = $("#go");
  const refresh = () => {
    $("#files").innerHTML = files.map((f) => `<span class="chip neutral">${esc(f.name)}</span>`).join("");
    const ok = $("#brief").value.trim().length > 20 && files.length;
    go.disabled = !ok; $("#why").textContent = ok ? `${files.length} file${files.length > 1 ? "s" : ""} ready` : "Add a brief and at least one document.";
  };
  fi.addEventListener("change", () => { files = [...files, ...fi.files]; refresh(); });
  ["dragover", "dragenter"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, () => drop.classList.remove("over")));
  drop.addEventListener("drop", (e) => { e.preventDefault(); files = [...files, ...e.dataTransfer.files]; refresh(); });
  $("#brief").addEventListener("input", refresh);
  $("#it").addEventListener("input", (e) => ($("#itv").textContent = e.target.value));
  $("#nf").addEventListener("submit", async (e) => {
    e.preventDefault(); go.disabled = true; go.textContent = "Starting…";
    const fd = new FormData();
    fd.append("brief", $("#brief").value.trim()); fd.append("name", $("#nm").value); fd.append("max_iters", $("#it").value);
    files.forEach((f) => fd.append("files", f));
    try { const r = await api("/api/forge", { method: "POST", body: fd }); location.hash = `#/build/${r.name}`; }
    catch (err) { go.disabled = false; go.textContent = "Build and test the agent"; $("#why").textContent = err.message; }
  });
}

function renderBuild(main, name) {
  main.innerHTML = `<div class="crumbs">Building · <span class="mono">${esc(name)}</span></div><h1>${esc(pretty(name))}</h1>
    <p class="muted" id="bstat">Starting…</p>
    <div class="grid" style="grid-template-columns:260px minmax(0,1fr);align-items:start">
      <div class="card steps" id="steps"></div><div class="console" id="console"></div></div>`;
  const tick = async () => {
    let j; try { j = await api(`/api/jobs/${name}`); } catch { return; }
    $("#steps").innerHTML = STAGES.map((s, i) => {
      const n = i + 1, cls = j.done || n < j.stage ? "done" : n === j.stage ? "on" : "";
      return `<div class="step ${cls}"><span class="b">${cls === "done" ? "✓" : n}</span>${s}</div>`;
    }).join("");
    const con = $("#console"), atBottom = con.scrollHeight - con.scrollTop - con.clientHeight < 40;
    con.innerHTML = j.log.map((l) => {
      const c = /^\[\d\/7\]/.test(l) ? "hl" : /PASS|KEPT|OK  /.test(l) ? "ok" : /FAIL|GAP|REVERTED|WARNING/.test(l) ? "bad" : /^\s{4}/.test(l) ? "dim" : "";
      return `<span class="${c}">${esc(l)}</span>`;
    }).join("\n");
    if (atBottom) con.scrollTop = con.scrollHeight;
    const mins = j.elapsed != null ? `${Math.floor(j.elapsed / 60)}m ${j.elapsed % 60}s` : "";
    $("#bstat").textContent = j.done ? "Done." : j.error ? "Failed." : `Running · ${mins} · stage ${j.stage || 1} of 7`;
    if (j.error) { $("#bstat").innerHTML = `<span class="down">Failed:</span> <span class="mono">${esc(j.error.split("\n").slice(-3).join(" "))}</span>`; clearInterval(state.poll); }
    if (j.done) { clearInterval(state.poll); delete state.cache[name]; await loadRuns(); setTimeout(() => (location.hash = `#/run/${name}`), 900); }
  };
  tick(); state.poll = setInterval(tick, 1500); loadRuns();
}

/* -------------------------------------------------------------- drawer */
function openDrawer(html) { $("#drawer-body").innerHTML = html; $("#drawer").hidden = false; }
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) $("#drawer").hidden = true;
  const fc = e.target.closest("[data-case]");
  if (fc) sessionStorage.setItem("openCase", fc.dataset.case);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#drawer").hidden = true; });
window.addEventListener("hashchange", route);
(async () => { await loadRuns(); loadHealth(); route(); setInterval(loadRuns, 15000); })();
