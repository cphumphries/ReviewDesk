// Title and abstract screening: the hub (plan, agreement, decisions) and the screening page.
import { api, h, toast, attempt, modal, num, pct, k2, kci, bar, highlight, confirmBox, plural, SETS, TA_LABEL, drafts, listen, stopwatch } from "./lib.js";
import { S, go, render } from "./app.js";
import { readinessBanner } from "./views_wizard.js";
import { progressMissing } from "./views_core.js";

const nameOf = (id) => (S.app.project.settings.reviewers.find((r) => r.id === id) || {}).name || id || "the reviewer not yet chosen";

// ------------------------------------------------------------------------------------------------ hub
export async function hub(el, args = []) {
  const p = S.app.project, o = S.ov;
  if (!o) { el.append(progressMissing()); return; }
  const page = h("div.page");
  el.append(page);
  readinessBanner(page, "screen");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", "Screen titles and abstracts"),
    h("p", "Records are shown in a random order, never ranked. Vote Include, Maybe or Exclude: Include and Maybe both send a record to full text, so vote Exclude only when the title or abstract shows that a criterion is clearly not met.")));
  const tasks = (o.tasks || []).filter((t) => t.task !== "fulltext");
  if (tasks.length) page.append(h("div.panel", h("h2", "Your screening"), h("div.tasks", tasks.map((t) => h("div.task",
    h("div", h("strong", t.label), h("div.small.muted", SETS[t.set])), h("div", h("div.small.muted", `${num(t.total - t.left)} of ${num(t.total)} done`), bar(t.total - t.left, t.total, `${t.label}: progress`)),
    h("button" + (t.left ? ".primary" : ""), { onclick: () => go(`screen/${t.task}/${t.set}`) }, t.left ? (t.left === t.total ? "Start" : "Continue") : "Review my votes"))))));
  if (o.verifying && o.verifying.lost) page.append(h("div.callout.warn", { role: "note" }, h("p", h("strong", "Your verification sample is no longer in the project's files. "),
    `An older copy of them may have been put back. Your votes are kept. Until the project lead has drawn the sample again (the same numbers give the same sample, and your votes count again), ReviewDesk keeps closed to you the pages that show what became of records: Review full texts, Extract data, Rate certainty, the audit log and every download.${p.is_lead ? " Draw it again below." : " Tell the project lead."}`)));
  else if (o.verifying) page.append(h("div.callout", { role: "note" }, h("p", h("strong", "You are screening a verification sample. "),
    `It is done without sight of what was decided on its records before, so until you have finished it (${plural(o.verifying.left, "record")} left) ReviewDesk keeps closed to you the pages that show what became of records: Review full texts, Extract data, Rate certainty, the audit log and every download. Nothing your votes find changes any record until the sample is finished, and until then you can change any of them. Your vote on the last record finishes it: its votes are then fixed, and everything opens again.`)));
  if (o.verifying && !o.verifying.lost && !tasks.some((t) => t.task === "verify")) page.append(h("div.callout.warn", { role: "note" }, h("p",
    "Your verification sample is not listed above just now, because the project's files were put back to a point before its records were there. Your votes are kept. Tell the project lead, who can put the newest copy back (Data checks).")));
  if (p.member && o.timing && o.timing.project) page.append(h("div.callout", { role: "note" }, timingNote(o.timing)));
  if (!Object.keys(o.sets).length) {
    const waiting = (o.imports || {}).unprocessed || 0;          // files added, but duplicates not yet removed
    page.append(h("div.callout", h("p", waiting
      ? `${plural(waiting, "imported file is", "imported files are")} waiting for duplicates to be removed: ${p.is_lead ? "on Import records, choose Remove duplicates and add the files to the records" : "the project lead does this on Import records"}. Screening starts from the de-duplicated records.`
      : "No records have been added yet. Import the database exports first."), h("button", { onclick: () => go("import") }, "Go to import")));
    return;
  }
  for (const [setName, sv] of Object.entries(o.sets)) page.append(await setPanel(setName, sv));
  if (p.is_lead && Object.values(o.sets).some((sv) => sv.phase !== "not_started")) page.append(evaluationPanel(o));
  // #/screening/decide (the "Decide together" button of a check) opens the disagreements straight away
  if (args[0] === "decide") { history.replaceState(null, "", "#/screening"); if (p.is_lead) conflictsModal("ta"); }
}

