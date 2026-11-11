// Guided set-up, and the "what is missing" checks shown on the Overview and on each step's page.
import { api, h, toast, confirmBox, lines, bar, download } from "./lib.js";
import { S, go } from "./app.js";
import { planEditor, planStatus, amendmentList } from "./views_robplan.js";

// ------------------------------------------------------------------------------------------------ the checks
// One answer from /api/readiness per render (app.js asks for it with the overview). A page that draws itself again
// after a change (a search saved, a file added) asks again, so what it shows is never out of date.
let cache = { promise: null, used: new Set() };

export function loadReadiness() {
  cache = { promise: api("/api/readiness").catch(() => null), used: new Set() };
  return cache.promise;
}

function readiness(step) {
  if (!cache.promise || cache.used.has(step)) {
    loadReadiness();
    cache.promise.then((rd) => { if (rd) S.ready = rd; });
  }
  cache.used.add(step);
  return cache.promise;
}

// Where a fix goes inside the guided set-up, instead of the set-up page.
const FIX_SCREEN = { "setup/review": "review", "setup/registration": "registration", "setup/dates": "registration", "setup/people": "team",
  "setup/criteria": "criteria", "setup/reasons": "criteria", "setup/plan": "plan", "setup/fulltext": "fulltext", "setup/robplan": "fulltext", "setup/known": "ai",
  "searches/databases": "searches" };

// The items of some steps, missing ones first, each once.
function collect(rd, steps) {
  const seen = new Set(), need = [], check = [];
  for (const st of steps) for (const x of [...st.missing, ...st.warnings]) {
    const keys = x.keys || [x.key];
    if (keys.every((k) => seen.has(k))) continue;
    keys.forEach((k) => seen.add(k));
    (x.blocks ? need : check).push(x);
  }
  return [need, check];
}

// Items whose button does the thing itself rather than only opening the page (the disagreements open the dialog
// where the two decide together): [label, route].
export const DIRECT_FIX = { ft_conflicts: ["Decide together", "fulltext/decide"], ta_conflicts: ["Decide together", "screening/decide"] };

function itemRow(x, route, words) {
  const to = route === undefined ? x.fix : route;
  const direct = DIRECT_FIX[x.key];
  return h("li" + (x.blocks ? ".needed" : ".check"),
    h("span.rd-tag", x.blocks ? "Needed" : "Check"),
    h("span.rd-text", words ? words(x.text) : x.text),
    direct ? h("button.rd-fix", { onclick: () => go(direct[1]) }, direct[0])
      : to && x.fix_label ? h("button.rd-fix", { onclick: () => go(to) }, x.fix_label) : null);
}

function itemList(items, mapFix, words) {
  return h("ul.rd-list", items.map((x) => itemRow(x, mapFix ? mapFix(x.fix) : undefined, words)));
}

// The route of each page the banner is shown on: a fix that only opens the page already open is left out.
const PAGE_ROUTE = { screen: "screening" };

// A long list of things worth checking shows the first few, with the rest a click away.
function checkList(check, mapFix, keep = 3) {
  if (check.length <= keep + 1) return itemList(check, mapFix);
  return [itemList(check.slice(0, keep), mapFix),
    h("details.rd-more", h("summary", `${check.length - keep} more to check`), itemList(check.slice(keep), mapFix))];
}

// On a step's page: what must be done before this step can start (nothing is shown when nothing is missing).
// Called right after the page is attached; it may fill in later, and puts itself after the page heading.
export function readinessBanner(page, step) {
  readiness(step).then((rd) => {
    if (!rd || !page.isConnected) return;
    page.querySelectorAll(":scope > .rd-banner").forEach((x) => x.remove());
    const [need, check] = collect(rd, rd.steps.filter((s) => s.page === step));
    if (!need.length && !check.length) return;
    const here = PAGE_ROUTE[step] || step;
    const mapFix = (fix) => (fix === here ? "" : fix);
    const box = h("section.rd-banner" + (need.length ? ".has-needed" : ""), { "aria-labelledby": `rd-h-${step}` },
      h("h2", { id: `rd-h-${step}` }, need.length ? "Before you go on" : "Worth checking"),
      need.length ? [h("p.small", need.length === 1 ? "This is needed before this step can go ahead:" : `These ${need.length} things are needed before this step can go ahead:`),
        itemList(need, mapFix)] : null,
      need.length && check.length ? h("h3", "Worth checking") : null,
      check.length ? checkList(check, mapFix) : null);
    place(page, box);
  });
}

function place(page, box) {
  const head = page.querySelector(":scope > .page-head");
  if (head) { head.after(box); return; }
  const watch = new MutationObserver(() => {
    const hd = page.querySelector(":scope > .page-head");
    if (hd) { watch.disconnect(); hd.after(box); }
  });
  watch.observe(page, { childList: true });
  setTimeout(() => { if (!box.isConnected) { watch.disconnect(); page.prepend(box); } }, 2500);
}

// Steps with their items, each item once (under the first step that lists it).
function grouped(steps, seen = new Set()) {
  const groups = [];
  for (const st of steps) {
    const items = [...st.missing, ...st.warnings].filter((x) => {
      const keys = x.keys || [x.key];
      if (keys.every((k) => seen.has(k))) return false;
      keys.forEach((k) => seen.add(k));
      return true;
    });
    if (items.length) groups.push([st, items]);
  }
  return groups;
}

function groupBlock(st, items, mapFix, keep = 3, words) {
  return h("div.rd-group",
    h("h3", st.title, st.status === "blocked" ? h("span.pill.exclude", "Something is missing") : st.status === "later" ? h("span.pill", "Later") : null),
    itemList(items.slice(0, keep), mapFix, words),
    items.length > keep ? h("details.rd-more", h("summary", `${items.length - keep} more`), itemList(items.slice(keep), mapFix, words)) : null);
}

