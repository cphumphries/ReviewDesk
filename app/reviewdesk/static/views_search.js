// Planning and logging searches, importing exports, and checking duplicates.
import { api, h, toast, attempt, modal, lines, num, plural, download, busyPage, SETS, confirmBox, onPageLeave } from "./lib.js";
import { S, go } from "./app.js";
import { readinessBanner } from "./views_wizard.js";

let G = null;
async function guidance() { if (!G) G = await api("/api/guidance"); return G; }
// The worksheet as typed and not yet saved: kept while the page is drawn again (after a search is recorded, say), as
// long as nobody has saved the worksheet meanwhile. Leaving the page asks first.
let wsKept = null;

// ------------------------------------------------------------------------------------------------ searches
export async function searches(el, args) {
  const g = await guidance();
  let ws = await api("/api/worksheet");
  const log = (await api("/api/searchlog")).entries;
  const p = S.app.project;
  ws.concepts = ws.concepts || []; ws.databases = ws.databases || [];
  let savedJson = JSON.stringify(ws);
  if (wsKept && wsKept.rev === ws.rev && wsKept.saved === savedJson) ws = wsKept.ws;
  const unsaved = () => JSON.stringify(ws) !== savedJson;
  wsKept = { ws, rev: ws.rev, saved: savedJson };
  onPageLeave(() => { wsKept = null; });
  const qt = g.question_types.find((x) => x.id === (ws.question_type || p.settings.review_type)) || g.question_types[0];
  const page = h("div.page");
  el.append(page);
  readinessBanner(page, "searches");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", "Plan and log searches"),
    h("p", "Choose the databases, build the search one concept at a time, run it in each database, and record every search you run. You run the searches on each database's own website; ReviewDesk keeps the plan and the record.")));
  const tabs = [["databases", "Choose databases"], ["build", "Build the search"], ["syntax", "Syntax for each database"], ["log", `Search log (${log.length})`]];
  let tab = args[0] || "databases";
  const body = h("div");
  const tabBar = h("div.tabs", { role: "tablist", "aria-label": "Search planning" }, tabs.map(([k, l], i) => h("button" + (k === tab ? ".on" : ""),
    { role: "tab", "aria-selected": String(k === tab), tabindex: k === tab ? "0" : "-1", onclick: () => { tab = k; draw(); },
      onkeydown: (e) => { if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return; e.preventDefault();
        const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length; tab = tabs[n][0]; draw(); tabBar.children[n].focus(); } }, l)));
  page.append(tabBar, body);
  const save = async (msg) => {
    const r = await attempt(() => api("/api/worksheet", ws), msg);
    ws.rev = r.rev;
    savedJson = JSON.stringify(ws);
    wsKept = { ws, rev: ws.rev, saved: savedJson };
  };
  S.leaveGuard = () => document.body.contains(page) && unsaved();
  S.leaveSave = async () => { try { await save("Worksheet saved"); return true; } catch (e) { return false; } };
  S.leaveText = () => ["Save the search worksheet?", "You have changed the search worksheet (its concepts, words or headings) but not saved it. If you leave without saving, the changes are lost."];
  const draw = () => {
    [...tabBar.children].forEach((b, i) => { b.classList.toggle("on", tabs[i][0] === tab); b.setAttribute("aria-selected", String(tabs[i][0] === tab)); b.tabIndex = tabs[i][0] === tab ? 0 : -1; });
    body.replaceChildren(({ databases: drawDatabases, build: drawBuild, syntax: drawSyntax, log: drawLog })[tab]());
  };

  const drawDatabases = () => {
    const typeSel = h("select", { "aria-label": "Kind of question", onchange: async (e) => { ws.question_type = e.target.value; await save(); searches(el.replaceChildren() || el, ["databases"]); } },
      g.question_types.map((x) => h("option", { value: x.id, selected: x.id === qt.id }, x.label)));
    const dbs = Object.fromEntries(g.databases.map((d) => [d.id, d]));
    const chosen = new Set(ws.databases);
    const row = (id, recommended) => {
      const d = dbs[id];
      const cb = h("input", { type: "checkbox", checked: chosen.has(id), onchange: async (e) => {
        e.target.checked ? chosen.add(id) : chosen.delete(id); ws.databases = [...chosen]; await save(e.target.checked ? `${d.name} added to the plan` : `${d.name} removed`); } });
      cb.setAttribute("aria-label", d.name);
      return h("div.dbcard", cb, h("div", h("strong", d.name), recommended ? h("span.pill.include", { style: { marginLeft: "8px" } }, "Recommended") : null,
        h("p", { style: { margin: "4px 0 2px" } }, d.what), h("p.small.muted", { style: { margin: 0 } }, `On: ${d.platforms}. Subject headings: ${d.headings}.`),
        d.tip ? h("p.small", { style: { margin: "4px 0 0" } }, d.tip) : null));
    };
    return h("div",
      h("div.panel", h("label.f", h("span", "What kind of question is it?"), typeSel), qt.note ? h("div.callout", h("p", qt.note)) : null,
        h("h2.sub", "Recommended for this kind of question"), qt.core.map((id) => row(id, true)),
        h("h2.sub", { style: { marginTop: "16px" } }, "Worth considering"), qt.consider.map((id) => row(id, false)),
        h("h2.sub", { style: { marginTop: "16px" } }, "Others"), g.databases.filter((d) => !qt.core.includes(d.id) && !qt.consider.includes(d.id)).map((d) => row(d.id, false))),
      h("p.muted.small", "Check which of these your library subscribes to (your library lists its databases on its website). Search each database on one platform only."));
  };

  const drawBuild = () => {
    const fw = g.frameworks[g.question_types.find((x) => x.id === (ws.question_type || p.settings.review_type))?.framework || "pico"];
    const list = h("div");
    const drawConcepts = () => list.replaceChildren(...ws.concepts.map((c, i) => {
      const hd = Object.assign({}, c.headings || {});
      return h("fieldset", h("legend", `Concept ${i + 1}`),
        h("div.grid2", h("label.f", h("span", "Name"), h("input", { type: "text", value: c.name || "", oninput: (e) => (c.name = e.target.value) })),
          h("label.f", h("span", `Part of ${fw.label}`), h("select", { onchange: (e) => (c.element = e.target.value) }, h("option", { value: "" }, "Choose…"),
            fw.elements.map(([k, l]) => h("option", { value: k + " " + l, selected: c.element === k + " " + l }, `${k}: ${l}`))))),
        h("label.f", h("span", "Free-text words, one per line"), h("textarea", { value: (c.terms || []).join("\n"), oninput: (e) => (c.terms = lines(e.target.value)) }),
          h("small", "Include synonyms, abbreviations and spelling variants; mark truncation with * (triag*).")),
        h("div.grid2", ws.databases.filter((d) => d !== "registers" && d !== "epistemonikos").map((d) => h("label.f", h("span", `Subject headings: ${(g.databases.find((x) => x.id === d) || {}).name || d}`),
          h("textarea", { style: { minHeight: "60px" }, value: (hd[d] || []).join("\n"), oninput: (e) => { c.headings = c.headings || {}; c.headings[d] = lines(e.target.value); } })))),
        h("label.f", h("span", "Notes"), h("input", { type: "text", value: c.notes || "", oninput: (e) => (c.notes = e.target.value) })),
        h("div.row.end", h("button.link", { onclick: () => { ws.concepts.splice(i, 1); drawConcepts(); } }, "Remove this concept")));
    }));
    drawConcepts();
    const preview = () => modal((box, close) => box.append(h("h2", "The search, in plain form"),
      h("p.muted", "This shows the logic only. Each database needs its own field codes and headings: see Syntax for each database."),
      h("pre.mono", { style: { whiteSpace: "pre-wrap", background: "var(--paper)", padding: "14px", borderRadius: "6px" } },
        ws.concepts.map((c, i) => `(${(c.terms || []).map((t) => /\s/.test(t) && !/^".*"$/.test(t) ? `"${t}"` : t).join(" OR ") || "…"})  ← ${c.name || "concept " + (i + 1)}`).join("\nAND\n")),
      h("div.row.end", h("button.primary", { onclick: close }, "Close"))));
    return h("div",
      h("div.panel", h("h2", "How to build it"), h("ol", g.steps.map((s) => h("li", s)))),
      h("div.panel", h("h2", "Concepts"), ws.databases.length ? null : h("div.callout.warn", h("p", "Choose the databases first, so you can note each one's subject headings here.")),
        list, h("div.row", h("button", { onclick: () => { ws.concepts.push({ name: "", terms: [], headings: {} }); drawConcepts(); } }, "Add a concept"),
          h("div.grow"), h("button", { onclick: preview }, "Show the combined logic"), h("button.primary", { onclick: () => save("Worksheet saved") }, "Save worksheet"))),
      h("div.panel", h("h2", "Check before you run it"),
        h("div.press", g.checklist.map((c, k) => h("label", h("input", { type: "checkbox", checked: !!(ws.press || [])[k], onchange: async (e) => {
          ws.press = g.checklist.map((_, j) => !!(ws.press || [])[j]); ws.press[k] = e.target.checked; await save(); } }), h("span", c)))),
        h("p.small.muted", "ReviewDesk's own prompts, covering the areas of PRESS 2015 (McGowan J, et al. J Clin Epidemiol 2016;75:40-6). Ticks are saved with the worksheet. A librarian's peer review, using PRESS itself, is better still.")));
  };

  const drawSyntax = () => h("div", g.platforms.map((pl) => h("div.panel", h("h2", pl.name),
    h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": `Syntax for ${pl.name}` }, h("table.t.syntax", h("tbody", pl.rows.map(([a, b]) => h("tr", h("td", a), h("td.mono", b)))))),
    h("p.small", { style: { marginTop: "12px" } }, h("strong", "Exporting: "), pl.export))));

  const drawLog = () => {
    const tb = log.length ? h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Database"), h("th", "Platform"), h("th", "Date"), h("th.num", "Records"), h("th", "Export files"), h("th", "Run by"), h("th", h("span.sr-only", "Actions")))),
      h("tbody", log.map((e) => h("tr", h("td", h("strong", e.database)), h("td", e.platform), h("td.nowrap", e.date), h("td.num", e.results ? num(parseInt(String(e.results).replace(/,/g, ""), 10)) : ""),
        h("td.small", (e.files || []).map((f) => `${f.filename} (${num(f.records)})`).join(", ") || h("span.muted", "none yet"), e.mismatch ? h("div.err-under", e.mismatch) : null),
        h("td", e.by), h("td", (e.by === p.me || p.is_lead) ? h("button.link", { "aria-label": `Edit the ${e.database} search of ${e.date}`, onclick: () => editSearch(e, () => searches(el.replaceChildren() || el, ["log"])) }, "Edit") : null))))))
      : h("p.muted", "No searches recorded yet. Record each search as you run it, with the exact strategy and the number of results.");
    return h("div", h("div.row", { style: { marginBottom: "14px" } }, h("button.primary", { onclick: () => editSearch({}, () => searches(el.replaceChildren() || el, ["log"])) }, "Record a search"),
      h("div.grow"), h("button", { onclick: () => download("searchlog") }, "Download the search log")), tb,
      h("p.small.muted", { style: { marginTop: "12px" } }, "The search log is your PRISMA-S record: database, platform, dates covered, date searched, exact strategy, limits and number of results."));
  };
  draw();
}