async function setPanel(setName, sv) {
  const p = S.app.project, lead = p.is_lead;
  const pl = sv.plan;
  const box = h("div.panel");
  box.append(h("div.row", h("h2.grow", `${sv.label}: ${plural(sv.records, "record")}`), h("span.pill" + (sv.phase === "screening" ? ".action" : ""), phaseName(sv.phase))));
  const aiNet = pl.ai_second_screener ? `, with the AI second screener sending anything ${nameOf(pl.single_reviewer)} excludes but the AI would keep to ${nameOf(pl.second_reviewer)}` : "";
  const confText = pl.conflicts === "discuss" ? " When the two disagree, they decide together." : " A record goes to full text if either sends it on.";
  const modeText = pl.mode === "dual" ? "Both reviewers screen every record." + confText : pl.mode === "single" ? `${nameOf(pl.single_reviewer)} screens every record${aiNet}.`
    : `Both screen a random ${Math.round(100 * pl.sample_fraction)}% first. If Cohen's kappa reaches ${pl.threshold_shown || k2(pl.kappa_threshold)}, ${nameOf(pl.single_reviewer)} screens the rest alone${pl.ai_second_screener ? `, with the AI second screener sending anything ${nameOf(pl.single_reviewer)} excludes but the AI would keep to ${nameOf(pl.second_reviewer)}` : ""}; otherwise both screen everything.`;
  box.append(h("p", h("strong", "Plan: "), modeText, pl.calibration_size && setName === "main" ? ` A calibration round of ${pl.calibration_size} records comes first.` : "", pl.inherited ? " (Taken from the outcome of the main screening.)" : ""));
  if (sv.phase === "not_started") {
    const drawsSample = pl.mode === "sample_then_single" || (pl.calibration_size > 0 && setName === "main");
    const why = h("div");
    if (lead) box.append(h("p.muted", `Starting puts the records in a random order (with a recorded seed)${drawsSample ? " and draws the samples" : ""}. The screening plan is then fixed${pl.ai_second_screener ? ", and so are the criteria the AI screener uses" : ""}.`),
      why,
      h("div.row", h("button.primary", { onclick: async () => {
        let pairs = 0;
        try { pairs = (await api("/api/dedup")).possible.filter((x) => !x.started).length; } catch (e) { /* not essential */ }
        const text = [`The records will be put in a random order${drawsSample ? " and the samples drawn" : ""}. After this, the screening plan cannot be changed${pl.ai_second_screener ? ", and the AI screener keeps the criteria as they are now (a later change to them is logged as a protocol amendment)" : ""}. Make sure every export for this set has been imported.`];
        if (pairs) text.push(h("p", h("strong", `${plural(pairs, "pair")} on the Check duplicates page ${pairs === 1 ? "has" : "have"} not been decided. `), "Once screening starts they can no longer be merged."));
        if (!await confirmBox("Start screening?", text, "Start screening")) return;
        try { await api("/api/screening/start", { set: setName }); toast("Screening started"); render(); }
        catch (e) { why.replaceChildren(h("div.callout.bad", { role: "alert" }, h("p", e.message))); e.toasted = true; why.querySelector("p").scrollIntoView({ block: "center" }); }
      } }, "Start screening"),
        h("button", { onclick: () => go("setup") }, "Change the plan")));
    else box.append(h("p.muted", "Waiting for the project lead to start screening."));
    return box;
  }
  // a calibration round that can never be finished (a project the earlier 1.3.0 started with one reviewer): why, and
  // for the lead, a way to end it
  if (sv.phase === "calibration" && sv.calibration_stuck) box.append(h("div.callout.warn", { role: "status" }, h("p", sv.calibration_stuck),
    lead ? h("div.row", h("button.primary", { onclick: async () => {
      if (!await confirmBox("End the calibration round?", ["Screening goes on with the plan as it is. The practice votes are kept but never counted, and the reports do not describe a calibration round."], "End the calibration round")) return;
      await attempt(() => api("/api/screening/calibration", { set: setName }), "Calibration round ended: screening goes on");
      render();
    } }, "End the calibration round")) : h("p.muted", "Only the project lead can end it.")));
  else if (sv.phase === "calibration") box.append(await agreementBlock(setName, "calibration", lead ? [
    h("button.primary", { "data-needs": "complete", onclick: async () => { await attempt(() => api("/api/screening/calibration", { set: setName }), "Calibration finished"); render(); } }, "Discussed: move on"),
    h("button", { "data-needs": "complete", onclick: async () => { await attempt(() => api("/api/screening/calibration", { set: setName, another: true }), "Another round drawn"); render(); } }, "Do another calibration round")] : []));
  if (sv.phase === "sample") box.append(await agreementBlock(setName, "sample", lead ? [
    h("button.primary", { "data-needs": "complete", onclick: async () => {
      const pv = applyPreview[setName];
      if (!await confirmBox("Apply the agreement rule?", ["This fixes how the remaining records are screened, using the pre-specified kappa threshold" + (pl.ai_second_screener ? " and the AI stopping limit" : "") + ". It cannot be undone.",
        pv ? h("p", h("strong", "What will happen: "), pv) : null, applyCaution[setName] ? h("p", applyCaution[setName]) : null].filter(Boolean), "Apply the rule")) return;
      await attempt(() => api("/api/screening/apply", { set: setName }), "Rule applied"); render(); } }, "Apply the agreement rule"),
    pl.ai_second_screener ? h("button", { "data-needs": "complete", onclick: () => dropAi(setName) }, "The AI step cannot be finished") : null].filter(Boolean) : []));
  if (sv.phase === "screening") {
    const sr = sv.sample_result;
    if (sr) box.append(h("div.callout" + (sv.remainder_mode === "single" ? ".good" : ""),
      h("p", h("strong", "Agreement sample: "), `${num(sr.n)} records, agreement ${pct(sr.agreement)}, Cohen's kappa ${sr.kappa_shown || k2(sr.kappa)} (${kci(sr)}); threshold ${sr.threshold_shown || k2(sr.threshold)}: ${sr.kappa_rule}.`),
      sr.ai_rule ? h("p", h("strong", "AI second screener: "), aiRuleNow(sr.ai_rule) + ".") : null,
      h("p", h("strong", "From here: "), sv.remainder_mode === "single"
        ? `${nameOf(sv.plan.single_reviewer)} screens the remaining records alone` + (sv.ai_net_active ? `; any record they exclude that the AI would keep goes to ${nameOf(sv.plan.second_reviewer)} for a second look.` : ".")
        : "both reviewers screen every remaining record.")));
    const c = sv.counts;
    box.append(h("div.stat", h("div", h("b", num(c.forward || 0)), h("span", "sent to full text")), h("div", h("b", num(c.excluded || 0)), h("span", "excluded")),
      h("div", h("b", num((c.pending || 0) + (c.not_open || 0))), h("span", "still to screen")), c.conflict ? h("div", h("b", num(c.conflict)), h("span", "to decide together")) : null));
    if (sv.sample) box.append(h("details", h("summary", "Agreement on the sample, in detail"), await agreementBlock(setName, "sample", [])));
    if (sv.remainder_mode === "dual" && sv.sample) box.append(h("details", h("summary", "Agreement on the records outside the sample"), await agreementBlock(setName, "remainder", [])));
    else if (sv.remainder_mode === "dual" && !sv.sample) box.append(h("details", h("summary", "Agreement between the two reviewers"), await agreementBlock(setName, "remainder", [])));
  }
  if (lead && (sv.counts.conflict || 0) > 0) box.append(h("button.primary", { onclick: () => conflictsModal("ta") }, "Decide the disagreements together"));
  if (sv.phase === "screening" && sv.verify) box.append(verifyBlock(setName, sv));
  return box;
}

// ------------------------------------------------------------------------------------------------ the verification sample
// A second person screens, without seeing any earlier decision, a random sample of the records that left the review on
// one reviewer's vote (evaluation.py). The lead draws it, once; everyone sees how far it has got; the lead sees the
// figures when its verifier has finished.
const WILSON_Z2 = 1.959964 * 1.959964;
const upperIfNone = (n) => (n > 0 ? WILSON_Z2 / (n + WILSON_Z2) : null);      // the 95% upper limit when 0 of n are sent on