// On the Overview: everything still missing now, grouped by step, with a way to fix each.
export function readinessPanel(box) {
  readiness("overview").then((rd) => {
    if (!rd || !box.isConnected) return;
    // the report step repeats what the earlier steps still need, so it is listed here only once they are done
    const open = rd.steps.filter((st) => st.status === "now" || st.status === "blocked");
    const groups = grouped(open.filter((st) => st.id !== "report" || open.length === 1));
    const setup = rd.steps.find((s) => s.id === "setup");
    const guided = rd.is_lead && setup && setup.status !== "done";
    // the plan may be complete before the guided set-up is: its last screens (the searches, Check and finish) stay offered
    // until screening starts
    const started = !!S.ov && Object.values(S.ov.sets || {}).some((v) => v.phase && v.phase !== "not_started");
    const unfinished = rd.is_lead && !guided && !started && wizardUnfinished(S.app.project);
    if (!groups.length && !guided && !unfinished) { box.replaceChildren(); return; }
    const total = groups.reduce((a, [, xs]) => a + xs.length, 0);
    box.replaceChildren(h("section.panel.rd-panel", { "aria-labelledby": "rd-panel-h" },
      h("h2#rd-panel-h", "Before you go on"),
      h("p.small.muted", total ? "What is missing now, step by step. “Needed” means the review cannot go on correctly without it; “Check” is worth a look."
        : guided ? "Set-up is not finished yet." : "The guided set-up is not finished yet."),
      guided ? h("p.small", "Set-up is not finished: ", h("a", { href: "#/wizard" }, "go through it one topic at a time in the guided set-up"), ".") : null,
      unfinished ? h("p.small", "You have not reached the end of the guided set-up: ", h("a", { href: "#/wizard" }, "continue the guided set-up where you left it"), ".") : null,
      groups.map(([st, items]) => groupBlock(st, items))));
  });
}

// ------------------------------------------------------------------------------------------------ the guided set-up
const SCREENS = [
  { id: "review", title: "The review", nav: "The review" },
  { id: "registration", title: "Protocol registration and planned dates", nav: "Registration" },
  { id: "team", title: "The team", nav: "The team" },
  { id: "criteria", title: "Eligibility criteria", nav: "Criteria" },
  { id: "plan", title: "The screening plan", nav: "Screening plan" },
  { id: "fulltext", title: "Full texts, extraction and risk of bias", nav: "Full texts" },
  { id: "ai", title: "The AI second screener", nav: "AI screener", when: (s) => !!s.screening.ai_second_screener && s.screening.mode !== "dual" },
  { id: "searches", title: "The searches", nav: "Searches" },
  { id: "finish", title: "Check and finish", nav: "Finish" },
];
const TYPES = [["intervention", "Effects of an intervention"], ["diagnostic", "Diagnostic or test accuracy"], ["prognostic", "Prognosis or prediction models"],
  ["qualitative", "Qualitative evidence"], ["prevalence", "Prevalence or incidence"], ["aetiology", "Aetiology or risk factors"], ["scoping", "Scoping review"], ["other", "Other"]];

const numOr = (v, dflt) => { const x = parseFloat(String(v).replace(",", ".")); return Number.isFinite(x) ? x : dflt; };
const today = () => new Date().toISOString().slice(0, 10);

// A labelled field, with what it means and an example from an invented review of exercise for knee osteoarthritis (read out after the label, not as part of it).
let fieldSeq = 0;
function field(label, input, help, eg) {
  const id = input.id || `wz-f${++fieldSeq}`;
  input.id = id;
  input.removeAttribute("aria-label");
  const notes = [help ? h("small", { id: id + "-help" }, help) : null, eg ? h("small.eg", { id: id + "-eg" }, h("strong", "For example: "), eg) : null];
  const by = [help ? id + "-help" : "", eg ? id + "-eg" : ""].filter(Boolean).join(" ");
  if (by) input.setAttribute("aria-describedby", by);
  return h("div.wzf", h("label", { for: id }, label), input, notes);
}
const eg = (text) => h("p.eg", h("strong", "For example: "), text);

export async function wizard(el, args = []) {
  const p = S.app.project;
  if (args[0] === "summary") return summaryPage(el);
  if (!p.is_lead) return readOnly(el);
  const s = JSON.parse(JSON.stringify(p.settings));
  const screens = SCREENS.filter((x) => !x.when || x.when(s));
  let id = args[0];
  if (!screens.some((x) => x.id === id)) id = firstToDo(screens);
  const k = screens.findIndex((x) => x.id === id);
  const scr = screens[k];
  setReturn(p, "");                       // back in the guided set-up: My settings and the AI page no longer offer the way back
  const started = !!S.ov && Object.values(S.ov.sets || {}).some((v) => v.phase && v.phase !== "not_started");
  const page = h("div.page.wz");
  el.append(page);
  let dirty = false;
  const markDirty = () => { dirty = true; };
  page.addEventListener("input", markDirty);
  page.addEventListener("change", markDirty);
  S.leaveGuard = () => (dirty || !!(built && built.dirty && built.dirty())) && document.body.contains(page);

  // save what is on this screen (if anything changed), then go on: every button on a screen that opens another page
  // does this too (nav), and leaving by the side bar offers "Save and go"
  let built = null;
  const saveNow = async (msg) => {
    if ((dirty || (built && built.dirty && built.dirty())) && built && built.save) {   // (a screen may keep its own: the plan)
      const ok = await built.save();
      if (ok === false) return false;
      dirty = false;
      toast(msg || "Saved");
    }
    return true;
  };
  const saveThen = async (route, msg) => {
    if (!(await saveNow(msg))) return;
    S.leaveGuard = null;
    S.leaveSave = null;
    go(route);
  };
  S.leaveSave = () => saveNow();
  // a screen's button that opens another page (My settings, the AI page) leaves a way back to this screen there
  const nav = async (route) => { setReturn(p, `wizard/${scr.id}`); await saveThen(route); };
  built = await ({ review: scReview, registration: scRegistration, team: scTeam, criteria: scCriteria, plan: scPlan,
    fulltext: scFulltext, ai: scAi, searches: scSearches, finish: scFinish })[scr.id]({ s, p, started, screens, nav });
  const seen = seenScreens(p, scr.id);
  const prev = screens[k - 1], next = screens[k + 1];
  const steps = h("ol.wz-steps", screens.map((x, i) => {
    const past = i !== k && (i < k || seen.has(x.id));
    return h("li" + (i === k ? ".on" : past ? ".past" : ""),
      // (its name is what it shows, in the same order: "Screen 3: The team (seen)")
      h("button", { "aria-current": i === k ? "step" : null, onclick: () => (i === k ? null : saveThen(`wizard/${x.id}`)) },
        h("span.sr-only", `Screen ${i + 1}: `), h("span.n", { "aria-hidden": "true" }, past ? "✓" : String(i + 1)), h("span.l", x.nav),
        h("span.sr-only", past ? " (seen)" : i === k ? " (this screen)" : "")));
  }));
  const title = h("h1", { tabindex: "-1" }, scr.title);
  page.append(
    h("div.page-head", h("p.wz-eyebrow", "Guided set-up"), title, built.intro ? h("p", built.intro) : null),
    h("nav.wz-progress", { "aria-label": "Screens of the guided set-up" },
      h("p.wz-count", `Screen ${k + 1} of ${screens.length}`), bar(k + 1, screens.length, "Progress through the guided set-up"), steps),
    h("div.wz-body", built.nodes),
    h("div.wz-actions",
      prev ? h("button", { onclick: () => saveThen(`wizard/${prev.id}`) }, "Back") : null,
      h("div.grow"),
      next ? [h("button", { onclick: () => saveThen("overview", "Saved. Come back to the guided set-up from the Overview or Project set-up.") }, "Finish later"),
        h("button.primary", { onclick: () => saveThen(`wizard/${next.id}`) }, built.saveLabel || "Save and continue")]
        : h("button.primary", { onclick: () => saveThen("overview") }, "Finish")));
  // moving from screen to screen, keyboard and screen-reader users start at the new screen's heading
  if (args[0]) setTimeout(() => { if (title.isConnected && (!document.activeElement || document.activeElement === document.body)) title.focus({ preventScroll: true }); }, 0);
}