function editSearch(e, done) {
  const g = G;
  modal((box, close) => {
    const f = { database: h("input", { type: "text", value: e.database || "", list: "dblist" }), platform: h("input", { type: "text", value: e.platform || "" }),
      date: h("input", { type: "date", value: e.date || new Date().toISOString().slice(0, 10) }), coverage: h("input", { type: "text", value: e.coverage || "", placeholder: "e.g. 1946 to 4 December 2026" }),
      string: h("textarea", { class: "tall mono", value: e.string || "" }), limits: h("input", { type: "text", value: e.limits || "", placeholder: "e.g. none; or English only" }),
      results: h("input", { type: "number", min: 0, step: 1, value: e.results || "" }), notes: h("input", { type: "text", value: e.notes || "" }) };
    box.append(h("h2", e.id ? "Edit the search record" : "Record a search"),
      h("datalist#dblist", g.databases.map((d) => h("option", { value: d.name }))),
      h("div.grid2", h("label.f", h("span", "Database"), f.database), h("label.f", h("span", "Platform or interface"), f.platform)),
      h("div.grid3", h("label.f", h("span", "Date searched"), f.date), h("label.f", h("span", "Dates covered"), f.coverage), h("label.f", h("span", "Records found"), f.results)),
      h("label.f", h("span", "The exact search, as run"), f.string, h("small", "Paste the full strategy, line by line, exactly as you ran it.")),
      h("div.grid2", h("label.f", h("span", "Limits and filters"), f.limits), h("label.f", h("span", "Notes"), f.notes)),
      h("div.row.end", e.id ? h("button.danger", { onclick: async () => {
        if (!await confirmBox("Delete this search record?", `The record of the ${e.database} search of ${e.date} will be removed from the search log.`, "Delete", true)) return;
        await attempt(() => api("/api/searchlog", { ...e, deleted: true }), "Deleted"); close(); done(); } }, "Delete") : null,
        h("div.grow"), h("button", { onclick: close }, "Cancel"),
        h("button.primary", { onclick: async () => {
          if (!f.database.value.trim()) { toast("Say which database or source was searched.", true); f.database.focus(); return; }
          if (!f.date.value) { toast("Give the date the search was run.", true); f.date.focus(); return; }
          if (f.date.value > new Date().toISOString().slice(0, 10)) { toast("The date searched is in the future.", true); f.date.focus(); return; }
          if (!f.string.value.trim() && !String(f.results.value).trim()) { toast("Paste the search as run and the number of records it found.", true); f.string.focus(); return; }
          await attempt(() => api("/api/searchlog", { id: e.id, by: e.by, database: f.database.value, platform: f.platform.value, date: f.date.value, coverage: f.coverage.value,
            string: f.string.value, limits: f.limits.value, results: f.results.value, notes: f.notes.value }), "Search recorded");
          close(); done();
        } }, "Save")));
  });
}

