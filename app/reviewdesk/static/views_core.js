// Start, overview, project set-up and personal settings.
import { api, h, toast, attempt, modal, confirmBox, pickFolder, lines, num, bar, plural, initialsOf, SETS } from "./lib.js";
import { S, go, render, nextStep, logo, problemCount } from "./app.js";
import { readinessBanner, readinessPanel, wizardBack } from "./views_wizard.js";
import { timelineStrip } from "./views_timeline.js";
import { planEditor, planStatus, planList, amendmentList } from "./views_robplan.js";

// ------------------------------------------------------------------------------------------------ start
export async function start(el) {
  const recent = S.app.recent || [];
  const openProject = async (path) => {
    const p = path || await pickFolder("Open a project", { mode: "project", note: "Find the project folder (it holds a file called reviewdesk-project.json). A project someone shared with you through OneDrive or Teams appears here once it is in your OneDrive." });
    if (!p) return;
    await attempt(() => api("/api/project/open", { path: p }));
    go("overview");
  };
  el.append(h("main.page", { style: { maxWidth: "880px", margin: "0 auto", paddingTop: "56px" } },
    h("div.row", { style: { marginBottom: "20px" } }, logo(), h("h1", "ReviewDesk")),
    h("p", { style: { fontSize: "17px", maxWidth: "62ch" } },
      "A guided workspace for systematic reviews: plan and log the searches, import the exports from each database, remove duplicates, screen titles and abstracts and full texts with a second reviewer, extract data, assess risk of bias, rate the certainty of the evidence (GRADE), and produce the PRISMA flow diagram and the reporting pack."),
    h("p.muted", { style: { maxWidth: "62ch" } }, "The review lives in a project folder that your team shares through OneDrive or Teams. Everyone runs ReviewDesk on their own computer and writes only their own files in that folder, so you can all work at the same time."),
    h("div.row", { style: { margin: "26px 0 34px" } },
      h("button.primary", { onclick: () => createProject() }, "Create a new project"),
      h("button", { onclick: () => openProject() }, "Open a project")),
    recent.length ? h("div.panel", h("h2", "Recent projects"),
      h("table.t", h("tbody", recent.map((r) => h("tr",
        h("td", h("button.link", { onclick: () => openProject(r.path) }, h("strong", r.title || "Untitled"))), h("td.muted.small", r.path), h("td.small.nowrap", (r.opened || "").slice(0, 10))))))) : null,
    h("div.panel.flat", h("h2", { style: { fontSize: "16px" } }, "Joining a project someone has shared with you"),
      h("p.muted", { style: { margin: "6px 0 0" } }, "In Teams, open the channel's Files and choose “Add shortcut to OneDrive” (or Sync); for a folder shared through OneDrive, open it in OneDrive on the web and choose “Add shortcut to My files”. Once it has synced to your computer, choose Open a project, find the folder, and then choose your name."))));
}

function createProject() {
  modal((box, close) => {
    const f = { title: h("input", { type: "text", placeholder: "e.g. Exercise programmes for adults with knee osteoarthritis: a systematic review" }),
      folder: h("input", { type: "text", placeholder: "e.g. Knee OA exercise review" }),
      name: h("input", { type: "text", value: S.app.user.my_name || "" }),
      initials: h("input", { type: "text", maxlength: 4, style: { width: "90px" } }),
      // typed or pasted (Copy as path in Windows puts quotes round it), or chosen; the server checks it is a folder
      parent: h("input#cp-parent", { type: "text", autocomplete: "off", spellcheck: "false", "aria-describedby": "cp-parent-help",
        placeholder: "Type or paste a folder's path, or choose it" }) };
    const cut = (t, n) => { t = t.split(":")[0].trim().replace(/\s+/g, " "); if (t.length <= n) return t; const w = t.slice(0, n + 1); return (w.includes(" ") ? w.slice(0, w.lastIndexOf(" ")) : t.slice(0, n)).replace(/[\s,;:–—-]+$/, ""); };
    f.title.addEventListener("input", () => { if (!f.folder._edited) f.folder.value = cut(f.title.value, 60); });
    f.folder.addEventListener("input", () => (f.folder._edited = true));
    f.name.addEventListener("input", () => { if (!f.initials._edited) f.initials.value = initialsOf(f.name.value); });   // titles such as Dr are skipped
    f.initials.addEventListener("input", () => (f.initials._edited = true));
    if (f.name.value) f.initials.value = initialsOf(f.name.value);
    box.append(h("h2", "Create a new project"),
      h("label.f", h("span", "Review title"), f.title),
      h("div.grid2", h("label.f", h("span", "Your name"), f.name), h("label.f", h("span", "Your initials"), f.initials, h("small", "Used to label your decisions."))),
      h("div.wzf", h("label", { for: "cp-parent" }, "Where to put it"), h("div.row", h("div.grow", f.parent),
        h("button", { onclick: async () => { const p = await pickFolder("Where should the project folder go?", { note: "Choose a folder your team can share, such as one in OneDrive or Teams." }); if (p) { f.parent.value = p; f.parent.focus(); } } }, "Choose…")),
        h("small#cp-parent-help", "Put it in a folder you will share with the other reviewer. Type or paste its path, or use Choose… to find it.")),
      h("label.f", h("span", "Name of the new project folder"), f.folder),
      h("p.muted.small", "You become the project lead: you set the eligibility criteria and the screening plan, and you process imports. Add the other reviewers in Project set-up."),
      h("div.row.end", h("button", { onclick: close }, "Cancel"),
        h("button.primary", { onclick: async () => {
          f.parent.value = f.parent.value.trim().replace(/^"(.*)"$/, "$1").trim();
          const r = await attempt(() => api("/api/project/create", { parent: f.parent.value, name: f.folder.value, title: f.title.value,
            lead_name: f.name.value, lead_id: f.initials.value }));
          close();
          chooseSetupWay(r.warning);
        } }, "Create project")));
  });
}

