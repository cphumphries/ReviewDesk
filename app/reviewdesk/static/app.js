// ReviewDesk: the page shell, the routes, and the "what to do next" logic.
import { api, h, toast, modal, confirmBox, attempt, num, plural, setProjectKey, clearOldToast, carryToast, leavePage, SETS } from "./lib.js";
import * as core from "./views_core.js";
import * as search from "./views_search.js";
import * as scr from "./views_screen.js";
import * as ft from "./views_ft.js";
import * as rep from "./views_report.js";
import * as aiv from "./views_ai.js";
import * as xv from "./views_extract.js";
import * as wz from "./views_wizard.js";
import * as tl from "./views_timeline.js";
import * as ct from "./views_certainty.js";

// leaveGuard: the page has something not saved (asks before it is left); leaveSave: saves it ("Save and go");
// leaveText: [title, text] of that question, if the page says it its own way; flushOnLeave: sends what is typed but
// not yet sent, and tries again what could not be saved, before the page is left
export const S = { app: null, ov: null, ready: null, leaveGuard: null, leaveSave: null, leaveText: null, flushOnLeave: null };

// The pages, as the side bar lists them. A page's steps (workflow.py) decide its tick; the page where the review's
// current step is done is marked "Next step". The steps carry the only numbers ("step 4 of 19"), so the pages have none.
const NAV = [
  { r: "overview", label: "Overview", icon: "◎" },
  { r: "timeline", label: "The steps, in order", icon: "≡" },
  { r: "searches", label: "Plan and log searches", steps: ["searches"] },
  { r: "import", label: "Import records", steps: ["import"] },
  { r: "duplicates", label: "Check duplicates", steps: ["duplicates"] },
  { r: "screening", label: "Screen titles and abstracts", steps: ["calibration", "sample", "agreement_rule", "screen"] },
  { r: "fulltext", label: "Review full texts", steps: ["get_fulltexts", "fulltext"] },
  { r: "extract", label: "Extract data", steps: ["extract", "rob"] },
  { r: "certainty", label: "Rate certainty (GRADE)", steps: ["certainty"] },
  { r: "report", label: "PRISMA and reports", steps: ["report"] },
  { gap: true },
  { r: "ai", label: "AI second screener", icon: "◇" },
  { r: "setup", label: "Project set-up", icon: "⚙" },
  { r: "settings", label: "My settings", icon: "☺" },
];

const VIEWS = {
  start: core.start, overview: core.overview, setup: core.setup, settings: core.settings,
  searches: search.searches, import: search.importer, duplicates: search.duplicates,
  screening: scr.hub, screen: scr.room, fulltext: ft.list, ft: ft.room, ai: aiv.ai, report: rep.report, extract: xv.list, x: xv.room,
  wizard: wz.wizard, timeline: tl.timeline, certainty: ct.list, c: ct.room,
};

// ------------------------------------------------------------------------------------------------ where the review is
// Each step's status as the pages show it: the readiness checks' status, with two corrections that timeline.py makes
// too (shown()), so that the Overview's card, the side bar and the steps page always agree:
//  - the agreement sample is done once both have finished it (the agreement rule is then under way);
//  - rating certainty is not done while extraction or risk of bias is still open (GRADE builds on both).
export function shownSteps() {
  const rd = S.ready;
  if (!rd) return [];
  const st = Object.fromEntries(rd.steps.map((x) => [x.id, x.status]));
  const fix = (x) => {
    let s = x.status;
    if (x.id === "sample" && s === "now" && ["now", "blocked"].includes(st.agreement_rule)) s = "done";
    if (x.id === "certainty" && s === "done" && [st.extract, st.rob].some((v) => v && v !== "done")) s = "now";
    return s === x.status ? x : { ...x, status: s };
  };
  return rd.steps.map(fix);
}

// The step the review is at: the first one that is under way or can start now (a step done outside ReviewDesk, such as
// registering the protocol, only when nothing else is open). The strip's "You are here" and the steps page use the same
// rule (views_timeline.js, currentOf; timeline.py).
export function currentStep() {
  const steps = shownSteps();
  const open = (x) => x.status === "now" || x.status === "blocked";
  let k = steps.findIndex((x) => open(x) && !x.outside);
  if (k < 0) k = steps.findIndex(open);
  return k < 0 ? null : { ...steps[k], n: k + 1, total: steps.length };
}

function navStatus(n) {
  if (!S.ready) return legacyStatus(n.r);
  const cur = currentStep();
  if (cur && cur.route === n.r) return "now";
  if (!n.steps) return "";
  const sts = shownSteps().filter((x) => n.steps.includes(x.id));
  return sts.length && sts.every((x) => x.status === "done") ? "done" : "";
}

export async function refresh() {
  S.app = await api("/api/app");
  setProjectKey(S.app.project ? S.app.project.key : "");
  if (S.app.project && S.app.project.member) {
    const ready = wz.loadReadiness();          // what is missing (views_wizard.js), asked for alongside the overview
    try { S.ov = await api("/api/overview"); }
    catch (e) { if (e.data && (e.data.damaged || e.status === 409)) throw e; S.ov = null; }
    S.ready = await ready;
  } else { S.ov = null; S.ready = null; }
}

// (what the action that called go() just said goes with it to the page it opens; anything older is cleared there)
export function go(route) { carryToast(); location.hash = "#/" + route; }

