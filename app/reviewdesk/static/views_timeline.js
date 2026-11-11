// The steps of the review in order: the infographic (made on the server, which escapes its text), the same steps
// as a list in words (the main content on a phone, and what screen readers read), and ways to download and print it.
import { api, h, toast, download, plural, safeName, listen } from "./lib.js";
import { S } from "./app.js";

// The pages a step's link goes to, named as the navigation names them.
const PAGES = { setup: "Project set-up", searches: "Plan and log searches", import: "Import records", duplicates: "Check duplicates",
  screening: "Screen titles and abstracts", ai: "AI second screener", fulltext: "Review full texts", extract: "Extract data",
  certainty: "Rate certainty (GRADE)", report: "PRISMA and reports", wizard: "Guided set-up" };
const STATUS = { done: ["Done", "include"], now: ["You can do this now", "action"], blocked: ["Something is missing", "maybe"],
  later: ["Later", ""] };
const LAYOUTS = { portrait: "Portrait (tall)", landscape: "Landscape (wide)" };
const PREF = "rd-timeline-layout";

const pref = () => { try { return localStorage.getItem(PREF) || ""; } catch (e) { return ""; } };
const keepPref = (v) => { try { localStorage.setItem(PREF, v); } catch (e) { /* storage off: fine */ } };
const narrow = () => window.matchMedia("(max-width: 640px)").matches;

export async function timeline(el) {
  const d = await api("/api/timeline");
  const page = h("div.page.tl-page");
  el.append(page);
  // On a phone the list is the main content and comes first; on a wider screen the picture leads. The tall picture
  // fits most screens at its own size; the wide one suits a large screen or a printed page.
  const phone = narrow();
  let layout = LAYOUTS[pref()] ? pref() : (phone || window.innerWidth < 1900 ? "portrait" : "landscape");
  const here = d.phases.find((ph) => ph.status === "here");
  const nowStep = currentOf(d);
  page.append(h("div.page-head", h("h1", { tabindex: "-1" }, "The steps, in order"),
    h("p", "Every step of the review, from setting up the project to reporting it, in the order they happen. Each step builds on the ones before it."),
    here && nowStep ? h("p.tl-here", h("strong", "You are here: "), `${here.title}, step ${nowStep.n} of ${d.steps.length} (${nowStep.title.toLowerCase()}).`) : null));

  // ---------------------------------------------------------------------------------------------- the picture
  const pic = h("div.tl-pic");
  const figure = h("figure.tl-figure", pic, h("figcaption.sr-only", "The picture shows the same steps as the list ", phone ? "above." : "below."));
  let full = false;
  const show = (v) => {
    layout = v;
    pic.replaceChildren(h("div.tl-svg", { html: v === "landscape" ? d.svg : d.svg_portrait }));   // made on the server, its text escaped
    const svg = pic.querySelector("svg");
    if (svg) svg.style.maxWidth = svg.getAttribute("width") + "px";
    figure.className = "tl-figure " + v + (full ? " full" : "");
  };
  const zoom = h("button", { "aria-pressed": "false", onclick: () => {
    full = !full; zoom.setAttribute("aria-pressed", String(full)); figure.classList.toggle("full", full); } },
    "Show the picture at full size");
  show(layout);
  const radios = Object.entries(LAYOUTS).map(([v, label]) => h("label.tl-seg-opt",
    h("input", { type: "radio", name: "tl-layout", value: v, checked: v === layout, onchange: () => { show(v); keepPref(v); } }), h("span", label)));
  const short = () => safeName(S.app.project.settings.short_title || "review");
  const tools = h("div.tl-tools",
    h("fieldset.tl-seg", h("legend.sr-only", "Picture layout"), h("span.tl-seg-label", { "aria-hidden": "true" }, "Layout"), radios),
    h("div.tl-actions",
      h("button", { onclick: () => savePng(layout === "landscape" ? d.svg : d.svg_portrait, `${short()} - steps of the review${layout === "portrait" ? " (portrait)" : ""}.png`) }, "Download picture (PNG)"),
      h("button", { onclick: () => download(layout === "landscape" ? "timeline_svg" : "timeline_svg_portrait") }, "Download SVG"),
      h("button", { onclick: () => window.print() }, "Print")));
  const picSection = h("section.tl-figwrap", { "aria-labelledby": "tl-pic-h" },
    h("h2", { id: "tl-pic-h", class: phone ? "" : "sr-only" }, "The picture"),
    tools,
    h("div.tl-under", h("p.small.muted.tl-printnote", "Print puts the wide picture on one landscape page. A3 paper is the easiest to read on a wall."), zoom),
    figure);

  // ---------------------------------------------------------------------------------------------- the list
  const list = h("section.tl-list", { "aria-labelledby": "tl-list-h" },
    h("h2", { id: "tl-list-h" }, "Step by step"),
    h("p.muted", "The same steps as the picture, with what each involves, who does it and where it is done in ReviewDesk."),
    h("ol.tl-phases", d.phases.map((ph) => phaseItem(ph, d))));

  page.append(phone ? [list, picSection] : [picSection, list]);

  // Printing (the Print button, or the browser's own) always uses the wide picture: it fits one landscape page.
  let before = null;
  const onBefore = () => { if (layout !== "landscape") { before = layout; show("landscape"); } };
  const onAfter = () => { if (before) show(before); before = null; };
  listen(window, "beforeprint", onBefore);           // (taken off again when the page is left)
  listen(window, "afterprint", onAfter);
}

