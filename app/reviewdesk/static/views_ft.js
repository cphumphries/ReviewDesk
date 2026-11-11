// Full-text review: the list of reports, the review page with the PDF beside the decision, and disagreements.
import { api, h, toast, attempt, modal, confirmBox, num, pct, k2, kci, bar, fileUrl, plural, download, FT_LABEL, stopwatch } from "./lib.js";
import { S, go, render } from "./app.js";
import { readinessBanner } from "./views_wizard.js";
import { recordDetails, timingNote } from "./views_screen.js";

const nameOf = (id) => (S.app.project.settings.reviewers.find((r) => r.id === id) || {}).name || id;
const STATUS = { "to do": ["To do", ""], "waiting for others": ["Waiting for the other reviewer", ""], included: ["Included", "include"], excluded: ["Excluded", "exclude"],
  awaiting: ["Awaiting information", "maybe"], ongoing: ["Ongoing study", "maybe"], not_retrieved: ["Not retrieved", ""], conflict: ["To decide together", "action"] };

export async function list(el, args = []) {
  const p = S.app.project;
  const d = await api("/api/fulltext");
  const page = h("div.page.wide");
  el.append(page);
  readinessBanner(page, "fulltext");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", "Review full texts"),
    h("p", "Every report that passed title and abstract screening is assessed in full by both reviewers, independently. Record the first exclusion reason that applies, in the order listed. Reports appear here as soon as they pass title and abstract screening.")));
  if (!d.rows.length) { page.append(h("div.callout", h("p", "No reports have reached full text yet."))); return; }
  const count = (st) => d.rows.filter((r) => r.status === st).length;
  page.append(h("div.stat", h("div", h("b", num(count("to do"))), h("span", "for you to do")), h("div", h("b", num(count("included"))), h("span", "included")),
    h("div", h("b", num(count("excluded"))), h("span", "excluded")), h("div", h("b", num(count("conflict"))), h("span", "to decide together")),
    h("div", h("b", num(d.rows.filter((r) => r.pdf).length) + " / " + num(d.rows.length)), h("span", "PDFs in the project"))));
  page.append(getPdfs(d));
  page.append(h("div.row", { style: { margin: "4px 0 16px" } }, h("div.grow"),
    p.is_lead && count("conflict") ? h("button.primary", { onclick: () => conflicts() }, `Decide together (${plural(count("conflict"), "report")})`) : null));
  const names = studyNames(d, p);
  if (names) page.append(names);
  let filter = count("to do") ? "to do" : "all";
  const tbl = h("div");
  const draw = () => {
    const rows = d.rows.filter((r) => filter === "all" || (filter === "nopdf" ? !r.pdf : r.status === filter));
    tbl.replaceChildren(h("div.tablewrap", h("table.t", h("thead", h("tr", h("th", "Record"), h("th", "Report"), h("th", "PDF"), h("th", "You"), h("th", "Status"))),
      h("tbody", rows.map((r) => h("tr.click", { onclick: (e) => { if (!e.target.closest("a")) go("ft/" + r.id); } },
        h("td.small.nowrap", r.id), h("td", h("a.rowlink", { href: "#/ft/" + encodeURIComponent(r.id) }, r.title), h("div.small.muted", `${(r.authors || "").split(";")[0]} ${r.year} ${r.journal}`)),
        h("td", r.pdf ? h("span.pill.include", "PDF") : h("span.pill", "none"),
          !r.pdf && r.search && r.search.status ? h("div.small.muted", r.search.status === "link_only" && r.search.url ? h("a", { href: r.search.url, target: "_blank", rel: "noopener" }, "web page found")
            : r.search.status === "not_found" ? "no free copy" : r.search.status === "no_answer" ? "not looked up yet" : "") : null),
        h("td.small", r.mine ? FT_LABEL[r.mine] + (r.my_reason ? `: ${r.my_reason}` : "") : ""),
        h("td", h("span.pill" + (STATUS[r.status]?.[1] ? "." + STATUS[r.status][1] : ""), STATUS[r.status]?.[0] || r.status), r.final_reason ? h("div.small.muted", r.final_reason) : null)))))));
  };
  page.append(h("div.row", { style: { marginBottom: "10px" } }, h("label.small", { for: "ft-filter" }, "Show "), h("select#ft-filter", { style: { maxWidth: "260px" }, onchange: (e) => { filter = e.target.value; draw(); } },
    [["to do", "Waiting for me"], ["all", "Everything"], ["conflict", "To decide together"], ["included", "Included"], ["excluded", "Excluded"], ["nopdf", "Without a PDF"]]
      .map(([v, l]) => h("option", { value: v, selected: filter === v }, l)))), tbl);
  draw();
  const ag = await api("/api/agreement?set=main&scope=fulltext");
  if (ag.complete && ag.stats.n) page.append(h("div.panel", { style: { marginTop: "18px" } }, h("h2", "Agreement at full text"),
    h("div.stat", h("div", h("b", pct(ag.stats.agreement)), h("span", "agreement")), h("div", h("b", k2(ag.stats.kappa)), h("span", `Cohen's kappa (${kci(ag.stats)})`)),
      h("div", h("b", num(ag.stats.n)), h("span", "reports compared"))), ag.stats.note ? h("p.small", ag.stats.note) : null, h("p.small.muted", ag.note)));
  // #/fulltext/decide (the "Decide together" button of a check, or of a report) opens the disagreements straight away
  if (args[0] === "decide") { history.replaceState(null, "", "#/fulltext"); if (p.is_lead && count("conflict")) conflicts(); }
}