function verifyBlock(setName, sv) {
  const v = sv.verify, lead = S.app.project.is_lead, me = S.app.project.me;
  const wrap = h("section.verify", { "aria-label": "Verification sample" }, h("h3", { tabindex: "-1", style: { margin: "16px 0 6px" } }, "Verification sample"));
  if (v.drawn) {
    const when = new Date(v.drawn_at).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
    // the lead and the verifier see how far it has got; anyone else only that it is being done (its size would tell the
    // single reviewer how many of their exclusions the AI kept)
    const state = v.ended ? "Ended by the project lead before every record was screened." : v.complete ? "Finished." : "Still being screened.";
    if (v.total === undefined) wrap.append(h("p.small", `Drawn on ${when} and screened by ${nameOf(v.reviewer)} without seeing any earlier decision. ${state}`));
    else wrap.append(h("p.small", `Drawn on ${when}: ${plural(v.total, "record")}, screened by ${nameOf(v.reviewer)} without seeing any earlier decision. ${num(v.done)} of ${num(v.total)} done.${v.ended ? " " + state : v.complete ? " Finished: its votes are fixed." : ""}`),
      bar(v.done, v.total, "Verification sample: progress"));
    if (!v.complete) wrap.append(h("p.small.muted", { style: { marginTop: "8px" } }, lead
      ? "Nothing changes until the sample is finished: only then does any record it sends on go to full text, and only then do the figures appear here. Until then nobody, the lead included, is shown how its votes compare with the earlier ones, and the pages that show what became of records are closed to its verifier."
      : "Nothing changes until the sample is finished: only then does any record it sends on go to full text."));
    if (lead && !v.complete) wrap.append(h("p.small", h("button.link", { onclick: () => endSample(setName, v) }, "The sample cannot be finished")));
    if (lead && v.ended && v.ended_why) wrap.append(h("p.small.muted", `Reason given: ${v.ended_why}`));
    if (lead && v.from_log) wrap.append(h("p.small.muted", "The project's state file no longer holds this sample (an older copy of it may have been put back). Nothing is lost: ReviewDesk is using the copy the audit log kept when the sample was drawn, with the same records, in the same order, for the same person."));
    if (v.results) wrap.append(verifyResults(v));
    return wrap;
  }
  // not drawn: an option the lead may never take, so it is kept folded away until they open it
  const fold = h("details.verify", h("summary", "Verification sample (optional): check the records that left on one vote"));
  if (v.lost_votes) {
    fold.open = true;
    fold.append(h("div.callout.warn", { role: "alert", style: { marginTop: "8px" } }, h("p", `This project holds ${plural(v.lost_votes, "vote")} of a verification sample, but the sample itself is no longer in the project: neither its state file nor the project lead's audit log holds it (Data problems and the sync messages may say more). Those votes count for nothing while it is missing, and the pages that show what became of records stay closed to whoever cast them. If nothing else has changed, drawing the sample again with the same numbers and the same person gives the same sample, and the votes count again.`)));
  }
  if (v.why_not) { fold.append(h("p.small.muted", { style: { marginTop: "8px" } }, v.why_not)); return fold; }
  const one = nameOf(v.single_reviewer);
  const whole = (x) => (/^\d+$/.test(String(x).trim()) ? Number(x) : NaN);      // a whole number, written out ("1e1" is not one)
  const size = h("input#vs-size", { type: "number", min: 1, max: v.population, step: 1, value: Math.min(v.population, 300), style: { width: "110px" } });
  const who = h("select#vs-who", { "aria-describedby": "vs-caution" }, v.candidates.map((c) => h("option", { value: c, selected: c === v.reviewer }, nameOf(c))));
  if (!v.reviewer) who.prepend(h("option", { value: "", selected: true }, "Choose who screens it"));
  const pool = () => (v.decoy_pools || {})[who.value] ?? v.decoy_pool ?? 0;
  const mix = h("input#vs-mix", { type: "number", min: 0, max: pool(), step: 1, value: Math.min(pool(), 40), style: { width: "110px" } });
  const says = h("p.small.muted", { role: "status" });
  const caution = h("p.small#vs-caution", { hidden: true });
  const tell = () => {
    const n = whole(size.value), m = mix.value.trim() === "" ? 0 : whole(mix.value);
    mix.max = pool();
    const why = (v.cautions || {})[who.value];
    caution.hidden = !why;
    caution.replaceChildren(why ? h("strong", "A caution about this choice: ") : "", why ? `${why} If someone else in the team can screen the sample, choose them, and say in your methods who screened it.` : "");
    if (!(n >= 1 && n <= v.population)) { says.textContent = `Choose a whole number between 1 and ${num(v.population)} for the records to check.`; return; }
    if (!(m >= 0 && m <= pool())) { says.textContent = `Between 0 and ${num(pool())} records can be mixed in: ${one} sent on that many which ${who.value ? nameOf(who.value) : "the person chosen"} has not screened.`; return; }
    says.textContent = (n === v.population ? "Every one of these records would be checked. " : `If ${who.value ? nameOf(who.value) : "the person chosen"} would send on none of the ${num(n)}, the share of such records that should have gone on is unlikely to be above ${pct(upperIfNone(n))} (the upper 95% limit), which is about ${num(Math.round(upperIfNone(n) * (v.population - n)))} of the ${num(v.population - n)} not checked. `)
      + `${plural(n + m, "record")} to screen in all.`;
  };
  [size, mix, who].forEach((x) => x.addEventListener("input", tell));
  tell();
  fold.append(
    h("p.small", { style: { marginTop: "8px" } }, `${one} has screened every record. ${plural(v.population, "record")} left the review on their vote alone${v.ai_net ? " (the AI would not keep them either)" : ""}, and nothing else in the review looks at ${v.population === 1 ? "it" : "them"} again. A verification sample measures how many should have gone on: a second person screens a random sample of them, mixed with some records ${one} sent on, without seeing any earlier decision or which kind each record is. When the sample is finished, any sampled record they would send on goes to full text.`),
    h("div.row", { style: { alignItems: "flex-end", gap: "14px" } },
      h("label.f", { for: "vs-size" }, h("span", `Records to check (of ${num(v.population)})`), size),
      h("label.f", { for: "vs-mix" }, h("span", `Records ${one} sent on, to mix in`), mix),
      h("label.f", { for: "vs-who" }, h("span", "Screened by"), who)),
    caution, says,
    h("div.row", { style: { marginTop: "8px" } }, h("button", { onclick: async () => {
      const n = whole(size.value), m = mix.value.trim() === "" ? 0 : whole(mix.value);
      if (!(n >= 1 && n <= v.population)) { toast(`Choose a whole number between 1 and ${num(v.population)} for the records to check.`, true); size.focus(); return; }
      if (!(m >= 0 && m <= pool())) { toast(`Between 0 and ${num(pool())} records can be mixed in.`, true); mix.focus(); return; }
      if (!who.value) { toast("Choose who screens the verification sample.", true); who.focus(); return; }
      const name = nameOf(who.value);
      if (!await confirmBox("Draw the verification sample?", [
        `${plural(n, "record")} will be drawn at random from the ${num(v.population)}${m ? `, mixed with ${plural(m, "record")} ${one} sent on,` : ""} and given to ${name} to screen.`,
        "The sample is drawn once, from a recorded seed: it cannot be drawn again or changed. Use the numbers your protocol gives.",
        `Until ${name} has finished it, nothing it finds changes any record, nobody is shown how its votes compare, and ReviewDesk closes to ${name} the pages that show what became of records (Review full texts, Extract data, Rate certainty, the audit log and every download). Tell them before you draw it.`,
        "Everyone in the team must use ReviewDesk 1.3.1 or later: an older version would not know these votes."], "Draw the sample")) return;
      await attempt(() => api("/api/screening/verify", { set: setName, size: n, decoys: m, reviewer: who.value, as: me }), "Verification sample drawn");
      await render();
      document.querySelector("section.verify h3")?.focus();       // (the form is gone: on to what replaced it)
    } }, "Draw the verification sample")));
  return fold;
}

