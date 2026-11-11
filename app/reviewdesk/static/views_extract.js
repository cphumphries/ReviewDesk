// Data extraction and risk of bias: the list of included studies, and the study page (PDF beside the form).
import { api, h, toast, attempt, modal, confirmBox, num, plural, fileUrl, download, lines, nameWords, carryToast, drafts, keepDraft, unsavedNote, listen } from "./lib.js";
import { S, go, render } from "./app.js";
import { readinessBanner } from "./views_wizard.js";

const YESNO = ["Yes", "No", "Unclear", "Not reported"];
const nameOf = (id) => { const r = S.app.project.settings.reviewers.find((x) => x.id === id); return r ? r.name || id : id ? `${id} (no longer in the project)` : "nobody yet"; };
// a person's first name, for short labels ("Use Sam's"); titles such as Dr or Prof are skipped
const first = (s) => (/\(no longer in the project\)$/.test(String(s || "")) ? String(s) : nameWords(s)[0] || String(s || ""));
// How severe a judgement is on its tool's scale (the tool says; a scale with no order has none): the colour of its pill
const sevClass = (t, j) => { const s = ((t && t.severity) || {})[j]; return s === undefined || s === null ? "" : s <= 0.5 && s >= 0 ? "include" : s === 1 ? "maybe" : s >= 2 ? "exclude" : ""; };
const OVERALL = new Set(["O", "AO"]);

// a value as text; a table as "Column: value; …" per row, in the form's column order
const show = (v, fl) => {
  if (!Array.isArray(v)) return v ?? "";
  const cols = (fl && fl.columns) || [];
  return v.map((r) => (cols.length ? cols.map((c) => [c.label, r[c.id]]) : Object.entries(r)).filter(([, x]) => String(x ?? "").trim())
    .map(([k, x]) => `${k}: ${x}`).join("; ")).filter(Boolean).join(" | ");
};

