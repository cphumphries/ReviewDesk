// Rating the certainty of the evidence with GRADE: the list of outcomes (#/certainty) and an outcome's page (#/c/<id>).
import { api, h, toast, attempt, modal, confirmBox, num, plural, download, carryToast, drafts, keepDraft, unsavedNote, listen } from "./lib.js";
import { S, go, render } from "./app.js";
import { readinessBanner } from "./views_wizard.js";

const nameOf = (id) => { const r = S.app.project.settings.reviewers.find((x) => x.id === id); return r ? r.name || id : id ? `${id} (no longer in the project)` : "nobody yet"; };
// a person's first name, for short labels ("Use Sam's"); titles such as Dr or Prof are skipped
const first = (s) => (/\(no longer in the project\)$/.test(String(s || "")) ? String(s)
  : (String(s || "").split(/\s+/).find((w) => w && !/^(dr|prof|professor|mr|mrs|ms|miss|mx|sir|dame)\.?$/i.test(w)) || String(s || "").split(" ")[0]));
const LEVEL_CLASS = { 4: "include", 3: "action", 2: "maybe", 1: "exclude" };
const SEVERITY = { "Low risk": 0, Low: 0, "Low risk except for concerns about uncontrolled confounding": 0.5, "Some concerns": 1, "Moderate risk": 1,
  Unclear: 1, "High risk": 2, "Serious risk": 2, High: 2, "Critical risk": 3, "Very high risk": 3, "Low risk of bias": 0, "At risk of bias": 2 };
const sevClass = (j) => { const s = SEVERITY[j]; return s === undefined ? "" : s <= 0.5 ? "include" : s === 1 ? "maybe" : "exclude"; };
const STATE = { finished: ["include", "Finished"], started: ["maybe", "Started"], "not started": ["", "Not started"], "check again": ["exclude", "Check again"] };
const statePill = (s) => { const [c, t] = STATE[s] || ["", "—"]; return h("span.pill" + (c ? "." + c : ""), t); };
const levelPill = (T, level) => level ? h("span.pill.cert." + LEVEL_CLASS[level], `${T.levels[level]} `, h("span.sym", { "aria-hidden": "true" }, T.symbols[level])) : null;
const minus = (n) => `−${Math.abs(n)}`;

