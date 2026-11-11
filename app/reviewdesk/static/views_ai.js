// The AI second screener page: set-up (the two models), runs, the known studies and, after the agreement sample, how the
// AI did. (It was in views_report.js; it has its own file now, so that it can change without touching the reports.)
import { api, h, toast, attempt, num, pct, bar, download, plural, SETS } from "./lib.js";
import { S, go } from "./app.js";
import { readinessBanner, wizardBack } from "./views_wizard.js";

// "4 minutes", "about 2.5 hours": a length of time as people say it.
function duration(sec) {
  if (sec < 90) return plural(Math.max(1, Math.round(sec)), "second");
  if (sec < 90 * 60) return plural(Math.round(sec / 60), "minute");
  const hrs = sec / 3600;
  return hrs < 10 ? `${hrs.toFixed(1).replace(/\.0$/, "")} hours` : plural(Math.round(hrs), "hour");
}

// After a trial run: how long it took, and so how long the rest would take.
function trialTiming(st, cov) {
  if (!st || st.running || !st.finished || !st.started || !/trial/.test(st.label || "") || !st.done) return null;
  const secs = (Date.parse(st.finished) - Date.parse(st.started)) / 1000;
  if (!(secs > 0)) return null;
  const perRecord = (2 * secs) / st.done;           // each record is one call to each of the two models
  const left = Object.values(cov || {}).reduce((a, c) => a + Math.max(0, (c.records || 0) - (c.done || 0)), 0);
  return h("p.small.ai-timing", h("strong", "Speed: "),
    `the trial made ${plural(st.done, "call")} in ${duration(secs)}, ${perRecord < 1 ? "under a second" : `about ${perRecord < 10 ? perRecord.toFixed(1) : Math.round(perRecord)} seconds`} a record. `,
    left ? `At that speed, the ${plural(left, "record")} the AI has not screened yet would take about ${duration(perRecord * left)}.` : "");
}