// ------------------------------------------------------------------------------------------------ import
let carryPending = null;           // files that could not be added stay on the list, with the reason

// Database names as people and export files write them, and the name ReviewDesk gives each.
const DB_NAMES = [["medline", "MEDLINE"], ["pubmed", "MEDLINE"], ["embase", "Embase"], ["cinahl", "CINAHL"], ["psycinfo", "APA PsycInfo"], ["scopus", "Scopus"],
  ["web of science", "Web of Science"], ["wos", "Web of Science"], ["savedrecs", "Web of Science"], ["central", "Cochrane CENTRAL"], ["cochrane", "Cochrane CENTRAL"], ["citation", "Citation searching"]];
// One name for a database however it is written ("Ovid MEDLINE", "MEDLINE (Ovid)", "CENTRAL"), to match a file to its search.
function dbKey(name) {
  const n = String(name || "").toLowerCase().trim();
  const m = DB_NAMES.find(([k]) => n.includes(k));
  return m ? m[1] : n.replace(/\(.*?\)/g, " ").replace(/\s+/g, " ").trim();
}

export async function importer(el) {
  const p = S.app.project;
  const r = await api("/api/imports");
  const log = (await api("/api/searchlog")).entries;
  const page = h("div.page");
  el.append(page);
  readinessBanner(page, "import");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", "Import records"),
    h("p", "Add the files you exported from each database (RIS, PubMed .nbib, Cochrane plain text, Web of Science, or CSV). Add every part if a database made you export in batches. The project lead then removes duplicates across all of them.")));
  const pending = carryPending || [];
  carryPending = null;
  const picker = h("input", { type: "file", multiple: true, accept: ".ris,.nbib,.txt,.csv,.medline", hidden: true, tabindex: "-1",
    onchange: (e) => { addFiles(e.target.files); e.target.value = ""; } });
  const drop = h("div.drop", h("p", { style: { margin: "0 0 10px" } }, "Drag export files here, or"),
    h("button.primary", { onclick: () => picker.click() }, "Choose files"), picker);
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); addFiles(e.dataTransfer.files); });
  const guessDb = (name) => {
    const n = name.toLowerCase();
    const m = DB_NAMES.find(([k]) => n.includes(k));
    return m ? m[1] : "";
  };
  // the search in the log for this database: the one search of it, or the one of its searches no file is linked to yet
  const matchSearch = (x) => {
    const want = dbKey(x.database);
    if (!want) return null;
    const same = log.filter((e) => dbKey(e.database) === want);
    if (same.length === 1) return same[0];
    const free = same.filter((e) => !(e.files || []).length && !pending.some((y) => y !== x && y.search_id === e.id));
    return free.length === 1 ? free[0] : null;
  };
  const autoLink = (x) => {
    if (x.linkedByHand) return;
    const e = matchSearch(x);
    x.search_id = e ? e.id : ""; x.search_date = e ? e.date : "";
  };
  const addFiles = (files) => {
    for (const f of files) {
      const x = { file: f, database: guessDb(f.name), set: "main", search_id: "", search_date: "",
        error: f.size ? "" : "This file is empty (0 bytes). Export it from the database again, then add the new file and remove this one." };
      pending.push(x);
      autoLink(x);
    }
    drawPending();
  };
  const pendBox = h("div");
  const drawPending = () => {
    if (!pending.length) { pendBox.replaceChildren(); return; }
    pendBox.replaceChildren(h("div.panel", h("h2", "Files to add"), h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "File"), h("th", "Database or source"), h("th", "Which search"), h("th", "Search log entry"), h("th", h("span.sr-only", "Actions")))),
      h("tbody", pending.map((x, i) => h("tr", h("td.small", x.file.name, h("br"), h("span.muted", `${num(Math.max(1, Math.round(x.file.size / 1024)))} KB`), x.error ? h("div.err-under", x.error) : null),
        h("td", h("input", { type: "text", value: x.database, list: "dbl2", "aria-label": `Database for ${x.file.name}`, oninput: (e) => (x.database = e.target.value),
          onchange: () => { const was = x.search_id; autoLink(x); if (x.search_id !== was) drawPending(); }, placeholder: "e.g. Embase" })),
        h("td", h("select", { "aria-label": `Which search ${x.file.name} is from`, onchange: (e) => (x.set = e.target.value) }, Object.entries(SETS).map(([k, l]) => h("option", { value: k, selected: x.set === k }, l)))),
        h("td", h("select", { "aria-label": `Search log entry for ${x.file.name}`, onchange: (e) => { x.linkedByHand = true; x.search_id = e.target.value; const s = log.find((q) => q.id === x.search_id); x.search_date = s ? s.date : ""; if (s && !x.database) { x.database = s.database; drawPending(); } } },
          h("option", { value: "" }, "Not linked"), log.map((s) => h("option", { value: s.id, selected: x.search_id === s.id }, `${s.database}, ${s.date}`))),
          x.search_id && !x.linkedByHand ? h("div.small.muted", "Matched by its database name: check it.") : null),
        h("td", h("button.link", { "aria-label": `Remove ${x.file.name} from the list`, onclick: () => { pending.splice(i, 1); drawPending(); } }, "Remove"))))))),
      h("datalist#dbl2", ["MEDLINE", "Embase", "Cochrane CENTRAL", "CINAHL", "APA PsycInfo", "Scopus", "Web of Science", "Citation searching", "Reference lists", "Hand-searching"].map((d) => h("option", { value: d }))),
      h("p.small.muted", { style: { marginTop: "10px" } }, "Which search: database searches for the main searches; update search for a re-run later; other methods for citation searching, reference lists and hand-searching."),
      h("div.row.end", h("button.primary", { onclick: upload }, pending.some((x) => x.error) ? `Try again (${plural(pending.length, "file")})` : `Add ${plural(pending.length, "file")}`))));
  };
  const upload = async () => {
    for (const x of [...pending]) {
      if (!x.database.trim()) { toast(`Say which database ${x.file.name} comes from.`, true); return; }
    }
    let ok = 0, bad = 0;
    for (const x of [...pending]) {
      if (!x.file.size) { bad++; continue; }
      const q = new URLSearchParams({ filename: x.file.name, set: x.set, database: x.database, search_id: x.search_id, search_date: x.search_date });
      try {
        const m = await api("/api/imports/upload?" + q.toString(), undefined, { raw: x.file });
        toast(`${x.file.name}: ${plural(m.parse.records, "record")} read`);
        pending.splice(pending.indexOf(x), 1); ok++;
      } catch (e) { x.error = e.message; bad++; }
    }
    if (bad) toast(`${plural(bad, "file was", "files were")} not added: see the reason under ${bad === 1 ? "its" : "each"} name.`, true);
    drawPending();
    if (ok) { carryPending = pending.slice(); importer(el.replaceChildren() || el); }
  };
  // the search each imported file came from, which can be chosen or changed after it was added (by whoever added it, or
  // the lead): the search log then checks the file holds every record its search found
  const linkCell = (m) => {
    const e = log.find((x) => x.id === m.search_id);
    const mayLink = (m.added_by === p.me || p.is_lead) && !m.damaged;
    if (!mayLink) return h("div.muted", e ? `${e.database}, ${e.date}` : "Not linked to a search");
    const name = m.original_name || m.filename;
    const guess = !e && log.filter((x) => dbKey(x.database) === dbKey(m.database));
    return h("div.link-cell", h("select", { "aria-label": `Search log entry for ${name}`, onchange: async (ev) => {
      await attempt(() => api("/api/imports/link", { id: m.id, search_id: ev.target.value }),
        ev.target.value ? `${name} linked to its search` : `${name} is no longer linked to a search`);
      const label = ev.target.getAttribute("aria-label");
      await importer(el.replaceChildren() || el);
      [...el.querySelectorAll(".link-cell select")].find((x) => x.getAttribute("aria-label") === label)?.focus();
    } }, h("option", { value: "" }, "Not linked"), log.map((x) => h("option", { value: x.id, selected: x.id === m.search_id }, `${x.database}, ${x.date}`))),
      guess && guess.length === 1 ? h("div.small", "Probably ", h("strong", `${guess[0].database}, ${guess[0].date}`), ": choose it above.") : null);
  };
  const unprocessed = r.imports.filter((m) => !m.processed && !m.missing);
  const missing = r.imports.filter((m) => m.missing);
  const damaged = r.imports.filter((m) => m.damaged);
  page.append(h("div.panel", drop), pendBox);
  drawPending();
  if (r.imports.length) {
    const sum = { read: 0 }; r.imports.forEach((m) => (sum.read += m.parse.records));
    page.append(h("div.panel", h("div.row", h("h2.grow", "Imported files"),
      p.is_lead ? h("button.primary", { disabled: !unprocessed.length, onclick: async () => {
        const res = await busyPage(page, "Removing duplicates… this can take a minute for a large search.", () => attempt(() => api("/api/imports/process", {})));
        toast(`${plural(res.summary.records, "unique record")}; ${plural(res.summary.duplicates, "duplicate")} removed`);
        go("duplicates");
      } }, unprocessed.length ? `Remove duplicates and add ${plural(unprocessed.length, "file")} to the records` : "All files are in the records")
        : (unprocessed.length ? h("span.lead-only", "The project lead adds these to the records") : null)),
      damaged.length ? h("div.callout.bad", { style: { marginTop: "12px" } }, h("p", `The details file of ${plural(damaged.length, "import")} cannot be read (it is damaged): ${damaged.map((m) => m.filename + ".meta.json").join(", ")}. Restore it from OneDrive's version history (right-click it in the project's imports folder). Nothing is processed until it is readable.`)) : null,
      missing.length ? h("div.callout.warn", { style: { marginTop: "12px" } }, h("p", `${plural(missing.length, "file has", "files have")} not arrived in the project folder yet (only ${missing.length > 1 ? "their" : "its"} details have). If the folder is still syncing, wait for it to finish; nothing is processed until every file is here.`)) : null,
      h("div.tablewrap", { style: { marginTop: "12px" } }, h("table.t", h("thead", h("tr", h("th", "File"), h("th", "Database"), h("th", "Search"), h("th.num", "Records"), h("th.num", "No title"), h("th.num", "No abstract"), h("th", "Added"), h("th", "Status"), h("th", h("span.sr-only", "Actions")))),
        h("tbody", r.imports.map((m) => h("tr", h("td.small", { title: "Saved in the project as " + m.filename }, m.original_name || m.filename), h("td", m.database),
          h("td.small", SETS[m.set], linkCell(m)), h("td.num", num(m.parse.records)),
          h("td.num", num(m.parse.no_title || 0)), h("td.num", num(m.parse.no_abstract)), h("td.small", `${m.added_by}, ${m.added.slice(0, 10)}`),
          h("td", m.damaged ? h("span.pill.exclude", { title: "Its details file cannot be read: restore it from OneDrive's version history" }, "Details damaged")
            : m.missing ? h("span.pill.exclude", "File not here yet") : m.processed ? h("span.pill.include", "In the records") : h("span.pill", "Waiting")),
          h("td", p.is_lead ? h("button.link", { "aria-label": `Remove ${m.original_name || m.filename}`, onclick: async () => {
            const text = m.processed ? `${m.original_name || m.filename} will be moved out of the project's imports and the records rebuilt without it. This is possible only before screening of its records starts.`
              : `${m.original_name || m.filename} will be moved out of the project's imports.`;
            if (await confirmBox("Remove this file?", text, "Remove", true)) { await attempt(() => api("/api/imports/remove", { id: m.id }), "Removed"); importer(el.replaceChildren() || el); } } }, "Remove") : null)))),
        h("tfoot", h("tr", h("td", h("strong", "Total")), h("td"), h("td"), h("td.num", h("strong", num(sum.read))), h("td", { colspan: 5 }))))),
      r.dedup ? h("p.small.muted", { style: { marginTop: "10px" } }, `Last de-duplicated ${r.dedup.processed.slice(0, 16).replace("T", " ")} by ${r.dedup.by}: ${plural(r.records, "unique record")}. `, h("a", { href: "#/duplicates" }, "Check the duplicates")) : null));
  }
}