// a rating in words: "Serious (−1)", "Strongly suspected (−1)" for publication bias, "Rated up one level (+1)", the
// start's label, or the text itself
function label(T, f, v) {
  if (v === null || v === undefined || v === "") return "";
  if (f === "start") return (T.start[v] || {}).label || String(v);
  if (T.domains.some((d) => d.id === f)) return (f === "publication" ? T.down_publication : T.down)[String(v)] + (Number(v) ? ` (${minus(v)})` : "");
  if (T.upgrades.some((u) => u.id === f)) return Number(v) ? `${T.up[String(v)]} (+${v})` : T.up["0"];
  return String(v);
}
// the ratings offered for a reason to rate down: three levels for imprecision, and for risk of bias with a ROBINS-I start
const allowedDown = (T, f, s) => [0, -1, -2].concat(T.extreme_always.includes(f) || (f === "rob" && s && s.extreme) ? [-3] : []);
const isSet = (x) => x !== null && x !== undefined && x !== "";
const join = (xs) => xs.length < 2 ? (xs[0] || "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
// what a certainty level means: without a single estimate when the results were not pooled (Murad et al. 2017)
const meanings = (T, o) => o.synthesis === "narrative" ? (o.kind === "accuracy" ? T.meaning_narrative_accuracy : T.meaning_narrative)
  : (o.kind === "accuracy" ? T.meaning_accuracy : T.meaning);

// GRADE's cautions for these ratings (certainty.cautions): shown, never blocking, but none passes without a reason
function cautions(T, R) {
  const startId = (R.start || {}).v;
  const s = T.start[startId];
  if (!s) return [];
  const out = [];
  const up = s.upgrade ? T.upgrades.reduce((n, u) => n + Math.max(0, Number((R[u.id] || {}).v || 0)), 0) : 0;
  const down = T.domains.filter((dm) => isSet((R[dm.id] || {}).v) && Number(R[dm.id].v) < 0).map((dm) => dm.name.toLowerCase());
  if (up && down.length) out.push({ id: "up_after_down", text: T.caution.up_after_down.replace("{domains}", join(down)) });
  if (up && s.design === "randomised") out.push({ id: "up_rct", text: T.caution.up_rct });
  // non-randomised studies assessed with ROBINS-I (or ROBINS-E), which start at High (GRADE guidance 18)
  if (T.start_nrs_high.includes(startId) && isSet((R.rob || {}).v)) {
    const tool = T.start_tool[startId] || "ROBINS-I";
    const all = T.domains.every((dm) => isSet((R[dm.id] || {}).v));
    const total = s.level + T.domains.reduce((n, dm) => n + Number((R[dm.id] || {}).v || 0), 0) + up;
    if (Number(R.rob.v) === 0) out.push({ id: "robins_not_down", text: T.caution.robins_not_down.replace("{tool}", tool) });
    else if (all && total >= 4) out.push({ id: "robins_high", text: T.caution.robins_high.replace("{tool}", tool) });
  }
  return out;
}
const needsWhy = (T, list) => list.some((c) => T.needs_why.includes(c.id));

// does a plain-language statement's wording match the certainty? (certainty.check_statement: generous, except that very
// low needs "very uncertain", not a bare "uncertain")
function checkStatement(T, text, level) {
  const W = T.wording, P = W.patterns;
  const t = String(text || "").replace(new RegExp(P.ignore, "gi"), " ");
  if (!t.trim() || !level) return { ok: true, why: "" };
  const has = (k) => new RegExp(P[k], "i").test(t);
  const ok = { 1: has("very_uncertain"), 2: has("may") && !has("very_uncertain"), 3: has("probably") && !has("very_uncertain"),
    4: !(has("uncertain") || has("may") || has("probably")) }[level];
  if (ok) return { ok: true, why: "" };
  let said = 4, word = "";
  for (const [lvl, k] of [[1, "very_uncertain"], [2, "may"], [3, "probably"], [0, "uncertain"]]) {
    const m = t.match(new RegExp(P[k], "i"));
    if (m) { said = lvl; word = m[0].split(/\s+/).join(" ").toLowerCase(); break; }
  }
  if (Number(level) === 1 && has("uncertain") && !has("very_uncertain")) { said = 0; word = "uncertain"; }
  const found = word && !said ? W.bare : word ? W.found.replace("{word}", word).replace("{said}", T.levels[said].toLowerCase()) : W.none;
  return { ok: false, why: W.why.replace("{level}", T.levels[level].toLowerCase()).replace("{rule}", W.rule[level]).replace("{found}", found) };
}

// start, minus the downgrades, plus the upgrades (not for test accuracy), kept between Very low and High
function worked(T, R) {
  const s = T.start[(R.start || {}).v];
  if (!s) return { level: null, steps: [] };
  let total = s.level;
  const steps = [];
  for (const dm of T.domains) {
    const x = (R[dm.id] || {}).v;
    if (!isSet(x)) return { level: null, steps };
    if (Number(x)) steps.push(`${dm.name.toLowerCase()} ${minus(x)}`);
    total += Number(x);
  }
  if (s.upgrade) for (const u of T.upgrades) { const x = Number((R[u.id] || {}).v || 0); if (x) steps.push(`${u.name.toLowerCase()} +${x}`); total += x; }
  return { level: Math.max(1, Math.min(4, total)), steps, start: s.level };
}

let pendingSaves = 0;         // ratings typed but not yet saved: the page asks before it is closed
window.addEventListener("beforeunload", (e) => { if (pendingSaves > 0) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------------------------------------ the list
export async function list(el, args) {
  const d = await api("/api/certainty");
  const T = d.terms;
  const page = h("div.page.wide");
  el.append(page);
  readinessBanner(page, "certainty");          // what is missing before this step (views_wizard.js)
  const [a, b] = d.reviewers;
  const who = (x) => nameOf(x);
  page.append(h("div.page-head", h("h1", "Rate certainty (GRADE)"), h("p", T.intro),
    h("p", a && b ? `${who(a)} and ${who(b)} rate each outcome independently: neither sees the other's ratings until both have finished it. The lead then records the agreed rating wherever they differ, and the agreed ratings make the evidence profile for the write-up.`
      : a ? `${who(a)} rates each outcome. GRADE ratings are usually made by two reviewers independently: the lead can choose the second in Project set-up (Screening plan).` : "")));
  if (d.is_lead) page.append(h("div.row", { style: { marginBottom: "16px" } },
    h("button.primary", { onclick: () => editOutcome(d, null) }, "Add an outcome"),
    d.studies.length ? null : h("span.small.muted", "No studies are included yet: you can add the outcomes now and choose their studies later.")));
  if (!d.outcomes.length) {
    page.append(h("div.callout", h("p", d.is_lead
      ? "No outcomes yet. Add each main outcome the review reports (for example pain at 12 weeks, serious adverse events, or a test's accuracy), say how important it is, and choose the included studies that report it. Both reviewers then rate it."
      : "No outcomes yet. The project lead adds each main outcome and chooses the studies that report it; you then rate it here.")));
  } else {
    const done = d.outcomes.filter((o) => o.both_done).length;
    const toAgree = d.outcomes.reduce((n, o) => n + o.to_agree, 0);
    page.append(h("div.stat", h("div", h("b", num(d.outcomes.length)), h("span", d.outcomes.length === 1 ? "outcome" : "outcomes")),
      h("div", h("b", num(done)), h("span", "rated by both")),
      h("div", h("b", num(toAgree)), h("span", toAgree === 1 ? "rating still to agree" : "ratings still to agree")),
      h("div", h("b", num(d.outcomes.filter((o) => o.complete).length)), h("span", "finished and agreed"))));
    page.append(h("div.tablewrap", h("table.t.c-list", h("caption.sr-only", "Outcomes"),
      h("thead", h("tr", h("th", "Outcome"), h("th", "Studies"), a ? h("th", nameOf(a)) : null, b ? h("th", nameOf(b)) : null,
        h("th.num", "To agree"), h("th", "Certainty"), d.is_lead ? h("th", h("span.sr-only", "Change")) : null)),
      h("tbody", d.outcomes.map((o) => h("tr.click", { onclick: (e) => { if (!e.target.closest("a, button")) go("c/" + encodeURIComponent(o.id)); } },
        h("td", h("a.rowlink", { href: "#/c/" + encodeURIComponent(o.id) }, h("strong", o.name)),
          h("div.small.muted", `${T.importance[o.importance] || ""} · ${o.kind === "accuracy" ? "Accuracy" : o.kind === "effect" ? "Effect" : "Other"}${o.comparison ? ` · ${o.comparison}` : ""}`)),
        h("td.small", o.studies.length ? o.studies.map((s) => s.label).join(", ") : h("span.pill.maybe", "None chosen"),
          o.gone.length ? h("div.small.muted", `${plural(o.gone.length, "study is", "studies are")} no longer included`) : null),
        a ? h("td", statePill(o.state_a)) : null, b ? h("td", statePill(o.state_b)) : null,
        h("td.num", o.both_done ? (o.to_agree ? h("span.pill.action", num(o.to_agree)) : "0") : "—"),
        h("td", o.complete ? levelPill(T, o.level) : o.both_done ? h("span.small.muted", o.to_agree ? "once agreed" : "see the outcome") : h("span.small.muted", "once both have finished")),
        d.is_lead ? h("td.nowrap", h("button.link.small", { "aria-label": `Change ${o.name}`, onclick: () => editOutcome(d, o) }, "Change"), " ",
          h("button.link.small", { "aria-label": `Remove ${o.name}`, onclick: () => removeOutcome(o) }, "Remove")) : null))))));
  }
  if ((d.accuracy || []).length) page.append(accuracyPanel(d));
  const why = d.download;
  page.append(h("div.panel", { style: { marginTop: "18px" } }, h("h2", "Downloads"),
    h("p.muted", "The evidence profile (the GRADE table for the write-up) holds the agreed ratings, with each reason for rating down or up as a footnote, the effect in words and the plain-language statement. Outcomes not yet finished and agreed are marked as such."),
    h("div.row", h("button", { onclick: () => download("certainty") }, "Evidence profile (Excel)"), why ? h("span.small.muted", why) : null)));
  if (d.outcomes.length) {
    const text = h("p.c-method", d.method_text);
    page.append(h("details.panel", h("summary", h("strong", "How certainty was rated, for the methods section")),
      h("p.small.muted", "Written from what has been done so far; it changes as outcomes are rated. Copy it into your methods and edit as you need."), text,
      h("button.small", { onclick: async () => { try { await navigator.clipboard.writeText(d.method_text); toast("Copied the paragraph"); } catch (e) { toast("Your browser did not allow copying: select the text and copy it instead.", true); } } }, "Copy the paragraph")));
  }
}

function editOutcome(d, o) {
  const T = d.terms;
  const cur = o || { name: "", importance: "critical", kind: "effect", comparison: "", measure: "", synthesis: "", participants: "", studies: [] };
  const fixed = !!(o && o.both_done);              // both have finished: kind and studies are fixed
  const finished = o ? [o.state_a === "finished" ? d.reviewers[0] : "", o.state_b === "finished" ? d.reviewers[1] : ""].filter(Boolean) : [];
  modal((box, close) => {
    const name = h("input#co-name", { type: "text", value: cur.name, placeholder: "e.g. Pain at 12 weeks" });
    const radios = (nm, opts, val, dis) => Object.entries(opts).map(([k, l]) => h("label.inline",
      h("input", { type: "radio", name: nm, value: k, checked: k === val, disabled: dis }), h("span", l)));
    const imp = h("fieldset", h("legend", "How important is it?"), radios("co-imp", T.importance, cur.importance),
      h("p.small.muted", "GRADE reports the critical and important outcomes; those of limited importance are usually left out of the summary."));
    const kindHelp = h("p.small.muted");
    const accHelp = h("div.callout.c-acc", { hidden: true }, h("p.small", h("strong", "Test accuracy: one outcome for each kind of result. "),
      "GRADE for test accuracy rates each accuracy outcome on its own (GRADE guidance 21, Schünemann et al. 2020), because the certainty often differs between them. Add one outcome each for true positives, false negatives, true negatives and false positives (for example “True positives (urgent patients the tool identifies)”), or at least one for sensitivity and one for specificity, each with the same studies."));
    const comp = h("input#co-comp", { type: "text", value: cur.comparison });
    const kind = h("fieldset", h("legend", "What kind of outcome?"), radios("co-kind", T.kinds, cur.kind, fixed), kindHelp, accHelp);
    const measure = h("input#co-measure", { type: "text", value: cur.measure });
    const resultSel = h("select#co-result", { "aria-describedby": "co-result-help" }, h("option", { value: "" }, "Choose…"),
      Object.entries(T.results).map(([k, l]) => h("option", { value: k, selected: k === (cur.result || cur.result_guess || "") }, l)));
    const resultWrap = h("label.f", { for: "co-result" }, h("span", "Which results this outcome covers"), resultSel,
      h("small#co-result-help", "The summary of findings shows them as numbers per 1,000 people tested (GRADE guidance 21), from the accuracy figures you enter on the list of outcomes."));
    const setKind = () => {
      const k = (box.querySelector("input[name=co-kind]:checked") || {}).value;
      kindHelp.textContent = k === "accuracy" ? "How well a test or tool identifies the condition against a reference standard. Rated with GRADE for test accuracy."
        : k === "effect" ? "What difference an intervention makes, compared with something else (for example pain with and without an exercise programme)." : "Anything else, rated like an effect.";
      accHelp.hidden = k !== "accuracy";
      resultWrap.hidden = k !== "accuracy";
      comp.placeholder = k === "accuracy" ? "e.g. the new test against the reference standard (say which)" : "e.g. the intervention compared with usual care";
      measure.placeholder = k === "accuracy" ? "e.g. true positives per 1,000 patients tested (sensitivity)" : "e.g. pain score from 0 to 10; mean difference";
    };
    kind.addEventListener("change", setKind);
    const syn = h("select#co-syn", Object.entries(T.synthesis).map(([k, l]) => h("option", { value: k, selected: k === (cur.synthesis || "") }, l)));
    const part = h("input#co-part", { type: "text", value: cur.participants || "", placeholder: "e.g. 2,410 patients" });
    const chosen = new Set((cur.studies || []).map((s) => s.key));
    const studies = h("fieldset", h("legend", "Which included studies contribute?"),
      d.studies.length ? d.studies.map((s) => h("label.inline.c-study",
        h("input", { type: "checkbox", value: s.key, checked: chosen.has(s.key), disabled: fixed }),
        h("span", h("strong", s.label), h("span.small.muted", ` ${s.reports.join(", ")}${s.title ? `: ${s.title.slice(0, 90)}${s.title.length > 90 ? "…" : ""}` : ""}`))))
        : h("p.small.muted", "No studies are included yet. Save the outcome now and choose its studies once studies are included at full text."));
    setKind();
    box.classList.add("wide");
    box.append(h("h2", o ? `Change “${o.name}”` : "Add an outcome"),
      h("p.muted", "One outcome for each main result the review reports, and the comparison it is for. Both reviewers then rate how certain the evidence for it is."),
      fixed ? h("div.callout", h("p.small", "Both reviewers have finished this outcome and have seen each other's ratings, so its kind and its studies can no longer change. Add a new outcome if they need to.")) : null,
      !fixed && finished.length ? h("div.callout.warn", h("p.small", `${finished.map((x) => first(nameOf(x))).join(" and ")} ${finished.length === 1 ? "has" : "have"} already finished this outcome. If you change its kind or its studies, ${finished.length === 1 ? "they look" : "they each look"} at it again and finish it again.`)) : null,
      h("label.f", { for: "co-name" }, h("span", "Name"), name, h("small", "Write it in sentence case (for example “Pain at 12 weeks”): the outputs put a small first letter inside sentences, but keep acronyms and names such as “ED length of stay”.")), imp, kind, resultWrap,
      h("label.f", { for: "co-comp" }, h("span", "Comparison, in plain words"), comp, h("small", "Write it as “X compared with Y” (or “X against Y” for accuracy): the suggested plain-language statements use it.")),
      h("label.f", { for: "co-measure" }, h("span", "Measure"), measure),
      h("div.grid2", h("label.f", { for: "co-syn" }, h("span", "How the results are synthesised"), syn, h("small", "Narrative ratings, where results are not pooled, follow Murad et al. (2017).")),
        h("label.f", { for: "co-part" }, h("span", "Number of participants (optional)"), part, h("small", "Leave it empty to add up the study sizes from the extraction."))),
      studies,
      h("div.row.end", h("button", { onclick: close }, "Cancel"),
        h("button.primary", { onclick: async () => {
          if (!name.value.trim()) { toast("Give the outcome a name.", true); name.focus(); return; }
          const pick = [...studies.querySelectorAll("input[type=checkbox]:checked")].map((x) => x.value);
          const kindNow = (box.querySelector("input[name=co-kind]:checked") || {}).value;
          const body = { o: o ? o.id : "", outcome: { name: name.value, importance: (box.querySelector("input[name=co-imp]:checked") || {}).value,
            kind: kindNow, comparison: comp.value, measure: measure.value, result: kindNow === "accuracy" ? resultSel.value : "",
            synthesis: syn.value, participants: part.value, studies: pick } };
          await attempt(() => api("/api/certainty/define", body), o ? "Outcome saved" : "Outcome added");
          close(); render();
        } }, o ? "Save the outcome" : "Add the outcome")));
  });
}

// ------------------------------------------------------------------------------------------------ test accuracy figures
// GRADE guidance 21: the summary of findings for a test gives the true and false results per 1,000 people tested at a
// stated pre-test probability. The lead enters, for each test (comparison), the condition, the pre-test probabilities
// with their sources, and the sensitivity and specificity (or the numbers per 1,000 directly).
const pct = (x) => Number(x).toFixed(1);          // one decimal place, as in the reports
function accuracyPanel(d) {
  const T = d.terms;
  const box = h("section.panel.c-accpanel", { style: { marginTop: "18px" } }, h("h2", "Accuracy figures for the summary of findings"),
    h("p.small.muted", T.acc_help));
  for (const g of d.accuracy) {
    const f = g.figures;
    const sec = h("div.c-accgroup", h("h3", g.comparison || "Test accuracy"),
      h("p.small.muted", `${g.outcomes.length === 1 ? "Outcome" : "Outcomes"}: ${g.outcomes.map((o) => `${o.name} (${o.result ? T.results[o.result].split(" (")[0].toLowerCase() : "which results it covers is not chosen"})`).join("; ")}`));
    if (!f) {
      sec.append(h("p", h("span.pill.maybe", "Not entered yet"), " ", d.is_lead
        ? "Enter the target condition, the pre-test probability with its source, and the sensitivity and specificity: the summary of findings for this test needs them."
        : "The project lead enters them: the summary of findings for this test needs them."));
    } else {
      sec.append(h("dl.kv.small", h("dt", "Target condition"), h("dd", f.condition), f.threshold ? [h("dt", "Threshold"), h("dd", f.threshold)] : null,
        g.sens ? [h("dt", "Sensitivity"), h("dd", g.sens)] : null, g.spec ? [h("dt", "Specificity"), h("dd", g.spec)] : null,
        h("dt", "Numbers per 1,000"), h("dd", T.acc_modes[f.mode] || "")));
      sec.append(h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": `Numbers per 1,000 people tested: ${g.comparison}` },
        h("table.t.c-acctable", h("caption.sr-only", `Numbers per 1,000 people tested: ${g.comparison}`),
          h("thead", h("tr", h("th", "Pre-test probability"), h("th", "Source"), ["tp", "fn", "tn", "fp"].map((r) => h("th.num", T.row_names[r])))),
          h("tbody", g.numbers.map((n) => h("tr", h("td", `${pct(n.p)}% (${num(n.with)} of 1,000 with the condition)`), h("td", n.source),
            ["tp", "fn", "tn", "fp"].map((r) => h("td.num", n[r] || "—"))))))));
    }
    if ((g.missing || []).length) sec.append(h("div.callout.warn", g.missing.map((m) => h("p.small", h("strong", `${m.name}: `), m.text))));
    if (d.is_lead) sec.append(h("button", { onclick: () => accuracyModal(d, g) }, f ? "Change the accuracy figures" : "Enter the accuracy figures"));
    box.append(sec);
  }
  return box;
}

function accuracyModal(d, g) {
  const T = d.terms;
  const f = g.figures || { condition: "", threshold: "", prev: [], mode: "pooled", sens: null, spec: null, interval: "ci", direct: [] };
  const me = S.app.project.me;
  modal((box, close) => {
    box.classList.add("wide");
    const inp = (id, value, attrs = {}) => h("input", { id, type: "text", value: value ?? "", ...attrs });
    const cond = inp("ca-cond", f.condition, { placeholder: "e.g. high acuity (needs to be seen within 10 minutes)" });
    const thr = inp("ca-thr", f.threshold, { placeholder: "e.g. Kellgren-Lawrence grade 2 or more" });
    const prevRows = [0, 1, 2].map((k) => {
      const pv = (f.prev || [])[k] || {};
      return { p: inp(`ca-p${k}`, pv.p ?? "", { inputmode: "decimal", placeholder: k ? "optional" : "e.g. 20", "aria-label": `Pre-test probability ${k + 1} (%)` }),
        src: inp(`ca-s${k}`, pv.source || "", { placeholder: k ? "optional" : "e.g. the median of the included studies", "aria-label": `Source of pre-test probability ${k + 1}` }) };
    });
    const radios = Object.entries(T.acc_modes).map(([k, l]) => h("label.inline", h("input", { type: "radio", name: "ca-mode", value: k, checked: k === (f.mode || "pooled") }), h("span", l)));
    const est = (what, v) => {
      const [e, lo, hi] = v || [null, null, null];
      const one = (k, x, lab) => inp(`ca-${what}-${k}`, x === null || x === undefined ? "" : String(x), { inputmode: "decimal", "aria-label": `${what === "sens" ? "Sensitivity" : "Specificity"}: ${lab}` });
      return { e: one("e", e, "estimate"), lo: one("lo", lo, "lower limit"), hi: one("hi", hi, "upper limit") };
    };
    const sens = est("sens", f.sens), spec = est("spec", f.spec);
    const ivl = h("select#ca-ivl", Object.entries(T.intervals).map(([k, l]) => h("option", { value: k, selected: k === (f.interval || "ci") }, l === "95% CI" ? "95% confidence interval" : l)));
    const pooled = h("div.c-accpooled",
      h("p.small.muted", "As proportions (0.85) or percentages (85%). Where the results were not pooled, give a typical value, such as the median, and the range across the studies."),
      h("div.c-accgrid", h("span"), h("span.small", "Estimate"), h("span.small", "Lower limit"), h("span.small", "Upper limit"),
        h("span", "Sensitivity"), sens.e, sens.lo, sens.hi, h("span", "Specificity"), spec.e, spec.lo, spec.hi),
      h("label.f", { for: "ca-ivl" }, h("span", "The interval is"), ivl));
    const directInputs = [0, 1, 2].map((k) => Object.fromEntries(["tp", "fn", "tn", "fp"].map((r) => [r,
      inp(`ca-d${k}-${r}`, (() => { const v = ((f.direct || [])[k] || {})[r]; return v ? `${v.n}${v.lo !== null && v.lo !== undefined ? ` (${v.lo} to ${v.hi})` : ""}` : ""; })(),
        { placeholder: "e.g. 170 (156 to 182)" })])));
    const direct = h("div.c-accdirect",
      h("p.small.muted", "Whole numbers per 1,000 people tested, with the interval in brackets if there is one: for example 170 (156 to 182). One group for each pre-test probability above, in the same order."),
      [0, 1, 2].map((k) => h("fieldset.c-accprev", h("legend", `Pre-test probability ${k + 1}${k ? " (if given)" : ""}`),
        h("div.c-acc4", ["tp", "fn", "tn", "fp"].map((r) => h("label.f", { for: `ca-d${k}-${r}` }, h("span", T.row_names[r]), directInputs[k][r]))))));
    const setMode = () => { const m = (box.querySelector("input[name=ca-mode]:checked") || {}).value; pooled.hidden = m === "direct"; direct.hidden = m !== "direct"; };
    const err = h("div.callout.bad", { hidden: true, role: "alert", tabindex: "-1" });
    box.append(h("h2", `Accuracy figures: ${g.comparison || "test accuracy"}`), h("p.small.muted", T.acc_help),
      h("div.grid2", h("label.f", { for: "ca-cond" }, h("span", "Target condition"), cond, h("small", "The condition the test is meant to find. The suggested statements say “people with …”.")),
        h("label.f", { for: "ca-thr" }, h("span", "Threshold or cut-off (optional)"), thr)),
      h("fieldset", h("legend", "Pre-test probability (prevalence among the people who would be tested)"),
        h("div.c-accgrid.c-acc2", h("span.small", "Percentage"), h("span.small", "Where it comes from"), prevRows.map((r) => [r.p, r.src]))),
      h("fieldset", { onchange: setMode }, h("legend", "How the numbers per 1,000 are found"), radios), pooled, direct, err,
      h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.primary", { onclick: async () => {
        const mode = (box.querySelector("input[name=ca-mode]:checked") || {}).value || "pooled";
        const figures = { comparison: g.comparison, condition: cond.value, threshold: thr.value, mode, interval: ivl.value,
          prev: prevRows.map((r) => ({ p: r.p.value, source: r.src.value })),
          sens: { est: sens.e.value, lo: sens.lo.value, hi: sens.hi.value }, spec: { est: spec.e.value, lo: spec.lo.value, hi: spec.hi.value },
          direct: directInputs.map((x) => Object.fromEntries(Object.entries(x).map(([r, el]) => [r, el.value]))) };
        try {
          await api("/api/certainty/accuracy", { figures, as: me });
        } catch (e) { err.replaceChildren(h("p", e.message)); err.hidden = false; err.focus(); return; }
        toast("Accuracy figures saved");
        close(); render();
      } }, "Save the accuracy figures")));
    setMode();
  });
}

