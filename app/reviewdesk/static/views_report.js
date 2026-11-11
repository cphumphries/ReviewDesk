// The PRISMA diagram and the reporting outputs (the AI second screener page is views_ai.js).
import { api, h, toast, download, plural, safeName } from "./lib.js";
import { S } from "./app.js";
import { readinessBanner } from "./views_wizard.js";

// ------------------------------------------------------------------------------------------------ PRISMA and reports
export async function report(el) {
  const [d, r] = await Promise.all([api("/api/prisma"), api("/api/reporting")]);
  const p = S.app.project;
  const page = h("div.page.wide.reports");
  el.append(page);
  readinessBanner(page, "report");          // what is missing before this step (views_wizard.js)
  page.append(h("div.page-head", h("h1", "PRISMA and reports"),
    h("p", "Everything ReviewDesk can write for your review, made from what was recorded. Check each file before you use it: ReviewDesk fills in what it knows and highlights what only you can write.")));
  if (d.hidden) { page.append(h("div.callout", h("p", d.hidden))); return; }
  page.append(h("section.panel.pack", { "aria-labelledby": "pack-h" },
    h("div", h("h2#pack-h", "The reporting pack"),
      h("p", `One .zip file with every file on this page that is ready (${plural(r.ready, "file")}), and a "Read me first" file that says what each one is for and which PRISMA item it answers. The flow diagram is in it as an SVG picture; download the PNG picture with its own button below.`),
      r.is_lead ? null : h("p.small.muted", "As a reviewer, your pack holds the files a reviewer may have. The project lead's pack also has every reviewer's votes and the AI's answers.")),
    r.closed ? null : h("button.primary", { onclick: () => download("rep_pack") }, "Download the reporting pack")));
  if (r.closed) page.append(h("div.callout", { role: "note" }, h("p", h("strong", "No downloads for now. "), r.closed)));
  if (!d.counts.complete) page.append(h("div.callout.warn", h("p", "Screening is not finished, so these numbers and files will still change. Download them again when it is.")));
  if (d.counts.checks.length) page.append(h("div.callout.bad", h("p", h("strong", "The numbers do not add up. "), "Check the records and decisions (Data checks) before you report them:"), d.counts.checks.map((c) => h("p.small", c))));
  const fig = h("div.prisma", { html: d.svg });
  const short = safeName(p.settings.short_title || "review");
  page.append(h("section.panel", { "aria-labelledby": "flow-h" },
    h("div.row", h("h2#flow-h.grow", "PRISMA 2020 flow diagram"),
      r.closed ? null : h("button", { onclick: () => pngOf(d.svg, `${short} - PRISMA flow diagram.png`) }, "Download picture (PNG)"),
      r.closed ? null : h("button", { onclick: () => download("prisma") }, "Download (SVG, opens in Word)")),
    h("p.small.muted", "PRISMA 2020 item 16a. The picture (PNG) goes into any document; the SVG stays sharp at any size."),
    fig, d.blind_note ? h("p.small.muted", { style: { marginTop: "10px" } }, d.blind_note) : null));
  fig.querySelector("svg")?.setAttribute("role", "img");
  fig.querySelector("svg")?.setAttribute("aria-label", "PRISMA 2020 flow diagram of the records at each stage");
  for (const g of r.groups) {
    page.append(groupPanel(g, r.closed ? r.closed_title + "." : ""));
    if (g.id === "methods") page.append(summaryPanel(d, r));
  }
}

const STATE_WORD = { later: "Not yet", lead: "Project lead only", none: "Nothing to list" };

// (closed: the reason nothing can be downloaded just now, said in full once at the top of the page and in a few
// words, which the server gives, beside each file)
function groupPanel(g, closed) {
  return h("section.panel.rep-group", { "aria-labelledby": `rep-${g.id}` }, h("h2", { id: `rep-${g.id}` }, g.title), h("p.muted", g.intro),
    h("ul.rep-list", g.items.map((it) => itemRow(it, closed))));
}

const fmtWord = (f) => (f === "Word" ? "Word document" : f.charAt(0).toLowerCase() + f.slice(1));

function itemRow(it, closed) {
  if (closed) {
    return h("li.rep-item",
      h("div.rep-text", h("h3", it.title), h("p.small", it.what), h("p.small.rep-prisma", it.prisma)),
      h("div.rep-files", h("p.small.rep-why", h("strong", "Not yet. "), closed)));
  }
  const ready = it.files.filter((f) => f.state === "ready");
  const wait = it.files.filter((f) => f.state !== "ready");
  const oneWhy = wait.length && wait.every((f) => f.why === wait[0].why && f.state === wait[0].state);
  const why = (f, withFormat) => h("p.small.rep-why", withFormat ? h("strong", `${f.format}: `) : null,
    f.state === "lead" ? null : h("strong", STATE_WORD[f.state] + ". "), f.why);
  return h("li.rep-item",
    h("div.rep-text", h("h3", it.title, it.lead_only ? h("span.pill", { style: { marginLeft: "8px" } }, "Project lead only") : null),
      h("p.small", it.what), h("p.small.rep-prisma", it.prisma)),
    h("div.rep-files",
      ready.map((f) => h("button", { onclick: () => download(f.kind), "aria-label": `Download ${fmtWord(f.format)}: ${it.title}` }, `Download ${fmtWord(f.format)}`)),
      oneWhy ? why(wait[0], ready.length > 0) : wait.map((f) => why(f, true))));
}

function summaryPanel(d, r) {
  const st = r.summary_state || ["ready", ""];
  return h("section.panel", { "aria-labelledby": "sum-h" }, h("h2#sum-h", "Short methods summary"),
    h("p.muted.small", "Written from what was actually done, for a first draft of the methods. The methods text above is the fuller version, as a Word document."),
    h("pre.summary", { tabindex: "0", role: "region", "aria-label": "Short methods summary text" }, d.summary),
    h("div.row", h("button", { onclick: async () => {
      try { await navigator.clipboard.writeText(d.summary); toast("Copied the methods summary"); } catch (e) { toast("Copying was blocked by the browser: select the text and copy it instead.", true); } } }, "Copy"),
      st[0] === "ready" ? h("button", { onclick: () => download("summary") }, "Download text file") : h("span.small.muted", r.closed ? r.closed_title + "." : st[1])));
}

// The flow diagram as a picture, drawn in the browser: the SVG on a canvas, twice its size or more for print.
async function pngOf(svgText, name) {
  const img = new Image();
  await new Promise((ok, bad) => {
    img.onload = ok;
    img.onerror = () => bad(new Error("The picture could not be made in this browser. Download the SVG instead: Word opens it too."));
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svgText);
  });
  const w = img.naturalWidth || 730, ht = img.naturalHeight || 900;
  const k = Math.min(4, Math.max(2, 2400 / w));
  const c = document.createElement("canvas");
  c.width = Math.round(w * k); c.height = Math.round(ht * k);
  const g = c.getContext("2d");
  g.fillStyle = "#fff"; g.fillRect(0, 0, c.width, c.height);
  g.drawImage(img, 0, 0, c.width, c.height);
  const blob = await new Promise((ok) => c.toBlob(ok, "image/png"));
  if (!blob) throw new Error("The picture could not be made in this browser. Download the SVG instead: Word opens it too.");
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  toast(`Downloaded ${name}`);
}