// Reports both reviewers included but whose study they named differently. ReviewDesk uses the first name, and reports
// with the same name are extracted as one study, so the two agree one name and correct their own decisions.
function studyNames(d, p) {
  const rows = d.rows.filter((r) => (r.study_names || []).length > 1);
  if (!rows.length) return null;
  const quote = (x) => `“${x}”`;
  return h("section.callout.warn", { "aria-labelledby": "sn-h" },
    h("h2#sn-h", { style: { fontSize: "16px", marginBottom: "6px" } }, rows.length === 1 ? "A study with two names" : `${num(rows.length)} studies with two names`),
    h("p.small", `You both included ${rows.length === 1 ? "this report" : "these reports"} but gave the study different names. ReviewDesk uses the name shown as in use, and reports with the same name are extracted as one study. Agree one name; then whoever gave the other name opens the report, keeps Include, corrects the study name and saves.`),
    h("ul.small", rows.map((r) => h("li", h("a", { href: "#/ft/" + encodeURIComponent(r.id) }, r.id), ` ${r.title}: `,
      r.study_names.map((x) => `${quote(x.name)} (${nameOf(x.by)})`).join(" and "), `; in use: ${quote(r.study)}.`))));
}

// ------------------------------------------------------------------------------------------------ getting the PDFs
// Free copies first (looked up automatically), then the library (each report's links, and a list for inter-library
// loans), then adding the PDFs found, many at once: each is placed only when the PDF itself says which report it is;
// any other waits for a person. Nothing outside ReviewDesk and the browser is needed.
function getPdfs(d) {
  const c = d.counts;
  const panel = h("section.panel.getpdfs", { "aria-labelledby": "gp-h" });
  const status = h("p.small", { role: "status" });
  const stopBtn = h("button", { hidden: true, onclick: async () => { await api("/api/fulltext/stop", {}); await poll(true); } }, "Stop");
  const findBtn = h("button" + (c.never_searched ? ".primary" : ""), { disabled: !c.missing, onclick: async () => {
    if (!d.email_set) { toast("Add your email address under My settings first: Unpaywall and OpenAlex ask for one.", true); return; }
    await attempt(() => api("/api/fulltext/fetch", {}));
    poll();
  } }, "Find free copies");
  let polling = null;
  const poll = async (once = false) => {
    if (!document.body.contains(panel)) return;            // the page was left: stop asking
    let st;
    try { st = await api("/api/fulltext/fetch"); } catch (e) { return; }
    stopBtn.hidden = !st.running;
    findBtn.disabled = !!st.running || !c.missing;
    // while it runs, or is stopping, the server's own words come first ("Stopping: ...")
    if (st.running) {
      const stopping = /^Stopping/.test(st.message || "");
      stopBtn.disabled = stopping;
      status.textContent = `${stopping ? st.message + " " : "Looking: "}${num(st.done)} of ${num(st.total)} checked, ${plural(st.found, "PDF")} saved…`;
      if (!once) { clearTimeout(polling); polling = setTimeout(poll, 1500); }
      return;
    }
    stopBtn.disabled = false;
    if (st.total === undefined) return;
    const bits = [`${plural(st.found, "PDF")} saved`];
    if (st.links) bits.push(`${plural(st.links, "web page")} found that may need the library`);
    if (st.no_answer) bits.push(`${num(st.no_answer)} not looked up because the services could not be reached`);
    status.textContent = (st.stopped ? `Stopped after ${num(st.done)} of ${num(st.total)}: ` : `Finished: `) + bits.join(", ") + "."
      + (st.message ? " " + st.message : "");
    if (st.found && document.body.contains(panel) && location.hash.replace(/^#\/?/, "").split("/")[0] === "fulltext") {
      status.append(" ", h("button.link.small", { onclick: () => render() }, "Show the updated list"));
    }
  };
  // (once the panel is on the page: poll() stops when it is not)
  if (d.fetch && (d.fetch.running || d.fetch.total !== undefined)) setTimeout(() => poll(), 0);
  const step = (title, ...kids) => h("li", h("h3", title), ...kids);
  const library = h("div.missing-list");
  const showMissing = async (btn) => {
    const m = await api("/api/fulltext/missing");
    btn.remove();
    if (!m.missing.length) { library.append(h("p.small", "Every report that needs one has its PDF.")); return; }
    library.append(h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": "Reports still without a PDF" }, h("table.t",
      h("caption.sr-only", "Reports still without a PDF, with where to look"),
      h("thead", h("tr", h("th", "Report"), h("th", "Where to look"))),
      h("tbody", m.missing.map((r) => {
        const L = r.links || {};
        const a = (href, text) => href ? h("a", { href, target: "_blank", rel: "noopener" }, text) : null;
        return h("tr", h("td", h("span.small.muted", r.id + " "), r.title, h("div.small.muted", `${(r.authors || "").split(";")[0]} ${r.year} ${r.journal}`)),
          h("td.small.links", a(L.library, "Library"), a(L.found, "Web page found"), a(L.doi, "Publisher"), a(L.pubmed, "PubMed"), a(L.scholar, "Google Scholar")));
      })))),
      m.library_prefix ? null : h("p.small.muted", "The Library links appear once the project lead adds the library’s link prefix in Project set-up (Full texts)."));
  };
  panel.append(h("h2#gp-h", "Get the PDFs"),
    h("p", c.missing ? `${num(c.with_pdf)} of ${plural(c.reports, "report")} have their PDF; ${num(c.missing)} still to find.` : `Every report that needs one has its PDF (${num(c.with_pdf)}).`,
      c.waiting ? ` ${plural(c.waiting, "added PDF is", "added PDFs are")} waiting to be placed.` : ""),
    h("ol.steps.gp",
      step("Free copies",
        h("p.small", "Looks each report up in Unpaywall, Europe PMC, OpenAlex and Semantic Scholar and saves any free PDF. Only the DOI, PubMed ID or title is sent, with your email address to Unpaywall and OpenAlex (which ask for one); the PDFs are then downloaded from the publisher or repository addresses these services give. A PDF already on a report is never replaced."),
        d.email_set ? null : h("p.small", h("a", { href: "#/settings" }, "Add your email address under My settings"), " first."),
        d.fetch && d.fetch.elsewhere ? h("p.small", `${d.fetch.elsewhere} is running a search on another computer; its results appear here as the files sync.`) : null,
        h("div.row", findBtn, stopBtn), status),
      step("The rest, through the library",
        h("p.small", "Open each report through the library (signed in with your library login), save the PDF to your computer, and add them all in step 3. Anything the library does not have can be requested by inter-library loan: the spreadsheet has the details a request asks for."),
        h("div.row", h("button", { disabled: !c.missing, onclick: (e) => showMissing(e.currentTarget) }, "Show the reports still to find"),
          h("button", { disabled: !c.missing, onclick: () => download("missing_csv") }, "List for a spreadsheet (.csv)"),
          h("button", { disabled: !c.missing, onclick: () => download("missing_ris") }, "List for a reference manager (.ris)")),
        library),
      step("Add PDFs", addPdfs())));
  if (c.waiting) waitingList(panel);
  return panel;
}