// Without the readiness checks (they could not be read): from the overview's counts.
function legacyStatus(r) {
  const o = S.ov;
  if (!o) return "";
  const main = o.sets.main || {};
  const done = { searches: o.searches > 0, import: o.records > 0 && !o.imports.unprocessed, duplicates: o.records > 0 && main.phase && main.phase !== "not_started",
    screening: main.phase === "screening" && !(main.counts.pending || main.counts.not_open || main.counts.conflict),
    fulltext: o.fulltext.records > 0 && !o.fulltext.counts.pending && !o.fulltext.counts.conflict && main.phase === "screening",
    extract: !!o.extraction && o.extraction.studies > 0 && o.extraction.finished === o.extraction.studies,
    certainty: !!o.certainty && o.certainty.outcomes > 0 && o.certainty.finished === o.certainty.outcomes, report: false };
  if (done[r]) return "done";
  const order = ["searches", "import", "duplicates", "screening", "fulltext", "extract", "certainty", "report"];
  const first = order.find((x) => !done[x]);
  return first === r ? "now" : "";
}

function shell(route, content) {
  const p = S.app.project;
  const me = p && p.settings.reviewers.find((x) => x.id === p.me);
  const tasksLeft = (S.ov?.tasks || []).reduce((a, t) => a + t.left, 0);
  const nav = h("nav.nav", { "aria-label": "Workflow" }, NAV.map((n) => {
    if (n.gap) return h("div.gap");
    const st = navStatus(n);
    const badge = n.r === "screening" ? (S.ov?.tasks || []).filter((t) => t.task !== "fulltext").reduce((a, t) => a + t.left, 0)
      : n.r === "fulltext" ? (S.ov?.tasks || []).filter((t) => t.task === "fulltext").reduce((a, t) => a + t.left, 0) : 0;
    const here = route === n.r || (route === "screen" && n.r === "screening") || (route === "ft" && n.r === "fulltext") || (route === "x" && n.r === "extract") || (route === "c" && n.r === "certainty") || (route === "wizard" && n.r === "setup");
    // the marks are drawn by the style sheet, so the link's name is its words alone ("Import records (done)")
    return h("a" + (here ? ".on" : "") + (st === "now" ? ".nav-now" : ""), { href: "#/" + n.r, "aria-current": here ? "page" : null },
      n.steps ? h("span.num" + (st ? "." + st : ""), { "aria-hidden": "true" }) : h("span.icon", { "aria-hidden": "true", "data-icon": n.icon }),
      h("span", n.label, st === "done" ? h("span.sr-only", " (done)") : st === "now" ? [h("span.sr-only", " (next step)"), h("span.nav-next", { "aria-hidden": "true" }, "Next step")] : null),
      badge ? h("span.badge", { title: "Records waiting for you" }, num(badge), h("span.sr-only", " waiting for you")) : h("span"));
  }));
  const skip = h("a.skip-link", { href: "#main-content", onclick: (e) => { e.preventDefault(); focusMain(); } }, "Skip to main content");
  const rail = h("aside.rail",
    h("div.brand", logo(), "ReviewDesk", h("button.menu-toggle", { "aria-expanded": "false", onclick: (e) => {
      const open = rail.classList.toggle("open"); e.currentTarget.setAttribute("aria-expanded", String(open)); } }, "Menu")),
    h("div.proj", h("strong", { title: p.title }, p.settings.short_title || p.title || "Untitled review"), h("button.link.small", { onclick: closeProject }, "Switch project")),
    nav,
    h("div.foot", `Version ${S.app.version}`, h("br"), tasksLeft ? `${plural(tasksLeft, "record")} waiting for you` : ""));
  const top = h("header.topbar",
    problemCount(p) ? h("button.pill.exclude", { onclick: dataProblems, title: "See what changed, and put it right" }, `Data problems (${num(problemCount(p))})`) : null,
    p.is_lead ? h("span.pill.action", "Project lead") : null,
    h("button.who", { onclick: chooseIdentity, title: "Change who you are in this project" },
      h("span.avatar", me ? me.id : "?"), me ? me.name : "Choose who you are"));
  const banner = p.lead_elsewhere ? h("div.callout.warn.banner", h("p.small", p.lead_elsewhere)) : null;
  // one team, one version: someone last worked on the project with another version of ReviewDesk (see versions.py)
  const versions = (p.version_warnings || []).length ? h("div.callout.bad.banner", { role: "alert" },
    h("p.small", h("strong", "Not everyone is using the same version of ReviewDesk. ")), p.version_warnings.map((w) => h("p.small", w))) : null;
  return h("div.shell", skip, rail, h("main.main#main-content", top, versions, banner, content));
}

// The keyboard goes to the page's main heading (after a page change, or from "Skip to main content").
export function focusMain(root) {
  const scope = root || document.getElementById("main-content") || document.getElementById("app");
  const head = scope && (scope.querySelector(".page h1, main h1, h1"));
  if (!head) return;
  if (!head.hasAttribute("tabindex")) head.tabIndex = -1;
  head.focus({ preventScroll: true });
}

// How many things need someone to look at them: the same number on the button and the Overview.
export const problemCount = (p) => (p.data_problems || 0) + (p.sync_problems || 0) + (p.warnings || []).filter((w) => !/is a second version of/.test(w)).length;