// the accuracy figures beside an accuracy outcome: what the summary of findings will show per 1,000 people tested
function accuracySide(T, a) {
  return h("div.c-accside", h("h3", "Per 1,000 people tested"),
    a.result_name ? h("p.small", `This outcome covers: ${a.result_name.toLowerCase()}.`) : null,
    a.prevalence.length && a.rows.length && a.rows.every((rw) => rw.values.every(Boolean))
      ? h("dl.kv.small", a.prevalence.map((pv, k) => [h("dt", `At ${pct(pv.p)}%`), h("dd", a.rows.map((rw) => h("div", `${rw.name}: ${rw.values[k]}`)),
        h("div.small.muted", pv.source))]))
      : null,
    a.missing ? h("p.small.callout.warn", a.missing) : null);
}

async function removeOutcome(o) {
  if (!(await confirmBox(`Remove “${o.name}”?`, "Its ratings are kept in the project, but the outcome is no longer shown or exported. The removal is recorded in the audit log.", "Remove it", true))) return;
  await attempt(() => api("/api/certainty/define", { o: o.id, deleted: true }), "Outcome removed");
  render();
}

// ------------------------------------------------------------------------------------------------ the outcome page
export async function room(el, args) {
  const oid = args[0];
  const tabWanted = args[1] || "";
  const d = await api("/api/certainty/outcome?o=" + encodeURIComponent(oid));
  if (d.gone) {                               // removed, or an old link: this page has no menu, so offer a way back
    el.append(h("main.page", h("div.page-head", h("h1", "Outcome not found")), h("div.callout.warn", h("p", d.gone)), h("button.primary", { onclick: () => go("certainty") }, "Go to the list of outcomes")));
    return;
  }
  const T = d.terms, v = d.view, o = v.outcome;
  const me = S.app.project.me;
  const [ra, rb] = v.reviewers;
  const role = v.role;
  const accuracy = o.kind === "accuracy";
  const R = JSON.parse(JSON.stringify(v.mine || {}));
  const locked = !role || v.done_mine;
  const live = h("div.sr-only", { "aria-live": "polite" });
  const sug = v.suggest;

  // ---- saving: one save at a time per rating, in order; typing saves after a short pause, leaving a box saves at once
  const queues = {};
  const marks = {};
  const flushers = new Set();
  const typing = (input, fn) => {
    let t = null;
    const now = () => { if (t) { clearTimeout(t); t = null; pendingSaves--; flushers.delete(now); fn(); } };
    input.addEventListener("input", () => { if (t) clearTimeout(t); else { pendingSaves++; flushers.add(now); } t = setTimeout(now, 1200); });
    input.addEventListener("change", () => { if (t) now(); else fn(); });
  };
  const flush = async () => { [...flushers].forEach((f) => f()); await Promise.all(Object.values(queues)); };
  // A rating that could not be saved (OneDrive had the file open, or ReviewDesk was closed) is kept and tried again:
  // after the next save that works, when ReviewDesk answers again, before finishing and before the page is left.
  const failed = new Map();
  let retrying = null, hadFailed = false;
  const retryFailed = () => {
    if (!failed.size) return Promise.resolve();
    if (!retrying) retrying = Promise.all([...failed.keys()].map((f) => save(f, failed.get(f).input))).finally(() => { retrying = null; });
    return retrying;
  };
  const flushAll = async () => { await flush(); if (failed.size) { await retryFailed(); await Promise.all(Object.values(queues)); } return [...failed.keys()].map((f) => nameFor(f)); };
  const notSavedText = (left) => `ReviewDesk could not save ${left.length === 1 ? "this rating" : `these ${left.length} ratings`} yet: ${left.join("; ")}. It keeps trying; wait a moment (OneDrive may have the file open), or check ReviewDesk is running.`;
  listen(window, "rd-online", () => retryFailed());
  S.flushOnLeave = () => (document.body.contains(live) ? flushAll() : null);
  S.leaveGuard = () => failed.size > 0 && document.body.contains(live);
  S.leaveSave = async () => { const left = await flushAll(); if (left.length) { toast(notSavedText(left), true); return false; } return true; };
  S.leaveText = () => ["Some ratings are not saved", `${notSavedText([...failed.keys()].map((f) => nameFor(f)))} If you leave now, they are lost.`];
  const save = (f, input) => {
    pendingSaves++;
    queues[f] = (queues[f] || Promise.resolve()).then(async () => {
      const r = R[f] || {};
      const m = marks[f];
      try {
        await api("/api/certainty/rate", { o: oid, f, v: r.v ?? "", x: r.x || "", as: me });
        failed.delete(f);
        if (input) input.removeAttribute("aria-invalid");
        live.textContent = `Saved: ${nameFor(f)}`;
        if (m) { m.textContent = "Saved"; m.classList.remove("notsaved"); m.hidden = false; clearTimeout(m._t); m._t = setTimeout(() => (m.hidden = true), 2500); }
        if (failed.size) setTimeout(retryFailed, 0);
        else if (hadFailed) { hadFailed = false; toast("The ratings that could not be saved before are saved now."); }
      } catch (e) {
        if (input) input.setAttribute("aria-invalid", "true");
        if (m) { clearTimeout(m._t); m.textContent = "Not saved yet"; m.classList.add("notsaved"); m.hidden = false; }
        const first = !failed.has(f);
        failed.set(f, { input });
        hadFailed = true;
        if (first || !retrying) toast(`${e.message} ${failed.size === 1 ? "This rating is" : "These ratings are"} kept on this page and saved as soon as ReviewDesk can: ${[...failed.keys()].map((x) => nameFor(x)).join("; ")}.`, true);
      } finally { pendingSaves--; }
    });
    return queues[f];
  };
  const nameFor = (f) => f === "start" ? "starting certainty" : f === "effect" ? "the effect in words" : f === "statement" ? "plain-language statement"
    : f === T.up_why.id ? "why rating up is justified" : ([...T.domains, ...T.upgrades].find((x) => x.id === f) || { name: f }).name.toLowerCase();
  const set = (f, key, val) => { R[f] = { ...(R[f] || {}), [key]: val }; };
  const savedMark = (f) => (marks[f] = h("span.saved", { hidden: true, "aria-hidden": "true" }, "Saved"));

  // ---- the rating form
  const resultBox = h("div.c-result", { "aria-live": "polite" });
  const cautionBox = h("div.c-cautions");
  const sugBox = h("div.c-suggestions");
  const stCheck = h("div.callout.warn.c-check", { hidden: true, role: "status" });
  let statementBox = null;
  let whyWrap = null;
  const drawCheck = () => {
    const w = worked(T, R);
    const r = checkStatement(T, statementBox ? statementBox.value : (R.statement || {}).v, w.level);
    stCheck.hidden = r.ok;
    stCheck.replaceChildren(r.ok ? "" : h("p.small", h("strong", "Check the wording. "), r.why, " The suggested wordings above match it. If you keep yours, the lead records why before the outcome counts as finished."));
  };
  const drawResult = () => {
    const w = worked(T, R);
    const s = T.start[(R.start || {}).v];
    const warn = cautions(T, R);
    resultBox.replaceChildren(w.level
      ? [h("div.c-level", levelPill(T, w.level)), h("div",
        h("p", `Starts at ${T.levels[w.start]}${w.steps.length ? `; ${w.steps.join(", ")}` : "; nothing rated down or up"}: ${T.levels[w.level]}.`),
        h("p.small.muted", meanings(T, o)[w.level]))]
      : h("p.muted", s ? "Choose a rating for each of the five reasons to rate down, and the certainty is worked out here." : "Choose where the evidence starts, then rate each reason to rate down; the certainty is worked out here."));
    cautionBox.replaceChildren(...warn.map((c) => h("div.callout.warn", h("p.small", h("strong", "GRADE caution. "), c.text,
      T.needs_why.includes(c.id) ? ` ${T.caution_ask}` : c.id === "robins_not_down" ? ` ${T.robins_ask}` : ""))));
    if (whyWrap) whyWrap.hidden = !needsWhy(T, warn);
    sugBox.replaceChildren(w.level && !locked ? [h("p.small", `Suggested wording for ${T.levels[w.level].toLowerCase()} certainty (choose one, then edit it):`),
      (v.statements[w.level] || []).map((x) => h("button.small", { "aria-label": `Use the suggestion: ${x.text}`, onclick: () => {
        if (!statementBox) return;
        statementBox.value = x.text; set("statement", "v", x.text); save("statement", statementBox); drawCheck(); statementBox.focus(); } }, x.text))] : null);
    drawCheck();
  };

  const rateTab = () => {
    const box = h("div");
    whyWrap = null;
    if (!role) {
      box.append(h("p.small.callout", `${ra && rb ? `${nameOf(ra)} and ${nameOf(rb)} rate` : `${nameOf(ra)} rates`} this outcome. You are not one of the reviewers, so there is nothing for you to rate here. Once both have finished, their ratings are under Compare and agree${d.is_lead ? ", where you record the agreed rating wherever they differ" : ""}.`),
        h("p", status()));
      if (v.both_done) box.append(h("button", { onclick: () => { tab = "compare"; drawTab(); } }, "See the ratings and agree differences"));
      return box;
    }
    const other = role === "a" ? rb : ra;
    box.append(h("p.small.callout", other ? `You rate this outcome on your own; so does ${first(nameOf(other))}. Neither of you sees the other's ratings until you have both finished. After that the outcome cannot be reopened, and the lead records the agreed rating wherever you differ.`
      : "You rate this outcome on your own; your ratings stand as the final ones."));
    if (v.stale_mine) box.append(h("div.callout.warn", h("p", "The lead changed this outcome's kind or studies after you finished it. Check your ratings, then finish it again.")));
    if (!v.studies.length) box.append(h("div.callout.warn", h("p", "This outcome has no studies yet: the lead chooses which included studies contribute to it. You can finish it once it has some.")));
    const s = T.start[(R.start || {}).v];

    // 1. the start
    const order = accuracy ? ["dta", "rct", "nrs_robins", "nrs"] : ["rct", "nrs_robins", "nrs", "dta"];
    const startSel = h("select#c-start", { disabled: locked, "aria-describedby": "c-start-help" }, h("option", { value: "" }, "Choose…"),
      order.map((k) => h("option", { value: k, selected: (R.start || {}).v === k }, T.start[k].label + (k === sug.start ? " (suggested)" : ""))));
    const reasonWrap = h("label.f.c-reason", { for: "c-start-why" }, h("span", "Why you chose a different start from the one suggested"),
      h("textarea#c-start-why", { disabled: locked, value: (R.start || {}).x || "", placeholder: "Required: it is shown to the lead with your ratings" }));
    const reason = reasonWrap.querySelector("textarea");
    const showReason = () => { reasonWrap.hidden = !(sug.start && startSel.value && startSel.value !== sug.start); };
    showReason();
    startSel.addEventListener("change", async () => {
      set("start", "v", startSel.value); showReason(); save("start", startSel);
      drawTab(); document.getElementById("c-start")?.focus();
    });
    typing(reason, () => { set("start", "x", reason.value); save("start", reason); });
    box.append(h("section.panel.c-sec", h("h2", "1. Where the evidence starts"),
      h("p.small.muted#c-start-help", T.help.start),
      h("div.callout" + (sug.start ? "" : ".warn"), h("p.small", sug.start ? [h("strong", "Suggested: "), T.start[sug.start].label, ". ", sug.why] : sug.why)),
      h("label.f", { for: "c-start" }, h("span", "Starting certainty", savedMark("start")), startSel), reasonWrap));

    // 2. the reasons to rate down
    const downs = h("section.panel.c-sec", h("h2", "2. Reasons to rate down"),
      h("p.small.muted", "For each, choose no serious concerns, serious (rate down one level) or very serious (two levels). Imprecision can also be extremely serious (three levels), as can risk of bias in non-randomised studies assessed with ROBINS-I. For publication bias GRADE's words are undetected, strongly suspected or very strongly suspected. Explain any rating down: the explanation becomes a footnote in the evidence profile."));
    for (const dm of T.domains) {
      const sid = `c-${dm.id}`;
      const cur = R[dm.id] || {};
      const opts = allowedDown(T, dm.id, s);
      const bad = isSet(cur.v) && !opts.includes(Number(cur.v));
      const sel = h("select", { id: sid, disabled: locked, "aria-describedby": sid + "-help" }, h("option", { value: "" }, "Choose…"),
        opts.map((x) => h("option", { value: String(x), selected: isSet(cur.v) && Number(cur.v) === x }, label(T, dm.id, x))),
        bad ? h("option", { value: String(cur.v), selected: true }, `${label(T, dm.id, cur.v)}: not allowed with this start`) : null);
      const robins = dm.id === "rob" && T.start_nrs_high.includes((R.start || {}).v);
      const x = h("textarea.support", { disabled: locked, value: cur.x || "", "aria-label": `Explanation for ${dm.name.toLowerCase()}`,
        placeholder: robins ? "Why: required, also if you do not rate down (GRADE guidance 18); it becomes a footnote"
          : "Why: required when you rate down (it becomes a footnote)" });
      sel.addEventListener("change", () => { set(dm.id, "v", sel.value === "" ? "" : Number(sel.value)); save(dm.id, sel); drawResult(); });
      typing(x, () => { set(dm.id, "x", x.value); save(dm.id, x); });
      const help = h("p.small.muted.help", { id: sid + "-help" }, accuracy ? dm.help_accuracy : dm.help,
        dm.id === "rob" && s && s.extreme ? " Extremely serious (−3) is possible here because non-randomised studies assessed with ROBINS-I start at High." : "");
      const robList = dm.id === "rob" ? h("ul.c-rob", { "aria-label": "The studies' risk of bias" }, v.studies.length ? v.studies.map((st) => h("li",
        h("strong", st.label), st.rob.length ? st.rob.map((a) => h("span.c-robitem", ` ${a.label} (${a.tool_name}): `,
          a.overall ? h("span.pill." + (sevClass(a.overall) || "x"), a.overall) : h("span.small.muted", a.both_done ? "not yet agreed" : "not finished by both reviewers")))
          : h("span.small.muted", " no risk-of-bias assessment yet"))) : h("li.small.muted", "No studies chosen yet.")) : null;
      downs.append(h("div.domain.c-domain", h("label", { for: sid }, h("strong", dm.name), savedMark(dm.id)), sel, help, robList, x,
        bad ? h("p.err-under", dm.id === "rob" ? "Extremely serious (−3) for risk of bias is only for non-randomised studies that start at High (ROBINS-I). Choose another rating."
          : `Extremely serious (−3) is not used for ${dm.name.toLowerCase()}. Choose another rating.`) : null));
    }
    box.append(downs);

    // 3. the reasons to rate up
    const ups = h("section.panel.c-sec", h("h2", "3. Reasons to rate up"));
    const anyUp = T.upgrades.some((u) => Number((R[u.id] || {}).v || 0) > 0);
    if (!s) ups.append(h("p.muted", "Choose where the evidence starts first: whether it can be rated up depends on it."));
    else if (!s.upgrade && !anyUp) ups.append(h("p.muted", T.no_upgrade_accuracy));
    if (s && (s.upgrade || anyUp)) {
      ups.append(h(s.design === "randomised" ? "div.callout" : "p.small.muted", s.design === "randomised" ? h("p.small", T.upgrade_note.randomised) : T.upgrade_note.nonrandomised));
      if (!s.upgrade) ups.append(h("div.callout.warn", h("p.small", T.no_upgrade_accuracy + " Set these back to None.")));
      for (const u of T.upgrades) {
        const sid = `c-${u.id}`;
        const cur = R[u.id] || {};
        const val = Number(cur.v || 0);
        const opts = [0, 1].concat(u.max > 1 ? [2] : []);
        const sel = h("select", { id: sid, disabled: locked, "aria-describedby": sid + "-help" },
          opts.map((x) => h("option", { value: String(x), selected: val === x }, label(T, u.id, x))));
        const x = h("textarea.support", { disabled: locked, value: cur.x || "", "aria-label": `Explanation for ${u.name.toLowerCase()}`,
          placeholder: "Why: required when you rate up (it becomes a footnote)" });
        sel.addEventListener("change", () => { set(u.id, "v", Number(sel.value)); save(u.id, sel); drawResult(); });
        typing(x, () => { set(u.id, "x", x.value); save(u.id, x); });
        ups.append(h("div.domain.c-domain", h("label", { for: sid }, h("strong", u.name), savedMark(u.id)), sel,
          h("p.small.muted.help", { id: sid + "-help" }, u.help), x));
      }
      // why rating up is justified: required where GRADE calls rating up unusual (after rating down, or randomised trials)
      const W = T.up_why;
      const why = h("textarea#c-upwhy", { disabled: locked, value: (R[W.id] || {}).v || "", "aria-describedby": "c-upwhy-help",
        placeholder: "Required: it becomes a footnote in the evidence profile" });
      typing(why, () => { set(W.id, "v", why.value); save(W.id, why); });
      whyWrap = h("div.c-upwhy", h("label.f", { for: "c-upwhy" }, h("span", W.name, savedMark(W.id)), why),
        h("p.small.muted#c-upwhy-help", W.help));
      ups.append(whyWrap);
    }
    box.append(ups);

    // 4. the certainty
    box.append(h("section.panel.c-sec", h("h2", "4. The certainty of the evidence"), resultBox, cautionBox));

    // 5. the effect, 6. the statement
    const eff = h("textarea#c-effect.tall", { disabled: locked, value: (R.effect || {}).v || "", "aria-describedby": "c-effect-help",
      placeholder: `${plural(v.studies.length, "study", "studies")}${d.participants ? `, ${d.participants} participants` : ""}; the estimate (95% CI), or a summary in words` });
    typing(eff, () => { set("effect", "v", eff.value); save("effect", eff); });
    const help = accuracy ? T.help_accuracy : T.help;
    box.append(h("section.panel.c-sec", h("h2", "5. What the evidence shows"),
      h("p.small.muted#c-effect-help", help.effect, o.synthesis === "narrative" ? " The results of this outcome are not pooled: summarise them in words." : ""),
      h("label.f", { for: "c-effect" }, h("span", "The effect in words", savedMark("effect")), eff)));
    statementBox = h("textarea#c-statement", { disabled: locked, value: (R.statement || {}).v || "", "aria-describedby": "c-statement-help" });
    statementBox.addEventListener("input", drawCheck);
    typing(statementBox, () => { set("statement", "v", statementBox.value); save("statement", statementBox); });
    box.append(h("section.panel.c-sec", h("h2", "6. Plain-language statement"), h("p.small.muted#c-statement-help", help.statement), sugBox,
      h("label.f", { for: "c-statement" }, h("span", "Statement", savedMark("statement")), statementBox), stCheck));
    drawResult();

    // finishing
    const err = h("div.callout.bad", { hidden: true, role: "alert", tabindex: "-1" });
    if (v.done_mine) box.append(h("div.xdone", h("p", h("span.pill.include", "Finished"), " You have finished this outcome."),
      v.both_done && rb ? h("p.small.muted", "You have both finished, so it cannot be reopened: tell the lead what needs changing, and the lead records it under Compare and agree.")
        : h("button", { onclick: () => setDone(false, err) }, "Reopen to change a rating"), h("p.small", status())));
    else box.append(h("div.xdone", h("p.small.muted", "When everything is rated, mark the outcome as finished. Once you have both finished, you see each other's ratings and the outcome can no longer be reopened."),
      err, h("button.primary", { onclick: () => setDone(true, err) }, "Mark this outcome as finished"), h("p.small", status())));
    return box;
  };
  const status = () => [ra, rb].filter(Boolean).map((x) => {
    let st = x === ra ? v.state_a : v.state_b;
    if (x === me && st === "not started" && Object.values(R).some((r) => r && (String(r.v ?? "") !== "" || String(r.x || "") !== ""))) st = "started";
    return `${x === me ? "You" : first(nameOf(x))}: ${st}`;
  }).join("; ") + ".";
  const setDone = async (done, err) => {
    const left = await flushAll();
    if (done && left.length) { err.replaceChildren(h("p", `The outcome is not marked as finished. ${notSavedText(left)}`)); err.hidden = false; err.focus(); return; }
    try {
      await api("/api/certainty/done", { o: oid, done, as: me });
      toast(done ? "Outcome finished" : "Reopened");
      reload("rate");
    } catch (e) {
      err.replaceChildren(h("p", e.message)); err.hidden = false; err.focus();
    }
  };

  // ---- comparing and agreeing (after both have finished)
  const compareTab = () => {
    const box = h("div");
    const c = v.comparison;
    if (!c) { box.append(h("p", "The comparison appears when both reviewers have finished this outcome.")); return box; }
    const items = c.items.filter((it) => !it.na);
    const open = items.filter((it) => it.differs && !it.agreed);
    const diff = items.filter((it) => it.differs);
    const sc = c.statement_check || { ok: true };
    const stText = String((items.find((it) => it.f === "statement") || {}).final || "");
    const mismatch = !sc.ok && !sc.kept && c.level;
    const others = c.problems.filter((x) => !(mismatch && x.startsWith(sc.why)));
    box.append(h("section.panel.c-final", h("h2", "The agreed certainty"),
      c.complete ? [h("div.c-result", h("div.c-level", levelPill(T, c.level)), h("div", h("p", meanings(T, o)[c.level]),
        h("p", h("strong", "Statement: "), stText)))]
        : c.level ? [h("div.c-result", h("div.c-level", levelPill(T, c.level)), h("div", h("p", meanings(T, o)[c.level]),
          h("p.small", h("span.pill.maybe", "Not finished"), " The outcome counts as finished once everything below is put right.")))]
          : h("p", open.length ? `${plural(open.length, "rating differs", "ratings differ")} between the two reviewers and ${open.length === 1 ? "needs" : "need"} agreeing first. ${d.is_lead ? "Talk them through, then record the agreed rating below." : "The lead records the agreed ratings."}`
            : "Every difference is agreed."),
      others.length ? h("div.callout.warn", others.map((x) => h("p.small", x))) : null,
      (c.cautions || []).map((w) => h("div.callout.warn", h("p.small", h("strong", "GRADE caution. "), w.text))),
      mismatch ? statementPanel(c, sc, stText) : null,
      sc.kept ? h("div.callout", h("p.small", h("strong", "Statement kept as worded. "), `Its wording differs from ${accuracy ? "the usual wording" : "GRADE's wording"} for ${T.levels[c.level].toLowerCase()} certainty. Reason (a footnote in the evidence profile): ${sc.reason}`),
        d.is_lead ? h("p.small", "To change it, record a different agreed statement below (Plain-language statement), or give a new reason.") : null,
        d.is_lead ? keepForm(c, sc.reason) : null) : null));
    box.append(h("p", diff.length ? `The two reviewers differed on ${plural(diff.length, "rating")}${diff.length > open.length ? ` (${num(diff.length - open.length)} agreed)` : ""}.` : "The two reviewers gave the same ratings."));
    for (const it of diff) box.append(agreePanel(it, c));
    // ratings both reviewers gave identically that the lead changed: each needs the lead's reason (a footnote)
    const changed = items.filter((it) => !it.differs && it.override);
    if (changed.length) {
      box.append(h("p", `The project lead changed ${plural(changed.length, "rating")} that both reviewers gave identically. ${changed.length === 1 ? "The change is" : "Each change is"} reported in a footnote and counted in the methods text.`));
      for (const it of changed) box.append(agreePanel(it, c));
    }
    const same = items.filter((it) => !it.differs && !it.override);
    if (same.length) box.append(h("details.panel", { open: !diff.length }, h("summary", `${plural(same.length, "rating")} that agree`),
      h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": "Ratings that agree" }, h("table.t.c-cmp", h("caption.sr-only", "Ratings that agree"),
        h("thead", h("tr", h("th", "Item"), h("th", "Rating"), h("th", "Explanation (footnote)"), d.is_lead ? h("th", h("span.sr-only", "Change")) : null)),
        h("tbody", same.map((it) => h("tr", h("td", it.name), h("td", isText(it.f) ? h("span.small", String(it.final ?? "")) : ratingPill(it.f, it.final)),
          h("td.small", isText(it.f) ? "" : it.final_x || (Number(it.final) ? h("span.pill.maybe", "missing") : "")),
          d.is_lead ? h("td", h("button.link.small", { "aria-label": `Change the agreed rating: ${it.name.toLowerCase()}`, onclick: () => agreeModal(it, c) }, "Change")) : null)))))));
    if (c.items.some((it) => it.na && T.upgrades.some((u) => u.id === it.f))) box.append(h("p.small.muted", `Reasons to rate up are not used: ${T.no_upgrade_accuracy}`));
    return box;
  };
  const isText = (f) => f === "effect" || f === "statement" || f === T.up_why.id;
  const ratingPill = (f, val) => {
    if (val === null || val === undefined || val === "") return h("span.small.muted", "(none)");
    if (f === "start") return h("span", label(T, f, val));
    const n = Number(val);
    return h("span.pill" + (n < 0 ? (n <= -2 ? ".exclude" : ".maybe") : n > 0 ? ".include" : ""), label(T, f, val));
  };
  // the statement's wording does not match the certainty: the lead changes it, or keeps it and records why
  // (a labelled part of the page, not an alert: it is there when the page opens, and a screen reader would read out
  // every suggestion and button in it at once)
  const statementPanel = (c, sc, stText) => h("section.callout.bad.c-mismatch", { "aria-labelledby": "c-mismatch-h" },
    // the explanation's first sentence says what the heading says: keep the rest
    h("p", h("strong#c-mismatch-h", "The statement's wording does not match the certainty. "), sc.why.replace(/^[^.]*\.\s*/, "")),
    h("p.small", h("strong", "Statement: "), `“${stText}”`),
    h("p.small", "Readers take the words “probably”, “may” and “very uncertain” as the certainty of the evidence (GRADE informative statements, Santesso et al. 2020), so the outcome does not count as finished until the wording matches or the lead records why it differs."),
    d.is_lead ? [h("p.small", h("strong", "Use a wording that matches"), ` (${T.levels[c.level].toLowerCase()} certainty; you can edit it afterwards under Plain-language statement):`),
      h("div.c-suggestions", [sc.suggestion].concat((v.statements[c.level] || []).map((x) => x.text).filter((t) => t !== sc.suggestion)).filter(Boolean)
        .map((t) => h("button.small", { "aria-label": `Use this as the agreed statement: ${t}`, onclick: async () => {
          await attempt(() => api("/api/certainty/agree", { o: oid, f: "statement", v: t, as: me }), "Agreed statement saved");
          reload("compare");
        } }, t))),
      h("p.small", h("strong", "Or keep the wording"), " and say why it differs (for example, a reason the journal or the protocol gives). The reason becomes a footnote in the evidence profile."),
      keepForm(c, "")]
      : h("p.small", "The project lead puts this right."));
  const keepForm = (c, was) => {
    const ta = h("textarea#c-keep", { value: was || "", placeholder: "Why the statement keeps this wording (required)" });
    const note = unsavedNote();
    keepDraft(ta, `c:${oid}:keep`, { note });
    return h("div.c-agree", h("label.small", { for: "c-keep" }, "Reason for keeping the wording"), ta,
      h("div.row", h("button.small", { onclick: async () => {
        if (!ta.value.trim()) { toast("Say why the statement keeps this wording: the reason becomes a footnote.", true); ta.focus(); return; }
        await attempt(() => api("/api/certainty/keep_statement", { o: oid, x: ta.value, as: me }), "Reason recorded: the statement is kept as worded");
        drafts.drop(`c:${oid}:keep`);
        reload("compare");
      } }, was ? "Save the new reason" : "Keep the wording and record the reason"), note));
  };
  const who = (x) => nameOf(x);
  const agreePanel = (it, c) => {
    const panel = h("div.panel.cmp" + (it.agreed ? ".settled" : ""), { id: "cmp-" + it.f });
    const shown = (side) => isText(it.f) ? h("span", String(side.v ?? "") || "(empty)")
      : [ratingPill(it.f, side.v), side.x ? h("div.small.quote", side.x) : null];
    const ov = it.override;
    panel.append(h("h3", it.name, ov ? h("span.pill.maybe.tiny", "changed by the lead") : it.agreed ? h("span.pill.include.tiny", "agreed") : h("span.pill.action.tiny", "differs")),
      h("dl.kv", h("dt", who(ra)), h("dd", shown(it.a)), rb ? [h("dt", who(rb)), h("dd", shown(it.b))] : null,
        it.agreed ? [h("dt", "Agreed"), h("dd", h("strong", isText(it.f) ? String(it.agreed.v ?? "") : label(T, it.f, it.agreed.v)), it.agreed.x ? h("div.small.quote", it.agreed.x) : null)] : null,
        ov ? [h("dt", "Why the lead changed it"), h("dd", ov.why ? h("span.small", ov.why) : h("span.pill.exclude", "reason missing"))] : null));
    if (d.is_lead) panel.append(agreeForm(it, c, () => {}));
    return panel;
  };
  const textTitle = { effect: ["The agreed effect in words", "Agreed wording of the effect", "Write the agreed effect in words."],
    statement: ["The agreed statement", "Agreed statement", "Write the agreed plain-language statement."],
    [T.up_why.id]: ["The agreed reason why rating up is justified", "Agreed reason why rating up is justified", "Write the agreed reason why rating up is justified."] };
  const agreeModal = (it, c) => modal((box, close) => {
    box.append(h("h2", isText(it.f) ? textTitle[it.f][0] : `The agreed rating: ${it.name.toLowerCase()}`),
      agreeForm(it, c, close), h("div.row.end", h("button", { onclick: close }, "Cancel")));
  });
  const agreeForm = (it, c, done) => {
    const id = "agree-" + it.f;
    const wrap = h("div.c-agree");
    const pick = it.agreed || (it.differs ? null : { v: it.final, x: it.final_x });
    let sel = null;
    // what the lead chose or typed and has not saved is kept when the page is drawn again (after another rating's Save)
    const dk = `c:${oid}:agree:${it.f}:`;
    const note = unsavedNote();
    const marks = [];
    const kept = (el, part) => { const n = unsavedNote(); marks.push(n); keepDraft(el, dk + part, { note: n }); return el; };
    const showNote = () => { note.hidden = marks.every((n) => n.hidden); };
    wrap.addEventListener("input", () => setTimeout(showNote, 0));
    wrap.addEventListener("change", () => setTimeout(showNote, 0));
    if (isText(it.f)) {
      const ta = h("textarea", { id, value: String((pick || it.a).v ?? "") });
      kept(ta, "v");
      const useA = h("button.small", { onclick: () => { ta.value = String(it.a.v ?? ""); ta.dispatchEvent(new Event("input", { bubbles: true })); ta.focus(); } }, `Use ${first(who(ra))}'s`);
      const useB = rb ? h("button.small", { onclick: () => { ta.value = String(it.b.v ?? ""); ta.dispatchEvent(new Event("input", { bubbles: true })); ta.focus(); } }, `Use ${first(who(rb))}'s`) : null;
      const sugg = it.f === "statement" && c.level ? (v.statements[c.level] || []).map((x) => h("button.small", { "aria-label": `Use the suggestion: ${x.text}`, onclick: () => { ta.value = x.text; ta.dispatchEvent(new Event("input", { bubbles: true })); ta.focus(); } }, x.text)) : [];
      // the statement's wording is checked against the certainty as the lead writes it
      const check = h("div.callout.warn.c-check", { hidden: true, role: "status" });
      if (it.f === "statement" && c.level) {
        const draw = () => { const r = checkStatement(T, ta.value, c.level); check.hidden = r.ok; check.replaceChildren(r.ok ? "" : h("p.small", h("strong", "Check the wording. "), r.why)); };
        ta.addEventListener("input", draw); draw();
      }
      wrap.append(h("label.small", { for: id }, textTitle[it.f][1]), ta, check, h("div.row", useA, useB),
        sugg.length ? h("div.c-suggestions", h("p.small", `Suggested wording for ${T.levels[c.level].toLowerCase()} certainty:`), sugg) : null,
        h("div.row", h("button.primary.small", { onclick: async () => {
          if (!ta.value.trim()) { toast(textTitle[it.f][2], true); ta.focus(); return; }
          await attempt(() => api("/api/certainty/agree", { o: oid, f: it.f, v: ta.value, as: me }), "Agreed wording saved");
          drafts.dropAll(dk);
          done(); reload("compare");
        } }, "Save the agreed wording"), note));
      showNote();
      return wrap;
    }
    const startNow = T.start[it.f === "start" ? "" : c.start] || null;
    const opts = it.f === "start" ? Object.keys(T.start)
      : T.domains.some((x) => x.id === it.f) ? allowedDown(T, it.f, startNow)
        : [0, 1].concat((T.upgrades.find((u) => u.id === it.f) || {}).max > 1 ? [2] : []);
    sel = h("select", { id }, h("option", { value: "" }, "Choose…"), opts.map((x) => h("option", { value: String(x), selected: pick && String(pick.v) === String(x) }, label(T, it.f, x))));
    const robins = it.f === "rob" && T.start_nrs_high.includes(c.start);
    const ta = h("textarea.support", { value: (pick || {}).x || "", "aria-label": `Explanation for the agreed ${it.name.toLowerCase()} (the footnote)`,
      placeholder: it.f === "start" ? "Why (optional)" : robins ? "Why: required, also if not rated down (GRADE guidance 18); it becomes the footnote" : "Why: required when rating down or up (it becomes the footnote)" });
    // a rating neither reviewer gave (for example, a change to one both gave identically) needs the lead's reason
    const gave = [it.a.v, it.b.v].map((x) => (x === null || x === undefined ? "" : String(x)));
    const whyId = id + "-why";
    const whyTa = h("textarea.support", { id: whyId, value: (it.agreed && it.agreed.why) || "", placeholder: "Required: it becomes a footnote and is counted in the methods text" });
    const whyWrap = h("div.c-agree", h("label.small", { for: whyId }, gave[0] === gave[1] ? `Why you are changing a rating both reviewers gave (${label(T, it.f, it.a.v) || "none"})` : "Why you are recording a rating neither reviewer gave"), whyTa);
    const showWhy = () => { whyWrap.hidden = sel.value === "" || gave.includes(sel.value); };
    kept(sel, "v"); kept(ta, "x"); kept(whyTa, "why");
    sel.addEventListener("change", showWhy);
    const use = (side) => { sel.value = side.v === null || side.v === undefined ? "" : String(side.v); ta.value = side.x || ""; [sel, ta].forEach((x) => x.dispatchEvent(new Event("change", { bubbles: true }))); sel.focus(); };
    wrap.append(h("label.small", { for: id }, `Agreed ${it.name.toLowerCase()}`), sel, ta, whyWrap,
      h("div.row", h("button.small", { onclick: () => use(it.a) }, `Use ${first(who(ra))}'s`), rb ? h("button.small", { onclick: () => use(it.b) }, `Use ${first(who(rb))}'s`) : null,
        h("button.primary.small", { onclick: async () => {
          if (sel.value === "") { toast("Choose the agreed rating.", true); sel.focus(); return; }
          const val = it.f === "start" ? sel.value : Number(sel.value);
          if (it.f !== "start" && val !== 0 && !ta.value.trim()) { toast("Give the explanation: it becomes the footnote in the evidence profile.", true); ta.focus(); return; }
          if (robins && val === 0 && !ta.value.trim()) { toast("Say why risk of bias is not rated down: GRADE guidance 18 generally rates this evidence down at least two levels. The explanation becomes a footnote.", true); ta.focus(); return; }
          if (!whyWrap.hidden && !whyTa.value.trim()) { toast("Say why you are changing the reviewers' rating: the reason becomes a footnote.", true); whyTa.focus(); return; }
          await attempt(() => api("/api/certainty/agree", { o: oid, f: it.f, v: val, x: ta.value, why: whyWrap.hidden ? "" : whyTa.value, as: me }), "Agreed rating saved");
          drafts.dropAll(dk);
          done(); reload("compare");
        } }, "Save the agreed rating"), note));
    showWhy();
    showNote();
    return wrap;
  };

  // ---- the outcome, beside the form
  const side = h("aside.c-side", h("h2", "The outcome"),
    h("dl.kv.small", h("dt", "Importance"), h("dd", T.importance[o.importance] || ""), h("dt", "Kind"), h("dd", T.kinds[o.kind] || ""),
      o.comparison ? [h("dt", "Comparison"), h("dd", o.comparison)] : null, o.measure ? [h("dt", "Measure"), h("dd", o.measure)] : null,
      h("dt", "Synthesis"), h("dd", T.synthesis[o.synthesis || ""]), d.participants ? [h("dt", "Participants"), h("dd", d.participants)] : null),
    h("h3", `Studies (${num(v.studies.length)})`),
    v.studies.length ? h("ul.c-studies", v.studies.map((st) => h("li", h("strong", st.label), h("span.small.muted", ` ${st.reports.join(", ")}`),
      st.design ? h("div.small", `Design (extraction): ${st.design}`) : null,
      st.n ? h("div.small", `Participants (extraction): ${st.n}`) : null))) : h("p.small.muted", "None chosen yet: the lead chooses them on the list of outcomes."),
    v.gone.length ? h("p.small.callout.warn", `${plural(v.gone.length, "study", "studies")} chosen for this outcome ${v.gone.length === 1 ? "is" : "are"} no longer included (a full-text decision changed).`) : null,
    accuracy ? h("p.small.muted", "GRADE for test accuracy rates true positives, false negatives, true negatives and false positives (or at least sensitivity and specificity) as separate outcomes (GRADE guidance 21). If this outcome covers accuracy as a whole, the lead can split it.") : null,
    accuracy && v.accuracy ? accuracySide(T, v.accuracy) : null,
    h("details", h("summary.small", "About GRADE"), h("p.small", T.intro),
      h("dl.kv.small", ["4", "3", "2", "1"].map((k) => [h("dt", `${T.levels[k]} ${T.symbols[k]}`), h("dd", meanings(T, o)[k])]))));

  // ---- tabs
  const tabs = [["rate", role ? "Your ratings" : "Ratings"]].concat(v.both_done ? [["compare", "Compare and agree"]] : []);
  let tab = tabs.find((t) => t[0] === tabWanted) ? tabWanted : v.both_done ? "compare" : "rate";
  const body = h("div.xtab", { role: "tabpanel", tabindex: "-1" });
  const tabBar = h("div.tabs", { role: "tablist", "aria-label": "Outcome" }, tabs.map(([k, l], i) => h("button", {
    role: "tab", id: "ctab-" + k, onclick: () => { tab = k; drawTab(); },
    onkeydown: (e) => { if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return; e.preventDefault();
      const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length; tab = tabs[n][0]; drawTab(); tabBar.children[n].focus(); } }, l)));
  const drawTab = () => {
    [...flushers].forEach((f) => f());
    [...tabBar.children].forEach((b, i) => { const on = tabs[i][0] === tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    body.setAttribute("aria-labelledby", "ctab-" + tab);
    body.replaceChildren(tab === "compare" ? compareTab() : rateTab());
  };
  const reload = async (t) => {
    await flush();
    const want = `#/c/${encodeURIComponent(oid)}/${t}`;
    if (location.hash === want) await render(); else { carryToast(); location.hash = want; }
  };
  drawTab();
  el.append(h("main.screen", h("div.screen-top", h("button", { onclick: () => go("certainty") }, "← All outcomes"),
    h("div.grow", h("h1.title", `Rate certainty: ${o.name}`), h("div.small.muted", `Outcome ${num(d.position)} of ${num(d.total)}. ${T.importance[o.importance] || ""}; ${(T.kinds[o.kind] || "").toLowerCase()}.${role ? " You are one of the two reviewers." : ""}`)),
    h("button", { disabled: !d.prev, "aria-label": "Previous outcome", onclick: () => go("c/" + encodeURIComponent(d.prev)) }, "←"),
    h("button", { disabled: !d.next, "aria-label": "Next outcome", onclick: () => go("c/" + encodeURIComponent(d.next)) }, "→")),
    h("div.c-body", h("div.c-wrap", h("section.c-main", { "aria-label": "Ratings" }, tabBar, body, live), side))));
}