// Files from a dropped folder (and its folders), read through the browser's file-system entries.
async function droppedFiles(dt) {
  const items = [...(dt.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (!items.length) return [...dt.files];
  const out = [];
  const walk = async (entry) => {
    if (entry.isFile) { out.push(await new Promise((res, rej) => entry.file(res, rej))); return; }
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
      if (!batch.length) break;
      for (const e of batch) await walk(e);
    }
  };
  for (const e of items) await walk(e);
  return out;
}

// What happened to the PDFs just added stays on screen while the page is drawn again with the new counts.
let carried = null;

function addPdfs() {
  const out = h("div");
  const result = h("div", { role: "status" });
  if (carried) { result.replaceChildren(...carried); carried = null; setTimeout(() => result.querySelector("button, a")?.focus(), 0); }
  const redraw = async () => { carried = [...result.childNodes]; await render(); };
  const pdfs = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, hidden: true, tabindex: "-1", onchange: (e) => send([...e.target.files]) });
  const folder = h("input", { type: "file", multiple: true, hidden: true, tabindex: "-1", webkitdirectory: true, onchange: (e) => send([...e.target.files]) });
  const send = async (files) => {
    if (!files.length) return;
    const list = files.filter((f) => /\.pdf$/i.test(f.name));
    const others = files.filter((f) => !/\.pdf$/i.test(f.name) && !/^\./.test(f.name));
    const got = [];
    for (let k = 0; k < list.length; k++) {
      result.textContent = `Adding ${num(k + 1)} of ${num(list.length)}…`;
      try { got.push(await api(`/api/fulltext/bulk?filename=${encodeURIComponent(list[k].name)}`, undefined, { raw: list[k] })); }
      catch (e) { got.push({ file: list[k].name, result: "refused", why: e.message }); }
    }
    for (const f of others) got.push({ file: f.name, result: "refused", why: "not a PDF" });
    const n = (r) => got.filter((x) => x.result === r).length;
    const WORD = { placed: "Placed", waiting: "Waiting for you", already: "Already there", refused: "Not added" };
    result.replaceChildren(h("div.callout" + (n("waiting") || n("refused") ? ".warn" : ".good"),
      h("p", `${plural(n("placed"), "PDF")} placed on ${n("placed") === 1 ? "its report" : "their reports"}`,
        n("waiting") ? `, ${num(n("waiting"))} waiting for you to place` : "", n("already") ? `, ${num(n("already"))} already there` : "",
        n("refused") ? `, ${num(n("refused"))} not added` : "", ". Check each placed PDF is the right paper."),
      h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": "What happened to each file" }, h("table.t",
        h("caption.sr-only", "What happened to each file"),
        h("thead", h("tr", h("th", "File"), h("th", "Result"), h("th", h("span.sr-only", "Actions")))),
        h("tbody", got.map((r) => h("tr", h("td.small", r.file),
          h("td.small", h("strong", WORD[r.result] || r.result), r.result === "placed" ? ` on ${r.record}: ${r.title || ""} (matched by ${r.how})` : r.why ? `: ${r.why}` : ""),
          h("td.small.nowrap", r.result === "placed" ? [h("a", { href: "#/ft/" + encodeURIComponent(r.record) }, "Open"), " ",
            h("button.link.small", { "aria-label": `Take ${r.file} off ${r.record}`, onclick: async (e) => {
              if (!(await confirmBox("Take this PDF off?", `It is moved to fulltexts/_replaced, and ${r.record} has no PDF again.`, "Take it off", true))) return;
              await attempt(() => api("/api/fulltext/remove", { record: r.record, sha: r.sha }), "Taken off");
              e.target.closest("tr").querySelector("td:nth-child(2)").textContent = "Taken off";
              e.target.remove();
              await redraw();
            } }, "Take it off")] : null)))))),
      n("waiting") ? h("button", { onclick: () => { const w = document.getElementById("wait-h"); if (w) { w.scrollIntoView({ block: "start" }); w.focus({ preventScroll: true }); } } }, "Place the waiting PDFs") : null));
    await redraw();             // the counts, the list and the PDFs waiting to be placed are all new
  };
  const zone = h("div.drop", h("p", { style: { margin: "0 0 8px" } }, "Drop PDFs or a folder of them here, or"),
    h("div.row", { style: { justifyContent: "center" } }, h("button", { onclick: () => pdfs.click() }, "Choose PDFs"), h("button", { onclick: () => folder.click() }, "Choose a folder")), pdfs, folder);
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", async (e) => { e.preventDefault(); zone.classList.remove("over"); send(await droppedFiles(e.dataTransfer)); });
  out.append(h("p.small", "ReviewDesk attaches a PDF by itself only when it is sure which report it is (file named R00123.pdf, or the report's DOI inside). Other PDFs wait for you to place them. Nothing is overwritten. Up to 100 MB each."), zone, result);
  return out;
}