// The screens of the guided set-up this browser has shown for this project (with the one shown now), so that going back
// to an earlier screen keeps the ticks on the later ones, and the guided set-up opens again where it was left. Kept in
// the browser only: it is a convenience, not a record.
const seenKey = (p) => "rd-wizard-seen:" + (p.key || p.path || "");
function readSeen(p) {
  let seen;
  try { seen = JSON.parse(localStorage.getItem(seenKey(p)) || "[]"); } catch (e) { seen = []; }
  return new Set(Array.isArray(seen) ? seen : []);
}
function seenScreens(p, id) {
  const out = readSeen(p);
  out.add(id);
  try { localStorage.setItem(seenKey(p), JSON.stringify([...out])); } catch (e) { /* storage off: the ticks follow the order only */ }
  return out;
}

// The lead began the guided set-up in this browser but has not reached its last screen (Check and finish).
export function wizardUnfinished(p) {
  const seen = readSeen(p);
  return seen.size > 0 && !seen.has("finish");
}

// The screen of the guided set-up a button on My settings or the AI page goes back to ("" when they were not opened
// from it). Kept for this browser tab only.
const returnKey = (p) => "rd-wizard-return:" + (p.key || p.path || "");
function setReturn(p, route) {
  try { if (route) sessionStorage.setItem(returnKey(p), route); else sessionStorage.removeItem(returnKey(p)); } catch (e) { /* storage off */ }
}
export function wizardBack() {
  const p = S.app && S.app.project;
  let route = "";
  try { route = (p && p.is_lead && sessionStorage.getItem(returnKey(p))) || ""; } catch (e) { route = ""; }
  return /^wizard\/[a-z]+$/.test(route) ? h("button", { onclick: () => go(route) }, "Back to the guided set-up") : null;
}

// Inside the guided set-up, a message that points to a part of Project set-up names the screen of the guided set-up
// that has it instead.
const SETUP_PART = { "The review": "review", "Protocol registration": "registration", "People": "team", "Eligibility criteria": "criteria",
  "Full-text exclusion reasons": "criteria", "Screening plan": "plan", "Full texts": "fulltext", "Risk-of-bias plan": "fulltext", "Known studies and registers": "ai" };
function wizardWords(screens) {
  const name = Object.fromEntries(screens.map((x) => [x.id, x.nav]));
  return (text) => String(text).replace(/\(Project set-up, ([^,)]+)(, [^)]*)?\)/g, (m, part) => {
    const id = SETUP_PART[part.trim()];
    return id && name[id] ? `(guided set-up, ${name[id]})` : m;
  });
}

// Where the guided set-up opens: the first screen with something needed on it; otherwise the first screen this browser
// has not shown yet (where the lead left it); once every screen has been shown, Check and finish.
function firstToDo(screens) {
  const st = S.ready && S.ready.steps.find((x) => x.id === "setup");
  const want = new Set((st ? st.missing : []).map((x) => FIX_SCREEN[x.fix]).filter(Boolean));
  const seen = readSeen(S.app.project);
  return (screens.find((x) => want.has(x.id)) || (seen.size ? screens.find((x) => !seen.has(x.id)) || screens[screens.length - 1] : null)
    || screens[0]).id;
}

async function saveSettings(changes) {
  try {
    const r = await api("/api/settings", { changes });
    S.app.project = r.project;
    return true;
  } catch (e) { toast(e.message, true); return false; }
}

// (1) the review
function scReview({ s }) {
  const F = { title: h("input", { type: "text", value: s.title || "" }), short: h("input", { type: "text", value: s.short_title || "", maxlength: 60 }),
    type: h("select", TYPES.map(([v, l]) => h("option", { value: v, selected: s.review_type === v }, l))),
    question: h("textarea", { value: s.question || "", style: { minHeight: "90px" } }) };
  return {
    intro: "What the review is about. Everyone in the team screens against the question, so write it as your protocol does.",
    nodes: [h("div.panel",
      field("Title", F.title, "The full title, as in your protocol.", "“Exercise programmes for adults with knee osteoarthritis: a systematic review”"),
      field("Short title", F.short, "A few words. Used to name the files you download.", "“Knee OA exercise review”"),
      field("Type of question", F.type, "Decides which databases ReviewDesk suggests and which way of building the search (PICO and the like) it offers.",
        "the knee review asks whether exercise programmes reduce pain, so it is “Effects of an intervention”."),
      field("Review question", F.question, "One or two sentences. Shown beside every record during screening, and given to the AI screener word for word if you use it.",
        "“In adults with knee osteoarthritis, how do structured exercise programmes compare with usual care or no exercise for pain and physical function?”"))],
    save: async () => {
      if (!F.title.value.trim()) { toast("Give the review a title.", true); F.title.focus(); return false; }
      return saveSettings({ title: F.title.value.trim(), short_title: F.short.value.trim(), review_type: F.type.value, question: F.question.value.trim() });
    },
  };
}