// ------------------------------------------------------------------------------------------------ duplicates
export async function duplicates(el) {
  const p = S.app.project;
  const d = await api("/api/dedup");
  const page = h("div.page.wide");
  el.append(page);
  readinessBanner(page, "duplicates");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", { tabindex: "-1" }, "Check duplicates"),
    h("p", "ReviewDesk merges records that are the same report found in more than one database. The rule is cautious, so most of these are certain; glance down the list for any pair that is clearly two different reports.")));
  if (!d.processed) { page.append(h("div.callout", h("p", "Nothing has been de-duplicated yet. Import the files first."), h("button", { onclick: () => go("import") }, "Go to import"))); return; }
  const files = d.files || [];
  page.append(h("div.panel", h("h2", "By file"), h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": "Records by file" }, h("table.t", h("thead", h("tr", h("th", "File"), h("th", "Database"), h("th.num", "Read"), h("th.num", "Duplicates of earlier records"), h("th.num", "Empty"), h("th.num", "New records"))),
    h("tbody", files.map((f) => h("tr", h("td.small", { title: "Saved in the project as " + f.filename }, f.original_name || f.filename), h("td", f.database), h("td.num", num(f.read)), h("td.num", num(f.duplicates)), h("td.num", num(f.empty)), h("td.num", num(f.new))))),
    h("tfoot", h("tr", h("td", h("strong", "Total")), h("td"), h("td.num", num(files.reduce((a, f) => a + f.read, 0))), h("td.num", num(files.reduce((a, f) => a + f.duplicates, 0))),
      h("td.num", num(files.reduce((a, f) => a + f.empty, 0))), h("td.num", h("strong", num(files.reduce((a, f) => a + f.new, 0)))))))),
    h("p.small.muted", { style: { marginTop: "10px" } }, "Files are read MEDLINE first, then Embase and the others, so the fullest record is the one kept."),
    h("div.row", { style: { marginTop: "6px" } }, h("button", { onclick: () => download("dedup") }, "Download the de-duplication log"),
      h("button", { onclick: () => modal((box, close) => box.append(h("h2", "The matching rule"), h("p", d.rule), h("p.muted.small", "Report this, word for word or summarised, in your methods (PRISMA-S item 16)."), h("div.row.end", h("button.primary", { onclick: close }, "Close")))) }, "Show the matching rule"))));
  // possible duplicates
  // after a decision the page is rebuilt: come back to the same place, with the keyboard focus on the next pair
  const again = async (fn, msg, rowIndex) => {
    const y = window.scrollY;
    await busyPage(page, "Re-checking duplicates…", () => attempt(fn, msg));
    await duplicates(el.replaceChildren() || el);
    window.scrollTo(0, y);
    if (rowIndex !== undefined) (el.querySelectorAll("tr[data-pair] button")[2 * rowIndex] || el.querySelector("tr[data-pair] button") || el.querySelector("h1"))?.focus();
  };
  if (d.possible.length) {
    const same = d.possible.filter((x) => x.identical && !x.started);
    const apart = d.possible.filter((x) => x.kinds_differ && !x.identical && !x.started);
    page.append(h("div.panel", h("h2", `Pairs to check (${num(d.possible.length)})`),
      h("p.muted", "These were not merged, but they may be the same report: their titles are almost the same, or they share a DOI or PubMed ID. Decide for each pair. A conference abstract, a preprint, a translation or a letter about a study is a separate report, so keep those apart."),
      p.is_lead && apart.length ? h("p", h("button", { onclick: async () => {
        const pairs = apart.map((x) => [x.a.id, x.b.id]);
        if (!await confirmBox(`Keep ${plural(pairs.length, "pair")} apart?`, `In each of these pairs one record is a different kind of report from the other (a conference abstract, preprint or letter beside an article). PRISMA 2020 counts them as separate reports, so they are kept apart and leave this list. Look down the table first if you are unsure.`, "Keep them apart")) return;
        await again(() => api("/api/dedup/notdup", { pairs }), `${plural(pairs.length, "pair")} kept apart`, 0); } },
        `Keep apart the ${plural(apart.length, "pair")} where one is a conference abstract, preprint or letter`)) : null,
      p.is_lead && same.length ? h("p", h("button", { onclick: async () => {
        const pairs = same.map((x) => [x.a.id, x.b.id]);
        if (!await confirmBox(`Merge ${plural(pairs.length, "pair")}?`, `Each of these ${pairs.length === 1 ? "pair has" : "pairs has"} the same title, year, first author and journal, and neither is a conference abstract, preprint, letter or translation. Check a few in the table first. A merge can be undone until screening starts.`, "Merge them")) return;
        await again(() => api("/api/dedup/merge", { pairs }), `${plural(pairs.length, "pair")} merged`, 0); } },
        `Merge the ${plural(same.length, "pair")} with the same title, year, first author and journal`)) : null,
      h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "First record"), h("th", "Second record"), h("th", "Why listed"), h("th", h("span.sr-only", "Actions")))),
        h("tbody", d.possible.slice(0, 150).map((x, k) => h("tr", { "data-pair": k }, h("td.small", recBit(x.a)), h("td.small", recBit(x.b)),
          h("td.small.why", x.why || "very similar titles", h("div.muted", `title ${Math.round(100 * x.similarity)}% alike`), x.identical ? h("div.muted", "same title, year, author and journal") : null),
          h("td.nowrap", x.started ? h("span.small.muted", "Screening has started: give both the same study name at full text if they are one study.")
            : p.is_lead ? [h("button", { "aria-label": `Same report: ${x.a.id} and ${x.b.id}`, onclick: () => again(() => api("/api/dedup/merge", { keep: x.a.id, drop: x.b.id }), "Merged", k) }, "Same report"), " ",
              h("button", { "aria-label": `Different reports: ${x.a.id} and ${x.b.id}`, onclick: () => again(() => api("/api/dedup/notdup", { kept: x.a.id, other: x.b.id }), "Kept apart", k) }, "Different")] : null)))))),
      d.possible.length > 150 ? h("p.small.muted", `Showing the first 150 of ${num(d.possible.length)}.`) : null));
  }
  // merges
  let q = "", rule = "", off = 0;
  const box = h("div");
  const rules = [...new Set(d.merges.map((m) => m.rule))];
  const draw = () => {
    const rows = d.merges.filter((m) => (!rule || m.rule === rule) && (!q || (m.title + m.kept_title).toLowerCase().includes(q)));
    box.replaceChildren(h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Kept"), h("th", "Merged into it"), h("th", "Matched on"), h("th", h("span.sr-only", "Actions")))),
      h("tbody", rows.slice(off, off + 50).map((m) => h("tr", h("td.small", h("span.muted", m.kept + " "), m.kept_title, h("br"), h("span.muted", `${m.kept_year} ${m.kept_journal}`)),
        h("td.small", m.title, h("br"), h("span.muted", `${m.year} ${m.journal || ""}`)), h("td.small", m.rule),
        h("td", p.is_lead && (m.fingerprint || m.from_record) ? h("button.link", { "aria-label": `${m.from_record ? "Undo the merge into" : "Not the same as"} ${m.kept}`, onclick: () =>
          again(() => api("/api/dedup/notdup", { kept: m.kept, other: m.fingerprint || m.from_record }), "Kept apart: the second record now has its own ID") }, m.from_record ? "Undo this merge" : "Not the same") : null)))))),
      h("div.row", { style: { marginTop: "10px" } }, h("span.small.muted", plural(rows.length, "merge")), h("div.grow"),
        h("button", { disabled: off === 0, onclick: () => { off = Math.max(0, off - 50); draw(); } }, "Previous"),
        h("button", { disabled: off + 50 >= rows.length, onclick: () => { off += 50; draw(); } }, "Next")));
  };
  page.append(h("div.panel", h("h2", `Merged records (${num(d.merges.length)})`),
    h("div.row", { style: { marginBottom: "12px" } }, h("input", { type: "text", placeholder: "Filter by title", "aria-label": "Filter merges by title", style: { maxWidth: "320px" }, oninput: (e) => { q = e.target.value.toLowerCase(); off = 0; draw(); } }),
      h("select", { "aria-label": "Filter merges by rule", style: { maxWidth: "300px" }, onchange: (e) => { rule = e.target.value; off = 0; draw(); } }, h("option", { value: "" }, "Every rule"), rules.map((r) => h("option", { value: r }, r)))), box));
  draw();
  if (p.is_lead) page.append(h("div.row.end", h("button.primary", { onclick: () => go("screening") }, "Done: go to screening")));
}

function recBit(r) {
  return [h("span.muted", r.id + " "), r.title, h("br"), h("span.muted", [r.year, r.journal, r.doi].filter(Boolean).join(" · ")),
    r.pubtype || (r.found_in || []).length ? h("div.muted", [r.pubtype, (r.found_in || []).length ? "found in " + r.found_in.join(", ") : ""].filter(Boolean).join("; ")) : null];
}