async function waitingList(panel) {
  const w = await api("/api/fulltext/waiting");
  if (!w.waiting.length) return;
  const box = h("div.waiting", h("h3#wait-h", { tabindex: "-1" }, `Waiting to be placed (${num(w.waiting.length)})`),
    h("p.small.muted", "Open each to see which paper it is, choose its report, and place it. Remove takes it out of the project folder (your own copy on your computer is not touched)."));
  const after = async (k) => {                   // keep the keyboard where it was: on the next file waiting
    await render();
    const rows = document.querySelectorAll(".wait-row select");
    (rows[Math.min(k, rows.length - 1)] || document.getElementById("wait-h") || document.getElementById("gp-h"))?.focus();
  };
  w.waiting.forEach((x, k) => {
    const label = x.from || x.name;
    const sel = h("select", { "aria-label": `Report for ${label}` }, h("option", { value: "" }, "Choose the report…"),
      w.records.map((r) => h("option", { value: r.id, selected: r.id === x.suggest }, `${r.id}${r.pdf ? " (has a PDF)" : ""}: ${r.authors} ${r.year}. ${r.title.slice(0, 80)}`)));
    const place = async () => {
      const rec = w.records.find((r) => r.id === sel.value);
      if (!rec) { toast("Choose the report this PDF belongs to.", true); sel.focus(); return; }
      if (rec.pdf && !(await confirmBox("Replace its PDF?", `${rec.id} already has a PDF. The one it has now is kept in fulltexts/_replaced.`, "Replace it"))) return;
      try { await api("/api/fulltext/assign", { name: x.name, record: rec.id, replace: rec.pdf }); }
      catch (e) {
        if (!/already the PDF of/.test(e.message) || !(await confirmBox("The same PDF on two reports?", [e.message], "Place it on both"))) { toast(e.message, true); return; }
        await attempt(() => api("/api/fulltext/assign", { name: x.name, record: rec.id, replace: rec.pdf, same_ok: true }));
      }
      toast(`Placed on ${rec.id}`);
      after(k);
    };
    box.append(h("div.wait-row", h("div", h("a", { href: fileUrl("/files/waiting/" + encodeURIComponent(x.name)), target: "_blank", rel: "noopener" }, label),
      h("div.small.muted", x.why)), sel,
      h("div.row", h("button.primary.small", { "aria-label": `Place ${label}`, onclick: place }, "Place"),
        h("button.small", { "aria-label": `Remove ${label}`, onclick: async () => {
          const who = x.by && x.by !== S.app.project.me ? ` ${nameOf(x.by)} added it.` : "";
          if (!(await confirmBox(`Remove ${label}?`, `It is deleted from the project folder, for everyone.${who} The copy on the computer it was added from is not touched.`, "Remove it", true))) return;
          await attempt(() => api("/api/fulltext/discard", { name: x.name }), "Removed"); after(k);
        } }, "Remove"))));
  });
  panel.append(box);
}