// A sample its verifier cannot finish (they have left, say) would keep its votes without effect, and the pages that
// show earlier decisions closed to them, for good. The lead can end it as it stands; every output then says so.
function endSample(setName, v) {
  modal((box, close) => {
    const why = h("input", { type: "text", placeholder: "e.g. the verifier left the team on 3 March", "aria-label": "Why the sample cannot be finished" });
    box.append(h("h2", "End the verification sample as it stands?"),
      h("p", `Use this only if ${nameOf(v.reviewer)} cannot finish the sample. Their votes so far (${num(v.done)} of ${num(v.total)}) then take effect: any sampled record they would send on goes to full text, the records they did not reach count as not checked, and the methods text says that the sample was ended, when and why. It cannot be undone, and the sample cannot be drawn again.`),
      h("p", "You have not been shown how its votes compare with the earlier ones, and ending it is not a way to see them sooner: report it as a departure from your protocol."),
      h("label.f", h("span", "Why it cannot be finished"), why),
      h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.danger", { onclick: async () => {
        if (why.value.trim().length < 3) { toast("Say why the sample cannot be finished.", true); why.focus(); return; }
        await attempt(() => api("/api/screening/verify/end", { set: setName, reason: why.value.trim(), as: S.app.project.me }), "Verification sample ended");
        close(); render(); } }, "End it as it stands")));
  });
}

function verifyResults(v) {
  const r = v.results, s = r.sent_on, ft = r.full_text, two = nameOf(v.reviewer), one = nameOf(v.single_reviewer);
  const ci = (x) => (x.ci ? `95% CI ${pct(x.ci[0])} to ${pct(x.ci[1])}` : "");
  const open = ft.pending + ft.conflict;
  if (!s.n) return h("div.callout", { style: { marginTop: "10px" } }, h("p", h("strong", "Result: "), "none of the sampled records was screened, so there are no figures."));
  const lines = [h("p", h("strong", "Result: "), `${two} would have sent on ${num(s.k)} of the ${plural(s.n, "sampled record")}${r.ended ? " they screened" : ""} (${pct(s.rate)}; ${ci(s)}).`
    + (r.decoys.n ? ` Of the ${plural(r.decoys.n, "record")} ${one} had sent on, ${two} sent on ${num(r.decoys.sent_on)}.` : ""))];
  if (s.k) lines.push(h("p", `${s.k === 1 ? "That record has" : "Those records have"} gone to full text: ${num(ft.included)} included, ${num(ft.excluded)} excluded`
    + (ft.other ? `, ${num(ft.other)} not retrieved, awaiting classification or ongoing` : "") + (open ? `, ${num(open)} still to decide` : "") + "."));
  if (r.not_sampled && r.included) lines.push(h("p", `Among the ${plural(r.not_sampled, "such record")} not checked, that suggests about ${num(Math.round(s.projected.records))} a second reviewer would have sent on (95% CI ${num(Math.round(s.projected.low))} to ${num(Math.round(s.projected.high))}) and about ${num(Math.round(r.included.projected.records))} that would have been included (95% CI ${num(Math.round(r.included.projected.low))} to ${num(Math.round(r.included.projected.high))}).`));
  else if (r.not_sampled && s.k) lines.push(h("p.small.muted", "The estimate of studies missed appears when those records have their full-text decisions."));
  lines.push(h("p.small.muted", "The methods summary (PRISMA and reports) says this in full, and the evaluation data has every record of the sample."));
  return h("div.callout", { style: { marginTop: "10px" } }, lines);
}

// ------------------------------------------------------------------------------------------------ time recording
// Shown wherever a person screens while the lead has time recording on: what is saved, and their own choice.
// changed(now): called with {project, mine} after the person's choice is saved; without it the page is drawn again (a
// page with work not yet saved passes one, and puts itself right without being redrawn).
export function timingNote(t, changed) {
  return h("p.small", { style: { margin: 0 } }, h("strong", "Time recording is on. "),
    t.mine ? "With each of your votes, ReviewDesk saves how long the record was on screen. " : "Your own votes are left out, as you chose. ",
    h("button.link", { onclick: () => timingBox(t, changed) }, "What is saved, and your choice"));
}

function timingBox(t, changed) {
  const me = S.app.project.me;
  modal((box, close) => {
    box.append(h("h2", "Time recording"),
      h("p", "The project lead has switched on time recording, to measure how long screening takes. With each vote ReviewDesk saves:"),
      h("ul", h("li", "how long the record was on screen before the vote;"), h("li", "how much of that time this page was the tab in front;"),
        h("li", "the longest stretch of that time with no key pressed and the pointer still (it shows when someone was away from the keyboard); and"),
        h("li", "how many times the record was shown before the vote.")),
      h("p", "Nothing else is measured: not which keys you press, not where the pointer is, nothing between records and nothing outside this page. The times are saved with your votes in the project folder, and leave ReviewDesk only in the evaluation data, which the project lead alone can download."),
      h("p", h("strong", "It is your choice. "), "If you would rather your votes were not timed, leave them out: nothing else changes, your votes count as before, and you can change your mind whenever you like. The audit log notes the choice, so that whoever studies the times knows why some votes have none."),
      h("p.small.muted", t.mine ? "At present the times of your votes are saved." : "At present your votes are left out: no times are saved with them."),
      h("div.row.end", h("button", { onclick: async () => {
        const now = await attempt(() => api("/api/timing/mine", { on: !t.mine, as: me }), t.mine ? "Your votes are no longer timed" : "The times of your votes are saved again");
        close();
        if (changed) changed(now); else render();
      } }, t.mine ? "Leave my votes out" : "Save the times of my votes again"), h("button.primary", { onclick: close }, "Close")));
  });
}