// After a project is made: the guided set-up, or the set-up page (all on one page).
function chooseSetupWay(warning) {
  let chosen = "setup";
  modal((box, close) => {
    const pick = (route) => { chosen = route; close(); };
    box.append(h("h2", "How would you like to set up the project?"),
      h("p", "Before the work starts, the project needs the review question, the eligibility criteria, the team and the screening plan. You can change any of it later, until screening starts."),
      h("div.choice-list",
        h("button.primary.choice", { onclick: () => pick("wizard") }, h("strong", "Guide me through it (recommended)"),
          h("span", "One topic at a time, with what each choice means and an example.")),
        h("button.choice", { onclick: () => pick("setup") }, h("strong", "Use the set-up page"),
          h("span", "Every setting on one page, for those who know what they want."))));
  }, () => {
    go(chosen);
    if (warning) setTimeout(() => toast(warning, true), 400);
  });
}

// ------------------------------------------------------------------------------------------------ overview
export async function overview(el) {
  const p = S.app.project, o = S.ov;
  if (!o) { el.append(progressMissing()); return; }
  const n = nextStep();
  const page = h("div.page");
  el.append(page);
  page.append(h("div.page-head", h("h1", p.title || "Untitled review"), p.settings.question ? h("p", p.settings.question) : null));
  const nProb = problemCount(p);
  if (nProb) page.append(h("div.callout.warn", h("p", h("strong", "Data problems. "),
    `${plural(nProb, "thing needs", "things need")} checking: something in the project folder differs from what ReviewDesk recorded, or a file has a second version.`),
    h("button", { onclick: () => import("./app.js").then((m) => m.dataProblems()) }, "See what changed")));
  if (n) page.append(h("section.next", h("div", h("h2", n.title), h("p", n.text)), h("button.primary", { onclick: () => go(n.route) }, n.label)));
  const ready = h("div"); page.append(ready); readinessPanel(ready);       // what is missing (views_wizard.js)
  const strip = h("div"); page.append(strip); timelineStrip(strip);       // the steps, in order (views_timeline.js)
  const tasks = (o.tasks || []);
  if (tasks.length) page.append(h("div.panel", h("h2", "Your screening"),
    h("div.tasks", tasks.map((t) => h("div.task", h("div", h("strong", t.label), h("div.muted.small", t.set !== "all" ? SETS[t.set] : "")),
      h("div", h("div.small.muted", `${num(t.total - t.left)} of ${num(t.total)} done`), bar(t.total - t.left, t.total, `${t.label}: progress`)),
      t.left ? h("button.primary", { "aria-label": `${t.left === t.total ? "Start" : "Continue"}: ${t.label}${t.set !== "all" ? ", " + SETS[t.set] : ""}`, onclick: () => go(t.task === "fulltext" ? "fulltext" : `screen/${t.task}/${t.set}`) }, t.left === t.total ? "Start" : "Continue") : h("span.pill.include", "Done"))))));
  const main = o.sets.main;
  const ftc = o.fulltext.counts;
  const step = (cls, title, text) => h("li" + (cls ? "." + cls : ""), h("h3", title), h("p", text));
  // records from the update search and from other methods are screened as their own sets: each is counted here too
  const later = Object.entries(o.sets).filter(([k]) => k !== "main");
  const mainDone = main && main.phase === "screening" && !main.counts.pending && !main.counts.not_open && !main.counts.conflict;
  const taDone = mainDone && later.every(([, v]) => !openIn(v));
  page.append(h("div.cols",
    h("div.panel", h("h2", "Where the review is"), h("ol.steps",
      step(o.searches ? "done" : "now", "Searches", o.searches ? `${plural(o.searches, "search", "searches")} logged.` : "No searches logged yet."),
      step(o.records ? "done" : (o.imports.total ? "now" : ""), "Records", o.records ? `${plural(o.records, "unique record")} after removing duplicates.` : o.imports.total ? `${plural(o.imports.total, "file")} imported, not yet processed.` : "Nothing imported yet."),
      step(taDone ? "done" : (main && main.phase !== "not_started") || later.some(([, v]) => v.phase !== "not_started") ? "now" : "",
        "Title and abstract screening", [main && main.phase !== "not_started" ? phaseText(main) : "Not started.",
          ...later.map(([k, v]) => ` ${SETS[k]}: ${v.phase === "not_started" ? `${plural(v.records, "record")}, not screened yet.`
            : `${num(v.counts.forward || 0)} sent on, ${num(v.counts.excluded || 0)} excluded, ${num(openIn(v))} still open.`}`)].join("")),
      step(o.fulltext.records && !ftc.pending && !ftc.conflict ? "done" : o.fulltext.records ? "now" : "", "Full texts",
        o.fulltext.records ? `${plural(o.fulltext.records, "report")}: ${num(ftc.included || 0)} included, ${num(ftc.excluded || 0)} excluded`
          + (ftc.awaiting ? `, ${num(ftc.awaiting)} awaiting information` : "") + (ftc.ongoing ? `, ${num(ftc.ongoing)} ongoing` : "") + (ftc.not_retrieved ? `, ${num(ftc.not_retrieved)} not retrieved` : "")
          + `, ${num((ftc.pending || 0) + (ftc.conflict || 0))} still open.` : "Nothing at full text yet."),
      step(o.extraction && o.extraction.studies && o.extraction.finished === o.extraction.studies ? "done" : o.extraction && o.extraction.studies ? "now" : "", "Data extraction",
        o.extraction && o.extraction.studies ? `${num(o.extraction.finished)} of ${plural(o.extraction.studies, "included study", "included studies")} extracted by both.` : "Starts when studies are included."),
      step("", "Report", "The PRISMA diagram fills in as you go.")),
      !nProb ? h("p.small.muted", { style: { marginTop: "10px" } }, h("button.link.small", { onclick: () => import("./app.js").then((m) => m.dataProblems()) }, "Data checks"),
        ": no problems found in the decisions, records and PDFs that were checked.") : null),
    h("div.panel", h("h2", "Records by source"), Object.keys(o.sets).length ? h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Source"), h("th.num", "Records"), h("th.num", "Sent on"), h("th.num", "Excluded"), h("th.num", "Open"))),
      h("tbody", Object.entries(o.sets).map(([k, v]) => h("tr", h("td", v.label), h("td.num", num(v.records)), h("td.num", num(v.counts.forward || 0)),
        h("td.num", num(v.counts.excluded || 0)), h("td.num", num(openIn(v))))))))
      : h("p.muted", "No records have been added yet."),
      o.ai.planned ? h("p.small.muted", { style: { marginTop: "12px" } }, `AI second screener: ${num(o.ai.coverage.done)} of ${num(o.ai.coverage.records)} records screened by the AI.`) : null)));
  if (p.is_lead && o.ai.status && o.ai.status.interrupted) page.append(h("div.callout.warn", h("p", o.ai.status.message), h("button", { onclick: () => go("ai") }, "Open the AI page")));
}