// Everything that differs from what ReviewDesk recorded (the data checks), second copies made by the sync service,
// and notes: each with what can be done about it.
export async function dataProblems() {
  let d = await api("/api/integrity");
  let changed = false;
  modal((box, close) => {
    const body = h("div");
    const act = async (path, args, msg) => { const r = await attempt(() => api(path, args), msg); d = r && r.report ? r : await api("/api/integrity"); changed = true; draw(); };
    const fixFor = (pr) => {
      const bits = [];
      const fixes = pr.fixes || [];
      const copies = (d.backups || {})[pr.file] || [];
      const lead = d.is_lead;
      if (fixes.includes("chain") && (pr.own || lead)) bits.push(h("button.primary", { onclick: () => act("/api/integrity/fix", { kind: "chain", file: pr.file }, "Put back") }, "Put back this computer's copy"));
      if (fixes.includes("chain") && !pr.own && !lead) bits.push(h("p.small.muted", "Tell the project lead: the lead can put it back from their computer's copy. The person it belongs to also puts back their own rows when they next open ReviewDesk."));
      if (fixes.includes("pdf")) bits.push(h("button.primary", { onclick: () => act("/api/integrity/fix", { kind: "pdf", record: pr.record }, "PDF put back") }, "Put back this computer's copy of the PDF"));
      if (fixes.includes("keep")) bits.push(h("button", { onclick: () => act("/api/integrity/fix", { kind: "keep", record: pr.record }, "Kept") }, pr.kind === "pdf_back" ? "It is the right PDF: keep it" : "The new file is right: keep it"));
      if (fixes.includes("takeoff")) bits.push(h("button", { onclick: () => act("/api/integrity/fix", { kind: "takeoff", record: pr.record }, "Taken off") }, "Take it off again"));
      if (fixes.includes("adopt")) bits.push(h("button.primary", { onclick: () => act("/api/integrity/fix", { kind: "adopt" }, "Recorded") }, "Record them"));
      if (fixes.includes("stray")) bits.push(h("button.primary", { onclick: () => act("/api/integrity/fix", { kind: "stray" }, "Moved to the waiting list") }, "Move them to the waiting list"));
      if (fixes.includes("seal") && lead) bits.push(h("button.primary", { onclick: () => act("/api/integrity/fix", { kind: "seal" }, "Put back") }, "Put back this computer's copy"));
      if (fixes.includes("backup") && lead && copies.length) {
        let pick = copies[0].file;
        bits.push(h("details", h("summary", "Restore a copy kept on this computer"),
          h("div.copylist", { role: "radiogroup", "aria-label": `Copies of ${pr.file}` }, copies.map((c, k) => h("label",
            h("input", { type: "radio", name: "copy-" + pr.sig, checked: k === 0, onchange: () => (pick = c.file) }),
            h("span", h("strong", c.saved), c.what ? ` (${c.what})` : "", k === 0 ? " — newest" : "")))),
          h("button", { onclick: async () => {
            try { await api("/api/restore", { file: pr.file, copy: pick }); toast("Restored"); }
            catch (err) {
              if (err.status !== 409 || !(await confirmBox("This copy may be out of date", [err.message, "Restore it anyway only if you are sure."], "Restore this older copy", true))) { if (err.status !== 409) toast(err.message, true); return; }
              await attempt(() => api("/api/restore", { file: pr.file, copy: pick, older_ok: true }), "Restored");
            }
            d = await api("/api/integrity"); changed = true; draw();
          } }, "Restore the chosen copy")));
      }
      if (fixes.includes("reseal") && lead) {
        const why = h("input", { type: "text", "aria-label": "Why the records can be sealed again", placeholder: "Why (kept in the audit log)" });
        bits.push(h("details", h("summary", "No copy is left: seal the records again as they are now"),
          h("p.small.muted", "Only if you are sure the records, exports and criteria have not been changed since screening started: they become the new reference."),
          why, h("button", { onclick: () => act("/api/integrity/fix", { kind: "reseal", reason: why.value }, "Sealed again") }, "Seal again")));
      }
      if (fixes.includes("accept") && lead && pr.sig) {
        const again = !!pr.log_behind;          // the plan's entry in the audit log is missing: logged again, not an amendment
        const why = h("input", { type: "text", "aria-label": again ? "How you know the plan is the one the team agreed" : "Why this change is correct",
          placeholder: again ? "How you know (kept in the audit log)" : "Why it is correct (kept in the audit log)" });
        bits.push(h("details", h("summary", again ? "The plan is the one the team agreed" : "This change is correct"),
          h("p.small.muted", again ? "The plan is recorded in the audit log again, with what you write here. This is not a protocol amendment, and nothing in the plan changes." :
            pr.kind === "criteria" || pr.kind === "robplan" ? "It is recorded as a protocol amendment, with your reason, and reported in the methods summary (PRISMA 2020 item 24c)." :
            "For a change you or the team made on purpose. It is recorded in the audit log with your reason, and reported again if the file changes again."),
          why, h("button", { onclick: () => act("/api/integrity/fix", { kind: "accept", sig: pr.sig, reason: why.value }, again ? "Recorded in the log again" : "Recorded as correct") },
            again ? "Record it in the log again" : "Record it as correct")));
      }
      if (!bits.length) bits.push(h("p.small.muted", pr.kind === "pdf_duplicate" ? "Tell the project lead if one PDF really holds both reports; otherwise take the wrong one off on its report's page."
        : d.is_lead ? "Put the file back with OneDrive's version history (right-click it in the project folder, Version history)." : "Tell the project lead."));
      return bits;
    };
    const draw = () => {
      body.replaceChildren();
      const r = d.report;
      if (r.problems.length) body.append(h("section", h("h3", "Changed outside ReviewDesk"),
        r.problems.map((pr) => h("div.panel", h("p", pr.text), fixFor(pr)))));
      if (d.result) body.prepend(h("div.callout", h("p.small", d.result)));
      for (const g of d.conflicts) if (g.state) {
        const sc = g.state, files = g.files.filter((f) => f.together);
        const blocked = (c) => !!(sc.advice && sc.advice !== c && (sc.spoils[c] || []).length);
        const head = sc.advice === "keep" ? "Keep the versions in use." : sc.advice === "use_other" ? "Use the second versions." : "";
        const next = sc.advice ? sc.next[sc.advice] : "";
        body.append(h("div.panel", h("h3", "Two versions of the screening state, the records and the de-duplication record"),
          h("p.small", "These files belong together: the screening state gives the records their IDs and says which have been screened. You choose once, for all of them."),
          h("table.t", h("thead", h("tr", h("th", "File"), h("th", "In use"), h("th", "Second version"))),
            h("tbody", files.map((f) => h("tr", h("td", f.file), h("td.small", describe(f.current)), h("td.small", `${f.copy}: ${describe(f.other)}`))))),
          h("p.small", sc.current), h("p.small", sc.other),
          h("p.small", head ? h("strong", head + " ") : null, sc.why),
          next ? h("p.small", next) : null,
          !sc.advice && (sc.next.keep || sc.next.use_other) ? h("p.small.muted", "After you choose, Remove duplicates (Import records) adds any export the chosen version does not have yet.") : null,
          h("p.small.muted", "The versions you do not keep are moved to state/_replaced, never deleted."),
          h("div.row", h("button" + (sc.advice === "keep" ? ".primary" : ""), { disabled: files.some((f) => f.current.unreadable) || blocked("keep"),
            onclick: () => act("/api/sync/keep", { copy: files[0].copy }, "Kept the versions in use") }, "Keep the versions in use"),
          h("button" + (sc.advice === "use_other" ? ".primary" : ""), { disabled: files.some((f) => f.other.unreadable) || blocked("use_other"),
            onclick: () => act("/api/sync/use", { copy: files[0].copy }, "Switched to the second versions") }, "Use the second versions"))));
      }
      for (const g of d.conflicts) for (const f of g.files.filter((x) => !x.together)) {
        const advice = f.advice === "use_other" ? "The second version looks newer." : f.advice === "keep" ? "The version in use looks newer." : "";
        body.append(h("div.panel", h("h3", `Two versions of ${f.file}`),
          h("table.t", h("thead", h("tr", h("th", "Version"), h("th", "What it says"))),
            h("tbody", h("tr", h("td", "In use"), h("td.small", describe(f.current))), h("tr", h("td", f.copy), h("td.small", describe(f.other))))),
          advice ? h("p.small", h("strong", advice)) : null,
          h("p.small.muted", "The version you do not keep is moved to state/_replaced, never deleted."),
          h("div.row", h("button" + (f.advice === "keep" ? ".primary" : ""), { disabled: !!f.current.unreadable,
            onclick: () => act("/api/sync/keep", { copy: f.copy }, "Kept the version in use") }, "Keep the version in use"),
          h("button" + (f.advice === "use_other" ? ".primary" : ""), { disabled: !!f.other.unreadable,
            onclick: () => act("/api/sync/use", { copy: f.copy }, "Switched to the second version") }, f.other.unreadable ? "The second version is damaged" : "Use the second version"))));
      }
      const notes = d.warnings.filter((w) => !/is a second version of/.test(w));
      if (notes.length) {
        body.append(h("div.panel", h("h3", "Notes"), notes.map((w) => h("p.small", w)),
          h("div.row", h("button", { onclick: () => act("/api/sync/dismiss", {}, "Notes cleared") }, "I have dealt with these: clear the notes"))));
      }
      if (!r.problems.length && !d.conflicts.length && !notes.length) body.append(h("div.callout.good", h("p", d.warnings.length && !d.is_lead
        ? "The project lead's files have a second version. Tell the project lead: only the lead can settle them."
        : "No problems found: everything that was checked matches what ReviewDesk recorded.")));
      if (d.pdf_checks_hidden) body.append(h("p.small.muted", "The checks of the full-text PDFs are not shown to you while you are screening a verification sample: they name records that reached full text. The other reviewers see them."));
      if ((d.repairs || []).length) body.append(h("section", h("h3", "Put right by this computer (nothing to do)"), d.repairs.map((w) => h("p.small", w))));
      body.append(h("p.small.muted", `Checked ${String(r.checked).slice(11, 16)}. `,
        r.sealed ? `Screening started from ${plural(r.records_sealed, "record")} on ${String(r.sealed).slice(0, 10)}; each vote, extraction answer and PDF is fingerprinted when it is saved.` : "The records are sealed when screening starts."),
        r.accepted && r.accepted.length ? h("details", h("summary.small", `${plural(r.accepted.length, "change")} recorded as correct`),
          h("ul.small", r.accepted.map((a) => h("li", `${String(a.t).slice(0, 16).replace("T", " ")}, ${a.by}: ${a.text} Reason: ${a.reason}`)))) : null);
    };
    box.append(h("h2", "Data problems"),
      h("p.muted", "ReviewDesk checks the project's files as it reads them: decisions, answers and judgements against their fingerprints and against the copy this computer keeps, PDFs and imported files against their fingerprints, and the records and criteria against what screening started from. What it finds changed, missing or added outside ReviewDesk is listed here, with a way to put it right. The checks are for accidents and sync faults; they are not a safeguard against someone who sets out to change the files."),
      body, h("div.row.end", h("button.primary", { onclick: close }, "Close")));
    draw();
  }, () => { if (changed) render(); });
}
export const syncProblems = dataProblems;