// The lead's part: switching time recording on or off, and where the data for a study of the screening is.
function evaluationPanel(o) {
  const t = o.timing || {};
  const box = h("section.panel", { "aria-labelledby": "eval-h" }, h("h2#eval-h", "Studying how the review was screened"),
    h("p.muted", "For the project lead, if the screening itself is to be evaluated: how accurate it was, how long it took, what each step added. Recording times and downloading the data change nothing about how records are screened. (A verification sample, when there is one to draw, is in the record set's panel above.)"));
  box.append(h("h3", { style: { margin: "10px 0 6px" } }, "Time recording"),
    h("p.small", t.project
      ? "On: with each vote, ReviewDesk saves how long the record was on screen. Everyone sees a notice on the screening pages and can leave their own votes out."
      : "Off. When it is on, ReviewDesk saves with each vote how long the record was on screen (and how much of that time the page was in front), so that the time screening took can be reported from what was measured. Everyone is told on the screening pages, and each person can leave their own votes out."),
    h("div.row", h("button", { onclick: async () => {
      if (t.project) {
        if (!await confirmBox("Switch time recording off?", ["From now on no times are saved with votes. The times already saved stay in the project."], "Switch it off")) return;
      } else if (!await confirmBox("Switch time recording on?", [
        "From now on ReviewDesk saves, with each vote, how long the record was on screen. It applies to everyone in the project who has not left their own votes out.",
        "This is data about the people in your team. Tell them before you switch it on, and check what your institution asks for before timing people's work (for a study of the screening: its ethics and data-protection rules, and each person's agreement).",
        "Everyone in the team must use ReviewDesk 1.3.1 or later: an older version would save votes without their times."], "Switch it on")) return;
      await attempt(() => api("/api/settings", { changes: { screening: { record_time: !t.project } } }), t.project ? "Time recording is off" : "Time recording is on");
      render();
    } }, t.project ? "Switch time recording off" : "Switch time recording on")));
  box.append(h("h3", { style: { margin: "16px 0 6px" } }, "The data"),
    h("p.small", "The evaluation data is one download with every record's outcome at each stage, both AI models' answers, every vote (with its times, when they are recorded) and each verification sample. It is on the PRISMA and reports page, for the project lead only."),
    h("div.row", h("button", { onclick: () => go("report") }, "Go to PRISMA and reports")));
  return box;
}

// The AI rule's result as it applies from now on (it is recorded in the past tense, for the methods).
function aiRuleNow(text) {
  return String(text).replace("excluded but the AI would keep went to", "excludes but the AI would keep goes to")
    .replace("was dropped and both reviewers screened every record", "is dropped and both reviewers screen every record")
    .replace("the AI step was not needed, because both reviewers screened every record", "the AI step is not needed, because both reviewers screen every record");
}

function phaseName(ph) {
  return { not_started: "Not started", calibration: "Calibration", sample: "Agreement sample", screening: "Screening" }[ph] || ph;
}

const applyPreview = {};
const applyCaution = {};

async function agreementBlock(setName, scope, actions) {
  const a = await api(`/api/agreement?set=${setName}&scope=${scope}`);
  const [ra, rb] = a.reviewers;
  const st = a.stats;
  const wrap = h("div");
  const label = { calibration: "Calibration", sample: "Agreement sample", remainder: (S.ov.sets[setName] || {}).sample ? "Records screened by both (outside the sample)" : "Records screened by both", fulltext: "Full texts" }[scope];
  wrap.append(h("h3", { style: { margin: "10px 0 6px" } }, label),
    h("div.row", h("div", { style: { minWidth: "min(260px, 100%)" } }, h("div.small", `${nameOf(ra)}: ${num(a.done_a)} of ${num(a.records)}`), bar(a.done_a, a.records, `${nameOf(ra)}: progress`)),
      h("div", { style: { minWidth: "min(260px, 100%)" } }, h("div.small", `${nameOf(rb)}: ${num(a.done_b)} of ${num(a.records)}`), bar(a.done_b, a.records, `${nameOf(rb)}: progress`))));
  if (!a.complete) {
    wrap.append(h("p.muted.small", { style: { marginTop: "10px" } }, scope === "remainder"
      ? "Agreement on these records is shown when both of you have screened all of them, so that nothing here hints at the other's votes."
      : "The comparison appears when both of you have finished. Neither of you can see the other's votes before then."));
    actions.forEach((b) => { if (b.dataset.needs === "complete") b.disabled = true; });
  } else {
    wrap.append(h("div.stat", h("div", h("b", pct(st.agreement)), h("span", "agreement")),
      h("div", h("b", st.kappa_shown || k2(st.kappa)), h("span", `Cohen's kappa (${kci(st)})`)),
      h("div", h("b", num(st.both_yes)), h("span", "both sent on")), h("div", h("b", num(st.only_a + st.only_b)), h("span", "disagreements"))),
      a.threshold !== undefined ? ruleCallout(setName, a, st) : null,
      scope === "sample" && (S.ov.sets[setName] || {}).phase === "sample" ? smallSample(setName, a, st) : null,
      st.note ? h("p.small", st.note) : null,
      h("p.small.muted", a.note + " " + (st.method || "")),
      a.disagreements.length ? h("details", { open: scope === "calibration" }, h("summary", a.disagreements.length === 1 ? "The disagreement" : `The ${num(a.disagreements.length)} disagreements`), h("div.tablewrap", h("table.t",
        h("thead", h("tr", h("th", "Record"), h("th", nameOf(ra)), h("th", nameOf(rb)))),
        h("tbody", a.disagreements.map((d) => h("tr", h("td.small", h("span.muted", d.id + " "), d.title),
          h("td.small", vote(d[ra])), h("td.small", vote(d[rb])))))))) : null);
  }
  if (actions.length) wrap.append(h("div.row", { style: { marginTop: "12px" } }, actions));
  return wrap;
}

// What applying the rule will do, before the lead applies it: kappa first, then (if planned) the AI stopping limit.
function ruleCallout(setName, a, st) {
  const pv = a.ai_preview;
  // kappa with enough decimals to show which side of the threshold it is on (0.795 against 0.80, never "0.80")
  const kap = st.kappa_shown || k2(st.kappa), thr = a.threshold_shown || k2(a.threshold);
  let text, good;
  if (!a.meets_threshold) {
    text = `Kappa ${kap} is below the threshold of ${thr}: under the rule, both reviewers screen every remaining record.`;
    good = false;
  } else if (pv && pv.missing) {
    text = `Kappa ${kap} reaches the threshold of ${thr}. The AI second screener has not yet seen ${plural(pv.missing, "record")}; run it to the end (AI second screener page) before applying the rule.`;
    good = false;
  } else if (pv && pv.over) {
    text = `Kappa ${kap} reaches the threshold of ${thr}, but the AI would keep ${pct(pv.keep_rate)} of records, above the ${pct(pv.limit, 0)} stopping limit: under the rule, the AI step is dropped and both reviewers screen every remaining record.`;
    good = false;
  } else if (pv) {
    text = `Kappa ${kap} reaches the threshold of ${thr}, and the AI would keep ${pct(pv.keep_rate)} of records, within the ${pct(pv.limit, 0)} limit: under the rule, one reviewer screens the rest, with the AI second screener behind them.`;
    good = true;
  } else if (((S.ov.sets[setName] || {}).plan || {}).ai_second_screener) {
    text = `Kappa ${kap} reaches the threshold of ${thr}: under the rule, one reviewer screens the rest, unless the AI keeps more records than its stopping limit allows (then both screen everything). The lead applies the rule.`;
    good = true;
  } else {
    text = `Kappa ${kap} reaches the threshold of ${thr}: under the rule, one reviewer screens the rest.`;
    good = true;
  }
  applyPreview[setName] = text;
  return h("div.callout" + (good ? ".good" : ".warn"), h("p", text));
}