async function conflicts() {
  const r = await api("/api/conflicts");
  const list = r.conflicts.filter((c) => c.stage === "ft");
  const dflt = S.app.project.settings.fulltext.disagreement_default;
  const me = S.app.project.me;
  let changed = false;
  modal((box, close) => {
    box.append(h("h2", "Decide together"), h("p.muted", "Talk each report through with the other reviewer, then record what you agreed. "
      + ((dflt || "").trim() ? `Your protocol's rule if you still disagree: “${dflt.trim()}”` : "No rule for a disagreement that remains is written in the set-up yet (Project set-up, Full texts).")),
      h("datalist#ft-studies", (r.studies || []).map((x) => h("option", { value: x }))));
    for (const c of list) {
      const reason = h("select", { "aria-label": `Exclusion reason for ${c.id}` }, h("option", { value: "" }, "Exclusion reason (for Exclude)"), r.reasons.map((x) => h("option", { value: x }, x)));
      const note = h("input", { type: "text", placeholder: "What you agreed, and why", "aria-label": `What you agreed on ${c.id}, and why` });
      const study = h("input", { type: "text", placeholder: "Study name, if included (e.g. Taylor 2025)", "aria-label": `Study name for ${c.id}`, list: "ft-studies",
        value: Object.values(c.votes).map((v) => v && v.study).find(Boolean) || "" });
      const panel = h("div.panel", { role: "group", "aria-label": `${c.id}: ${c.title}` });
      const save = async (decision) => {
        if (decision === "exclude" && !reason.value) { toast("Choose the exclusion reason first.", true); reason.focus(); return; }
        await attempt(() => api("/api/resolve", { record: c.id, stage: "ft", decision, reason: decision === "exclude" ? reason.value : "", note: note.value, study: decision === "include" ? study.value : "", as: me }), "Recorded");
        changed = true;
        panel.replaceChildren(h("p", h("span.muted", c.id + " "), c.title), h("p.small", "Agreed: ", h("strong", FT_LABEL[decision]), decision === "exclude" ? ` (${reason.value})` : ""));
        panel.classList.add("settled");
        const next = [...box.querySelectorAll(".panel:not(.settled) select, .panel:not(.settled) button")][0] || box.querySelector("button.primary");
        if (next) next.focus();
      };
      panel.append(h("p", h("span.muted", c.id + " "), h("strong", c.title)), recordDetails(c),
        h("div.small", Object.entries(c.votes).map(([w, v]) => h("div", `${r.names[w]}: `, h("strong", FT_LABEL[v.d] || v.d), v.reason ? ` (${v.reason})` : "",
          v.study ? ` [study: ${v.study}]` : "", v.note ? h("span.muted", ` "${v.note}"`) : ""))),
        h("h3", `What you agreed on ${c.id}`),
        h("div.grid2", reason, study), note,
        h("div.row", { style: { marginTop: "8px" } }, ["include", "exclude", "awaiting", "ongoing", "not_retrieved"].map((d) =>
          h("button", { onclick: () => save(d), "aria-label": `${FT_LABEL[d]}: ${c.id}` }, FT_LABEL[d]))));
      box.append(panel);
    }
    box.append(h("div.row.end", h("button.primary", { onclick: close }, "Done")));
  }, () => { if (changed) render(); });
}