function describe(v) {
  if (!v) return "missing";
  if (v.unreadable) return "cannot be read (damaged)";
  const bits = [];
  if (v.rev !== undefined && v.rev !== null) bits.push(`revision ${v.rev}`);
  if (v.records !== undefined) bits.push(plural(v.records, "record"));
  if (v.merges !== undefined) bits.push(plural(v.merges, "merge"));
  if (v.phases) bits.push(Object.entries(v.phases).map(([k, x]) => `${SETS[k] || k}: ${x}`).join("; "));
  const when = v.saved_at || v.updated || v.processed;
  if (when) bits.push(`saved ${String(when).slice(0, 16).replace("T", " ")}`);
  const who = v.saved_by || v.updated_by || v.by;
  if (who) bits.push(`by ${who}`);
  if (v.saved_on) bits.push(`on computer ${v.saved_on}`);
  if (v.modified) bits.push(`file changed ${v.modified}`);
  return bits.join(", ") || "no details";
}

// A shared file that cannot be read: what happened, and (for the lead's computer) a way back.
function damagedPage(app, e, inside = false) {
  const dmg = e.data.damaged;
  const copies = dmg.copies || [];
  let pick = copies.length ? copies[0].file : "";
  const restore = async (older) => {
    try { await api("/api/restore", { file: dmg.file, copy: pick, older_ok: older }, { anyProject: true }); toast("Restored"); render(); }
    catch (err) {
      if (err.status === 409 && !older) {
        if (await confirmBox("This copy may be out of date", [err.message, "Restore it anyway only if you are sure the other computer's work can be lost or will be redone."], "Restore this older copy", true)) restore(true);
      } else toast(err.message, true);
    }
  };
  // (a file that has not arrived yet is not damaged: the heading says which it is)
  const notHere = dmg.missing || /is not on this computer yet/.test(e.message || "");
  app.replaceChildren(h(inside ? "div.page" : "main.page", h("h1", notHere ? "A project file is not on this computer yet" : "A project file cannot be read"),
    h("div.callout.bad", h("p", e.message)),
    dmg.can_restore ? h("div.panel", h("h2", "Put back a copy kept on this computer"),
      h("p", `ReviewDesk keeps the last few versions of ${dmg.file} that this computer saved. Choose one (the newest is usually right). If you, as the lead, also work on another computer, that computer may have a newer copy: check there first. The damaged file is moved to state/_replaced, not deleted.`),
      h("div.copylist", { role: "radiogroup", "aria-label": "Copies on this computer" }, copies.map((c, k) => h("label",
        h("input", { type: "radio", name: "copy", value: c.file, checked: k === 0, onchange: () => (pick = c.file) }),
        h("span", h("strong", c.saved), c.what ? ` (${c.what})` : "", k === 0 ? " — newest" : "")))),
      h("button.primary", { onclick: () => restore(false) }, "Restore the chosen copy"))
      : h("p", dmg.lead ? `Only the project lead can put the project file back, and this computer's newest copy of it names ${dmg.lead} as the lead. Their own computer shows them this page too, with a copy to restore if it keeps one; or anyone can use OneDrive's version history: right-click the file in the project folder and choose Version history.`
        : "This computer has no copy of that file. The project lead can restore it from their computer (it shows them this page too), or anyone can use OneDrive's version history: right-click the file in the project folder and choose Version history."),
    h("div.row", h("button", { onclick: () => render() }, "Try again"), h("button", { onclick: async () => { await api("/api/project/close", {}, { anyProject: true }); go("start"); } }, "Open another project"))));
}