// A small agreement sample, or one in which either reviewer sent on very few records, gives a kappa that chance alone
// can push high or low. Said plainly, with the numbers, before the lead applies the rule; it does not stop the rule.
const SMALL_SAMPLE = 50, FEW_SENT_ON = 5;
function smallSample(setName, a, st) {
  applyCaution[setName] = "";
  const [ra, rb] = a.reviewers;
  const n = st.n || 0, ya = st.a_yes || 0, yb = st.b_yes || 0;
  const small = n < SMALL_SAMPLE, few = Math.min(ya, yb) < FEW_SENT_ON;
  if (!n || (!small && !few)) return null;
  const why = small && few ? "so few records, and so few sent on," : small ? "so few records" : "so few records sent on";
  const text = `Caution: the agreement sample has ${small ? "only " : ""}${plural(n, "record")}, and ${nameOf(ra)} sent on ${num(ya)} of them `
    + `and ${nameOf(rb)} ${num(yb)} (Include or Maybe). With ${why} kappa can come out high or low largely by chance, so it says little `
    + `about how well the two of you agree (${kci(st)}). Decide whether this is enough to let one reviewer screen the rest alone. `
    + "You can still apply the rule; if you do, report the sample's size and kappa's confidence interval with the result.";
  applyCaution[setName] = text;
  return h("div.callout.warn", { role: "note" }, h("p", text));
}

function vote(v) {
  if (!v) return "";
  const lab = TA_LABEL[v.d] || v.d;
  return [h("span.pill." + (v.d === "include" ? "include" : v.d === "maybe" ? "maybe" : "exclude"), lab), v.reason ? " " + v.reason : "", v.note ? h("div.muted", v.note) : null];
}

export async function conflictsModal(stage) {
  let r;
  try { r = await api("/api/conflicts"); }
  catch (e) { toast(e.message, true); return; }       // (closed to someone screening a verification sample, with the reason)
  const list = r.conflicts.filter((c) => c.stage === stage);
  const me = S.app.project.me;
  let changed = false;
  modal((box, close) => {
    box.append(h("h2", "Decide together"), h("p.muted", "Talk each one through with the other reviewer, then record what you agreed. If you still cannot agree, send it to full text."));
    for (const c of list) {
      const note = h("input", { type: "text", placeholder: "What you agreed, and why (optional)", "aria-label": `What you agreed on ${c.id}, and why` });
      const panel = h("div.panel", { role: "group", "aria-label": `${c.id}: ${c.title}` });
      const save = async (decision) => {
        await attempt(() => api("/api/resolve", { record: c.id, stage, decision, note: note.value, as: me }), "Recorded");
        changed = true;
        panel.replaceChildren(h("p", h("span.muted", c.id + " "), c.title), h("p.small", "Agreed: ", h("strong", decision === "include" ? "Send to full text" : "Exclude")));
        panel.classList.add("settled");
        const next = [...box.querySelectorAll(".panel:not(.settled) input, .panel:not(.settled) button")][0] || box.querySelector("button.primary");
        if (next) next.focus();
      };
      panel.append(h("p", h("span.muted", c.id + " "), h("strong", c.title)), recordDetails(c),
        h("div.small", Object.entries(c.votes).map(([w, v]) => h("div", `${r.names[w]}: `, vote(v)))), note,
        h("div.row", { style: { marginTop: "8px" } },
          h("button", { onclick: () => save("include"), "aria-label": `Send ${c.id} to full text` }, "Send to full text"),
          h("button", { onclick: () => save("exclude"), "aria-label": `Exclude ${c.id}` }, "Exclude")));
      box.append(panel);
    }
    box.append(h("div.row.end", h("button.primary", { onclick: close }, "Done")));
  }, () => { if (changed) render(); });
}

// What the two reviewers need in front of them to decide together: the citation and the abstract.
export function recordDetails(c) {
  const cite = [c.authors ? shortAuthors(c.authors) : "", c.journal, c.year, c.pubtype].filter(Boolean).map((x) => String(x).replace(/[.\s]+$/, "")).join(". ");
  return h("div", cite ? h("p.small.muted", cite + ".") : null,
    c.abstract ? h("details", h("summary.small", "Abstract"), h("div.abs-small", splitAbstract(c.abstract).map((x) => h("p", x)))) : h("p.small.muted", "No abstract."));
}