function phaseItem(ph, d) {
  const steps = d.steps.filter((s) => s.phase === ph.id);
  const tag = ph.status === "here" ? h("span.pill.action", "You are here") : ph.status === "done" ? h("span.pill.include", "Done") : null;
  return h("li.tl-phase" + (ph.status ? "." + ph.status : ""),
    h("h3", ph.title, tag ? " " : null, tag),
    h("ol.tl-steps", { start: steps.length ? steps[0].n : 1 }, steps.map((s) => stepItem(s))));
}

function stepItem(s) {
  const [word, cls] = STATUS[s.status] || ["", ""];
  const bits = [];
  bits.push(h("p", s.what));
  if (s.question) bits.push(h("p", s.facts.join(" ")));         // what kappa is
  if (s.branch) {
    const b = s.branch;
    bits.push(h("ul.tl-branch",
      [b.yes, b.no].map((x) => h("li", h("strong", `If ${x.when}: `), [x.summary, ...x.facts].join(" ")))));
    if (b.then) bits.push(h("p", b.then));
  }
  const meta = [h("div", h("dt", "Who"), h("dd", s.who))];
  for (const n of s.notes || []) meta.push(h("div", h("dt", n.label || "Note"), h("dd", n.text)));
  meta.push(h("div", h("dt", "Where"), h("dd", s.outside || !s.route ? "Outside ReviewDesk" + (s.route && PAGES[s.route] ? "; record it in " : "") : "",
    s.route && PAGES[s.route] ? h("a", { href: "#/" + s.route }, PAGES[s.route]) : null)));
  if (word) meta.push(h("div", h("dt", "Status"), h("dd", h("span.pill" + (cls ? "." + cls : ""), word))));
  return h("li.tl-step" + (s.status ? "." + s.status : "") + (s.outside ? ".outside" : ""),
    h("h4", h("span.tl-num", { "aria-hidden": "true" }, String(s.n)), h("span.sr-only", `Step ${s.n}: `), s.title),
    bits, h("dl.tl-meta", meta));
}