export function logo() {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("width", "26"); s.setAttribute("height", "26"); s.setAttribute("viewBox", "0 0 32 32"); s.setAttribute("aria-hidden", "true");
  s.innerHTML = '<rect x="3" y="5" width="26" height="22" rx="4" fill="#2D5A88"/><path d="M8 12h16M8 16h11M8 20h13" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/><circle cx="24" cy="20" r="3.2" fill="#1E7552" stroke="#fff" stroke-width="1.5"/>';
  return s;
}

async function closeProject() {
  if (S.leaveGuard && S.leaveGuard() && !(await confirmBox("Leave without saving?", "You have changed settings on this page but not saved them. If you switch project, the changes are lost.", "Switch without saving", true))) return;
  S.leaveGuard = null;
  await api("/api/project/close", {}, { anyProject: true });
  go("start");
}

let identityOpen = false;
export async function chooseIdentity() {
  const p = S.app.project;
  if (identityOpen) return;
  if (S.leaveGuard && S.leaveGuard() && !(await confirmBox("Leave without saving?", "You have changed settings on this page but not saved them. Choosing who you are reloads the page, so the changes would be lost.", "Leave without saving", true))) return;
  identityOpen = true;
  modal((box, close) => {
    box.append(h("h2", "Who are you in this project?"),
      h("p.muted", "Your decisions are saved under your name, in your own file in the project folder. Choose yourself; if you are not listed, ask the project lead to add you in Project set-up."),
      h("div", { style: { display: "grid", gap: "8px" } }, p.settings.reviewers.map((r) =>
        h("button" + (r.id === p.me ? ".primary" : ""), { onclick: async () => {
          if (r.id !== p.me && p.me && !(await confirmBox(`Switch to ${r.name}?`,
            `Everything you do from now on, on this computer, is recorded as ${r.name}'s. Only switch if you are ${r.name}.`, `I am ${r.name}`))) return;
          if (r.id !== p.me && !p.me && r.role === "lead" && !(await confirmBox(`Are you ${r.name}?`,
            `${r.name} is the project lead. Choose this only if you are ${r.name}: the lead's choices change the shared project files.`, `I am ${r.name}`))) return;
          await api("/api/me", { reviewer: r.id }); S.leaveGuard = null; close(); await render(); } },
          h("span.avatar", r.id), `${r.name}${r.role === "lead" ? " (project lead)" : ""}`))));
  }, () => { identityOpen = false; });
}