// ------------------------------------------------------------------------------------------------ review page
export async function room(el, args) {
  const rid = args[0];
  const p = S.app.project, s = p.settings;
  const d = await api("/api/fulltext");
  const mineRows = d.rows.filter((r) => r.required.includes(p.me));
  const idx = mineRows.findIndex((r) => r.id === rid);
  const r = await api("/api/record?id=" + encodeURIComponent(rid));
  const mine = r.mine.ft;
  const row = d.rows.find((x) => x.id === rid);
  const done = mineRows.filter((x) => x.mine).length;
  const nextTodo = () => (mineRows.find((x, k) => k > idx && !x.mine) || mineRows.find((x) => !x.mine && x.id !== rid));

  const pdfPane = h("div.pdfbox");
  const drawPdf = (has) => {
    if (has) {
      pdfPane.replaceChildren(h("iframe", { src: fileUrl(`/files/fulltext/${encodeURIComponent(rid)}.pdf`) + "#view=FitH", title: "Full text" }),
        h("div.row", { style: { padding: "8px 14px", borderTop: "1px solid var(--rule)" } }, h("span.small.muted.grow", "Wrong file? Add the right PDF to replace it, or take it off."), uploader(),
          h("button", { onclick: async () => {
            if (!(await confirmBox("Take this PDF off the report?", "It is moved to fulltexts/_replaced, not deleted. Everyone in the project then sees that this report has no PDF.", "Take it off", true))) return;
            await attempt(() => api("/api/fulltext/remove", { record: rid }), "PDF taken off");
            drawPdf(false);
          } }, "Take it off")));
    } else {
      const L = r.links;
      pdfPane.replaceChildren(h("div.nopdf", h("h2", "No PDF yet"),
        h("p.muted", "Find the full text, save the PDF, and add it here. Everyone in the project then sees the same file."),
        h("ul", L.url ? h("li", h("a", { href: L.url, target: "_blank", rel: "noopener" }, "The record's own web page (for a trial register record, the register entry)")) : null,
          L.library ? h("li", h("a", { href: L.library, target: "_blank", rel: "noopener" }, "Open through the library")) : null,
          L.found ? h("li", h("a", { href: L.found, target: "_blank", rel: "noopener" }, "Web page found by the free-copy search")) : null,
          L.doi ? h("li", h("a", { href: L.doi, target: "_blank", rel: "noopener" }, "Publisher's page (DOI)")) : null,
          L.pubmed ? h("li", h("a", { href: L.pubmed, target: "_blank", rel: "noopener" }, "PubMed")) : null,
          L.scholar ? h("li", h("a", { href: L.scholar, target: "_blank", rel: "noopener" }, "Search Google Scholar")) : null),
        uploader(true),
        h("p.small.muted", { style: { marginTop: "14px" } }, "If no copy can be found through the library, the authors or inter-library loan, record the report as Not retrieved. A registered or ongoing study with no results yet needs no PDF: choose Ongoing study.")));
    }
  };
  const uploader = (big) => {
    const input = h("input", { type: "file", accept: "application/pdf,.pdf", hidden: true, tabindex: "-1", onchange: (e) => send(e.target.files[0]) });
    const send = async (file) => {
      if (!file) return;
      try { await api(`/api/fulltext/upload?record=${encodeURIComponent(rid)}`, undefined, { raw: file }); }
      catch (e) {
        if (!/already the PDF of/.test(e.message) || !(await confirmBox("The same PDF on two reports?", [e.message], "Add it to both"))) { toast(e.message, true); return; }
        await attempt(() => api(`/api/fulltext/upload?record=${encodeURIComponent(rid)}&same_ok=1`, undefined, { raw: file }));
      }
      toast("PDF added");
      drawPdf(true);
    };
    const lab = h("span", h("button" + (big ? ".primary" : ""), { onclick: () => input.click() }, big ? "Add the PDF" : "Replace PDF"), input);
    if (big) {
      const z = h("div.drop", { style: { marginTop: "12px" } }, h("p", { style: { margin: "0 0 8px" } }, "Drop the PDF here, or"), lab);
      z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("over"); });
      z.addEventListener("dragleave", () => z.classList.remove("over"));
      z.addEventListener("drop", (e) => { e.preventDefault(); z.classList.remove("over"); send(e.dataTransfer.files[0]); });
      return z;
    }
    return lab;
  };
  drawPdf(r.pdf);

  // while the lead has time recording on: how long the report's page is open before its decision (the PDF is often
  // read elsewhere, which the page cannot see: timing.py says what the two times then mean)
  const timing = (S.ov || {}).timing || {};
  let watch = null, drawn = false;
  const timeBox = h("div.callout", { role: "note", hidden: true });
  // (as in the screening room: put right in place, never by drawing the page again, which would lose an unsaved decision)
  const applyTiming = (now) => {
    if (now) { timing.project = !!now.project; timing.mine = !!now.mine; }
    if (timing.mine && !watch) { watch = stopwatch({ quiet: false, scope: "ft" }); if (drawn) watch.show(rid); }
    else if (!timing.mine && watch) { watch.stop(); watch = null; }
    timeBox.hidden = !timing.project;
    timeBox.replaceChildren(timing.project ? timingNote(timing, applyTiming) : "");
  };
  let choice = mine?.d || "";
  const reasonBox = h("div.reasons", d.reasons.map((x) => h("label", h("input", { type: "radio", name: "reason", value: x, checked: mine?.reason === x }), h("span", x))));
  const study = h("input#ft-study", { type: "text", value: mine?.study || "", placeholder: "e.g. Taylor 2025", "aria-describedby": "ft-study-help" });
  const note = h("textarea", { value: mine?.note || "", "aria-label": "Note", placeholder: "Note (optional): for example, which criterion failed and where", style: { minHeight: "60px" } });
  const opts = h("div", { style: { display: "grid", gap: "8px" } });
  const extra = h("div");
  const drawChoice = (focus) => {
    opts.replaceChildren(...[["include", "Include"], ["exclude", "Exclude"], ["awaiting", "Awaiting information"], ["ongoing", "Ongoing study"], ["not_retrieved", "Not retrieved"]].map(([v, l]) =>
      h("button" + (choice === v ? ".primary" : ""), { "aria-pressed": String(choice === v), "data-v": v, onclick: () => { choice = v; drawChoice(v); } }, l)));
    if (focus) opts.querySelector(`[data-v="${focus}"]`)?.focus();
    extra.replaceChildren(choice === "exclude" ? h("div", { style: { marginTop: "14px" } }, h("h3", "First reason that applies"), reasonBox) : choice === "include" ? h("div.wzf", { style: { marginTop: "14px" } }, h("label", { for: "ft-study" }, "Study name (optional)"), study,
        h("small#ft-study-help", "Give every report of one study the same name (for example Taylor 2025), so that they are extracted as one study. Left empty, the report counts as a study of its own, named by its first author and year."))
      : choice === "awaiting" ? h("p.small.muted", { style: { marginTop: "12px" } }, "Awaiting information: it is not yet clear whether the study is eligible (for example, you have asked the authors). It is listed as awaiting classification, not included.")
      : choice === "ongoing" ? h("p.small.muted", { style: { marginTop: "12px" } }, "Ongoing study: a registered or ongoing study with no results yet, such as a trial register entry. It is listed as an ongoing study, not included.") : "");
    // choosing Exclude shows the reasons: the whole list is scrolled into view, clear of the Save bar
    if (focus === "exclude") reasonBox.scrollIntoView({ block: "nearest" });
  };
  drawChoice();
  // the decision as it stands on the page; before the page is left with a change not saved, it asks (with Save and go)
  const now = () => JSON.stringify([choice, choice === "exclude" ? (reasonBox.querySelector("input:checked") || {}).value || "" : "", note.value.trim(), choice === "include" ? study.value.trim() : ""]);
  let savedState = now();
  const record = async () => {
    if (!choice) { toast("Choose a decision.", true); return false; }
    const reason = choice === "exclude" ? (reasonBox.querySelector("input:checked") || {}).value : "";
    if (choice === "exclude" && !reason) { toast("Choose the exclusion reason.", true); return false; }
    const time = watch ? watch.read(rid) : undefined;
    let res;
    try { res = await attempt(() => api("/api/decide", { record: rid, stage: "ft", decision: choice, reason, note: note.value.trim(), study: choice === "include" ? study.value.trim() : "", as: p.me, ...(time ? { time } : {}) }), "Saved"); }
    catch (e) { return false; }
    if (watch) { watch.clear(rid); watch.show(rid); }       // (a change of mind on this page is timed from here)
    if (res && res.timing && (!!res.timing.mine !== !!watch || !!res.timing.project !== !!timing.project)) applyTiming(res.timing);
    savedState = now();
    return true;
  };
  const save = async () => {
    if (!(await record())) return;
    const n = nextTodo();
    go(n ? "ft/" + n.id : "fulltext");
  };
  S.leaveGuard = () => document.body.contains(opts) && now() !== savedState;
  S.leaveSave = record;
  S.leaveText = () => ["Save your decision on this report?", "You have changed your decision or note on this report but not saved it. If you leave without saving, the change is lost."];
  const crit = s.criteria;
  const side = h("aside.side",
    h("div.rid", h("span", r.id), r.found_in.length ? h("span", r.found_in.join(", ")) : null),
    h("h2", { style: { fontFamily: "var(--serif)", fontSize: "19px", marginBottom: "6px" } }, r.title),
    h("p.small.muted", citeLine(r)),
    r.abstract ? h("details", h("summary.small", "Abstract"), h("div.abs-small", r.abstract)) : null,
    mine ? h("div.callout", h("p.small", `You recorded: ${FT_LABEL[mine.d]}${mine.reason ? " (" + mine.reason + ")" : ""}. Saving again replaces it.`)) : null,
    row && row.status === "conflict" ? h("div.callout.warn", h("p.small", "The two of you disagree on this report. "
      + (p.is_lead ? "Talk it through and record what you agree together." : "The project lead records what you agree together.")),
      p.is_lead ? h("button", { onclick: () => go("fulltext/decide") }, "Decide together") : null) : null,
    timeBox,
    h("h3", "Your decision"), opts, extra, h("h3", "Note"), note,
    // kept in view at the foot of the panel, so that it can be reached without scrolling on a laptop screen
    h("div.row.ft-save", h("button.primary", { onclick: save }, "Save and go to the next")),
    h("details", { style: { marginTop: "18px" } }, h("summary", "Eligibility criteria"),
      crit.include.length ? [h("h3", "Include"), h("ul", crit.include.map((x) => h("li", x)))] : null,
      crit.exclude.length ? [h("h3", "Exclude"), h("ul", crit.exclude.map((x) => h("li", x)))] : null,
      crit.rules.length ? [h("h3", "Borderline cases"), h("ol", crit.rules.map((x) => h("li", x)))] : null));
  const prev = mineRows[idx - 1], next = mineRows[idx + 1];
  el.append(h("main.screen", h("div.screen-top", h("button", { onclick: () => go("fulltext") }, "← All full texts"),
    h("div.grow", h("h1.title", "Full-text review"), h("div.small.muted", `${num(done)} of ${num(mineRows.length)} done`)), bar(done, mineRows.length, "Full texts done"),
    h("button", { disabled: !prev, "aria-label": "Previous report", onclick: () => go("ft/" + prev.id) }, "←"), h("button", { disabled: !next, "aria-label": "Next report", onclick: () => go("ft/" + next.id) }, "→")),
    h("div.ft-body", pdfPane, side)));
  drawn = true;
  applyTiming();
  if (watch) watch.show(rid);
}

// "Smith J; Jones K; Lee M; et al. Journal 2020." without doubled full stops.
function citeLine(r) {
  const au = (r.authors || "").split(";").map((x) => x.trim()).filter(Boolean);
  const who = au.slice(0, 3).join("; ") + (au.length > 3 ? "; et al" : "");
  const parts = [who, [r.journal, r.year].filter(Boolean).join(" ")].filter(Boolean).map((x) => x.replace(/[.\s]+$/, ""));
  return parts.length ? parts.join(". ") + "." : "";
}