// ------------------------------------------------------------------------------------------------ the screening page
export async function room(el, args) {
  let [task, setName] = [args[0] || "screen", args[1] || "main"];
  if (!["calibration", "sample", "screen", "second", "verify"].includes(task)) task = "screen";
  const p = S.app.project, s = p.settings;
  const stage = task === "calibration" ? "cal" : task === "verify" ? "vs" : "ta";
  const label = { calibration: "Calibration", sample: "Agreement sample", screen: "Title and abstract screening", second: "Second look", verify: "Verification sample" }[task] || task;
  // while the lead has time recording on (and this person has not left their own votes out): how long each record is
  // on screen before its vote (lib.js stopwatch; timing.py)
  const timing = (S.ov || {}).timing || {};
  let watch = null;
  const timeBox = h("div.callout", { role: "note", hidden: true });
  // put the page right with what is true now: at the start, after this person's own choice in the dialog, and when a
  // vote's answer says that recording has changed since the page was opened (the lead switched it, or another tab)
  const applyTiming = (now) => {
    if (now) { timing.project = !!now.project; timing.mine = !!now.mine; }
    if (timing.mine && !watch) { watch = stopwatch({ scope: "scr:" + stage }); if (shown) watch.show(shown); }
    else if (!timing.mine && watch) { watch.stop(); watch = null; }
    timeBox.hidden = !timing.project;
    timeBox.replaceChildren(timing.project ? timingNote(timing, applyTiming) : "");
  };
  let items = (await api(`/api/queue?task=${task}&set=${setName}`)).items;
  // the queue is in screening order; open at the first record not yet voted on, so ← goes back to earlier votes
  let i = items.findIndex((x) => !x.done);
  if (i < 0) i = 0;
  let shown = null;                 // the record on screen: a key press votes on this one, and only once it is shown
  const cache = new Map();
  const getRec = async (id) => { if (!cache.has(id)) cache.set(id, api("/api/record?id=" + encodeURIComponent(id))); return cache.get(id); };
  const prog = h("div.small.muted");
  const progBar = h("div.bar", { role: "progressbar", "aria-label": "Records done", "aria-valuemin": 0, "aria-valuemax": 100 }, h("i"));
  const reading = h("div.reading");
  const note = h("textarea", { placeholder: "A note to yourself or for the discussion (optional)", "aria-label": "Note", style: { minHeight: "70px" }, "aria-describedby": "scr-note-unsaved" });
  // a note is saved with the vote: one typed and not yet voted on is kept for its record when moving to another
  let noteKey = null, noteBase = "", noteVoted = false;
  const noteSays = h("p.small.unsaved-note#scr-note-unsaved", { hidden: true });
  const noteMark = () => {
    const off = !!noteKey && drafts.has(noteKey);
    note.classList.toggle("unsaved", off);
    noteSays.hidden = !off;
    noteSays.textContent = off ? (noteVoted ? "Note not saved yet: vote again to save it with your vote." : "Note not saved yet: it is saved with your vote.") : "";
  };
  note.addEventListener("input", () => { if (noteKey) drafts.set(noteKey, note.value, noteBase); noteMark(); });
  const btn = Object.fromEntries(["include", "maybe", "exclude"].map((d) => [d, h(`button.decide.${d}`, { onclick: () => decide(d) }, TA_LABEL[d], h("kbd", d[0].toUpperCase()))]));
  const posLabel = h("span.small.muted");
  const said = h("div.sr-only", { role: "status", "aria-live": "polite", "aria-atomic": "true" });   // read out after a vote
  let busy = false;

  const crit = s.criteria;
  const side = h("aside.side",
    task === "second" ? h("div.callout", h("p.small", "These records were excluded by the single reviewer but the AI would keep them. Screen each one as you would any other record.")) : null,
    task === "verify" ? h("div.callout", h("p.small", "A random sample of the records screened so far, for a second person to check. Screen each one as you would any other record: no earlier decision is shown, and your vote here is your own. You can go back (←) and change a vote until you vote on the last record: that finishes the sample, and its votes are then fixed.")) : null,
    timeBox,
    h("h3", "Review question"), h("p", s.question || s.title),
    crit.include.length ? [h("h3", "Include"), h("ul", crit.include.map((x) => h("li", x)))] : null,
    crit.exclude.length ? [h("h3", "Exclude"), h("ul", crit.exclude.map((x) => h("li", x)))] : null,
    crit.rules.length ? [h("h3", "Borderline cases"), h("ol", crit.rules.map((x) => h("li", x)))] : null,
    h("h3", "Note"), note, noteSays,
    h("p.small.muted", { style: { marginTop: "14px" } }, "Keys: I include, M maybe, E exclude, ← previous, → next. Pressed the wrong key? Press ← and vote again: your corrected vote is the one that counts."));

  const top = h("div.screen-top", h("button", { onclick: () => go("screening"), "aria-label": "Back to the screening page" }, "← Back"),
    h("div.grow", h("h1.title", `${label}${setName !== "main" ? " (" + SETS[setName] + ")" : ""}`), prog), progBar);
  const keys = h("div.screen-keys", btn.include, btn.maybe, btn.exclude, h("div.spacer"), posLabel,
    h("button", { onclick: () => move(-1), "aria-label": "Previous record" }, "←"), h("button", { onclick: () => move(1), "aria-label": "Next record" }, "→"));
  const root = h("main.screen", top, h("div.screen-body", reading, side), keys, said);
  el.append(root);

  const updateProgress = () => {
    const done = items.filter((x) => x.done).length;
    prog.textContent = `${num(done)} of ${num(items.length)} done, ${num(items.length - done)} left`;
    const w = items.length ? (100 * done) / items.length : 0;
    progBar.firstChild.style.width = w + "%";
    progBar.setAttribute("aria-valuenow", Math.round(w));
  };

  // focus: after a vote the keyboard goes to the new record's heading, so a second Enter (on the vote button that was
  // pressed) cannot vote on the next record by accident; screen readers read the heading out
  async function show(focus = false) {
    shown = null;
    updateProgress();
    if (!items.length) { if (watch) watch.stop(); reading.replaceChildren(h("article", h("h2", "Nothing to screen here."), h("button", { onclick: () => go("screening") }, "Back to screening"))); return; }
    if (i >= items.length) {
      if (watch) watch.stop();            // no record is on screen now
      const end = h("h2.rec-title", { tabindex: "-1" }, "That is every record in this task.");
      reading.replaceChildren(h("article", end,
        h("p", "Thank you. Your decisions are saved. You can go back through them with the ← key, or return to the screening page."),
        h("button.primary", { onclick: () => go("screening") }, "Back to screening")));
      Object.values(btn).forEach((b) => { b.classList.remove("on"); b.setAttribute("aria-pressed", "false"); });
      posLabel.textContent = "";
      noteKey = null; note.value = ""; noteMark();
      if (focus) end.focus({ preventScroll: true });
      return;
    }
    const it = items[i];
    let r;
    try { r = await getRec(it.id); }
    catch (e) { cache.delete(it.id); if (watch) watch.stop(); reading.replaceChildren(h("article", h("div.callout.bad", h("p", e.message)), h("button", { onclick: () => show() }, "Try again"))); return; }
    if (items[i] !== it) return;
    const mine = r.mine[stage];
    Object.entries(btn).forEach(([d, b]) => { b.classList.toggle("on", !!mine && mine.d === d); b.setAttribute("aria-pressed", String(!!mine && mine.d === d)); });
    noteKey = `scr:${stage}:${it.id}`; noteBase = mine?.note || ""; noteVoted = !!mine;
    const kept = drafts.get(noteKey, noteBase);
    note.value = kept === undefined ? noteBase : kept;
    noteMark();
    posLabel.textContent = `${num(i + 1)} of ${num(items.length)}` + (mine ? `, you voted ${TA_LABEL[mine.d]}` : "");
    const abs = (r.abstract || "").trim();
    const head = h("h2.rec-title", { tabindex: "-1", html: highlight(r.title || "(no title)", s.highlight.include, s.highlight.exclude) });
    reading.replaceChildren(h("article", { "aria-label": `Record ${num(i + 1)} of ${num(items.length)}` },
      h("div.rid", h("span", r.id), r.found_in.length ? h("span", "Found in " + r.found_in.join(", ")) : null, r.doi ? h("a", { href: "https://doi.org/" + r.doi, target: "_blank", rel: "noopener" }, "DOI") : null),
      head,
      h("div.rec-meta", r.authors ? h("span", shortAuthors(r.authors)) : null, r.journal ? h("span.j", r.journal) : null, r.year ? h("span", r.year) : null, r.pubtype ? h("span", r.pubtype) : null),
      h("div.abstract", abs ? splitAbstract(abs).map((para) => h("p", { html: highlight(para, s.highlight.include, s.highlight.exclude) })) : h("p.none", "No abstract. Decide on the title; vote Exclude only if the title clearly fails a criterion.")),
      r.keywords ? h("div.kw", "Keywords: " + r.keywords.slice(0, 400)) : null));
    reading.scrollTop = 0;
    shown = it.id;
    // this record is on screen from now (the one before it was until now; the same one drawn again has not left it)
    if (watch) watch.show(it.id);
    if (focus) head.focus({ preventScroll: true });
    if (items[i + 1]) getRec(items[i + 1].id).catch(() => cache.delete(items[i + 1]?.id));
  }

  // The vote that finishes a verification sample fixes every vote in it (from then on its verifier can see what was
  // decided before), so it is asked for, not slipped into: a wrong key on the last record could not be put right.
  // The server asks for it too ("finish"): a second tab, or another computer, does not know how many are left.
  const askFinish = (d) => confirmBox("Finish the verification sample?", [
    `This is the last record. Your vote on it (${TA_LABEL[d]}) finishes the sample, and its votes are then fixed: they cannot be changed afterwards, because from that moment you can see what was decided on these records before.`,
    "To look over or change an earlier vote first, cancel and go back with ←."], `Vote ${TA_LABEL[d]} and finish`);
  const leaveFinished = (msg, bad = false) => {       // nothing is left to do here: its votes are fixed and the task is gone
    if (watch) watch.stop();
    busy = false;
    toast(msg, bad);
    go("screening");
  };
  async function decide(d, finish = false) {
    if (busy || i >= items.length || !shown || shown !== items[i].id) return;     // still loading: ignore the key
    busy = true;
    const it = items[i];
    const correcting = !!it.done;
    const last = task === "verify" && !it.done && items.filter((x) => !x.done).length === 1;
    if (last && !finish) {
      if (!await askFinish(d)) { busy = false; return; }
      finish = true;
    }
    try {
      const time = watch ? watch.read(shown) : undefined;
      const res = await api("/api/decide", { record: shown, stage, decision: d, note: note.value.trim(), as: p.me, ...(time ? { time } : {}), ...(finish ? { finish: true } : {}) });
      if (watch) watch.clear(it.id);        // (a vote that could not be saved keeps its time for the next try)
      if (res.timing && (!!res.timing.mine !== !!watch || !!res.timing.project !== !!timing.project)) applyTiming(res.timing);
      cache.delete(it.id);
      drafts.drop(`scr:${stage}:${it.id}`);
      it.done = true;
      if (res.sample_finished) { leaveFinished("Verification sample finished: its votes are fixed, and the pages that were closed to you are open again."); return; }
      keys.classList.remove("flash"); void keys.offsetWidth; keys.classList.add("flash");
      const firstOpen = items.findIndex((x) => !x.done);
      if (correcting) {
        // a corrected vote: on to the record after it, as when going through them with →
        i = i + 1 < items.length ? i + 1 : firstOpen >= 0 ? firstOpen : items.length;
      } else {
        const nxt = items.findIndex((x, k) => k > i && !x.done);
        i = nxt >= 0 ? nxt : firstOpen >= 0 ? firstOpen : items.length;
      }
      said.textContent = `Saved: ${TA_LABEL[d]}. ` + (i < items.length ? `Record ${num(i + 1)} of ${num(items.length)}.` : "That is every record in this task.");
      await show(true);
    } catch (e) {
      if (e.data && e.data.last_of_sample && !finish) {      // the others were voted on elsewhere: this one is the last
        busy = false;
        if (await askFinish(d)) await decide(d, true);
        return;
      }
      if (e.data && e.data.sample_finished) { leaveFinished(e.message, true); return; }     // finished elsewhere meanwhile
      toast(e.message, true);
    }
    busy = false;
  }
  async function move(step) {
    if (busy) return;                     // a vote is being saved: it moves on by itself (moving now would skip a record)
    i = Math.max(0, Math.min(items.length, i + step));
    await show();
    said.textContent = i < items.length ? `Record ${posLabel.textContent}.` : "That is every record in this task.";
  }

  const onKey = (e) => {
    if (!document.body.contains(root)) { document.removeEventListener("keydown", onKey); return; }
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT" || e.target.tagName === "SELECT" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector(".modal-back")) return;
    if (e.repeat) { e.preventDefault(); return; }       // a key held down votes once, not on every record that follows
    const k = e.key.toLowerCase();
    if (k === "i" || k === "1") decide("include");
    else if (k === "m" || k === "2") decide("maybe");
    else if (k === "e" || k === "3") decide("exclude");
    else if (k === "arrowleft") move(-1);
    else if (k === "arrowright") move(1);
    else return;
    e.preventDefault();
  };
  listen(document, "keydown", onKey);          // (taken off when the page is left)
  applyTiming();
  show();
}