export async function ai(el) {
  const p = S.app.project;
  const d = await api("/api/ai");
  const page = h("div.page");
  el.append(page);
  readinessBanner(page, "ai");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("div.row", h("h1.grow", "AI second screener"), wizardBack()),
    h("p", "Two AI models from different developers each read every title and abstract against your criteria. The AI never excludes a record. When one reviewer screens alone, any record that reviewer excludes but the AI would keep goes to the second reviewer, so a person always makes the final call.")));
  if (!d.planned) page.append(h("div.callout.warn", h("p", "The screening plan does not use the AI second screener. To use it, turn it on in Project set-up before screening starts, and describe it in your protocol."),
    p.is_lead ? h("button", { onclick: () => go("setup") }, "Open project set-up") : null));
  // coverage (refreshed while a run is going)
  const coverage = h("div");
  // the tables scroll inside their own box on a phone, never the page
  const drawCoverage = (cov) => coverage.replaceChildren(Object.keys(cov).length ? h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Set"), h("th.num", "Records"), h("th.num", "Screened by both models"), h("th.num", "Failed (count as kept)"), h("th", h("span.sr-only", "Progress")))),
    h("tbody", Object.entries(cov).map(([k, c]) => h("tr", h("td", SETS[k]), h("td.num", num(c.records)), h("td.num", num(c.done)), h("td.num", num(c.failed)), h("td", { style: { width: "180px" } }, bar(c.done, c.records, `${SETS[k]}: screened by the AI`)))))))
    : h("p.muted", "No records yet."));
  drawCoverage(d.coverage);
  page.append(h("div.panel", h("h2", "Progress"), coverage, p.is_lead ? null : statusLine(d.status)));
  if (!p.is_lead) {
    page.append(h("p.muted", "The project lead runs the AI. Its answers are shown only to the lead, and only after the agreement sample is finished, so that nobody's screening is influenced by them."));
    return;
  }
  // set-up
  const cfg = d.config;
  const locked = !!cfg.locked;
  const fixed = locked || d.models_fixed;
  const mA = h("input#ai-model-a", { type: "text", value: cfg.passes.A.model, disabled: fixed, list: "models" });
  const mB = h("input#ai-model-b", { type: "text", value: cfg.passes.B.model, disabled: fixed, list: "models" });
  const conc = h("input", { type: "number", min: 1, max: 16, value: cfg.concurrency });
  const models = h("datalist#models");
  // what Check the connection found: the models, listed on the page (and offered in the two boxes). Not read out as a
  // whole: the message after Check the connection says how many there are, and the list is a labelled part of the page
  const found = h("div.ai-models", { role: "region", "aria-label": "Models your AI service offers", hidden: true });
  const showModels = (r) => {
    const local = new Set(r.local || []);
    found.hidden = false;
    const use = (box, m) => { box.value = m; box.dispatchEvent(new Event("input", { bubbles: true })); box.focus(); };
    found.replaceChildren(!r.models.length ? h("p.small", "The service answered, but it lists no models. Ask the service which models your key may use.")
      : [h("p.small", h("strong", `${r.elm ? `Your service offers ${plural(r.models.length, "model")}; ${num(local.size)} ${local.size === 1 ? "is" : "are"} hosted by the University` : `Your service offers ${plural(r.models.length, "model")}`}. `),
        fixed ? "" : "Choose two from different developers: type a name into each box above, or use the buttons."),
      h("ul.ai-model-list", r.models.map((m) => h("li", h("span.mono", m), local.has(m) ? h("span.pill.include.tiny", "University-hosted") : null,
        fixed ? null : h("span.ai-use", h("button.link.small", { "aria-label": `Use ${m} as the first AI model`, onclick: () => use(mA, m) }, "Use as first"),
          h("button.link.small", { "aria-label": `Use ${m} as the second AI model`, onclick: () => use(mB, m) }, "Use as second")))))]);
  };
  page.append(h("div.panel", h("h2", "Set-up"),
    d.key_set ? h("p.small", `Service: ${d.endpoint}. Your key is saved on this computer.`) : h("div.callout.warn", h("p", "Add your AI service's address and API key under My settings to run the AI."), h("button", { onclick: () => go("settings") }, "Open My settings")),
    models,
    h("div.grid2", h("label.f", h("span", "First AI model (works through the criteria in order)"), mA), h("label.f", h("span", "Second AI model (guards against missed studies)"), mB)),
    fixed ? h("p.small.muted", locked ? "The models are fixed because the agreement rule has been applied: the method you report must be the one that was used."
      : "The models are fixed because the agreement sample is finished: the AI is checked against the sample with the set-up it was run with.") : null,
    h("div.grid2", h("label.f", h("span", "Calls at the same time"), conc, h("small", "4 is gentle on the service; a full run of 3,000 records takes a few hours.")), h("div")),
    h("div.row", h("button", { disabled: !d.key_set, onclick: async () => {
      const r = await attempt(() => api("/api/ai/models", {}));
      models.replaceChildren(...r.models.map((m) => h("option", { value: m })));
      showModels(r);
      toast(r.elm ? `${plural(r.models.length, "model")} available; ${num((r.local || []).length)} hosted by the University.` : `${plural(r.models.length, "model")} available.`);
    } }, "Check the connection"),
      h("button", { onclick: async () => {
        await attempt(() => api("/api/ai/config", { passes: fixed ? undefined : { A: { model: mA.value }, B: { model: mB.value } }, concurrency: Math.min(16, Math.max(1, parseInt(conc.value, 10) || 4)) }), "Saved");
        readinessBanner(page, "ai");        // the checks above the page are asked again: the saved set-up may answer them
      } }, "Save set-up")),
    found,
    h("p.small.muted", { style: { marginTop: "10px" } }, d.elm ? "Each call sends the review question, the eligibility criteria and the record's title, abstract, year, journal and publication type. With ELM's University-hosted models (Qwen, Llama, Mistral) the records stay in University data centres; models from outside the University are refused."
      : "Each call sends the review question, the eligibility criteria and the record's title, abstract, year, journal and publication type to your AI service. Check where your provider processes the data before you run it."),
    h("details", h("summary", "The exact prompts"), h("p.small.muted", `Fingerprints: first model's prompt ${d.fingerprints.A}, second model's prompt ${d.fingerprints.B}. Report these with your methods; they change if the criteria or question change.`),
      h("pre.mono", { style: { whiteSpace: "pre-wrap", background: "var(--paper)", padding: "12px", borderRadius: "6px" } }, d.prompts.A),
      h("pre.mono", { style: { whiteSpace: "pre-wrap", background: "var(--paper)", padding: "12px", borderRadius: "6px" } }, d.prompts.B))));
  // run
  const status = h("div");
  const runBtns = [h("button", { onclick: () => run("trial") }, "Try 20 records"), h("button", { onclick: () => run("known") }, "Check the known studies"),
    h("button.primary", { onclick: () => run("all") }, "Screen every record")];
  const stopBtn = h("button.danger", { onclick: async () => { await attempt(() => api("/api/ai/stop", {}), "Stopping: the calls under way finish first"); stopBtn.disabled = true; } }, "Stop");
  const setRunning = (running) => {
    runBtns.forEach((b) => { b.disabled = !d.key_set || running; });
    stopBtn.hidden = !running;
    if (!running) stopBtn.disabled = false;
  };
  let polling = false;
  const poll = async () => {
    if (!document.body.contains(page)) { polling = false; return; }          // left the page
    let x;
    try { x = await api("/api/ai"); } catch (e) { setTimeout(poll, 4000); return; }
    status.replaceChildren(statusLine(x.status), trialTiming(x.status, x.coverage));
    drawCoverage(x.coverage);
    setRunning(!!x.status.running);
    if (x.status.running) { setTimeout(poll, 2000); return; }
    polling = false;
    if (x.status.finished) { toast(x.status.error ? x.status.message : (x.status.message || "Finished"), !!x.status.error); ai(el.replaceChildren() || el); }
  };
  const startPolling = () => { if (!polling) { polling = true; poll(); } };
  // All three run buttons are off while the request to start is out (asking the service for its models can take a
  // while): a click on another one would otherwise start a second run. (ReviewDesk refuses a second run too.)
  const run = async (scope) => {
    runBtns.forEach((b) => { b.disabled = true; });
    try { await attempt(() => api("/api/ai/run", { scope })); } catch (e) { setRunning(false); throw e; }
    setRunning(true); startPolling();
  };
  page.append(h("div.panel", h("h2", "Run it"),
    h("ol", h("li", "Try it on 20 random records, to check the connection and the speed: when it finishes, the time it took is shown below."),
      h("li", "Check the known studies: the AI should keep every one (see below)."),
      h("li", "Screen every record. Run this before the agreement sample is finished; nobody sees the answers until it is.")),
    h("div.row", runBtns, stopBtn),
    status));
  status.replaceChildren(statusLine(d.status), trialTiming(d.status, d.coverage));
  setRunning(!!d.status.running);
  if (d.status.running) startPolling();
  // known studies
  const showAns = (k) => d.visible || (d.is_lead && !k.hidden && (k.A || k.B));
  const anyRecords = Object.values(d.coverage || {}).some((c) => c.records);
  if (d.known.length) page.append(h("div.panel", h("h2", "Known studies"),
    h("p.small.muted", "Eligible studies you already know about. Each should be in the records (or the search needs work) and the AI should keep each one."),
    h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Known study"), h("th", "In the records"), h("th", "First model"), h("th", "Second model"))),
      h("tbody", d.known.map((k) => h("tr", h("td.small", k.known.title || (k.known.doi ? "DOI " + k.known.doi : "") || (k.known.pmid ? "PubMed ID " + k.known.pmid : "")),
        h("td", k.record || (anyRecords ? h("span.pill.exclude", "Not found") : h("span.pill", "Nothing imported yet"))),
        showAns(k) ? [h("td.small", k.A || "not run", k.reasons ? h("div.muted", k.reasons.A) : null), h("td.small", k.B || "not run", k.reasons ? h("div.muted", k.reasons.B) : null)]
          : h("td.small.muted", { colspan: 2 }, k.hidden ? (k.hidden_why === "verifying" ? "Shown when you have finished the verification sample (the AI's answers would show which of its records the AI kept)" : k.hidden_why === "not_started" ? "Shown once screening has started (it might fall in the agreement sample)" : "In the calibration or agreement sample: shown when both reviewers have finished it") : k.record ? "Not run yet" : ""))))))));
  // validation
  if (d.visible && d.validation) {
    const v = d.validation, c = v.combined;
    const row = (lab, x) => h("tr", h("td", lab), h("td.num", pct(x.sensitivity)), h("td.small", x.sens_ci ? `${pct(x.sens_ci[0])} to ${pct(x.sens_ci[1])}` : ""), h("td.num", pct(x.specificity)), h("td.num", `${x.tp}/${x.tp + x.fn}`));
    page.append(h("div.panel", h("h2", "How the AI did on the agreement sample"),
      h("p.muted", `Against ${v.reference}. These figures are reported; they do not decide anything, because the AI never excludes. The only rule is the stopping limit on how many records the AI keeps.`),
      v.coverage_note ? h("div.callout.warn", h("p.small", v.coverage_note)) : null,
      h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", h("span.sr-only", "Screener")), h("th.num", "Sensitivity"), h("th", "95% CI (Wilson)"), h("th.num", "Specificity"), h("th.num", "Kept / sent on"))),
        h("tbody", row("Both models (kept unless both exclude)", c), row(`First model alone (${v.models.A})`, v.A), row(`Second model alone (${v.models.B})`, v.B)))),
      h("div.stat", { style: { marginTop: "14px" } }, h("div", h("b", pct(v.keep_rate)),
          h("span", v.not_sent ? `of the ${num(v.records)} records answered so far kept by the AI (${num(v.not_sent)} not yet sent)` : `of all ${num(v.records)} records kept by the AI`)),
        d.limit !== undefined && v.keep_rate !== null ? h("div", h("b", pct(d.limit, 0)), h("span", v.keep_rate > d.limit ? "stopping limit: the AI keeps more than this, so the step would be dropped" : "stopping limit: the AI keeps less than this")) : null,
        h("div", h("b", num(v.passes_disagree)), h("span", "records where the two models disagreed")),
        v.failed ? h("div", h("b", num(v.failed)), h("span", "records with a failed answer (kept)")) : null),
      v.missed.length ? h("p.small", `Records the reviewers sent on but the AI would not have kept: ${v.missed.join(", ")}.`) : null,
      h("button", { onclick: () => download("ai") }, "Download every AI answer")));
  }
}

function statusLine(st) {
  if (!st || (!st.running && !st.message)) return h("p.small.muted", "No AI run since ReviewDesk was started.");
  const counts = st.total ? `${num(st.done)} of ${plural(st.total, "call")} made${st.errors ? `; ${num(st.errors)} failed (those records count as kept)` : ""}.` : "";
  return h("div", { style: { marginTop: "10px" } },
    h(st.error || st.interrupted ? "div.callout.warn" : "div", h("p.small" + (st.error || st.interrupted ? "" : ".muted"),
      (st.running ? `Running (${st.label})` + (st.message && st.message !== "Running" ? `: ${st.message}` : ".") : (st.message || "")))),
    counts ? h("p.small.muted", counts) : null,
    st.total ? bar(st.done, st.total, "AI calls made") : null);
}