// (2) protocol registration and planned dates
function scRegistration({ s }) {
  const reg = { registry: "", id: "", date: "", url: "", ...(s.registration || {}) };
  const dates = { start: "", searches: "", update_search: "", end: "", ...(s.dates || {}) };
  let yes = !!(reg.id || reg.url);
  const R = { registry: h("input", { type: "text", value: reg.registry, list: "wz-registries" }), id: h("input", { type: "text", value: reg.id }),
    date: h("input", { type: "date", value: reg.date, max: today() }), url: h("input", { type: "text", value: reg.url, inputmode: "url" }) };
  const D = Object.fromEntries(["start", "searches", "update_search", "end"].map((k) => [k, h("input", { type: "date", value: dates[k] })]));
  const fields = h("div");
  const draw = () => fields.replaceChildren(yes
    ? [h("div.grid2", field("Registry", R.registry, "Where it is registered.", "PROSPERO (for health and social care reviews) or OSF Registries."),
      field("Registration number", R.id, "As the registry gives it.", "PROSPERO numbers look like CRD42026 followed by six digits.")),
    h("div.grid2", field("Date registered", R.date), field("Link to the record", R.url, "The address of the record on the registry's website (starting https://).")),
    h("datalist#wz-registries", ["PROSPERO", "OSF Registries", "INPLASY", "Research Registry"].map((x) => h("option", { value: x })))]
    : [h("div.callout", h("p", "That is fine for now. Register it before you run the final searches, then come back and record the number here (or in Project set-up).")),
      field("Where you plan to register it (optional)", R.registry, "", "PROSPERO or OSF Registries."),
      h("datalist#wz-registries", ["PROSPERO", "OSF Registries", "INPLASY", "Research Registry"].map((x) => h("option", { value: x })))]);
  draw();
  const radio = (v, label) => h("label.inline", h("input", { type: "radio", name: "wz-reg", checked: yes === v, onchange: () => { yes = v; draw(); } }), h("span", label));
  return {
    intro: "Registering the protocol means publishing your plan (the question, the criteria and the methods) on a public register, with a date, before you search. Readers can then check that the review did what it planned and was not changed to fit what was found. PRISMA 2020 asks for the registry and number (item 24a).",
    nodes: [h("div.panel", h("fieldset", h("legend", "Has the protocol been registered?"), radio(true, "Yes, it is registered"), radio(false, "Not registered yet")), fields),
      h("div.panel", h("h2", "Planned dates"),
        h("p.muted", "The dates in your protocol's timetable. ReviewDesk uses them for the timeline and for reminders (for example when the update search is due). You can change them at any time."),
        h("div.grid2", field("Work starts", D.start), field("Final searches run", D.searches, "Run every database search within a week or two of each other.")),
        h("div.grid2", field("Update search", D.update_search, "The searches run again, unchanged, before the analysis."), field("Review finished", D.end)),
        eg("the knee review plans its searches for mid-November, and runs them again unchanged the following spring."))],
    save: async () => {
      const url = R.url.value.trim();
      if (yes && url && !/^https?:\/\//i.test(url)) { toast("The link should start with https:// (copy it from the registry's page).", true); R.url.focus(); return false; }
      if (yes && R.date.value && R.date.value > today()) { toast("The date registered is in the future: give the date it was registered.", true); R.date.focus(); return false; }
      if (D.update_search.value && D.searches.value && D.update_search.value < D.searches.value) { toast("The update search is planned before the final searches: check the dates.", true); D.update_search.focus(); return false; }
      if (D.end.value && D.start.value && D.end.value < D.start.value) { toast("The review is planned to finish before it starts: check the dates.", true); D.end.focus(); return false; }
      return saveSettings({ registration: yes ? { registry: R.registry.value.trim(), id: R.id.value.trim(), date: R.date.value, url }
        : { registry: R.registry.value.trim(), id: "", date: "", url: "" },
      dates: Object.fromEntries(Object.entries(D).map(([k, x]) => [k, x.value])) });
    },
  };
}

// (3) the team
function scTeam({ s, p, started }) {
  const people = s.reviewers.map((r) => ({ ...r }));
  const planned = new Set([...(s.screening.reviewers || []), s.screening.single_reviewer, s.screening.second_reviewer].filter(Boolean));
  const box = h("div");
  const draw = () => box.replaceChildren(h("div.tablewrap", h("table.t.wz-people", h("thead", h("tr", h("th", "Name"), h("th", "Initials"), h("th", "Role"), h("th", h("span.sr-only", "Remove")))),
    h("tbody", people.map((r, i) => h("tr",
      h("td", { "data-label": "Name" }, h("input", { type: "text", value: r.name, "aria-label": `Name of person ${i + 1}`, oninput: (e) => { r.name = e.target.value; } })),
      h("td", { "data-label": "Initials" }, h("input", { type: "text", value: r.id, maxlength: 4, "aria-label": `Initials of person ${i + 1}`,
        disabled: s.reviewers.some((x) => x.id === r.id),
        title: s.reviewers.some((x) => x.id === r.id) ? "Initials cannot change once saved: each person's work is recorded under them." : null,
        oninput: (e) => { r.id = e.target.value.toUpperCase(); } })),
      h("td", { "data-label": "Role" }, h("select", { "aria-label": `Role of person ${i + 1}`, onchange: (e) => { people.forEach((x) => (x.role = "reviewer")); r.role = e.target.value; if (e.target.value !== "lead") people[0].role = "lead"; draw(); } },
        h("option", { value: "lead", selected: r.role === "lead" }, "Project lead"), h("option", { value: "reviewer", selected: r.role !== "lead" }, "Reviewer"))),
      h("td", r.role !== "lead" && !(started && planned.has(r.id)) ? h("button.link", { "aria-label": `Remove ${r.name || "person " + (i + 1)}`, onclick: () => { people.splice(i, 1); box.dispatchEvent(new Event("change", { bubbles: true })); draw(); } }, "Remove") : null)))))),
    h("button", { style: { marginTop: "10px" }, onclick: () => { people.push({ id: "", name: "", role: "reviewer" }); draw(); box.querySelector(`input[aria-label="Name of person ${people.length}"]`)?.focus(); } }, "Add a person"));
  draw();
  return {
    intro: "Everyone who screens, extracts data or assesses risk of bias. Each person's votes are saved under their initials, in their own file in the project folder.",
    nodes: [h("div.panel", h("h2", "What the project lead does"),
      h("p", "The project lead writes the shared project files: the set-up, the records once duplicates are removed, the screening plan and the decisions agreed after a disagreement. The lead also starts each stage (screening, the agreement rule). Everyone else records their own votes and extraction answers. You are the project lead of this project."),
      eg("in an MSc review, the supervisor is often the project lead and the student is the reviewer.")),
    h("div.panel", h("h2", "People"), box,
      h("p.small.muted", { style: { marginTop: "10px" } }, "Initials: up to 4 letters or numbers, different for each person. They label each person's work, so they cannot change once saved: to correct someone else's, remove them and add them again before they start.",
        started ? " People in the screening plan cannot be removed now that screening has started." : ""))],
    save: async () => {
      const list = people.filter((r) => r.id || r.name);
      if (list.some((r) => !(r.id && r.name.trim()))) { toast("Give every person a name and initials.", true); return false; }
      const newLead = list.find((r) => r.role === "lead");
      if (newLead && newLead.id !== p.me && !(await confirmBox(`Make ${newLead.name} the project lead?`, "Only the project lead can change the set-up, so you would not be able to finish the guided set-up yourself.", "Make them the lead", true))) return false;
      return saveSettings({ reviewers: list.map((r) => ({ id: r.id, name: r.name.trim(), role: r.role })) });
    },
  };
}

// (4) eligibility criteria
function scCriteria({ s, started }) {
  const area = (arr, cls = "tall") => h("textarea", { class: cls, value: (arr || []).join("\n") });
  const C = { include: area(s.criteria.include), exclude: area(s.criteria.exclude), rules: area(s.criteria.rules, ""), reasons: area(s.ft_reasons, "") };
  return {
    intro: "The criteria decide which studies are in the review. Every record is screened against them, and they are shown beside each one. Write one point per line.",
    nodes: [started ? h("div.callout.warn", h("p", "Screening has started. A change to the criteria now is a protocol amendment: it is dated in the audit log; record why, and report it in your review (PRISMA 2020 item 24c). The AI screener keeps the criteria as they were when screening started.")) : null,
      h("div.panel",
        field("Include (every point must be met)", C.include, "What a study must have: for example its population, the tool or intervention, the setting and the study design.",
          "“Adults (18 or over) with knee osteoarthritis, diagnosed clinically or on imaging.”"),
        field("Exclude (any one point rules a record out)", C.exclude, "Reviewers vote Exclude only when one of these is clearly met.",
          "“Studies of people who have had a knee replacement, or animal studies.”"),
        field("Rules for borderline cases (optional)", C.rules, "What to do when the title and abstract leave it unclear.",
          "“If the abstract does not say whether participants had osteoarthritis, vote Maybe.” The knee review's rule is that a record is excluded only when a criterion is clearly not met."),
        field("Full-text exclusion reasons, in the order to apply them", C.reasons, "Reviewers record the first reason that applies, so list them in the order of your criteria. PRISMA reports how many reports were excluded for each.",
          "the knee review excludes non-English studies at full text and counts them, so “Not in English” is one of its reasons."))],
    save: () => saveSettings({ criteria: { include: lines(C.include.value), exclude: lines(C.exclude.value), rules: lines(C.rules.value) }, ft_reasons: lines(C.reasons.value) }),
  };
}

// (5) the screening plan
function scPlan({ s, started }) {
  const sc = s.screening;
  const ids = s.reviewers.filter((x) => x.id).map((x) => [x.id, `${x.name || x.id} (${x.id})`]);
  const sel = (val, label) => h("select", { disabled: started, "aria-label": label }, h("option", { value: "" }, "Choose…"), ids.map(([v, l]) => h("option", { value: v, selected: v === val }, l)));
  const P = { mode: sc.mode, r1: sel(sc.reviewers[0], "First reviewer"), r2: sel(sc.reviewers[1], "Second reviewer"), single: sel(sc.single_reviewer, "Who screens alone"), second: sel(sc.second_reviewer, "Second reviewer for records the AI would keep"),
    cal: h("input", { type: "number", min: 0, max: 1000, step: 10, value: sc.calibration_size, disabled: started }),
    frac: h("input", { type: "number", min: 5, max: 90, step: 1, value: Math.round(100 * sc.sample_fraction), disabled: started }),
    kappa: h("input", { type: "text", value: sc.kappa_threshold, spellcheck: false, autocomplete: "off", disabled: started, inputmode: "decimal" }),
    ai: h("input", { type: "checkbox", checked: !!sc.ai_second_screener, disabled: started }),
    stop: h("input", { type: "number", min: 5, max: 100, step: 5, value: Math.round(100 * sc.ai_keep_rate_stop), disabled: started }),
    conflicts: h("select", { disabled: started }, h("option", { value: "forward", selected: sc.conflicts_at_title_abstract !== "discuss" }, "Send the record on to full text (recommended)"),
      h("option", { value: "discuss", selected: sc.conflicts_at_title_abstract === "discuss" }, "Decide together")) };
  const modes = [["dual", "Both reviewers screen every record", "Each reviewer screens every title and abstract on their own, without seeing the other's votes. The standard for a systematic review."],
    ["sample_then_single", "Both screen a random sample first; one screens the rest if they agree well", "Both screen the same random share of the records. If their agreement on it is high enough, one reviewer screens the rest alone; otherwise both screen everything. AMSTAR 2 accepts this when the two agree on at least 80% of the sample, and Cochrane counts two screeners of titles and abstracts as desirable but not mandatory."],
    ["single", "One reviewer screens every record", "Not recommended: AMSTAR 2 expects at least a sample to be screened by two reviewers. The AI second screener lowers the chance of missing a study, but no reporting standard counts an AI as a second reviewer, so report single screening as a limitation."]];
  const plan = h("div");
  const draw = () => {
    const m = P.mode;
    plan.replaceChildren(
      h("div.grid2", field("First reviewer", P.r1), field("Second reviewer", P.r2)),
      h("p.small.muted", { style: { marginTop: "-6px" } }, m === "single"
        ? "With one reviewer screening, these are the people who assess full texts (when both assess them), judge risk of bias and rate certainty. If you work alone, choose yourself as the first reviewer and leave the second empty."
        : "The two people who screen in duplicate. They also assess risk of bias."),
      m === "single" ? h("p.small", "No calibration round: it is a practice round for two people who screen the same records, so there is none when one reviewer screens every record.") : field("Calibration records", P.cal, "A practice round: both reviewers screen the same records, then talk through every disagreement, so that you apply the criteria the same way before any votes count. 0 for none; at most 1,000.", "a review expecting a few thousand records might use 100."),
      m === "sample_then_single" ? h("div.grid2",
        field("Agreement sample (% of the records)", P.frac, "The random share both screen independently, without seeing each other's votes. 5 to 90.", "the knee review uses 20%."),
        field("Kappa needed for one reviewer to screen the rest", P.kappa, "Cohen's kappa measures how far two reviewers agree beyond what chance alone would give: 1 is perfect agreement and 0 is no better than chance. It is not a percentage: at 0.80 or more, the two also agree on at least 80% of the sample, as AMSTAR 2 asks, but a lower threshold may not. ", "the knee review uses 0.80.")) : null,
      m !== "dual" ? field(m === "single" ? "Who screens" : "Who screens the rest on their own", P.single, m === "sample_then_single" ? "One of the two reviewers above." : "") : null,
      m !== "dual" ? h("fieldset", h("legend", "AI second screener"),
        h("label.inline", P.ai, h("span", "Use the AI as a second screener behind single screening")),
        h("p.small.muted", "Choosing it adds one screen to this guided set-up: what the AI needs."),
        h("p.small", h("strong", "What it does: "), "two AI models from different developers each read every title and abstract against your criteria. Only the title, abstract and bibliographic details are sent."),
        h("p.small", h("strong", "What it never does: "), "it never excludes a record and never decides one. Any record the single reviewer excludes that the AI would keep goes to the second reviewer below. Nobody sees its answers until the agreement sample is finished, and then only the project lead."),
        h("div.grid2", field("Second reviewer for those records", P.second, "The other of the two reviewers."),
          field("Drop the AI step if it would keep more than (% of records)", P.stop, "If the AI keeps more than this, it is not narrowing anything down: the AI step is dropped and both reviewers screen everything. 5 to 100.",
            "50: in the knee review, if the AI keeps over half the records, both screen everything."))) : null);
  };
  const modeBox = h("fieldset", h("legend", "How are the records screened?"), modes.map(([v, l, d]) => h("label.inline.wz-mode",
    h("input", { type: "radio", name: "wz-mode", value: v, checked: P.mode === v, disabled: started, onchange: () => { P.mode = v; draw(); } }),
    h("span", h("strong", l), h("br"), h("span.small.muted", d)))));
  draw();
  return {
    saveLabel: started ? "Continue" : null,
    intro: "Who screens which records, and what decides it. The plan is fixed when screening starts, because the protocol pre-specifies it.",
    nodes: [started ? h("div.callout.warn", h("p", "Screening has started, so the screening plan is fixed: it is shown here as it was recorded. If the team works differently from now on, that is a protocol amendment: record why, and report it in your review (PRISMA 2020 item 24c).")) : null,
      h("div.panel", modeBox, eg("the knee review uses the middle one: both screen a calibration round, then a random 20% independently. If kappa is at least 0.80, one reviewer screens the rest with the AI second screener behind them; otherwise both screen everything."), h("hr"), plan,
        field("When the two reviewers disagree at title and abstract", P.conflicts, "Sending the record on is the cautious choice: anything either reviewer wants to see is read in full.", "the knee review sends on anything either reviewer includes."))],
    save: async () => {
      if (started) return true;
      const n = (x) => numOr(x.value, NaN);
      const bad = [];
      [P.frac, P.kappa, P.cal, P.stop, P.r2].forEach((x) => x.removeAttribute("aria-invalid"));
      if (P.mode === "sample_then_single" && !(n(P.frac) >= 5 && n(P.frac) <= 90)) bad.push([P.frac, "the agreement sample must be 5 to 90%"]);
      if (P.mode === "sample_then_single" && !(n(P.kappa) >= 0.01 && n(P.kappa) <= 1)) bad.push([P.kappa, "the kappa needed must be a number between 0.01 and 1, such as 0.8"]);
      if (P.mode !== "single" && !(Number.isInteger(n(P.cal)) && n(P.cal) >= 0 && n(P.cal) <= 1000)) bad.push([P.cal, "calibration records must be a whole number from 0 to 1,000"]);
      if (P.mode !== "dual" && P.ai.checked && !(n(P.stop) >= 5 && n(P.stop) <= 100)) bad.push([P.stop, "the AI stopping limit must be 5 to 100%"]);
      for (const [x] of bad) x.setAttribute("aria-invalid", "true");
      if (bad.length) { bad[0][0].focus(); toast("Check the screening plan: " + bad.map((x) => x[1]).join("; ") + ".", true); return false; }
      if (P.r1.value && P.r1.value === P.r2.value) {
        P.r2.setAttribute("aria-invalid", "true"); P.r2.focus();
        toast("The first and second reviewers must be two different people.", true); return false;
      }
      return saveSettings({ screening: { mode: P.mode, reviewers: [P.r1.value, P.r2.value].filter(Boolean), single_reviewer: P.single.value, second_reviewer: P.second.value,
        sample_fraction: numOr(P.frac.value, 20) / 100, kappa_threshold: numOr(P.kappa.value, 0.8), calibration_size: parseInt(P.cal.value, 10) || 0,
        ai_second_screener: P.mode !== "dual" && P.ai.checked, ai_keep_rate_stop: numOr(P.stop.value, 50) / 100, conflicts_at_title_abstract: P.conflicts.value } });
    },
  };
}

// (6) full texts, extraction and risk of bias
async function scFulltext({ s, started }) {
  const [x, pd] = await Promise.all([api("/api/extract"), api("/api/rob/plan")]);
  const ft = s.fulltext || {};
  const F = { mode: h("select", { disabled: started }, h("option", { value: "dual", selected: ft.mode !== "single" }, "Both reviewers assess every full text"),
    h("option", { value: "single", selected: ft.mode === "single" }, "One reviewer (not a systematic-review standard)")),
  dis: h("textarea", { value: ft.disagreement_default || "", rows: 3, style: { minHeight: "70px" } }),
  lib: h("input", { type: "text", value: ft.library_prefix || "", placeholder: "https://yourlibrary.idm.oclc.org/login?url=" }) };
  const pick = (cur, label) => h("select", { "aria-label": label }, h("option", { value: "" }, "Choose…"), x.reviewers.map((r) => h("option", { value: r.id, selected: r.id === cur }, `${r.name} (${r.id})`)));
  const ids = x.reviewers.map((r) => r.id);
  const E = { first: pick(x.form_full.first && ids.includes(x.form_full.first) ? x.form_full.first : x.people.first, "First extractor"),
    second: pick(x.form_full.second && ids.includes(x.form_full.second) ? x.form_full.second : x.people.second, "Second extractor") };
  const nameOf = (id) => (s.reviewers.find((r) => r.id === id) || {}).name || id;
  const plan = planEditor(pd, { nameOf });
  // someone working alone: "Both reviewers" cannot be used, so one reviewer is chosen (saved with this screen)
  const alone = !started && s.reviewers.filter((r) => r.id).length < 2;
  if (alone) F.mode.value = "single";
  return {
    intro: "How full texts are assessed, who extracts the data, and which risk-of-bias tool is used for each study design.",
    nodes: [h("div.panel", h("h2", "Full texts"),
      field("Full-text review", F.mode, started ? "Fixed now that screening has started." : alone ? "You are the only person in the project, so one reviewer assesses the full texts: “Both reviewers” needs a second person. Change it when a second reviewer joins (People), before screening starts." : "Both reviewers assessing every full text independently is the standard.",
        "in the knee review both reviewers assess every full text."),
      field("If the reviewers still disagree after discussing it", F.dis, "Your protocol's rule, in your own words. It is reported in the methods, and shown to you both when you decide a disagreement together.",
        "“Discuss; if still not agreed, include the study and report the disagreement.”"),
      field("Library link prefix (optional)", F.lib, "Put in front of a DOI link so it goes through your library's login and the PDF opens with the library's subscription. Ask your library for it, or leave it empty.")),
    h("div.panel", h("h2", "Data extraction"),
      h("p", "The first extractor extracts every item from each included study. The second checks those items against the paper, and extracts the outcome data independently: neither sees the other's outcome data until both have finished the study."),
      h("div.grid2", field("First extractor (extracts every item)", E.first), field("Second extractor (checks, and extracts outcome data)", E.second)),
      eg("in the knee review, one reviewer extracts the study characteristics and the project lead checks them; both extract the outcome data independently."),
      h("p.small.muted", "The extraction form itself is set up on the Extract data page: start from the general form and adapt it to your protocol.")),
    h("div.panel#wz-robplan", h("h2", "The risk-of-bias plan"),
      h("p", "For each study design your protocol says the review includes, the tool used to assess risk of bias. It is needed before screening starts and fixed once it starts, so that the choice of tool cannot follow the results. Reviewers then choose a study's design when they add an assessment, and the tool follows."),
      planStatus(pd, nameOf), amendmentList(pd, nameOf), plan.node,
      eg("a review of exercise for back pain might tick randomised trials (RoB 2) and cluster-randomised trials (RoB 2 for cluster-randomised trials); a review of a diagnostic test, diagnostic test accuracy studies (QUADAS-2); a review that also includes qualitative studies defines its appraisal checklist as a tool of its own."))],
    save: async () => {
      if (E.first.value && E.first.value === E.second.value) { toast("The first and second extractors must be different people.", true); E.second.focus(); return false; }
      if (E.first.value && ids.length > 1 && !E.second.value) { toast("Choose the second extractor.", true); E.second.focus(); return false; }
      const changes = { fulltext: { library_prefix: F.lib.value.trim(), disagreement_default: F.dis.value.trim() } };
      if (!started) changes.fulltext.mode = F.mode.value;
      if (!(await saveSettings(changes))) return false;
      if (E.first.value && (E.first.value !== x.form_full.first || E.second.value !== (x.form_full.second || ""))) {
        try { await api("/api/extract/form", { form: { ...x.form_full, first: E.first.value, second: E.second.value } }); }
        catch (e) { toast(e.message, true); return false; }
      }
      return plan.save();
    },
    dirty: () => plan.dirty(),
  };
}

// (7) the AI second screener: what the lead needs
function scAi({ s, nav }) {
  const u = S.app.user || {};
  const known = h("textarea", { value: (s.known_studies || []).map((k) => k.doi || k.pmid || k.title).join("\n") });
  const ok = (yes, text) => h("span.pill." + (yes ? "include" : "exclude"), yes ? "Done" : text);
  return {
    intro: "The AI second screener runs from the project lead's own computer, so a few things are set up there rather than in the project.",
    nodes: [h("div.panel", h("h2", "What you need, on your own computer"),
      h("ol.wz-needs",
        h("li", h("div", h("strong", "The AI service's address and key, under My settings. "), "They stay on this computer and are never put in the shared folder."),
          h("div.row", ok(!!(u.elm_url && u.elm_key_set), "Not set yet"), h("button", { onclick: () => nav("settings") }, "Open My settings"))),
        h("li", h("div", h("strong", "The two models, on the AI second screener page. "), "Check them (Check the connection lists what your service offers), choose two from different developers, and save the set-up."),
          h("div.row", h("button", { onclick: () => nav("ai") }, "Open the AI second screener"))),
        h("li", h("div", h("strong", "A run on every record before the agreement sample is finished. "), "Nobody sees the answers until the sample is finished, and then only you."))),
      eg("the knee review uses two large language models from different developers, hosted by the reviewers' university and sent only titles, abstracts and bibliographic details.")),
    h("div.panel", h("h2", "Known studies"),
      field("Eligible studies you already know", known, "One per line: a DOI, a PubMed ID or a title. ReviewDesk checks that the searches find every one, and, before you rely on it, that the AI keeps every one.",
        "two or three trials of exercise for knee osteoarthritis that you have already read."))],
    save: () => saveSettings({ known_studies: lines(known.value).map((v) => /^10\.\d{4,}\//.test(v) ? { doi: v } : /^\d{6,9}$/.test(v) ? { pmid: v } : { title: v }) }),
  };
}

// (8) the searches: saved into the search worksheet
async function scSearches({ s }) {
  const [g, ws] = await Promise.all([api("/api/guidance"), api("/api/worksheet")]);
  const qt = g.question_types.find((x) => x.id === (ws.question_type || s.review_type)) || g.question_types[0];
  const chosen = new Set(ws.databases || []);
  const dbs = g.databases.filter((d) => d.id !== "registers");
  const order = [...dbs.filter((d) => qt.core.includes(d.id)), ...dbs.filter((d) => !qt.core.includes(d.id))];
  const boxes = order.map((d) => {
    const cb = h("input", { type: "checkbox", value: d.id, checked: chosen.has(d.id), "aria-label": d.name });
    return h("div.dbcard", cb, h("div", h("strong", d.name), qt.core.includes(d.id) ? h("span.pill.include", { style: { marginLeft: "8px" } }, "Recommended") : null,
      h("p.small", { style: { margin: "2px 0 0" } }, d.what), h("p.small.muted", { style: { margin: 0 } }, `On: ${d.platforms}.`)));
  });
  const regs = h("textarea", { value: (ws.registers || []).join("\n"), style: { minHeight: "70px" } });
  const other = h("textarea", { value: (ws.other_sources || []).join("\n"), style: { minHeight: "70px" } });
  return {
    intro: "Where you will look for studies. ReviewDesk then checks that every one has a search logged, with what PRISMA-S (the reporting standard for searches) asks for.",
    nodes: [h("div.panel", h("h2", "Databases"), qt.note ? h("p.small.muted", qt.note) : null, h("div.wz-dbs", boxes),
      eg("the knee review searches three databases within one week: MEDLINE and Embase (on Ovid) and CENTRAL.")),
    h("div.panel", h("h2", "Trial registers and other sources"),
      field("Trial registers (one per line)", regs, "Registers of planned and ongoing studies.", "ClinicalTrials.gov and WHO ICTRP (the knee review lists registry entries but does not include them)."),
      field("Other ways you will look for studies (one per line, optional)", other, "", "hand-searching Osteoarthritis and Cartilage; backward and forward citation searching of the included studies."),
      h("p.small.muted", "You build each search and log it on the Plan and log searches page."))],
    save: async () => {
      const cur = await api("/api/worksheet");
      const picked = boxes.map((b) => b.querySelector("input")).filter((c) => c.checked).map((c) => c.value);
      const reg = lines(regs.value);
      // registers stay in the plan if any are named here, or if they were chosen on the search page without names
      if (reg.length || (chosen.has("registers") && !(ws.registers || []).length)) picked.push("registers");
      try { await api("/api/worksheet", { ...cur, databases: [...new Set(picked)], registers: reg, other_sources: lines(other.value) }); return true; }
      catch (e) { toast(e.message, true); return false; }
    },
  };
}

// (9) check and finish
function scFinish({ screens }) {
  const words = wizardWords(screens);
  const rd = S.ready;
  const want = ["setup", "register", "searches", "import", "duplicates", "calibration", "sample", "screen", "ai"];
  const steps = rd ? rd.steps.filter((x) => want.includes(x.id)) : [];
  const firstScreen = steps.find((x) => ["calibration", "sample", "screen"].includes(x.id));
  const show = steps.filter((x) => !["calibration", "sample", "screen"].includes(x.id) || x === firstScreen);
  const ids = new Set(screens.map((x) => x.id));
  const mapFix = (fix) => (FIX_SCREEN[fix] && ids.has(FIX_SCREEN[fix]) ? `wizard/${FIX_SCREEN[fix]}` : fix);
  const setup = steps.find((x) => x.id === "setup");
  const seen = new Set();
  const own = setup ? grouped([setup], seen) : [];
  const later = grouped(show.filter((x) => x.id !== "setup"), seen);
  const setupNeeded = setup ? setup.missing.length : 0;
  return {
    intro: "What is still missing, for the set-up and for the first steps of the work, and a summary of every choice to check against your protocol.",
    nodes: [
      !rd ? h("div.callout.warn", h("p", "The checks could not be read just now. Reload the page in a moment."))
        : !setupNeeded ? h("div.callout.good", h("p", h("strong", "The set-up is complete. "), "Nothing the screening plan needs is missing."))
          : h("div.callout.warn", h("p", h("strong", `The set-up is not complete: ${setupNeeded === 1 ? "one thing is" : setupNeeded + " things are"} still needed. `), "Screening cannot start until they are done.")),
      own.length ? h("section.panel", h("h2", "The set-up"), own.map(([st, items]) => itemList(items, mapFix, words))) : null,
      later.length ? h("section.panel.rd-panel", h("h2", "Needed later, before each step starts"),
        h("p.small.muted", "These are for later steps: you do not need them to finish the set-up, but each step checks them before it starts."),
        later.map(([st, items]) => groupBlock(st, items, mapFix, 4, words))) : null,
      h("section.panel", h("h2", "The set-up summary"),
        h("p", "A plain list of every choice you made. Check it against your protocol before screening starts, and keep a copy with your records."),
        h("div.row", h("a.btn", { href: "#/wizard/summary", target: "_blank" }, "Open the summary to print (new tab)"),
          h("button", { onclick: () => download("setup_summary") }, "Download the summary as text"))),
      h("p.muted", "You can change any of this later in Project set-up, until screening starts (after that the screening plan and the risk-of-bias plan are fixed).")],
  };
}

// ------------------------------------------------------------------------------------------------ the set-up summary
function summaryBody(sm) {
  return sm.sections.map((sec) => h("section.wz-sum", h("h2", sec.title),
    h("dl", sec.rows.map(([label, value]) => [h("dt", label), h("dd", Array.isArray(value) ? h("ol", value.map((v) => h("li", v))) : value)]))));
}

async function summaryPage(el) {
  const sm = await api("/api/setup_summary");
  el.append(h("div.page.wz-print",
    h("div.page-head", h("p.wz-eyebrow", "Set-up summary"), h("h1", sm.title),
      h("p", `Made by ReviewDesk ${sm.version} on ${new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}. Check every line against the protocol before screening starts.`)),
    h("div.row.no-print", h("button.primary", { onclick: () => window.print() }, "Print"), h("button", { onclick: () => download("setup_summary") }, "Download as text"),
      h("button", { onclick: () => go("wizard/finish") }, "Back to the guided set-up")),
    summaryBody(sm)));
}

async function readOnly(el) {
  const p = S.app.project;
  const lead = p.settings.reviewers.find((r) => r.role === "lead");
  const sm = await api("/api/setup_summary");
  el.append(h("div.page.wz",
    h("div.page-head", h("p.wz-eyebrow", "Guided set-up"), h("h1", "The project's set-up"),
      h("p", `Only the project lead (${lead ? lead.name : "the lead"}) can change the set-up. This is how it stands, so that you can check it against the protocol.`)),
    h("div.row", h("a.btn", { href: "#/wizard/summary", target: "_blank" }, "Open the summary to print (new tab)"),
      h("button", { onclick: () => download("setup_summary") }, "Download the summary as text")),
    summaryBody(sm)));
}