// A set's records still open at title and abstract (all of them, before its screening starts).
const openIn = (v) => (v.phase === "not_started" ? v.records : (v.counts.pending || 0) + (v.counts.not_open || 0) + (v.counts.conflict || 0));

function phaseText(m) {
  const c = m.counts;
  if (m.phase === "calibration") return "Calibration round in progress.";
  if (m.phase === "sample") return `Agreement sample of ${num(m.sample)} records in progress.`;
  const mode = m.remainder_mode === "single" ? ("one reviewer" + (m.ai_net_active ? ", with the AI second screener" : "")) : "both reviewers";
  return `Screened by ${mode}. ${num(c.forward || 0)} sent on, ${num(c.excluded || 0)} excluded, ${num((c.pending || 0) + (c.conflict || 0))} still open.`;
}

// ------------------------------------------------------------------------------------------------ set-up
let planDraft = null;              // the risk-of-bias plan's unrecorded ticks, across the redraw after Save settings
export async function setup(el, args = []) {
  const p = S.app.project;
  const s = JSON.parse(JSON.stringify(p.settings));
  const lead = p.is_lead;
  const started = S.ov && Object.values(S.ov.sets).some((v) => v.phase && v.phase !== "not_started");
  const dis = !lead;
  const page = h("div.page");
  el.append(page);
  readinessBanner(page, "setup");          // what is missing before this step (views_wizard.js)
  let dirty = false;
  const dirtyNote = h("span.dirty", { hidden: true }, "Unsaved changes");
  const markDirty = () => { if (lead && !dirty) { dirty = true; dirtyNote.hidden = false; } };
  page.addEventListener("input", markDirty);
  page.addEventListener("change", markDirty);
  let robPlan = null;                          // the risk-of-bias plan's editor: saved on its own
  S.leaveGuard = () => (dirty || (robPlan && robPlan.dirty())) && document.body.contains(page);
  page.append(h("div.page-head", h("div.row", h("h1.grow", "Project set-up"),
    h("button", { onclick: () => go("wizard") }, lead ? "Guided set-up" : "See the set-up summary")),
    h("p", lead ? "Everything here is shared with the team. The screening plan is fixed once screening starts, so settle it and the criteria first and register them in your protocol. Criteria can still be clarified later, but every change is dated in the audit log and is a protocol amendment: record why, and report it in your review (PRISMA 2020 item 24c). The AI screener keeps the criteria as they were when screening started."
      : "Only the project lead can change these settings. They are shown here so you can check them.")));
  if (started) page.append(h("div.callout.warn", h("p", "Screening has started, so the screening plan is fixed. A change to the criteria now is a protocol amendment: record why, and report it in your review (PRISMA 2020 item 24c).")));

  const inp = (v, attrs = {}) => h("input", { type: "text", value: v ?? "", disabled: dis, ...attrs });
  const area = (arr, attrs = {}) => h("textarea", { disabled: dis, ...attrs, value: (arr || []).join("\n") });

  // review
  const F = { title: inp(s.title), short: inp(s.short_title), question: h("textarea", { disabled: dis, value: s.question || "", style: { minHeight: "70px" } }),
    type: h("select", { disabled: dis }, [["intervention", "Effects of an intervention"], ["diagnostic", "Diagnostic or test accuracy"], ["prognostic", "Prognosis or prediction models"],
      ["qualitative", "Qualitative evidence"], ["prevalence", "Prevalence or incidence"], ["aetiology", "Aetiology or risk factors"], ["scoping", "Scoping review"], ["other", "Other"]]
      .map(([v, l]) => h("option", { value: v, selected: s.review_type === v }, l))) };
  page.append(h("div.panel#setup-review", h("h2", { tabindex: "-1" }, "The review"), h("label.f", h("span", "Title"), F.title),
    h("div.grid2", h("label.f", h("span", "Short title"), F.short, h("small", "Used to name downloaded files.")), h("label.f", h("span", "Type of question"), F.type)),
    h("label.f", h("span", "Review question"), F.question, h("small", "Shown on every screening page and given to the AI screener."))));

  // protocol registration and planned dates (PRISMA 2020 item 24a; the timeline uses the dates)
  const reg = { registry: "", id: "", date: "", url: "", ...(s.registration || {}) };
  const G = { registry: inp(reg.registry, { list: "setup-registries" }), id: inp(reg.id), date: h("input", { type: "date", value: reg.date || "", disabled: dis }),
    url: inp(reg.url, { inputmode: "url", placeholder: "https://" }) };
  page.append(h("div.panel#setup-registration", h("h2", { tabindex: "-1" }, "Protocol registration"),
    h("p.muted", "Register the protocol (for example in PROSPERO or on OSF) before the final searches are run, so that readers can check the review kept to its plan. Leave the number empty if it is not registered yet."),
    h("div.grid2", h("label.f", h("span", "Registry"), G.registry), h("label.f", h("span", "Registration number"), G.id, h("small", "For example, a PROSPERO number starts CRD42."))),
    h("div.grid2", h("label.f", h("span", "Date registered"), G.date), h("label.f", h("span", "Link to the record"), G.url)),
    h("datalist#setup-registries", ["PROSPERO", "OSF Registries", "INPLASY", "Research Registry"].map((x) => h("option", { value: x })))));
  const dts = { start: "", searches: "", update_search: "", end: "", ...(s.dates || {}) };
  const D = Object.fromEntries(Object.keys(dts).map((k) => [k, h("input", { type: "date", value: dts[k] || "", disabled: dis })]));
  page.append(h("div.panel#setup-dates", h("h2", { tabindex: "-1" }, "Planned dates"),
    h("p.muted", "As your protocol's timetable gives them. Used for the timeline and for reminders, such as when the update search is due."),
    h("div.grid2", h("label.f", h("span", "Work starts"), D.start), h("label.f", h("span", "Final searches run"), D.searches)),
    h("div.grid2", h("label.f", h("span", "Update search"), D.update_search), h("label.f", h("span", "Review finished"), D.end))));

  // people
  const people = s.reviewers.map((r) => ({ ...r }));
  const peopleBox = h("div");
  const drawPeople = () => peopleBox.replaceChildren(h("table.t", h("thead", h("tr", h("th", "Name"), h("th", "Initials"), h("th", "Role"), h("th", h("span.sr-only", "Actions")))),
    h("tbody", people.map((r, i) => h("tr",
      h("td", h("input", { type: "text", value: r.name, disabled: dis, "aria-label": `Name of person ${i + 1}`, oninput: (e) => { r.name = e.target.value; refreshPlanPeople(); } })),
      h("td", { style: { width: "110px" } }, h("input", { type: "text", value: r.id, maxlength: 4, "aria-label": `Initials of person ${i + 1}`, disabled: dis || s.reviewers.some((x) => x.id === r.id), title: s.reviewers.some((x) => x.id === r.id) ? "Initials cannot change once saved: decisions are recorded under them. To correct someone else's, remove them and add them again before they start." : null, oninput: (e) => { r.id = e.target.value.toUpperCase(); refreshPlanPeople(); } })),
      h("td", { style: { width: "170px" } }, h("select", { disabled: dis, "aria-label": `Role of person ${i + 1}`, onchange: (e) => { people.forEach((x) => (x.role = "reviewer")); r.role = e.target.value; if (e.target.value !== "lead") people[0].role = "lead"; drawPeople(); } },
        h("option", { value: "lead", selected: r.role === "lead" }, "Project lead"), h("option", { value: "reviewer", selected: r.role !== "lead" }, "Reviewer"))),
      h("td", { style: { width: "90px" } }, lead && r.role !== "lead" && !s.reviewers.some((x) => x.id === r.id && started) ? h("button.link", { onclick: () => { people.splice(i, 1); drawPeople(); refreshPlanPeople(); } }, "Remove") : null))))),
    lead ? h("button", { style: { marginTop: "10px" }, onclick: () => { people.push({ id: "", name: "", role: "reviewer" }); drawPeople(); refreshPlanPeople(); markDirty();
      peopleBox.querySelector(`input[aria-label="Name of person ${people.length}"]`)?.focus(); } }, "Add a person") : null);
  let refreshPlanPeople = () => {};
  drawPeople();
  page.append(h("div.panel#setup-people", h("h2", { tabindex: "-1" }, "People"), h("p.muted", "The lead writes the shared project files (records, screening plan, agreed decisions). Everyone records their own decisions."), peopleBox));

  // criteria
  const C = { include: area(s.criteria.include, { class: "tall" }), exclude: area(s.criteria.exclude, { class: "tall" }), rules: area(s.criteria.rules, { class: "tall" }) };
  page.append(h("div.panel#setup-criteria", h("h2", { tabindex: "-1" }, "Eligibility criteria"), h("p.muted", "One point per line. These are shown beside every record during screening and, if you use it, sent to the AI screener word for word."),
    h("label.f", h("span", "Include (every point must be met)"), C.include),
    h("label.f", h("span", "Exclude (any one point rules a record out)"), C.exclude),
    h("label.f", h("span", "Rules for borderline cases"), C.rules, h("small", "For example: “If the abstract does not say whether the tool was used in real time, vote Maybe.”"))));
  const R = { reasons: area(s.ft_reasons), hin: area(s.highlight.include), hex: area(s.highlight.exclude) };
  page.append(h("div.panel#setup-reasons", h("h2", { tabindex: "-1" }, "Full-text exclusion reasons and highlighting"),
    h("label.f", h("span", "Full-text exclusion reasons, in the order to apply them"), R.reasons, h("small", "Reviewers record the first reason that applies, so list them in your criteria's order.")),
    h("div.grid2", h("label.f", h("span", "Words to highlight in green"), R.hin, h("small", "One per line; end with * to match any ending (triag*).")),
      h("label.f", h("span", "Words to highlight in red"), R.hex))));

  // screening plan
  const sc = s.screening;
  const ids = () => people.filter((x) => x.id).map((x) => [x.id, `${x.name || x.id} (${x.id})`]);
  const opts = (val) => [h("option", { value: "" }, "Choose…"), ...ids().map(([v, l]) => h("option", { value: v, selected: v === val }, l))];
  const sel = (val, attrs = {}) => h("select", { disabled: dis || started, ...attrs }, opts(val));
  const P = { mode: sc.mode, r1: sel(sc.reviewers[0]), r2: sel(sc.reviewers[1]), single: sel(sc.single_reviewer), second: sel(sc.second_reviewer),
    frac: h("input", { type: "number", min: 5, max: 90, step: 1, value: Math.round(100 * sc.sample_fraction), disabled: dis || started }),
    kappa: h("input", { type: "text", value: sc.kappa_threshold, spellcheck: false, autocomplete: "off", disabled: dis || started, inputmode: "decimal" }),
    cal: h("input", { type: "number", min: 0, max: 1000, step: 10, value: sc.calibration_size, disabled: dis || started }),
    ai: h("input", { type: "checkbox", checked: sc.ai_second_screener, disabled: dis || started }),
    stop: h("input", { type: "number", min: 5, max: 100, step: 5, value: Math.round(100 * sc.ai_keep_rate_stop), disabled: dis || started }),
    conflicts: sc.conflicts_at_title_abstract };
  refreshPlanPeople = () => { for (const x of [P.r1, P.r2, P.single, P.second]) { const v = x.value; x.replaceChildren(...opts(v)); x.value = v; } };
  const modeBox = h("div");
  const plan = h("div");
  const drawPlan = () => {
    const m = P.mode;
    plan.replaceChildren(
      h("div.grid2", h("label.f", h("span", "First reviewer"), P.r1), h("label.f", h("span", "Second reviewer"), P.r2)),
      m === "single" ? h("p.small.muted", "With one reviewer screening, the first and second reviewers are the people who assess full texts (when both assess them), judge risk of bias and rate certainty. If you work alone, choose yourself as the first reviewer and leave the second empty. There is no calibration round: it is a practice round for two people who screen the same records.") : null,
      h("div.grid3", m === "single" ? h("div") : h("label.f", h("span", "Calibration records"), P.cal, h("small", "Both screen these first, then discuss. 0 for none; at most 1,000.")),
        m === "sample_then_single" ? h("label.f", h("span", "Agreement sample (%)"), P.frac, h("small", "Random share both screen: 5 to 90.")) : h("div"),
        m === "sample_then_single" ? h("label.f", h("span", "Kappa needed for single screening"), P.kappa, h("small", "Between 0.01 and 1. At 0.80 or more, the two also agree on at least 80% of the sample, as AMSTAR 2 asks (kappa 0.80 is not the same as 80% agreement).")) : h("div")),
      m !== "dual" ? h("div.grid2", h("label.f", h("span", m === "single" ? "Who screens" : "Who screens the rest on their own"), P.single)) : null,
      m !== "dual" ? h("fieldset", h("legend", "AI second screener"),
        h("label.inline", P.ai, h("span", "Use the AI as a second screener behind single screening")),
        h("p.small.muted", "Every record is also screened by two AI models. Any record the single reviewer excludes that the AI would keep is screened by the second reviewer below. The AI never excludes anything. Needs your AI service's address and API key (My settings)."),
        h("div.grid2", h("label.f", h("span", "Second reviewer for those records"), P.second), h("label.f", h("span", "Drop the AI step if it keeps more than (%) of records"), P.stop,
          h("small", "5 to 100. Then both reviewers screen everything instead.")))) : null);
  };
  const modes = [["dual", "Both reviewers screen every record", "The standard for a systematic review."],
    ["sample_then_single", "Both screen a random sample; one screens the rest if agreement is high", "If kappa on the sample reaches the threshold, one reviewer screens the rest; otherwise both screen everything. AMSTAR 2 accepts a sample screened by both with at least 80% agreement; Cochrane counts two screeners of titles and abstracts as desirable but not mandatory."],
    ["single", "One reviewer screens every record", "Not recommended: AMSTAR 2 expects at least a sample to be screened by two reviewers. The AI second screener lowers the chance of missing a study, but no reporting standard counts an AI as a second reviewer."]];
  modeBox.append(...modes.map(([v, l, d]) => h("label.inline", { style: { alignItems: "flex-start" } },
    h("input", { type: "radio", name: "mode", value: v, checked: P.mode === v, disabled: dis || started, onchange: () => { P.mode = v; drawPlan(); } }),
    h("span", h("strong", l), h("br"), h("span.small.muted", d)))));
  drawPlan();
  const conf = h("select", { disabled: dis || started }, h("option", { value: "forward", selected: P.conflicts === "forward" }, "Send the record on to full text (recommended)"),
    h("option", { value: "discuss", selected: P.conflicts === "discuss" }, "Decide together"));
  // a calibration round that cannot be finished (ReviewDesk 1.3.0 could start one with one reviewer): the lead ends it
  const stuck = (((S.ov || {}).sets || {}).main || {}).calibration_stuck;
  const stuckBox = stuck ? h("div.callout.warn", { role: "status" }, h("p", stuck),
    h("p", "Ending it lets screening go on with the plan as it is: the calibration records are screened with the others, and the practice votes are kept but never counted. The audit log records that the round was ended."),
    lead ? h("div.row", h("button.primary", { onclick: async () => {
      if (!await confirmBox("End the calibration round?", ["Screening goes on with the plan as it is. The practice votes are kept but never counted, and the reports do not describe a calibration round."], "End the calibration round")) return;
      await attempt(() => api("/api/screening/calibration", { set: "main" }), "Calibration round ended: screening goes on");
      render();
    } }, "End the calibration round")) : h("p.muted", "Only the project lead can end it.")) : null;
  page.append(h("div.panel#setup-plan", h("h2", { tabindex: "-1" }, "Screening plan"), stuckBox, modeBox, h("hr"), plan,
    h("label.f", h("span", "When two reviewers disagree at title and abstract"), conf)));

  const FT = { mode: h("select", { disabled: dis || started }, h("option", { value: "dual", selected: s.fulltext.mode === "dual" }, "Both reviewers assess every full text"),
    h("option", { value: "single", selected: s.fulltext.mode === "single" }, "One reviewer (not a systematic-review standard)")),
    lib: inp(s.fulltext.library_prefix, { placeholder: "e.g. https://yourlibrary.idm.oclc.org/login?url=" }),
    dis_default: h("textarea", { disabled: dis, rows: 3, style: { minHeight: "70px" }, value: s.fulltext.disagreement_default || "" }),
    upd: h("select", { disabled: dis }, [["inherit", "The same way the main screening ended"], ["dual", "By both reviewers"], ["single", "By one reviewer"]].map(([v, l]) => h("option", { value: v, selected: (s.other_sets.update || {}).mode === v }, l))),
    oth: h("select", { disabled: dis }, [["dual", "By both reviewers"], ["single", "By one reviewer"]].map(([v, l]) => h("option", { value: v, selected: (s.other_sets.other || {}).mode === v }, l))) };
  // someone working alone: "Both reviewers" cannot be used, so one reviewer is chosen for them (saved with Save settings)
  const alone = lead && !started && s.reviewers.filter((r) => r.id).length < 2;
  const preset = alone && FT.mode.value !== "single";     // (shown chosen; kept when Save settings is pressed)
  if (preset) FT.mode.value = "single";
  const ftAlone = alone ? h("small", "You are the only person in the project, so one reviewer assesses the full texts: “Both reviewers” needs a second person. Change it when a second reviewer joins (People), before screening starts.", preset ? " Press Save settings to keep it." : "") : null;
  page.append(h("div.panel#setup-fulltext", h("h2", { tabindex: "-1" }, "Full texts, update searches and other methods"),
    h("div.grid2", h("label.f", h("span", "Full-text review"), FT.mode, ftAlone), h("label.f", h("span", "Library link prefix"), FT.lib, h("small", "Put in front of a DOI link to go through your library's login. Ask your library for yours, or leave it empty."))),
    h("label.f", h("span", "If reviewers still disagree after discussion"), FT.dis_default,
      h("small", "Your protocol's rule, reported in the methods. For example: “Discuss; if still not agreed, include the study and report the disagreement.”")),
    h("div.grid2", h("label.f", h("span", "Records from the update search are screened"), FT.upd), h("label.f", h("span", "Records from other methods (citation searching, hand-searching) are screened"), FT.oth))));

  // the risk-of-bias plan: the tool for each study design (its own save; a change after screening starts is an amendment)
  const rp = h("div.panel#setup-robplan", h("h2", { tabindex: "-1" }, "Risk-of-bias plan"));
  page.append(rp);
  const nameOf = (id) => (s.reviewers.find((r) => r.id === id) || {}).name || id;
  const drawRobPlan = async () => {
    let pd;
    try { pd = await api("/api/rob/plan"); } catch (e) { rp.append(h("p.muted", e.message)); return; }
    const intro = h("p.muted", "For each study design the review includes, the tool used to assess risk of bias, as your protocol sets it. It is fixed before screening starts, so that the choice of tool cannot follow the results. Reviewers then choose a study's design when they add an assessment, and the tool follows.");
    if (!lead) { rp.replaceChildren(rp.firstChild, intro, planStatus(pd, nameOf), planList(pd), amendmentList(pd, nameOf)); return; }
    robPlan = planEditor(pd, { nameOf, draft: planDraft, onSaved: () => { if (!dirty) render(); else drawRobPlan(); } });
    planDraft = null;
    // what is typed in the plan is the plan's own (it is saved by its own button, not by Save settings)
    robPlan.node.addEventListener("input", (e) => e.stopPropagation());
    robPlan.node.addEventListener("change", (e) => e.stopPropagation());
    rp.replaceChildren(rp.firstChild, intro, planStatus(pd, nameOf), amendmentList(pd, nameOf), robPlan.node,
      h("div.row.end", h("button.primary", { onclick: () => robPlan.save(true) }, pd.recorded ? "Save the change to the plan" : "Record the risk-of-bias plan")));
  };
  await drawRobPlan();

  const K = { known: h("textarea", { disabled: dis, value: (s.known_studies || []).map((k) => k.doi || k.pmid || k.title).join("\n") }),
    regs: h("textarea", { disabled: dis, style: { minHeight: "60px" }, value: (s.registers || []).map((r) => `${r.name} | ${r.records}`).join("\n") }) };
  page.append(h("div.panel#setup-known", h("h2", { tabindex: "-1" }, "Known studies and registers"),
    h("label.f", h("span", "Known eligible studies"), K.known, h("small", "One per line: a DOI, a PubMed ID or a title. Used to check that the searches find them and, before you rely on it, that the AI keeps them.")),
    h("label.f", h("span", "Trial registers searched, for the PRISMA diagram"), K.regs, h("small", "Leave empty: ReviewDesk takes registers from the search log. Use only for a register you searched but did not log: one per line, as name | number of records (for example ClinicalTrials.gov | 14)."))));

  if (lead) page.append(h("div.setup-save", dirtyNote, h("button.primary", { onclick: async () => {
    const known = lines(K.known.value).map((x) => /^10\.\d{4,}\//.test(x) ? { doi: x } : /^\d{6,9}$/.test(x) ? { pmid: x } : { title: x });
    const regs = lines(K.regs.value).map((x) => { const [n, c] = x.split("|"); return { name: (n || "").trim(), records: parseInt(c, 10) || 0 }; });
    const changes = { title: F.title.value, short_title: F.short.value, question: F.question.value, review_type: F.type.value,
      reviewers: people.filter((r) => r.id || r.name).map((r) => ({ id: r.id, name: r.name, role: r.role })),
      criteria: { include: lines(C.include.value), exclude: lines(C.exclude.value), rules: lines(C.rules.value) },
      ft_reasons: lines(R.reasons.value), highlight: { include: lines(R.hin.value), exclude: lines(R.hex.value) },
      known_studies: known, registers: regs,
      registration: { registry: G.registry.value.trim(), id: G.id.value.trim(), date: G.date.value, url: G.url.value.trim() },
      dates: Object.fromEntries(Object.entries(D).map(([k, x]) => [k, x.value])),
      fulltext: { mode: FT.mode.value, library_prefix: FT.lib.value, disagreement_default: FT.dis_default.value },
      other_sets: { update: { mode: FT.upd.value }, other: { mode: FT.oth.value } } };
    if (!started) {
      const bad = [];
      const n = (x) => numOr(x.value, NaN);
      const flag = (field, ok, msg) => { field.setAttribute("aria-invalid", ok ? "false" : "true"); if (!ok) bad.push([field, msg]); };
      flag(P.frac, P.mode !== "sample_then_single" || (n(P.frac) >= 5 && n(P.frac) <= 90), "the agreement sample must be 5 to 90%");
      flag(P.kappa, P.mode !== "sample_then_single" || (n(P.kappa) >= 0.01 && n(P.kappa) <= 1), "the kappa needed must be a number between 0.01 and 1, such as 0.8");
      flag(P.cal, P.mode === "single" || Number.isInteger(n(P.cal)) && n(P.cal) >= 0 && n(P.cal) <= 1000, "calibration records must be a whole number from 0 to 1,000");
      flag(P.stop, !P.ai.checked || P.mode === "dual" || (n(P.stop) >= 5 && n(P.stop) <= 100), "the AI stopping limit must be 5 to 100%");
      if (bad.length) { bad[0][0].focus(); bad[0][0].scrollIntoView({ block: "center" }); toast("Check the screening plan: " + bad.map((x) => x[1]).join("; ") + ".", true); return; }
    }
    if (people.some((r) => (r.id || r.name) && !(r.id && r.name.trim()))) { toast("Give every person a name and initials.", true); return; }
    if (!started && P.r1.value && P.r1.value === P.r2.value) {
      P.r2.setAttribute("aria-invalid", "true"); P.r2.focus(); P.r2.scrollIntoView({ block: "center" });
      toast("The first and second reviewers must be two different people.", true); return;
    }
    P.r2.removeAttribute("aria-invalid");
    if (changes.registration.url && !/^https?:\/\//i.test(changes.registration.url)) { toast("The link to the registration should start with https:// (copy it from the registry's page).", true); G.url.focus(); return; }
    if (!started) changes.screening = { mode: P.mode, reviewers: [P.r1.value, P.r2.value].filter(Boolean), single_reviewer: P.single.value, second_reviewer: P.second.value,
      sample_fraction: numOr(P.frac.value, 20) / 100, kappa_threshold: numOr(P.kappa.value, 0.8), calibration_size: parseInt(P.cal.value, 10) || 0,
      ai_second_screener: P.ai.checked, ai_keep_rate_stop: numOr(P.stop.value, 50) / 100, conflicts_at_title_abstract: conf.value };
    const r = await attempt(() => api("/api/settings", { changes }), "Settings saved");
    dirty = false;
    // ticks in the risk-of-bias plan are recorded by the plan's own button: they are kept while the page is drawn again
    const planLeft = robPlan && robPlan.dirty();
    if (planLeft) planDraft = robPlan.state();
    if (r.result.changed_after_start.length) toast("Saved. You changed the criteria after screening started, so this is a protocol amendment: record why, and report it in your review (PRISMA 2020 item 24c).", true);
    else if (!r.result.changed.length && !planLeft) toast("Nothing had changed.");
    await render();
    const b = planLeft && document.querySelector("#setup-robplan .row.end button.primary");
    if (b) {
      if (!r.result.changed_after_start.length) toast(`${r.result.changed.length ? "Settings saved." : "Nothing else had changed."} Your ticks in the risk-of-bias plan are kept but not saved yet: now press “${b.textContent}”.`);
      b.scrollIntoView({ block: "center" }); b.focus({ preventScroll: true });
    }
  } }, "Save settings")));
  // a link from a "what is missing" item opens the panel it is about (#/setup/plan, #/setup/people, ...)
  // (after the page is drawn: render() then scrolls to the top)
  const target = args[0] && page.querySelector(`#setup-${CSS.escape(args[0])}`);
  if (target) setTimeout(() => { target.scrollIntoView({ block: "start" }); target.querySelector("h2")?.focus({ preventScroll: true }); }, 0);
}

// ------------------------------------------------------------------------------------------------ my settings
export async function settings(el) {
  const u = S.app.user;
  const page = h("div.page");
  el.append(page);
  const f = { name: h("input", { type: "text", value: u.my_name || "" }), email: h("input", { type: "email", value: u.unpaywall_email || "" }),
    key: h("input", { type: "password", placeholder: u.elm_key_set ? `A key is saved (${u.elm_key_hint}). Type a new one to replace it.` : "Your AI service's API key", autocomplete: "off" }),
    url: h("input", { type: "text", value: u.elm_url || "", placeholder: "e.g. https://api.example.org/v1" }) };
  const ELM = "https://elm.edina.ac.uk/api/v1";
  page.append(h("div.page-head", h("div.row", h("h1.grow", "My settings"), wizardBack()), h("p", "These belong to you on this computer. They are never written into the shared project folder.")),
    h("div.panel", h("label.f", h("span", "My name"), f.name),
      h("label.f", h("span", "My email address, for finding open-access full texts"), f.email, h("small", "Unpaywall and OpenAlex, free services that find legal open-access copies, ask for an email address with each look-up."))),
    h("div.panel", h("h2", "AI service (only needed by whoever runs the AI second screener)"),
      h("label.f", h("span", "Service address"), f.url, h("small", "The address of your institution's or provider's OpenAI-compatible service. Check your organisation's rules on sending records to it. ",
        h("button.link", { onclick: () => { f.url.value = ELM; } }, "Use the University of Edinburgh's ELM"))),
      h("label.f", h("span", "API key"), f.key, h("small", "Ask your AI service for a key (University of Edinburgh staff and students request one from ELM). It is kept in your personal settings on this computer (encrypted for your Windows account on Windows), never in the shared project folder, and is only ever sent to the address above.")),
      u.elm_key_set ? h("button.link", { onclick: async () => { await attempt(() => api("/api/user", { elm_key: "" }), "Key removed"); render(); } }, "Remove the saved key") : null),
    h("div.row.end", h("button.primary", { onclick: async () => {
      const body = { my_name: f.name.value, unpaywall_email: f.email.value, elm_url: f.url.value };
      if (f.key.value.trim()) body.elm_key = f.key.value.trim();
      const r = await attempt(() => api("/api/user", body), "Saved");
      if (r && r.key_cleared) toast("The service address changed, so the saved key was removed: a key is only ever sent to the service it was saved for. Enter the key for the new service.");
      render();
    } }, "Save")),
    h("p.small.muted", { style: { marginTop: "24px" } }, `Settings file: ${u.config_file}. ReviewDesk ${S.app.version}, Python ${S.app.python}.`));
}

// The project's progress (the Overview's figures) could not be read: said, with a way to try again.
export function progressMissing() {
  return h("div.page", h("h1", "The project's progress could not be read"),
    h("div.callout.warn", h("p", "ReviewDesk could not read the project's progress just now (it may be busy, or a file may be syncing). Try again in a moment.")),
    h("button.primary", { onclick: () => render() }, "Try again"));
}

// A typed number, or the default only when the box is empty or not a number (so 0 stays 0).
function numOr(v, dflt) { const x = parseFloat(String(v).replace(",", ".")); return Number.isFinite(x) ? x : dflt; }