// The project was opened through a folder path too long for Windows on this computer: what that means, and what to do.
function pathWarning(text) {
  modal((box, close) => box.append(h("h2", "This project's folder path is too long for Windows"),
    h("div.callout.warn", h("p", text)),
    h("p.small.muted", "ReviewDesk shows this once each time it is started."),
    h("div.row.end", h("button.primary", { onclick: close }, "I understand"))));
}

let renderSeq = 0;
let shownPage = null;                 // the address of the page last drawn: a new one gets the focus on its heading
export async function render() {
  const mine = ++renderSeq;                       // a newer render (another click, a hash change) wins
  const app = document.getElementById("app");
  let route = (location.hash.replace(/^#\/?/, "") || "").split("/")[0];
  const args = (location.hash.replace(/^#\/?/, "") || "").split("/").slice(1).map(decodeURIComponent);
  const newPage = location.hash !== shownPage;
  if (newPage) clearOldToast();
  try { await refresh(); }
  catch (e) {
    if (mine !== renderSeq) return;
    leavePage();
    if (e.data && e.data.damaged) { damagedPage(app, e); return; }
    app.replaceChildren(h("main.page", h("h1", "ReviewDesk could not open this page"), h("div.callout.bad", h("p", e.message)),
      e.data && e.data.expired ? null : h("div.row", h("button.primary", { onclick: () => render() }, "Try again"))));
    focusMain(app);
    return;
  }
  if (mine !== renderSeq) return;
  if (S.app.path_warning) pathWarning(S.app.path_warning);       // sent once in each session, so shown once
  if (!S.app.project) route = "start";
  else if (!route || route === "start") route = "overview";
  const view = VIEWS[route] || core.overview;
  if (S.app.project && !S.app.project.member && route !== "start") {
    app.replaceChildren(shell(route, h("div.page", h("div.callout.warn", h("p", "Choose who you are in this project before you start."),
      h("button.primary", { onclick: chooseIdentity }, "Choose who I am")))));
    chooseIdentity();
    return;
  }
  const full = route === "screen" || route === "ft" || route === "x" || route === "c" || route === "start";
  const content = h("div");
  leavePage();                                    // what the page before set up outside itself is undone
  app.replaceChildren(full ? content : shell(route, content));
  // while a slow page is fetched, it says so (instead of an empty screen)
  const opening = h("p.muted.opening", { role: "status" }, "Opening the page…");
  content.append(opening);
  try { await view(content, args); opening.remove(); }
  catch (e) {
    if (mine !== renderSeq) return;
    if (e.data && e.data.damaged) { damagedPage(content, e, true); return; }      // only this page is affected
    if (e.status === 409 && /no longer at|No project is open/.test(e.message)) {
      app.replaceChildren(h("main.page", h("div.callout.bad", h("p", e.message)), h("button.primary", { onclick: () => go("start") }, "Open a project")));
      return;
    }
    if (e.data && e.data.verifying) {           // closed to someone screening a verification sample: not a fault
      content.replaceChildren(h(full ? "main.page" : "div.page", h("h1", e.data.title || "Closed until you finish the verification sample"), h("div.callout", h("p", e.message)),
        h("div.row", h("button.primary", { onclick: () => go("screening") }, "Go to Screen titles and abstracts"))));
      window.scrollTo(0, 0); shownPage = location.hash; focusMain(content);
      return;
    }
    // what went wrong, and a way on: the same page again, or (for a page drawn without the side bar) the Overview
    content.replaceChildren(h(full ? "main.page" : "div.page", h("h1", "This page could not be opened"), h("div.callout.bad", h("p", e.message)),
      h("div.row", h("button.primary", { onclick: () => render() }, "Try again"), full ? h("button", { onclick: () => go("overview") }, "Go to the Overview") : null)));
  }
  if (mine !== renderSeq) return;
  window.scrollTo(0, 0);
  // keyboard and screen-reader users start at the new page's heading, not back at the top of the side bar; after an
  // action that redrew the same page, only if the keyboard focus was lost with what was redrawn
  const lost = !document.activeElement || document.activeElement === document.body;
  shownPage = location.hash;
  if ((newPage || lost) && !document.querySelector(".modal-back")) focusMain(content);
}

const andList = (xs) => (xs.length < 2 ? xs.join("") : xs.slice(0, -1).join(", ") + " and " + xs[xs.length - 1]);

// The pages, as the side bar names them, for a button that opens one.
const PAGE_NAMES = Object.fromEntries(NAV.filter((n) => n.r).map((n) => [n.r, n.label]));
// Which step each kind of screening task belongs to.
const TASK_STEP = { calibration: "calibration", sample: "sample", screen: "screen", second: "screen", fulltext: "fulltext" };
const SCREEN_STEPS = ["calibration", "sample", "screen"];

// The single most useful next step: always about the step the review is at (currentStep), so that the Overview's card,
// the side bar's "Next step" and the strip's "You are here" agree.
export function nextStep() {
  const p = S.app.project, o = S.ov;
  if (!o) return null;
  if (!S.ready) return legacyNextStep();
  const lead = p.is_lead;
  const t = (title, text, label, route) => ({ title, text, label, route });
  const open = (route) => `Open ${PAGE_NAMES[(route || "").split("/")[0]] || "the page"}`;
  const leadName = (p.settings.reviewers.find((r) => r.role === "lead") || {}).name || "the project lead";
  const cur = currentStep();
  if (!cur) return t("Every step in ReviewDesk is done", "Download the PRISMA flow diagram, the tables and the methods summary for the write-up.",
    open("report"), "report");
  const items = [...cur.missing, ...cur.warnings];
  const direct = (x) => wz.DIRECT_FIX[x.key] || [x.fix_label || open(x.fix), x.fix];
  if (cur.id === "setup") {
    if (lead) return t("Finish setting up the project",
      `Still to do: ${andList([...new Set(cur.missing.map((x) => x.short).filter(Boolean))].slice(0, 5)) || "check the set-up"}. The guided set-up takes you through it one topic at a time.`,
      "Continue the guided set-up", "wizard");
    return t("Set up the project", cur.missing.length ? cur.missing[0].text : `${leadName} (the project lead) is setting up the project.`, "See the set-up", "wizard");
  }
  if (cur.status === "blocked" && cur.missing.length) {
    const x = cur.missing[0];
    const [label, route] = direct(x);
    return t(`${cur.title}: something is missing`, x.text, route ? label : open(cur.route), route || cur.route || "overview");
  }
  const main = o.sets.main;
  if (SCREEN_STEPS.includes(cur.id) && (!main || main.phase === "not_started")) return lead
    ? t("Start screening", "Look over the merges and any possible duplicates (Check duplicates), then start title and abstract screening. Records are put in a random order first.", "Go to screening", "screening")
    : t("Waiting for screening to start", `${leadName} (the project lead) starts title and abstract screening.`, "See screening", "screening");
  // records of this step waiting for me (records found by other methods are the citation-searching step's own work)
  const stepOf = (x) => (x.set === "other" && x.task !== "fulltext" ? "citations" : TASK_STEP[x.task]);
  const task = (o.tasks || []).find((x) => x.left > 0 && stepOf(x) === cur.id);
  if (task) {
    const fresh = task.left === task.total, ft = task.task === "fulltext";
    return t(`${fresh ? "Start" : "Continue"}: ${task.label}${SETS[task.set] && task.set !== "main" ? ` (${SETS[task.set]})` : ""}`,
      `${num(task.left)} of ${plural(task.total, ft ? "report" : "record")} ${task.left === 1 ? "is" : "are"} waiting for you.`,
      ft ? "Go to full texts" : fresh ? "Start screening" : "Continue screening", ft ? "fulltext" : `screen/${task.task}/${task.set}`);
  }
  if (cur.id === "import" && o.imports.unprocessed) return lead
    ? t("Process the imported files", `${plural(o.imports.unprocessed, "file is", "files are")} waiting to be de-duplicated into the record list.`, "Go to imports", "import")
    : t("Waiting for the project lead", `Your files are imported. ${leadName} (the project lead) now removes duplicates and starts screening.`, "See imports", "import");
  // what this step needs from me first (the lead's own items are the lead's), then anything else it lists
  const mine = (y) => y.who === "me" || (lead && y.who === "lead");
  const x = cur.missing.find((y) => mine(y) && y.fix) || items.find((y) => y.who === "me" && y.fix) || items.find((y) => y.fix) || items[0];
  if (x) {
    const [label, route] = direct(x);
    return t(cur.title, x.text, route ? label : open(cur.route), route || cur.route || "overview");
  }
  if (!lead && cur.who === "lead") return t(cur.title, `${leadName} (the project lead) does this step. ${cur.what}`, open(cur.route), cur.route || "overview");
  return t(cur.title, cur.what, open(cur.route), cur.route || "overview");
}

// Without the readiness checks (they could not be read just now): from the overview's counts.
function legacyNextStep() {
  const p = S.app.project, o = S.ov;
  const lead = p.is_lead, s = p.settings;
  const main = o.sets.main;
  const t = (title, text, label, route) => ({ title, text, label, route });
  if (lead && (s.reviewers.length < 2 || !s.criteria.include.length || !(s.question || "").trim())) return t("Finish setting up the project",
    "Add the second reviewer, the review question and the eligibility criteria. Everyone screens against the criteria you write here.", "Open project set-up", "setup");
  const myTasks = (o.tasks || []).filter((x) => x.left > 0);
  if (myTasks.length) {
    const x = myTasks[0];
    return t(`${x.left === x.total ? "Start" : "Continue"}: ${x.label}`, `${x.left.toLocaleString("en-GB")} of ${x.total.toLocaleString("en-GB")} records are waiting for you.`,
      x.left === x.total ? "Start screening" : "Continue screening", x.task === "fulltext" ? "fulltext" : `screen/${x.task}/${x.set}`);
  }
  if (!o.records && !o.imports.total) return t("Plan and run your searches",
    "Choose the databases, build the search, run it in each database and export the results as RIS files.", "Plan searches", "searches");
  if (o.imports.unprocessed) return lead ? t("Process the imported files", `${plural(o.imports.unprocessed, "file is", "files are")} waiting to be de-duplicated into the record list.`, "Go to imports", "import")
    : t("Waiting for the project lead", "Your files are imported. The lead now removes duplicates and starts screening.", "See imports", "import");
  if (!main || main.phase === "not_started") return lead ? t("Check duplicates, then start screening",
    "Look over the merges, then start title and abstract screening. Records are put in a random order first.", "Go to screening", "screening")
    : t("Waiting for screening to start", "The project lead starts title and abstract screening.", "See screening", "screening");
  if (main.phase === "calibration") return t("Calibration", lead ? "When you have both finished, compare your decisions, discuss every disagreement, then move on to the agreement sample." :
    "Waiting for the other reviewer to finish calibration.", "Go to screening", "screening");
  if (main.phase === "sample") return t("Agreement sample", lead ? "When you have both finished the sample, apply the agreement rule to decide how the rest is screened." :
    "Waiting for the other reviewer to finish the agreement sample.", "Go to screening", "screening");
  if (o.conflicts && lead) return t("Settle disagreements", `${plural(o.conflicts, "record needs", "records need")} a joint decision.`, "Open disagreements", main && main.counts.conflict ? "screening" : "fulltext");
  const fto = o.fulltext.counts || {};
  if ((fto.pending || 0) + (fto.conflict || 0) > 0) return t("Full texts", "Every record that passed title and abstract screening needs its full text assessed by both reviewers.", "Go to full texts", "fulltext");
  const xp = o.extraction || {};
  if (xp.studies && xp.finished < xp.studies) return t("Extract data", `${plural(xp.studies - xp.finished, "included study needs", "included studies need")} data extraction and risk of bias.`, "Go to extraction", "extract");
  return t("Report", "See the PRISMA flow diagram and download the files for your appendix.", "Open reports", "report");
}

// Leaving a page with unsaved changes: Cancel, Leave without saving, or (where the page can save itself, as each screen
// of the guided set-up can) Save and go. Answers "save", "leave" or false.
function leaveChoice() {
  if (!S.leaveSave) return confirmBox("Leave without saving?", "You have changed settings on this page but not saved them. If you leave, the changes are lost.",
    "Leave without saving", true).then((ok) => (ok ? "leave" : false));
  const [title, text] = (S.leaveText && S.leaveText()) || ["Save what you typed?", "You have changed something on this screen but not saved it."];
  return new Promise((resolve) => {
    let answer = false;
    const saveBtn = h("button.primary", { onclick: () => { answer = "save"; close(); } }, "Save and go");
    const close = modal((box) => {
      box.append(h("h2", title), h("p", text),
        h("div.row.end", h("button", { onclick: () => close() }, "Cancel"),
          h("button.danger", { onclick: () => { answer = "leave"; close(); } }, "Leave without saving"), saveBtn));
    }, () => resolve(answer));
    saveBtn.focus();                      // saving loses nothing, so Enter does that
  });
}

// A page with unsaved changes (Project set-up, the guided set-up) can ask before it is left.
let lastHash = location.hash;
window.addEventListener("hashchange", async () => {
  if (S.flushOnLeave) {                   // a page that saves as you type: send what is typed, try failed saves again
    const want = location.hash;
    try { await S.flushOnLeave(); } catch (e) { /* what is still not saved is asked about below */ }
    if (location.hash !== want) return;   // moved on again meanwhile: that change is handled on its own
  }
  if (S.leaveGuard && S.leaveGuard()) {
    const want = location.hash;
    history.replaceState(null, "", lastHash || "#/");
    const choice = await leaveChoice();
    if (!choice) return;
    if (choice === "save" && !(await S.leaveSave())) return;
    S.leaveGuard = null;
    carryToast();                         // "Saved" goes with the person to the page they chose
    history.replaceState(null, "", want);
  }
  S.leaveGuard = null;
  S.leaveSave = null;
  S.leaveText = null;
  S.flushOnLeave = null;
  lastHash = location.hash;
  render();
});
window.addEventListener("beforeunload", (e) => { if (S.leaveGuard && S.leaveGuard()) { e.preventDefault(); e.returnValue = ""; } });

// Another computer's work reaches this one through the shared folder. When the window comes back into view (after
// another window, or another tab), a page that only shows progress is drawn again if the review's progress changed
// meanwhile, so that its counts are never out of date. Pages where something is typed or chosen are left alone.
const FRESHEN = new Set(["overview", "timeline", "screening", "fulltext", "report"]);
const progressOf = (o, rd) => JSON.stringify([o && ["sets", "fulltext", "tasks", "conflicts", "records", "imports", "searches", "extraction", "certainty"].map((k) => o[k]),
  rd && rd.steps.map((x) => [x.id, x.status, [...x.missing, ...x.warnings].map((y) => y.text)])]);
let freshening = false;
async function freshen() {
  const route = (location.hash.replace(/^#\/?/, "") || "overview").split("/")[0];
  if (freshening || document.visibilityState === "hidden" || !S.app || !S.app.project || !S.app.project.member || !FRESHEN.has(route)
    || document.querySelector(".modal-back") || (S.leaveGuard && S.leaveGuard())) return false;
  freshening = true;
  try {
    const before = progressOf(S.ov, S.ready);
    let now;
    try { now = await Promise.all([api("/api/overview"), api("/api/readiness")]); } catch (e) { return false; }
    if (progressOf(now[0], now[1]) === before || location.hash !== shownPage) return false;
    const y = window.scrollY, had = document.activeElement;
    const where = had && had !== document.body ? (had.id ? `#${CSS.escape(had.id)}` : had.getAttribute("href") ? `[href="${CSS.escape(had.getAttribute("href"))}"]` : "") : "";
    await render();
    window.scrollTo(0, y);
    const back = where && document.querySelector(where);
    if (back) back.focus({ preventScroll: true });
    return true;
  } finally { freshening = false; }
}
window.addEventListener("focus", () => freshen());
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") freshen(); });

// A PDF shown in the page is another document: when the keyboard moves into it, its frame does not match :focus, so
// the frame that has the focus is marked instead (style.css: iframe.has-focus), and the focus can be seen.
const markFrame = () => setTimeout(() => document.querySelectorAll("iframe").forEach((f) => f.classList.toggle("has-focus", document.activeElement === f)), 0);
window.addEventListener("blur", markFrame);
window.addEventListener("focus", markFrame);
document.addEventListener("focusin", markFrame);
render();