function shortAuthors(a) {
  const xs = a.split(";").map((x) => x.trim()).filter(Boolean);
  return xs.length > 4 ? xs.slice(0, 3).join("; ") + "; et al." : xs.join("; ");
}

// Structured abstracts (BACKGROUND: ... METHODS: ...) read better as paragraphs.
function splitAbstract(t) {
  const parts = t.split(/\s(?=(?:BACKGROUND|INTRODUCTION|OBJECTIVES?|AIMS?|PURPOSE|METHODS?|DESIGN|SETTING|PARTICIPANTS|INTERVENTIONS?|MEASUREMENTS?|RESULTS?|FINDINGS|CONCLUSIONS?|INTERPRETATION|DISCUSSION|TRIAL REGISTRATION|IMPLICATIONS?)\s*:)/);
  return parts.length > 1 ? parts : [t];
}

// If the AI screening cannot be completed (the service is gone, say), the lead can drop it. That is the
// conservative choice: both reviewers then screen every record, as if the stopping rule had been met.
function dropAi(setName) {
  modal((box, close) => {
    const why = h("input", { type: "text", placeholder: "e.g. the AI service was withdrawn on 3 March", "aria-label": "Why the AI step cannot be finished" });
    box.append(h("h2", "Drop the AI step?"),
      h("p", "Use this only if the AI screening cannot be finished. Both reviewers will then screen every remaining record, and the methods summary will report that the AI step was dropped and why. It cannot be undone."),
      h("label.f", h("span", "Why it cannot be finished"), why),
      h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.danger", { onclick: async () => {
        if (!why.value.trim()) { toast("Say why the AI step cannot be finished.", true); return; }
        await attempt(() => api("/api/screening/apply", { set: setName, drop_ai: why.value.trim() }), "Rule applied without the AI step");
        close(); render(); } }, "Drop it: both screen everything")));
  });
}