// ------------------------------------------------------------------------------------------------ the PNG
// The picture is drawn onto a canvas at twice its size and saved. Inside a picture the browser cannot use the page's
// fonts, so they are put into it first (from ReviewDesk's own font files).
let FACES = null;
async function fontFaces() {
  if (FACES !== null) return FACES;
  const files = [["Plex", 400, "ibm-plex-sans-latin-400-normal.woff2"], ["Plex", 600, "ibm-plex-sans-latin-600-normal.woff2"],
    ["Serif4", 400, "source-serif-4-latin-400-normal.woff2"], ["Serif4", 600, "source-serif-4-latin-600-normal.woff2"]];
  const out = [];
  for (const [fam, w, f] of files) {
    try {
      const r = await fetch("/static/fonts/" + f);
      if (!r.ok) continue;
      const bytes = new Uint8Array(await r.arrayBuffer());
      let bin = "";
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      out.push(`@font-face{font-family:${fam};src:url(data:font/woff2;base64,${btoa(bin)}) format("woff2");font-weight:${w}}`);
    } catch (e) { /* without the font the picture uses the next one named in it */ }
  }
  FACES = out.join("");
  return FACES;
}

async function savePng(svgText, name) {
  const faces = await fontFaces();
  const text = faces ? svgText.replace(/<svg\b[^>]*>/, (m) => `${m}<style>${faces}</style>`) : svgText;
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(text);
  try { await img.decode(); } catch (e) { toast("The picture could not be drawn in this browser. Download the SVG instead.", true); return; }
  const m = svgText.match(/<svg\b[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"/);
  const w = m ? parseFloat(m[1]) : img.naturalWidth, ht = m ? parseFloat(m[2]) : img.naturalHeight;
  const c = document.createElement("canvas");
  c.width = Math.round(w * 2); c.height = Math.round(ht * 2);
  const g = c.getContext("2d");
  g.fillStyle = "#FFFFFF";
  g.fillRect(0, 0, c.width, c.height);
  g.drawImage(img, 0, 0, c.width, c.height);
  const blob = await new Promise((res) => c.toBlob(res, "image/png"));
  if (!blob) { toast("The picture could not be made in this browser. Download the SVG instead.", true); return; }
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  toast(`Downloaded ${name}`);
}

// The step the review is at: the first under way or ready to start, leaving out the steps done outside ReviewDesk unless
// nothing else is open (as the Overview's next step and the side bar do: app.js, currentStep).
function currentOf(d) {
  const open = d.steps.filter((s) => s.status === "now" || s.status === "blocked");
  return open.find((s) => !s.outside) || open[0] || null;
}

// ------------------------------------------------------------------------------------------------ the Overview's strip
// A compact strip for the Overview: the phases as a row of dots, where the review is, and a link to the full timeline.
export async function timelineStrip(box) {
  let d;
  try { d = await api("/api/timeline?svg=0"); } catch (e) { return; }      // the Overview carries on without it
  if (!document.body.contains(box)) return;
  const here = d.phases.find((ph) => ph.status === "here");
  const step = currentOf(d);
  const allDone = !step && d.steps.length && d.steps.every((s) => s.status === "done" || s.outside);
  const words = here && step
    ? [h("strong", "You are here: "), `${here.title}, step ${step.n} of ${d.steps.length}.`]
    : allDone ? [h("strong", "Every step in ReviewDesk is done.")]
      : [`${plural(d.steps.length, "step")} in ${plural(d.phases.length, "phase")}, from planning to reporting.`];
  box.replaceChildren(h("section.tl-strip", { "aria-label": "Where the review is, in the steps of the review" },
    h("ol.tl-dots", d.phases.map((ph) => h("li" + (ph.status ? "." + ph.status : ""), { title: ph.title + (ph.status === "here" ? " (you are here)" : ph.status === "done" ? " (done)" : "") },
      h("span.dot", { "aria-hidden": "true" }),
      h("span.sr-only", ph.title + (ph.status === "here" ? ", you are here" : ph.status === "done" ? ", done" : ""))))),
    h("p.tl-strip-text", words),
    h("a.tl-strip-link", { href: "#/timeline" }, "See every step, in order")));
}