// ------------------------------------------------------------------------------------------------ the list
export async function list(el) {
  const d = await api("/api/extract");
  const page = h("div.page.wide");
  el.append(page);
  readinessBanner(page, "extract");          // what is missing before this step (views_wizard.js)
  const ppl = d.people;
  const gone = ppl.gone || [];
  const who = nameOf;
  page.append(h("div.page-head", h("h1", "Extract data"),
    h("p", `Each included study is extracted in full by ${who(ppl.first)}. ${ppl.second ? `${who(ppl.second)} checks every item against the paper, and extracts the outcome data independently: neither sees the other's outcome data until both have finished the study. ` : ""}Risk of bias is judged by both reviewers, independently, for each result, then agreed.`)));
  if (gone.length && !d.is_lead) page.append(h("div.callout.warn", h("p", `${gone.join(" and ")} ${gone.length === 1 ? "is" : "are"} named as an extractor but no longer in the project. Their part of the extraction waits until the lead chooses the extractors again.`)));
  if (d.is_lead) page.append(formPanel(d));
  if (!d.studies.length) {
    page.append(h("div.callout", h("p", "No studies are included yet. Studies appear here when both reviewers include a report at full text (reports given the same study name are one study).")));
    return;
  }
  const done = d.studies.filter((s) => s.done_first && (s.done_second || !ppl.second)).length;
  page.append(h("div.stat", h("div", h("b", num(d.studies.length)), h("span", "included studies")),
    h("div", h("b", num(done)), h("span", "extracted by both")),
    h("div", h("b", num(d.studies.reduce((a, s) => a + s.to_agree, 0))), h("span", "items still to agree")),
    h("div", h("b", num(d.studies.reduce((a, s) => a + s.rob.both_done, 0)) + " / " + num(d.studies.reduce((a, s) => a + s.rob.assessments, 0))), h("span", "risk-of-bias assessments finished by both"))));
  const st = (done_, started) => done_ ? h("span.pill.include", "Finished") : started ? h("span.pill.maybe", "Started") : h("span.pill", "Not started");
  page.append(h("div.tablewrap", h("table.t", h("caption.sr-only", "Included studies"),
    h("thead", h("tr", h("th", "Study"), h("th", nameOf(ppl.first)), ppl.second ? h("th", nameOf(ppl.second)) : null,
      h("th.num", "To agree"), h("th", "Risk of bias"))),
    h("tbody", d.studies.map((s) => h("tr.click", { onclick: (e) => { if (!e.target.closest("a")) go("x/" + s.key); } },
      h("td", h("a.rowlink", { href: "#/x/" + encodeURIComponent(s.key) }, s.label), h("div.small.muted", `${plural(s.reports.length, "report")}: ${s.reports.join(", ")}`)),
      h("td", st(s.done_first, s.started_first)), ppl.second ? h("td", st(s.done_second, s.started_second)) : null,
      h("td.num", s.done_first && (s.done_second || !ppl.second) ? (s.to_agree ? h("span.pill.action", num(s.to_agree)) : "0") : "—"),
      h("td.small", s.rob.assessments ? `${num(s.rob.both_done)} of ${plural(s.rob.assessments, "assessment")} finished by both` : "none yet")))))));
  if (d.orphans && d.orphans.length) page.append(h("div.callout.warn", { style: { marginTop: "14px" } },
    h("p", `Extraction or risk-of-bias answers are kept for ${plural(d.orphans.length, "report")} that ${d.orphans.length === 1 ? "is" : "are"} no longer included: ${d.orphans.map((o) => `${o.key}${o.study ? ` (${o.study})` : o.title ? ` (${o.title.slice(0, 50)}…)` : ""}`).join(", ")}. They come back if the report is included again (check its full-text decision).`)));
  page.append(h("div.panel", { style: { marginTop: "18px" } }, h("h2", "Downloads"),
    h("p.muted", "Only values both extractors have finished, and agreed where they differed, go into the workbook; risk of bias the same. Each shows what is still open, and the workbook lists where in each paper the values came from."),
    h("div.row", h("button", { onclick: () => download("extraction") }, "Extraction (Excel)"), h("button", { onclick: () => download("rob") }, "Risk of bias (Excel)"),
      h("button", { onclick: () => download("rob_svg") }, "Risk-of-bias summary (picture)"))));
}

function formPanel(d) {
  const f = d.form_full;
  const started = d.studies.some((s) => s.started_first || s.started_second);
  const ids = d.reviewers.map((r) => r.id);
  const pick = (id, cur) => h("select", { id }, h("option", { value: "" }, "Choose…"), d.reviewers.map((r) => h("option", { value: r.id, selected: r.id === cur }, `${r.name} (${r.id})`)));
  const fsel = pick("x-first", f.first && ids.includes(f.first) ? f.first : d.people.first);
  const ssel = pick("x-second", f.second && ids.includes(f.second) ? f.second : d.people.second);
  const gone = [f.first, f.second].filter((x) => x && !ids.includes(x));
  const items = (f.sections || []).reduce((a, s) => a + s.fields.length, 0);
  const check = () => {
    if (!fsel.value) { toast("Choose the first extractor.", true); fsel.focus(); return false; }
    if (d.reviewers.length > 1 && !ssel.value) { toast("Choose the second extractor.", true); ssel.focus(); return false; }
    if (fsel.value === ssel.value) { toast("The first and second extractors must be different people.", true); ssel.focus(); return false; }
    return true;
  };
  const useTemplate = async (t) => {
    if (!check()) return;
    if (started && !(await confirmBox("Replace the form?", "Extraction has started. Answers already given are kept, but items the new form does not have are no longer shown or exported, and items that already have answers must keep their kind and who extracts them.", "Replace the form", true))) return;
    await attempt(() => api("/api/extract/form", { template: t, first: fsel.value, second: ssel.value }), "Form saved");
    render();
  };
  return h("div.panel", h("h2", "The extraction form"),
    h("p", h("strong", f.name || "Extraction form"), ` — ${plural(items, "item")}${f.rev ? `, revision ${f.rev}` : " (not saved yet)"}.`),
    gone.length ? h("div.callout.warn", h("p.small", `${gone.join(" and ")} ${gone.length === 1 ? "is" : "are"} named as an extractor but no longer in the project. Choose the extractors again and save.`)) : null,
    h("div.grid2", h("label.f", { for: "x-first" }, h("span", "First extractor (extracts every item)"), fsel),
      h("label.f", { for: "x-second" }, h("span", "Second extractor (checks, and extracts outcome data)"), ssel)),
    h("div.row", h("button", { onclick: () => useTemplate("generic") }, "Use the general form"),
      h("button", { onclick: () => { if (check()) editForm(f, fsel.value, ssel.value, d.answered || []); } }, "Edit the items"),
      h("button", { onclick: async () => { if (!check()) return; await attempt(() => api("/api/extract/form", { form: { ...f, first: fsel.value, second: ssel.value } }), "Saved"); render(); } }, "Save the extractors")));
}

function editForm(f0, firstId, secondId, answeredIds) {
  const f = JSON.parse(JSON.stringify(f0));
  const answered = new Set(answeredIds);
  const wasIn = {};                     // every item this form has had, with its section, so a removed one can be put back
  for (const [id, it] of Object.entries(f0.removed_items || {})) wasIn[id] = JSON.parse(JSON.stringify(it));
  for (const sec of f0.sections || []) for (const fl of sec.fields) wasIn[fl.id] = { ...JSON.parse(JSON.stringify(fl)), section: sec.title };
  const kindName = { text: "short text", long: "long text", number: "number", choice: "one of a list", yesno: "yes / no", table: "table" };
  modal((box, close) => {
    const body = h("div");
    const putBack = (id) => {
      const { section, ...it } = wasIn[id];
      let sec = f.sections.find((x) => x.title === section);
      if (!sec) { sec = { title: section || "Put back", fields: [] }; f.sections.push(sec); }
      sec.fields.push(it);
      draw();
      body.querySelector(`[data-item="${CSS.escape(id)}"] input`)?.focus();
    };
    const removedList = () => {
      const here = new Set(f.sections.flatMap((s) => s.fields.map((x) => x.id)).filter(Boolean));
      const out = Object.keys(wasIn).filter((id) => !here.has(id));
      if (!out.length) return null;
      return h("fieldset", h("legend", "Removed items"),
        h("p.small.muted", "Answers to a removed item are kept. Put it back to show them again."),
        out.map((id) => h("div.row", h("span", `${wasIn[id].label} (${kindName[wasIn[id].type] || wasIn[id].type}, ${wasIn[id].mode === "both" ? "both extract" : "first extracts, second checks"})${answered.has(id) ? " — has answers" : ""}`),
          h("button.small", { "aria-label": `Put back ${wasIn[id].label} (${kindName[wasIn[id].type] || wasIn[id].type}, ${wasIn[id].mode === "both" ? "both extract" : "first extracts, second checks"})`, onclick: () => putBack(id) }, "Put it back"))));
    };
    const draw = () => {
      body.replaceChildren(...f.sections.map((sec, si) => h("fieldset", h("legend", `Section ${si + 1}: ${sec.title}`),
        h("label.f", h("span", "Section title"), h("input", { type: "text", value: sec.title, oninput: (e) => (sec.title = e.target.value) })),
        sec.fields.map((fl, fi) => h("div.edit-item", { role: "group", "aria-label": fl.label || "New item", "data-item": fl.id || "" },
          h("div.grid2", h("label.f", h("span", "Item"), h("input", { type: "text", value: fl.label, oninput: (e) => (fl.label = e.target.value) })),
            h("label.f", h("span", "Kind of answer"), h("select", { onchange: (e) => { fl.type = e.target.value; draw(); } },
              [["text", "Short text"], ["long", "Long text"], ["number", "Number"], ["choice", "One of a list"], ["yesno", "Yes / No / Unclear / Not reported"], ["table", "Table (rows of results)"]]
                .map(([v, l]) => h("option", { value: v, selected: fl.type === v }, l))))),
          h("label.f", h("span", "Who"), h("select", { onchange: (e) => (fl.mode = e.target.value) },
            h("option", { value: "check", selected: fl.mode !== "both" }, "First extracts, second checks"),
            h("option", { value: "both", selected: fl.mode === "both" }, "Both extract independently (outcome data)"))),
          fl.type === "choice" ? h("label.f", h("span", "Choices, one per line"), h("textarea", { value: (fl.options || []).join("\n"), oninput: (e) => (fl.options = lines(e.target.value)) })) : null,
          fl.type === "table" ? h("label.f", h("span", "Columns, one per line"), h("textarea", { value: (fl.columns || []).map((c) => c.label).join("\n"),
            oninput: (e) => (fl.columns = lines(e.target.value).map((l, k) => ({ id: (fl.columns || [])[k]?.label === l ? fl.columns[k].id : l.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 30) || `c${k}`, label: l, type: "text" }))) })) : null,
          h("label.f", h("span", "Help for the extractors (optional)"), h("input", { type: "text", value: fl.help || "", oninput: (e) => (fl.help = e.target.value) })),
          fl._ask ? h("div.row.ask-remove", h("span.small", `“${fl.label}” has answers. Removing it hides them (they are kept, and come back if you put the item back).`),
            h("button.small.danger", { onclick: () => { delete fl._ask; sec.fields.splice(fi, 1); draw(); } }, "Remove it"),
            h("button.small", { onclick: () => { delete fl._ask; draw(); } }, "Keep it"))
            : h("div.row", h("button.link.small", { onclick: () => {
              if (fl.id && answered.has(fl.id)) { fl._ask = true; draw(); body.querySelector(".ask-remove button")?.focus(); return; }
              sec.fields.splice(fi, 1); draw();
            } }, `Remove "${fl.label || "this item"}"`)))),
        h("div.row", h("button.small", { "aria-label": `Add an item to this section (${sec.title || "a new section"})`, onclick: () => { sec.fields.push({ label: "", type: "text", mode: "check", help: "" }); draw(); [...body.querySelectorAll("fieldset")[si].querySelectorAll(".edit-item")].pop()?.querySelector("input")?.focus(); } }, "Add an item to this section"),
          sec.fields.length ? null : h("button.link.small", { onclick: () => { f.sections.splice(si, 1); draw(); } }, "Remove this empty section")))),
        h("button", { onclick: () => { f.sections.push({ title: "New section", fields: [] }); draw(); } }, "Add a section"), removedList());
    };
    draw();
    box.classList.add("wide");
    box.append(h("h2", "Edit the extraction form"),
      h("p.muted", "Renaming an item keeps its answers; removing one hides its answers (they are kept in the project, and come back if you put the item back). Once an item has answers, its kind of answer and who extracts it cannot change: add a new item instead. Outcome data should be extracted by both, independently."),
      body, h("div.row.end", h("button", { onclick: close }, "Cancel"),
        h("button.primary", { onclick: async () => { await attempt(() => api("/api/extract/form", { form: { ...f, first: firstId, second: secondId } }), "Form saved"); close(); render(); } }, "Save the form")));
  });
}

// ------------------------------------------------------------------------------------------------ the study page
let pendingSaves = 0;          // answers typed but not yet saved: the page asks before it is closed
window.addEventListener("beforeunload", (e) => { if (pendingSaves > 0) { e.preventDefault(); e.returnValue = ""; } });
const keepPlace = {};          // where the side panel was scrolled, per study and tab, across a reload of the page

export async function room(el, args) {
  const key = args[0];
  const tabWanted = args[1] || "";
  const d = await api("/api/extract/study?key=" + encodeURIComponent(key));
  if (d.study.key !== key) { location.replace(`#/x/${encodeURIComponent(d.study.key)}${tabWanted ? "/" + tabWanted : ""}`); return; }
  const p = S.app.project;
  const me = p.me, st = d.state, f = d.form;
  const flds = f.sections.flatMap((s) => s.fields);
  const reps = d.study.reports;
  let rep = (reps.find((r) => r.pdf) || reps[0]).id;
  const live = h("div.sr-only", { "aria-live": "polite" });

  // the PDF, with a choice of report when a study has several
  const pdfPane = h("div.pdfbox");
  const pdfLink = h("a.small", { target: "_blank", rel: "noopener" }, "Open the PDF in a new tab");
  const drawPdf = () => {
    const r = reps.find((x) => x.id === rep);
    const url = fileUrl(`/files/fulltext/${encodeURIComponent(r.id)}.pdf`);
    pdfLink.href = url;
    pdfLink.hidden = !r.pdf;
    const chooser = reps.length > 1 ? h("div.row.report-pick", h("label.small", { for: "x-report" }, "Report"),
      h("select#x-report", { onchange: (e) => { rep = e.target.value; drawPdf(); } }, reps.map((x) => h("option", { value: x.id, selected: x.id === rep }, `${x.id}: ${x.title.slice(0, 70)}${x.pdf ? "" : " (no PDF)"}`)))) : null;
    pdfPane.replaceChildren(chooser, r.pdf ? h("iframe", { src: url + "#navpanes=0&view=FitH", title: `Full text of ${r.id}` })
      : h("div.nopdf", h("h2", "No PDF for this report"), h("p.muted", "Add it on the full-text page for this report, then come back."),
        h("a", { href: "#/ft/" + encodeURIComponent(r.id) }, `Open ${r.id} on the full-text page`)));
  };
  drawPdf();

  const role = st.role;
  const myDone = role === "first" ? st.done_first : role === "second" ? st.done_second : false;
  const canEdit = (fl) => {
    if (!role || myDone) return false;
    if (role === "second" && fl.mode === "check" && !st.done_first) return false;
    return true;
  };

  // ---- saving: one save at a time per item, in order; typing saves after a short pause, leaving a box saves at once.
  // A save that is refused (OneDrive has the file open, or ReviewDesk was closed) is remembered and tried again: after
  // the next save that works, when ReviewDesk answers again, before the study is finished and before the page is left.
  const queues = {};
  const savedMarks = {};
  const failed = new Map();      // what could not be saved: key -> { label, retry() }
  const markSaved = (fid) => { const m = savedMarks[fid]; if (m) { m.textContent = "Saved"; m.classList.remove("notsaved"); m.hidden = false; clearTimeout(m._t); m._t = setTimeout(() => (m.hidden = true), 2500); } };
  const markFailed = (fid) => { const m = savedMarks[fid]; if (m) { clearTimeout(m._t); m.textContent = "Not saved yet"; m.classList.add("notsaved"); m.hidden = false; } };
  let retrying = null;
  const retryFailed = () => {
    if (!failed.size) return Promise.resolve();
    if (!retrying) retrying = Promise.all([...failed.values()].map((x) => x.retry())).finally(() => { retrying = null; });
    return retrying;
  };
  let hadFailed = false;
  const worked = () => {                         // the folder can be written again
    if (failed.size) { setTimeout(retryFailed, 0); return; }
    if (hadFailed) { hadFailed = false; toast("The answers that could not be saved before are saved now."); }
  };
  const refused = (k, label, retry, e) => {
    const first = !failed.has(k);
    failed.set(k, { label, retry });
    hadFailed = true;
    if (first || !retrying) toast(`${e.message} ${failed.size === 1 ? "This answer is" : "These answers are"} kept on this page and saved as soon as ReviewDesk can: ${[...failed.values()].map((x) => x.label).join("; ")}.`, true);
  };
  const saveValue = (fl, getValue, getWhere, input) => {
    pendingSaves++;
    const v0 = getValue(), where0 = getWhere();
    st.mine[fl.id] = { ...(st.mine[fl.id] || {}), v: v0, pg: where0 };     // shown at once if the tab is redrawn
    queues[fl.id] = (queues[fl.id] || Promise.resolve()).then(async () => {
      const v = getValue(), where = getWhere();
      try {
        await api("/api/extract/value", { key, f: fl.id, v, pg: where });
        st.mine[fl.id] = { v, pg: where };
        failed.delete(fl.id);
        if (input) input.removeAttribute("aria-invalid");
        live.textContent = `Saved: ${fl.label}`;
        markSaved(fl.id);
        worked();
      } catch (e) {
        if (input) input.setAttribute("aria-invalid", "true");
        markFailed(fl.id);
        refused(fl.id, fl.label, () => saveValue(fl, getValue, getWhere, input), e);
      } finally { pendingSaves--; }
    });
    return queues[fl.id];
  };
  const flushers = new Set();
  const typing = (el, fn) => {                // save 1.2 s after typing stops, and at once on leaving the box
    let t = null;
    const now = () => { if (t) { clearTimeout(t); t = null; pendingSaves--; flushers.delete(now); fn(); } };
    el.addEventListener("input", () => {
      if (t) clearTimeout(t); else { pendingSaves++; flushers.add(now); }
      t = setTimeout(now, 1200);
    });
    el.addEventListener("change", () => { if (t) now(); else fn(); });
  };
  const flush = async () => { [...flushers].forEach((f) => f()); await Promise.all(Object.values(queues)); };
  // everything typed is sent and every refused save tried again; what is still not saved, by name
  const flushAll = async () => { await flush(); if (failed.size) { await retryFailed(); await Promise.all(Object.values(queues)); } return [...failed.values()].map((x) => x.label); };
  const notSavedText = (left) => `ReviewDesk could not save ${left.length === 1 ? "this answer" : `these ${left.length} answers`} yet: ${left.join("; ")}. It keeps trying; wait a moment (OneDrive may have the file open), or check ReviewDesk is running.`;
  listen(window, "rd-online", () => retryFailed());
  // before the page is left: what is typed is sent and refused saves tried again; anything still not saved is asked about
  S.flushOnLeave = () => (document.body.contains(live) ? flushAll() : null);
  S.leaveGuard = () => failed.size > 0 && document.body.contains(live);
  S.leaveSave = async () => { const left = await flushAll(); if (left.length) { toast(notSavedText(left), true); return false; } return true; };
  S.leaveText = () => ["Some answers are not saved", `${notSavedText([...failed.values()].map((x) => x.label))} If you leave now, they are lost.`];

  const fieldInput = (fl, id, val, disabled) => {
    if (fl.type === "long") return h("textarea", { id, disabled, value: val ?? "" });
    if (fl.type === "choice" || fl.type === "yesno") {
      const opts = fl.type === "choice" ? fl.options : YESNO;
      const extra = val && !opts.includes(val) ? [h("option", { value: val, selected: true }, `${val} (not on the form now)`)] : [];
      return h("select", { id, disabled }, h("option", { value: "" }, "Choose…"), extra, opts.map((o) => h("option", { value: o, selected: o === val }, o)));
    }
    return h("input", { id, disabled, type: "text", inputmode: fl.type === "number" ? "decimal" : null, value: val ?? "" });
  };
  const tableInput = (fl, rows0, onSave, disabled, idp) => {
    let rows = JSON.parse(JSON.stringify(Array.isArray(rows0) && rows0.length ? rows0 : [{}]));
    const earlier = rows0 && !Array.isArray(rows0) ? String(rows0) : "";
    const wrap = h("div.tablewrap.edit");
    const draw = (focusRow) => {
      wrap.replaceChildren(earlier ? h("p.small.muted", `Earlier answer (before this item became a table): ${earlier}`) : null,
        h("table.t.cells", { tabindex: disabled ? "0" : null }, h("caption.sr-only", fl.label),
          h("thead", h("tr", fl.columns.map((c) => h("th", { scope: "col" }, c.label)), h("th", h("span.sr-only", "Remove")))),
          h("tbody", rows.map((r, k) => h("tr", fl.columns.map((c) => h("td",
            c.type === "choice" ? h("select", { disabled, "aria-label": `${fl.label}: ${c.label}, row ${k + 1}`, onchange: (e) => { r[c.id] = e.target.value; onSave(() => rows); } },
              h("option", { value: "" }, "—"), (c.options || []).map((o) => h("option", { value: o, selected: r[c.id] === o }, o)))
              : (() => { const inp = h("input", { type: "text", disabled, value: r[c.id] || "", "aria-label": `${fl.label}: ${c.label}, row ${k + 1}`, inputmode: c.type === "number" ? "decimal" : null });
                typing(inp, () => { r[c.id] = inp.value; onSave(() => rows); });
                inp.addEventListener("input", () => (r[c.id] = inp.value));
                return inp; })())),
            h("td", h("button.link.small", { disabled, "aria-label": `Remove row ${k + 1} of ${fl.label}`, onclick: async () => {
              if (Object.values(r).some((x) => String(x || "").trim()) && !(await confirmBox("Remove this row?", `Row ${k + 1} of ${fl.label} has values in it.`, "Remove the row", true))) return;
              rows.splice(k, 1); if (!rows.length) rows.push({}); draw(Math.max(0, k - 1)); onSave(() => rows); } }, "×")))))),
        disabled ? null : h("button.small", { id: idp + "-add", onclick: () => { rows.push({}); draw(rows.length - 1); } }, "Add a row"));
      if (focusRow !== undefined) (wrap.querySelectorAll("tbody tr")[focusRow]?.querySelector("input, select") || wrap.querySelector(`#${idp}-add`))?.focus();
    };
    draw();
    return wrap;
  };

  const dataTab = () => {
    const box = h("div");
    const roleText = role === "first" ? `You extract every item. ${st.second ? `${first(nameOf(st.second))} then checks them, and extracts the outcome items (marked "both") independently.` : ""}`
      : role === "second" ? `You check each item against the paper, and extract the items marked "both" yourself. ${st.done_first ? `${first(nameOf(st.first))}'s answers are shown under each item: leave yours empty if the answer shown is right, or give the right answer.` : `${first(nameOf(st.first))} has not finished this study yet: you can extract the "both" items now, and check the rest once ${first(nameOf(st.first))} has finished.`}`
      : "You are not one of the extractors named on the form. Once both have finished, their answers are under Compare and agree.";
    box.append(h("p.small.callout", roleText));
    if (!role) {
      box.append(st.both_done ? h("button", { onclick: () => { tab = "compare"; drawTab(); } }, "See the answers and agree differences") : h("p", "Both extractors have not finished this study yet."));
      return box;
    }
    for (const sec of f.sections) {
      const fs = h("section.xsec", h("h3", sec.title));
      for (const fl of sec.fields) {
        const id = `x-${fl.id}`;
        const mine = st.mine[fl.id] || {};
        const other = (st.other || {})[fl.id];
        const dis = !canEdit(fl);
        const where = h("input.where", { type: "text", value: mine.pg || "", disabled: dis, "aria-label": `Where in the paper: ${fl.label}`, placeholder: "Page or table" });
        const mark = h("span.saved", { hidden: true, "aria-hidden": "true" });
        savedMarks[fl.id] = mark;
        let input;
        if (fl.type === "table") input = tableInput(fl, mine.v, (get) => saveValue(fl, get, () => "", null), dis, id);
        else {
          input = fieldInput(fl, id, mine.v, dis);
          const save = () => saveValue(fl, () => input.value, () => where.value, input);
          if (fl.type === "choice" || fl.type === "yesno") input.addEventListener("change", save); else typing(input, save);
          typing(where, save);
        }
        const ref = role === "second" && other && fl.mode === "check" ? h("div.ref", h("span.small", `${first(nameOf(st.first))}: `), h("strong", show(other.v, fl) || "(left empty)"),
          other.pg ? h("span.small.muted", ` (${other.pg})`) : null,
          !dis && fl.type !== "table" && !mine.v ? h("button.link.small", { "aria-label": `Copy to correct it: ${fl.label}`, onclick: () => { input.value = other.v; input.focus(); input.dispatchEvent(new Event("change")); } }, "Copy to correct it") : null) : null;
        const lockNote = role === "second" && fl.mode === "check" && !st.done_first ? h("span.small.muted", " (checked once the first extractor has finished)") : null;
        fs.append(h("div.xfield" + (fl.mode === "both" ? ".both" : ""),
          h("label", { for: fl.type === "table" ? null : id, id: id + "-l" }, fl.label, fl.mode === "both" ? h("span.pill.action.tiny", "both") : null, lockNote, mark),
          fl.help ? h("p.small.muted.help", fl.help) : null, ref,
          h("div.xinput", input, fl.type === "table" ? null : where)));
      }
      box.append(fs);
    }
    if (myDone) box.append(h("div.xdone", h("p", h("span.pill.include", "Finished"), " You have finished this study."),
      st.both_done && st.second ? h("p.small.muted", d.is_lead ? "You have both finished, so it cannot be reopened: record any change under Compare and agree."
        : "You have both finished, so it cannot be reopened: tell the lead what needs changing, and the lead records it under Compare and agree.")
        : h("button", { onclick: () => setDone(false) }, "Reopen to change an answer")));
    else box.append(h("div.xdone",
      h("p.small.muted", role === "second" && !st.done_first ? `You can finish once ${first(nameOf(st.first))} has finished.` : `When every item is done, mark the study as finished. Once you have both finished, you see each other's answers and the study can no longer be reopened.`),
      h("button.primary", { disabled: role === "second" && !st.done_first, onclick: () => setDone(true) }, "Mark this study as finished")));
    return box;
  };
  const setDone = async (done) => {
    const left = await flushAll();
    if (done && left.length) { toast(`The study is not marked as finished. ${notSavedText(left)}`, true); return; }
    if (done && role === "second") {
      const now = await api("/api/extract/study?key=" + encodeURIComponent(key));
      if (now.state.seen !== st.seen) { toast(`${first(nameOf(st.first))} has changed some answers since you opened this study: check them again.`, true); reload("data"); return; }
    }
    if (done) {
      const empty = flds.filter((x) => x.mode === "both" && !String(show(st.mine[x.id]?.v, x) || "").trim()).map((x) => x.label);
      if (empty.length && !(await confirmBox("Finish with outcome items empty?", [h("p", "These items, which you extract yourself, have no answer:"), h("ul", empty.map((x) => h("li", x))),
        h("p", "Finish only if the paper does not report them. Once you have both finished you cannot reopen the study.")], "Finish anyway"))) return;
    }
    await attempt(() => api("/api/extract/done", { key, done, seen: st.seen }), done ? "Study finished" : "Reopened");
    reload("data");
  };

  // ---- the Compare tab (after both have finished)
  const compareTab = () => {
    const box = h("div");
    if (!st.both_done) { box.append(h("p", "The comparison appears when both extractors have finished this study.")); return box; }
    const diff = st.comparison.filter((c) => c.status === "differs" || c.status === "changed");
    const open = diff.filter((c) => !c.agreed);
    const same = st.comparison.filter((c) => c.status === "same" || c.status === "checked");
    box.append(h("p", diff.length ? `${plural(open.length, "item")} still to agree${diff.length > open.length ? ` (${num(diff.length - open.length)} agreed)` : ""}. ${d.is_lead ? "Talk them through against the paper, then record the agreed value." : "The lead records the agreed values."}` : "Every item agrees."));
    for (const c of diff) {
      const fl = flds.find((x) => x.id === c.f) || { type: "text", label: c.label };
      const id = "agree-" + c.f;
      const panel = h("div.panel.cmp" + (c.agreed ? ".settled" : ""), { id: "cmp-" + c.f });
      const who = (name, v, pg, other) => [h("dt", name), h("dd", fl.type === "table" ? cmpTable(fl, v, `${fl.label}: ${name}`, other) : (show(v, fl) || "(empty)"), pg ? h("span.small.muted", ` (${pg})`) : null)];
      panel.append(h("h3", c.label, c.status === "changed" ? h("span.pill.maybe.tiny", "changed after the check") : null),
        h("dl.kv", who(nameOf(st.first), c.a, c.pa, c.b), who(c.status === "changed" ? `${nameOf(st.second)} checked` : nameOf(st.second), c.b, c.pb, c.a),
          c.agreed ? [h("dt", "Agreed"), h("dd", h("strong", fl.type === "table" ? cmpTable(fl, c.final, `${fl.label}: agreed`) : show(c.final, fl)))] : null));
      if (d.is_lead) {
        // what the lead chose or typed and has not saved is kept when the page is drawn again (after another item's Save)
        const base = c.agreed ? c.final : (fl.type === "table" ? c.a : "");
        const dk = `x:${key}:agree:${c.f}`;
        let pending = drafts.get(dk, base);
        if (pending === undefined) pending = base;
        const note = unsavedNote();
        const keep = () => { drafts.set(dk, pending, base); const off = drafts.has(dk); note.hidden = !off; panel.classList.toggle("unsaved", off); };
        const slot = h("div");
        const drawInput = () => {
          const inp = fl.type === "table" ? tableInput(fl, pending, (get) => { pending = get(); keep(); }, false, id) : fieldInput(fl, id, pending, false);
          if (fl.type !== "table") { const on = (e) => { pending = e.target.value; keep(); }; inp.addEventListener("input", on); inp.addEventListener("change", on); }
          slot.replaceChildren(inp);
        };
        drawInput();
        keep();
        panel.append(h("label.small", { for: fl.type === "table" ? null : id }, "Agreed value"), slot,
          h("div.row", h("button.small", { onclick: () => { pending = c.a; drawInput(); keep(); } }, `Use ${first(nameOf(st.first))}'s`),
            h("button.small", { onclick: () => { pending = c.b; drawInput(); keep(); } }, `Use ${first(nameOf(st.second))}'s`),
            h("button.primary.small", { onclick: async () => {
              if (!String(show(pending, fl) || "").trim() && !(await confirmBox("Record an empty agreed value?", "The agreed value is empty: the workbook will show nothing for this item.", "Record it empty"))) return;
              await attempt(() => api("/api/extract/agree", { key, f: c.f, v: pending ?? "" }), "Agreed value saved");
              drafts.drop(dk);
              keepPlace[key + ":compare:focus"] = c.f;
              reload("compare");
            } }, "Save the agreed value"), note));
      }
      box.append(panel);
    }
    if (same.length) box.append(h("details", h("summary", `${plural(same.length, "item")} that agree`), h("dl.kv", same.map((c) => {
      const fl = flds.find((x) => x.id === c.f) || {};
      return [h("dt", c.label), h("dd", fl.type === "table" ? cmpTable(fl, c.final, `${c.label}: agreed`) : show(c.final, fl))];
    }))));
    return box;
  };
  const filled = (v) => Array.isArray(v) ? v.filter((r) => Object.values(r || {}).some((x) => String(x || "").trim())) : [];
  const cmpTable = (fl, v, label, other) => {
    const rows = filled(v), them = filled(other);
    if (!rows.length) return v && !Array.isArray(v) ? String(v) : "(empty)";
    const differs = (k, c) => other !== undefined && String((them[k] || {})[c.id] ?? "").trim() !== String(rows[k][c.id] ?? "").trim();
    return h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": label || fl.label }, h("table.t.cmp-table",
      h("thead", h("tr", fl.columns.map((c) => h("th", { scope: "col" }, c.label)))),
      h("tbody", rows.map((r, k) => h("tr", fl.columns.map((c) => differs(k, c)
        ? h("td.diff", r[c.id] || "", h("span.sr-only", " (differs)")) : h("td", r[c.id] || "")))))));
  };

  // ---- the Risk of bias tab
  const robTab = () => {
    const box = h("div");
    const two = d.rob_people;
    const amReviewer = two.includes(me);
    box.append(h("p.small.callout", `Each result is judged by ${two.map((x) => first(nameOf(x))).join(" and ")}, independently, with the tool the risk-of-bias plan gives for the study's design: use the official tool's signalling questions, then record each domain's judgement here with the quotes that support it. Neither sees the other's judgements until both have finished, and after that the assessment cannot be reopened. If you cannot agree, follow your protocol; recording the more severe judgement is the usual rule.`));
    for (const a of d.rob) {
      const t = d.tools[a.tool] || { name: a.tool_name, severity: {} };
      const panel = h("div.panel.rob", { "data-a": a.a }, h("div.rob-head", h("h3", { tabindex: "-1" }, a.label),
        h("div.row", !a.both_done || d.is_lead ? h("button.link.small", { "aria-label": `Rename ${a.label}`, onclick: () => renameRob(a) }, "Rename") : null,
          !a.both_done || d.is_lead ? h("button.link.small", { "aria-label": `Remove ${a.label}`, onclick: () => removeRob(a) }, "Remove") : null)),
        h("p.small.muted", h("strong", "Design: "), a.design_name || "not recorded (added before the risk-of-bias plan)", ". ", h("strong", "Tool: "), a.tool_name, ".",
          a.link ? [" ", h("a", { href: a.link[1], target: "_blank" }, a.link[0])] : null),
        a.warning ? h("div.callout.warn", { role: "note" }, h("p.small", h("strong", "Check the design. "), a.warning)) : null,
        a.superseded ? h("div.callout.warn", { role: "note" }, h("p.small", h("strong", "Check the tool. "), a.superseded)) : null);
      const mineDone = a.done_mine;
      if (!a.both_done && amReviewer) {
        for (const dm of a.domains) {
          const sid = `rob-${a.a}-${dm.d}`;
          const cur = dm.mine || { j: "", q: "" };
          const scale = dm.scale || [];
          const q = h("textarea.support", { disabled: mineDone, value: cur.q || "", "aria-label": `Support for ${dm.name}`, placeholder: "Supporting quotes, with page numbers" });
          const off = cur.j && !scale.includes(cur.j) ? [h("option", { value: cur.j, selected: true }, `${cur.j} (not on this tool's scale now: choose again)`)] : [];
          const sel = h("select", { id: sid, disabled: mineDone }, h("option", { value: "" }, "Choose…"), off, scale.map((x) => h("option", { value: x, selected: cur.j === x }, x)));
          sel.addEventListener("change", () => saveJ(a, dm, () => sel.value, () => q.value, sel));
          typing(q, () => saveJ(a, dm, () => sel.value, () => q.value, q));
          const mark = h("span.saved", { hidden: true, "aria-hidden": "true" });
          savedMarks["rob:" + a.a + dm.d] = mark;
          panel.append(h("div.domain", h("label", { for: sid }, h("strong", OVERALL.has(dm.d) ? "" : dm.d + " "), dm.name,
            dm.legacy ? h("span.small.muted", " (kept from before ReviewDesk 1.3.0; not needed now)") : null, " ", mark), sel, q,
          dm.d === "O" && a.rule ? h("p.small.muted.help", `${a.rule}. It may be more severe than every domain (for example, several domains with problems that add up): if so, say why in its support box.`) : null));
        }
        if (!a.domains.some((dm) => OVERALL.has(dm.d))) panel.append(h("p.small.muted", `${a.tool_short} has no overall judgement: its domains are reported.`));
        panel.append(h("div.row", mineDone ? [h("span.pill.include", "Finished"), h("button", { onclick: () => robDone(a, false) }, "Reopen")]
          : h("button.primary", { onclick: () => robDone(a, true) }, "Mark this assessment as finished"),
          h("span.small.muted", `${first(nameOf(two[0]))}: ${a.done_a ? "finished" : "not finished"}; ${first(nameOf(two[1]))}: ${a.done_b ? "finished" : "not finished"}`)));
      } else if (!a.both_done) {
        panel.append(h("p.small", `Waiting for both reviewers: ${first(nameOf(two[0]))} ${a.done_a ? "has finished" : "has not finished"}, ${first(nameOf(two[1]))} ${a.done_b ? "has finished" : "has not finished"}.`));
      } else {
        panel.append(h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": `Judgements for ${a.label}` }, h("table.t.rob-table", h("caption.sr-only", `Judgements for ${a.label}`),
          h("thead", h("tr", h("th", "Domain"), h("th", nameOf(a.reviewers[0])), h("th", nameOf(a.reviewers[1])), h("th", "Agreed"))),
          h("tbody", a.domains.map((dm) => h("tr", h("td", h("strong", dm.d + " "), dm.name),
            h("td", dm.a ? h("span.pill." + (sevClass(t, dm.a) || "x"), dm.a) : "—"),
            h("td", dm.b ? h("span.pill." + (sevClass(t, dm.b) || "x"), dm.b) : "—"),
            h("td", dm.final && !(d.is_lead && (a.problems || []).length && OVERALL.has(dm.d)) ? h("div", h("strong", dm.final), overrideNote(dm))
              : dm.final ? h("div", h("strong", dm.final), overrideNote(dm), agreeCell(a, dm, t))
                : d.is_lead ? agreeCell(a, dm, t) : h("span.small.muted", "to agree"))))))));
        const quotes = a.domains.filter((dm) => dm.qa || dm.qb);
        if (quotes.length) panel.append(h("details", h("summary.small", "Supporting quotes"), h("dl.kv.small", quotes.map((dm) => [h("dt", `${dm.d} ${dm.name}`),
          h("dd", dm.qa ? h("div", h("strong", first(nameOf(a.reviewers[0])) + ": "), dm.qa) : null, dm.qb ? h("div", h("strong", first(nameOf(a.reviewers[1])) + ": "), dm.qb) : null)]))));
        if ((a.problems || []).length) panel.append(h("div.callout.warn", { role: "note" }, h("strong", "Check the overall judgement. "), a.problems.join(" "),
          d.is_lead ? " Agree the overall judgement again." : " The project lead agrees the final judgements."));
        if (d.is_lead) panel.append(h("p.small.muted", "Where the two differ, choose the agreed judgement. 'Use the more severe' applies the usual rule for disagreements that remain. To change a judgement both gave, record the new one with your reason: it is printed under the risk-of-bias tables."));
      }
      box.append(panel);
    }
    box.append(addPanel());
    return box;
  };
  const overrideNote = (dm) => dm.override ? h("p.small.muted", `Both reviewers judged “${dm.override.from}”; changed by the lead. Reason: ${dm.override.why}`) : null;
  // adding an assessment: the reviewer chooses the study's design from the plan, and the tool follows
  const addPanel = () => {
    const pl = d.plan || { designs: [] };
    const panel = h("div.panel", h("h3", "Add an assessment"));
    if (!pl.recorded) {
      panel.append(h("div.callout.warn", h("p", "The risk-of-bias plan has not been recorded yet, so no assessment can be added. The plan says which tool is used for each study design, as the protocol sets it; the project lead records it in Project set-up (Risk-of-bias plan)."),
        d.is_lead ? h("button", { onclick: () => go("setup/robplan") }, "Open the risk-of-bias plan") : null));
      return panel;
    }
    const sug = d.suggest || { text: "", designs: [] };
    const usable = pl.designs.filter((x) => !x.none);
    const pick = sug.designs.find((x) => usable.some((u) => u.id === x)) || "";
    const design = h("select#rob-design", h("option", { value: "" }, "Choose the study's design…"),
      pl.designs.map((x) => h("option", { value: x.id, disabled: x.none, selected: x.id === pick }, x.none ? `${x.name} (no assessment: ${x.reason})` : x.name)));
    const about = h("div.rob-about", { "aria-live": "polite" });
    const showTool = () => {
      const x = usable.find((u) => u.id === design.value);
      const t = x ? d.tools[x.tool] || {} : null;
      about.replaceChildren(!x ? null : h("p.small", h("strong", "Tool: "), t.name || x.tool, ". ", t.help || "", t.link ? [" ", h("a", { href: t.link[1], target: "_blank" }, t.link[0])] : null),
        x && (sug.warnings || {})[x.id] ? h("p.small.callout.warn", h("strong", "Check the design. "), sug.warnings[x.id]) : null);
    };
    design.addEventListener("change", showTool);
    showTool();
    const label = h("input#rob-label", { type: "text", placeholder: "e.g. Pain at 6 weeks, exercise vs usual care" });
    panel.append(h("p.small.muted", "One assessment per result that feeds the synthesis (an outcome, and the comparison it is for). Either reviewer can add it; both then judge it. Check the list above first, so it is not added twice."),
      sug.text ? h("p.small", `Extraction records this study's design as “${sug.text}”.`, pick ? " Its design in the plan is chosen below." : "") : null,
      h("div.grid2", h("label.f", { for: "rob-design" }, h("span", "Study design (from the risk-of-bias plan)"), design), h("label.f", { for: "rob-label" }, h("span", "Result assessed"), label)), about,
      h("p.small.muted", "Is the study's design not in the list? It cannot be assessed until the project lead adds it to the risk-of-bias plan (Project set-up, Risk-of-bias plan). Once screening has started, that is a protocol amendment, with its reason."),
      h("button", { onclick: async () => {
        if (!design.value) { toast("Choose the study's design: the tool follows from it.", true); design.focus(); return; }
        if (!label.value.trim()) { toast("Say which result this assessment is for.", true); label.focus(); return; }
        const x = usable.find((u) => u.id === design.value);
        if ((sug.warnings || {})[x.id] && !(await confirmBox("Add it with this design?", `${sug.warnings[x.id]} The tool follows the design, so check it first.`, "Add it anyway"))) return;
        if (d.rob.some((y) => y.label.trim().toLowerCase() === label.value.trim().toLowerCase() && y.design === design.value)
          && !(await confirmBox("Add it again?", `There is already an assessment called “${label.value.trim()}” with this design.`, "Add another"))) return;
        const had = new Set(d.rob.map((y) => y.a));
        const r = await attempt(() => api("/api/rob/define", { key, design: design.value, label: label.value }), "Assessment added");
        const added = ((r && r.rob) || []).find((y) => !had.has(y.a));
        if (added) keepPlace[key + ":rob:focus"] = added.a;        // the keyboard goes to the new assessment
        reload("rob");
      } }, "Add the assessment"));
    return panel;
  };
  const renameRob = (a) => modal((box, close) => {
    const inp = h("input#rob-rename", { type: "text", value: a.label });
    box.append(h("h2", "Rename the assessment"), h("label.f", { for: "rob-rename" }, h("span", "Result assessed"), inp),
      h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.primary", { onclick: async () => {
        await attempt(() => api("/api/rob/define", { key, a: a.a, label: inp.value }), "Renamed"); close(); reload("rob"); } }, "Rename")));
  });
  const removeRob = async (a) => {
    if (!(await confirmBox(`Remove “${a.label}”?`, "Its judgements are kept in the project but no longer shown or exported.", "Remove it", true))) return;
    await attempt(() => api("/api/rob/define", { key, a: a.a, label: a.label, deleted: true }), "Removed");
    reload("rob");
  };
  const saveJ = (a, dm, getJ, getQ, input) => {
    const qk = "rob:" + a.a + dm.d;
    pendingSaves++;
    queues[qk] = (queues[qk] || Promise.resolve()).then(async () => {
      const j = getJ(), q = getQ();
      try {
        await api("/api/rob/judge", { key, a: a.a, d: dm.d, j, q });
        dm.mine = { j, q };
        failed.delete(qk);
        input.removeAttribute("aria-invalid");
        live.textContent = `Saved: ${dm.name}`;
        markSaved(qk);
        worked();
      } catch (e) {
        input.setAttribute("aria-invalid", "true");
        markFailed(qk);
        refused(qk, `${a.label}, ${dm.name}`, () => saveJ(a, dm, getJ, getQ, input), e);
      } finally { pendingSaves--; }
    });
    return queues[qk];
  };
  const robDone = async (a, done) => {
    const left = await flushAll();
    if (done && left.length) { toast(`The assessment is not marked as finished. ${notSavedText(left)}`, true); return; }
    await attempt(() => api("/api/rob/done", { key, a: a.a, done }), done ? "Assessment finished" : "Reopened");
    reload("rob");
  };
  const agreeCell = (a, dm, t) => {
    const sel = h("select", { "aria-label": `Agreed judgement for ${dm.name}` }, h("option", { value: "" }, "Choose…"),
      (dm.scale || []).map((x) => h("option", { value: x, selected: x === (dm.final || dm.severer) }, x)));
    // a judgement chosen and not yet saved is kept when the table is drawn again (after another domain's Save)
    const dk = `x:${key}:rob:${a.a}:${dm.d}`;
    const note = unsavedNote();
    keepDraft(sel, dk, { note });
    const post = async (j, why) => { await attempt(() => api("/api/rob/agree", { key, a: a.a, d: dm.d, j, why }), "Agreed judgement saved"); drafts.drop(dk); reload("rob"); };
    const save = async (j) => {
      if (!j) { toast("Choose the agreed judgement.", true); return; }
      if (dm.a && dm.a === dm.b && j !== dm.a) {                // a change to what both reviewers gave: with its reason
        modal((mb, close) => {
          const why = h("textarea#rob-why", { rows: 3 });
          mb.append(h("h2", "Why change what both reviewers gave?"),
            h("p", `Both reviewers judged ${dm.d} ${dm.name} as “${dm.a}”. You are recording “${j}”. Give the reason: it is printed under the risk-of-bias tables, and the methods text counts the judgements you changed.`),
            h("label.f", { for: "rob-why" }, h("span", "Reason"), why),
            h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.primary", { onclick: async () => {
              if (why.value.trim().length < 5) { toast("Give the reason.", true); why.focus(); return; }
              close(); await post(j, why.value.trim());
            } }, "Record the change")));
          why.focus();
        });
        return;
      }
      await post(j, "");
    };
    return h("div.agree", sel, h("button.small", { "aria-label": `Save the agreed judgement for ${dm.name}`, onclick: () => save(sel.value) }, "Save"),
      dm.severer ? h("button.link.small", { onclick: () => save(dm.severer) }, `Use the more severe (${dm.severer})`) : null, note);
  };

  // ---- tabs
  const tabs = [["data", "Data"], ["rob", `Risk of bias (${d.rob.length})`]].concat(st.both_done ? [["compare", "Compare and agree"]] : []);
  let tab = tabs.find((t) => t[0] === tabWanted) ? tabWanted : "data";
  const body = h("div.xtab", { role: "tabpanel", tabindex: "-1" });
  const tabBar = h("div.tabs", { role: "tablist", "aria-label": "Study" }, tabs.map(([k, l], i) => h("button", {
    role: "tab", id: "xtab-" + k, onclick: () => { tab = k; drawTab(); },
    onkeydown: (e) => { if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return; e.preventDefault();
      const n = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length; tab = tabs[n][0]; drawTab(); tabBar.children[n].focus(); } }, l)));
  const drawTab = () => {
    [...flushers].forEach((f) => f());           // anything typed but not yet sent is sent (and shown) first
    [...tabBar.children].forEach((b, i) => { const on = tabs[i][0] === tab; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    body.setAttribute("aria-labelledby", "xtab-" + tab);
    body.replaceChildren(({ data: dataTab, rob: robTab, compare: compareTab })[tab]());
  };
  const side = h("aside.side.xside", h("div.rid", h("span", reps.map((r) => r.id).join(", ")), pdfLink),
    h("h2.xtitle", d.study.label), h("p.small.muted", reps[0].title), tabBar, body, live);
  const reload = async (t) => {
    await flush();
    keepPlace[key + ":" + t] = side.scrollTop;
    const want = `#/x/${encodeURIComponent(key)}/${t}`;
    if (location.hash === want) await render(); else { carryToast(); location.hash = want; }
  };
  drawTab();
  el.append(h("main.screen", h("div.screen-top", h("button", { onclick: () => go("extract") }, "← All studies"),
    h("div.grow", h("h1.title", `Extract data: ${d.study.label}`), h("div.small.muted", `Study ${num(d.position)} of ${num(d.total)}${role ? `. You are the ${role === "first" ? "first extractor" : "second extractor"}.` : ""}`)),
    h("button", { disabled: !d.prev, "aria-label": "Previous study", onclick: () => go("x/" + encodeURIComponent(d.prev)) }, "←"),
    h("button", { disabled: !d.next, "aria-label": "Next study", onclick: () => go("x/" + encodeURIComponent(d.next)) }, "→")),
    h("div.ft-body.x-body", pdfPane, side)));
  // after a save that reloads the page, come back to the same place
  const back = keepPlace[key + ":" + tab];
  if (back !== undefined) {
    requestAnimationFrame(() => {
      side.scrollTop = back;
      const fid = keepPlace[key + ":compare:focus"];
      if (tab === "compare" && fid) { delete keepPlace[key + ":compare:focus"]; const next = [...side.querySelectorAll(".panel.cmp:not(.settled)")][0]; (next?.querySelector("input, select, button") || document.getElementById("cmp-" + fid))?.focus({ preventScroll: true }); }
      const aid = keepPlace[key + ":rob:focus"];
      if (tab === "rob" && aid) {
        delete keepPlace[key + ":rob:focus"];
        const head = [...side.querySelectorAll(".panel.rob")].find((x) => x.dataset.a === aid)?.querySelector("h3");
        if (head) { head.scrollIntoView({ block: "center" }); head.focus({ preventScroll: true }); }   // (clear of the sticky bars)
      }
    });
    delete keepPlace[key + ":" + tab];
  }
}
