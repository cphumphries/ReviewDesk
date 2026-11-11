// Shared helpers: talking to the local server, building elements, messages, dialogs.

// Let append() and replaceChildren() skip empty slots (null, false), as h() does, and flatten lists.
for (const name of ["append", "replaceChildren"]) {
  const orig = Element.prototype[name];
  Element.prototype[name] = function (...xs) { return orig.apply(this, xs.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false)); };
}

function readToken() {
  const u = new URL(location.href);
  const t = u.searchParams.get("t");
  if (t) {
    try { localStorage.setItem("rd-token", t); } catch (e) { /* storage off: keep it in memory */ }
    u.searchParams.delete("t");
    history.replaceState(null, "", u.pathname + u.search + u.hash);
    return t;
  }
  try { return localStorage.getItem("rd-token") || ""; } catch (e) { return ""; }
}
export const TOKEN = readToken();
// The project this page was opened on: every change carries it, so a tab left open after switching project in
// another tab cannot write into the wrong project.
let PROJECT_KEY = "";
export function setProjectKey(k) { PROJECT_KEY = k || ""; }

export class ApiError extends Error {
  constructor(msg, data = {}, status = 0) { super(msg); this.data = data; this.status = status; }
}

// (No "reload this page": the page carries on once ReviewDesk answers again, and a reload would lose what it is keeping.)
export const NOT_RUNNING = "ReviewDesk is not running. Start it again from the 'Start ReviewDesk' file, then try again here: this page carries on from where it was.";
let wasDown = false;            // a request found ReviewDesk not running: the next one that gets an answer says so
// ReviewDesk answered again after it was not running: the message saying it is not running goes, and a page that
// keeps answers it could not save hears "rd-online" and sends them again.
function backOnline() {
  if (!wasDown) return;
  wasDown = false;
  const t = document.getElementById("toast");
  if (t && t.className && t.textContent.startsWith("ReviewDesk is not running")) toast("ReviewDesk is running again.");
  window.dispatchEvent(new Event("rd-online"));
}

export async function api(path, body, opts = {}) {
  const init = { method: body === undefined && !opts.raw ? "GET" : "POST", headers: { "X-Token": TOKEN } };
  if (PROJECT_KEY && init.method === "POST" && !opts.anyProject) init.headers["X-Project"] = PROJECT_KEY;
  if (opts.raw) { init.body = opts.raw; init.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
  let r;
  // a page waits at most this long for an answer (a lost connection can otherwise leave it on "Loading" for good)
  // (only for reading: a change, such as removing duplicates from a large search, may rightly take minutes)
  const ctl = typeof AbortController === "function" && init.method === "GET" ? new AbortController() : null;
  const limit = ctl ? setTimeout(() => ctl.abort(), opts.timeout || 90000) : null;
  if (ctl) init.signal = ctl.signal;
  try { r = await fetch(path, init); }
  catch (e) {
    clearTimeout(limit);
    if (ctl && ctl.signal.aborted) throw new ApiError("ReviewDesk took too long to answer. Try again in a moment; if it keeps happening, close ReviewDesk and start it again.");
    wasDown = true;
    throw new ApiError(NOT_RUNNING);
  }
  let j = null;
  let text = "";
  try { text = await r.text(); } catch (e) { /* the answer was cut off */ }
  finally { clearTimeout(limit); }
  try { j = JSON.parse(text); } catch (e) { /* not JSON */ }
  backOnline();
  if (!r.ok) throw new ApiError((j && j.error) || failedText(r.status), j || {}, r.status);
  if (!text.trim()) return {};
  if (!j || typeof j !== "object") throw new ApiError("ReviewDesk sent an answer this page cannot read. Reload the page; if it happens again, close ReviewDesk and start it again.", {}, r.status);
  return j;
}
// What a refusal says when the server gave no reason of its own (never a status code).
function failedText(status) {
  if (status === 401 || status === 403) return "This page's session has ended. Open ReviewDesk again from the 'Start ReviewDesk' file.";
  if (status >= 500) return "Something went wrong inside ReviewDesk, so this was not done. Try again; if it happens again, close ReviewDesk and start it again (the log file says what happened).";
  return "ReviewDesk could not do this just now. Try again in a moment.";
}

// ------------------------------------------------------------------------------------------------ leaving a page
// Things a page sets up outside its own part of the document (a listener on the window, a timer) are undone when the
// page is left or drawn again: the page registers them here, and app.js runs them before it draws the next page.
const cleanups = [];
export function onPageLeave(fn) { cleanups.push(fn); }
export function leavePage() { while (cleanups.length) { try { cleanups.pop()(); } catch (e) { /* the page is going anyway */ } } }
// window.addEventListener for the life of the page only
export function listen(target, type, fn, opts) { target.addEventListener(type, fn, opts); onPageLeave(() => target.removeEventListener(type, fn, opts)); }

// ------------------------------------------------------------------------------------------------ time on a record
// How long a record is on screen before its vote, for a project whose lead has switched time recording on and a person
// who has not left their own votes out (timing.py says what is measured and why; a page makes a stopwatch only then).
//   show(key)   this record is on screen now (the one that was, if another, has left it; the same one: nothing changes)
//   stop()      nothing is on screen (the end of the list, a record that could not be loaded, the page left)
//   read(key)   {shown, active, quiet, views} so far, in milliseconds, to send with the vote
//   clear(key)  the vote was saved: forget the record (a later change of mind is timed from when it is shown again)
// What was measured for a record that left the screen without a vote is kept, and added to when it is shown again, for
// as long as ReviewDesk stays open in this browser tab: across the pages of ReviewDesk, but not across a reload.
// shown: by the clock. active: while this page is the visible tab of a window in front. quiet: the longest stretch of
// the active time with no key, pointer or scroll event (quiet: false leaves it out, for a page that shows a PDF, whose
// reader keeps those events to itself). Only the fact of an event is used: never which key, or where the pointer is.
// scope keeps one page's records apart from another's (a record screened at title and abstract and read at full text).
const KEPT = new Map();
export function stopwatch({ quiet: withQuiet = true, scope = "" } = {}) {
  const K = (k) => scope + "|" + k;
  let key = null, t0 = 0, active = 0, quiet = 0, since = 0, last = 0, on = false;
  const now = () => performance.now();
  const inFront = () => document.visibilityState === "visible" && document.hasFocus();
  const settle = () => {                  // bring the running record's counts up to this moment
    const t = now();
    if (key !== null && on) { active += t - since; quiet = Math.max(quiet, t - last); }
    since = t;
    return t;
  };
  const change = () => { const t = settle(); const was = on; on = inFront(); if (on && !was) last = t; };
  const input = () => { if (key === null || !on) return; const t = now(); quiet = Math.max(quiet, t - last); last = t; };
  const total = () => {
    const t = settle(), k = KEPT.get(K(key)) || { shown: 0, active: 0, quiet: 0, views: 0 };
    return { shown: k.shown + (t - t0), active: k.active + active, quiet: Math.max(k.quiet, quiet), views: k.views + 1 };
  };
  listen(document, "visibilitychange", change);
  listen(window, "focus", change);
  listen(window, "blur", change);
  for (const ev of ["keydown", "pointerdown", "pointermove", "wheel", "scroll", "touchstart"]) listen(document, ev, input, { capture: true, passive: true });
  const api_ = {
    show(k) { if (k === key) return; api_.stop(); key = k; t0 = since = last = now(); active = quiet = 0; on = inFront(); },
    stop() { if (key === null) return; KEPT.set(K(key), total()); key = null; },
    read(k) {
      const x = k === key ? total() : KEPT.get(K(k));
      if (!x) return undefined;
      const out = { shown: Math.round(x.shown), active: Math.round(x.active), views: x.views };
      if (withQuiet) out.quiet = Math.round(x.quiet);
      return out;
    },
    clear(k) { KEPT.delete(K(k)); if (k === key) key = null; },
  };
  onPageLeave(() => api_.stop());         // (what the record on screen had so far is kept for when it is shown again)
  return api_;
}

// ------------------------------------------------------------------------------------------------ drafts
// What a person has chosen or typed in a row that has its own Save button, and not yet saved. When the page is drawn
// again (after another row's Save, say) the row starts from the draft, so that a redraw never replaces a choice a
// person made. A draft holds only while the saved value it was made from is unchanged; once that changes (it was
// saved, or someone else changed it), the draft is dropped.
const DRAFTS = new Map();
const same = (a, b) => JSON.stringify(a ?? "") === JSON.stringify(b ?? "");
export const drafts = {
  get(key, base) {
    const x = DRAFTS.get(key);
    if (!x) return undefined;
    if (!same(x.base, base)) { DRAFTS.delete(key); return undefined; }
    return JSON.parse(JSON.stringify(x.v));
  },
  set(key, v, base) { if (same(v, base)) DRAFTS.delete(key); else DRAFTS.set(key, { v: JSON.parse(JSON.stringify(v ?? "")), base }); },
  drop(key) { DRAFTS.delete(key); },
  dropAll(prefix) { for (const k of [...DRAFTS.keys()]) if (k.startsWith(prefix)) DRAFTS.delete(k); },
  has(key) { return DRAFTS.has(key); },
  any(prefix) { return [...DRAFTS.keys()].some((k) => k.startsWith(prefix)); },
};
// A box drawn with its saved value (base) that keeps what is chosen or typed in it as a draft, starts from the draft,
// and says "Not saved yet" (note) while it differs from what is saved.
export function keepDraft(el, key, { base = el.value, note = null } = {}) {
  const v = drafts.get(key, base);
  if (v !== undefined && (el.tagName !== "SELECT" || [...el.options].some((o) => o.value === v))) el.value = v;
  const mark = () => { const off = String(el.value) !== String(base); el.classList.toggle("unsaved", off); if (note) note.hidden = !off; };
  const keep = () => { drafts.set(key, el.value, base); mark(); };
  el.addEventListener("input", keep);
  el.addEventListener("change", keep);
  mark();
  return el;
}
// closing the tab would lose them: the browser asks first
window.addEventListener("beforeunload", (e) => { if (DRAFTS.size) { e.preventDefault(); e.returnValue = ""; } });
export const unsavedNote =(text = "Not saved yet") => h("span.unsaved-note", { hidden: true }, text);

export const fileUrl = (path) => path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(TOKEN);

// An address ReviewDesk may put in a link: its own pages (#/...), its own files (/...), downloads it made (blob:),
// and ordinary web addresses. Addresses come from imported records and search services, so anything else
// (javascript:, data:, file:) is dropped.
export function safeHref(v) {
  const s = String(v ?? "").trim();
  return s.startsWith("#") || (s.startsWith("/") && !s.startsWith("//")) || /^(https?:\/\/|blob:)/i.test(s);
}

// h("div.cls#id", {attrs}, ...children)
export function h(sel, attrs, ...kids) {
  const m = sel.match(/^([a-z0-9]+)?((?:[.#][\w-]+)*)$/i);
  const el = document.createElement(m[1] || "div");
  (m[2].match(/[.#][\w-]+/g) || []).forEach((p) => p[0] === "." ? el.classList.add(p.slice(1)) : (el.id = p.slice(1)));
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "onclick") el.addEventListener("click", busyWhile(el, v));
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    // The one place a page sets markup. Only four callers use it, each with markup ReviewDesk made itself and escaped:
    // a record's title and abstract through highlight() below (every piece through esc()), and the two pictures the
    // server draws (their text through html.escape). tests/test_security_ui.py shows hostile record text stays text.
    // eslint-disable-next-line no-unsanitized/property
    else if (k === "html") el.innerHTML = v;
    else if (k === "href" && !safeHref(v)) continue;     // only web, in-app and file-download addresses
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  if (el.tagName === "A" && el.getAttribute("target") === "_blank") el.setAttribute("rel", "noopener noreferrer");
  add(el, kids);
  return el;
}
// A button whose click starts something slow (anything that returns a promise) is switched off until it
// finishes, so a second click cannot send the same request twice.
function busyWhile(el, fn) {
  return (e) => {
    if (el.disabled || el.getAttribute("aria-busy") === "true") { e.preventDefault(); return; }
    const hadFocus = document.activeElement === el || e.detail === 0;    // clicked from the keyboard, or focused
    const r = fn(e);
    if (!r || typeof r.then !== "function") return;
    const btn = el.tagName === "BUTTON";
    if (btn) { el.disabled = true; el.setAttribute("aria-busy", "true"); }
    r.catch((err) => { if (err && !err.toasted && err.message) toast(err.message, true); })
      .finally(() => {
        if (!btn) return;
        el.disabled = false; el.removeAttribute("aria-busy");
        // disabling the button took the keyboard focus away: give it back if nothing else has taken it
        if (hadFocus && document.body.contains(el) && (!document.activeElement || document.activeElement === document.body)) el.focus();
      });
  };
}

function add(el, kids) {
  for (const k of kids) {
    if (k === undefined || k === null || k === false) continue;
    if (Array.isArray(k)) add(el, k);
    else el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

export function toast(msg, bad = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.setAttribute("role", bad ? "alert" : "status");
  t.className = "show" + (bad ? " bad" : "");
  t.title = bad ? "Click to close" : "";
  t.onclick = () => (t.className = "");
  t._at = Date.now();
  t._carry = false;
  clearTimeout(t._h);
  // a problem stays long enough to read (about 15 words a second is fast reading); click it to close it sooner
  t._h = setTimeout(() => (t.className = ""), bad ? Math.max(8000, 400 * msg.split(/\s+/).length) : 2600);
}

// A message left over from the page before goes when the page changes. One shown by the action that changed the page
// stays: that action calls carryToast() as it goes (go() in app.js does), or it was shown a moment ago.
export function clearOldToast(ms = 500) {
  const t = document.getElementById("toast");
  if (!t || !t.className) return;
  if (t._carry || Date.now() - (t._at || 0) <= ms) { t._carry = false; return; }   // (carried to one page only)
  clearTimeout(t._h); t.className = "";
}
export function carryToast(ms = 1500) {
  const t = document.getElementById("toast");
  if (t && t.className && Date.now() - (t._at || 0) <= ms) t._carry = true;
}

export async function attempt(fn, okMsg) {
  try {
    const r = await fn();
    if (okMsg) toast(okMsg);
    return r;
  } catch (e) {
    toast(e.message, true);
    e.toasted = true;
    throw e;
  }
}

// Grey out a part of the page (and say why) while something slow runs.
export async function busyPage(el, text, fn) {
  const note = h("div.busy-note", { role: "status" }, text);
  el.classList.add("busy");
  el.prepend(note);
  try { return await fn(); } finally { el.classList.remove("busy"); note.remove(); }
}

export const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : (many || one + "s")}`;

let modalSeq = 0;
const openDialogs = [];             // the dialogs open now, the newest (the one on top) last
const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex='-1'])";

// The first thing in a dialog the keyboard can use, or its heading.
export function focusFirst(box) {
  const f = box.querySelector("input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), button.primary:not([disabled])")
    || box.querySelector("button:not([disabled])");
  if (f) { f.focus(); return; }
  const head = box.querySelector("h2");
  if (head) { head.tabIndex = -1; head.focus(); }
}

export function modal(build, onClose) {
  const back = h("div.modal-back");
  const box = h("div.modal", { role: "dialog", "aria-modal": "true" });
  back.append(box);
  const before = document.activeElement;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    const k = openDialogs.indexOf(back);
    if (k >= 0) openDialogs.splice(k, 1);
    back.remove(); document.removeEventListener("keydown", keys, true);
    if (before && document.body.contains(before)) before.focus();
    if (onClose) onClose();
  };
  // Escape closes; Tab stays inside the dialog (keyboard and screen-reader users cannot wander behind it). Only the
  // dialog on top answers: a folder chooser opened from another dialog has the keys to itself, and Escape closes only it.
  const keys = (e) => {
    if (!document.body.contains(back) || openDialogs[openDialogs.length - 1] !== back || e._rdDialog) return;
    if (e.key === "Escape") { e._rdDialog = true; e.preventDefault(); e.stopImmediatePropagation(); close(); return; }
    if (e.key !== "Tab") return;
    e._rdDialog = true;
    const f = [...box.querySelectorAll(FOCUSABLE)].filter((x) => x.offsetParent !== null);
    if (!f.length) { e.preventDefault(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !box.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener("keydown", keys, true);
  openDialogs.push(back);
  // a click outside the dialog does not close it: that would lose whatever had been typed in it
  build(box, close);
  const head = box.querySelector("h2");
  if (head) { head.id = head.id || `dlg-${++modalSeq}`; box.setAttribute("aria-labelledby", head.id); }
  else box.setAttribute("aria-label", "Dialog");
  document.body.append(back);
  focusFirst(box);
  return close;
}

export function confirmBox(title, text, okLabel, danger = false) {
  return new Promise((resolve) => {
    let answered = false;
    const cancel = h("button", { onclick: () => { answered = true; close(); resolve(false); } }, "Cancel");
    const close = modal((box) => {
      box.append(h("h2", title), ...[].concat(text).map((x) => (x instanceof Node ? x : h("p", x))),
        h("div.row.end", cancel,
          h("button" + (danger ? ".danger" : ".primary"), { onclick: () => { answered = true; close(); resolve(true); } }, okLabel)));
    }, () => { if (!answered) resolve(false); });
    cancel.focus();                       // the safe choice has the focus: Enter does not start something irreversible
  });
}

// A folder browser, because a web page cannot ask the computer for a folder path itself.
export function pickFolder(title, { mode = "any", note = "" } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    modal(async (box, close) => {
      const list = h("div.folders", { role: "group", "aria-label": "Folders in this folder" });
      const crumbs = h("div.crumbs", { "aria-live": "polite" });
      const roots = h("div.row", { role: "group", "aria-label": "Places" });
      const useBtn = h("button.primary", "Use this folder");
      let cur = null;
      // after moving to another folder, the keyboard goes to the first folder in it (the button pressed is gone)
      const load = async (path, moved = false) => {
        let r;
        try { r = await api("/api/fs?path=" + encodeURIComponent(path || "")); } catch (e) { toast(e.message, true); return; }
        cur = r;
        crumbs.textContent = r.path;
        roots.replaceChildren(...r.roots.map((x) => h("button", { onclick: () => load(x.path, true) }, x.label)));
        list.replaceChildren(
          ...(r.parent ? [h("button", { onclick: () => load(r.parent, true) }, h("span", h("span", { "aria-hidden": "true" }, "↑ "), "Up one level"), h("span"))] : []),
          ...r.dirs.map((d) => h("button", { onclick: () => load(d.path, true), ondblclick: () => mode === "project" && d.is_project && (finish(d.path), close()) },
            h("span", h("span", { "aria-hidden": "true" }, "📁 "), d.name), d.is_project ? h("span.pill.action", "ReviewDesk project") : h("span"))));
        if (!r.dirs.length) list.append(h("p.muted.small", { style: { padding: "10px 12px", margin: 0 } }, "No folders in here."));
        useBtn.disabled = mode === "project" ? !r.is_project : false;
        useBtn.textContent = mode === "project" ? (r.is_project ? "Open this project" : "Choose a project folder") : "Use this folder";
        const lost = !box.contains(document.activeElement) || document.activeElement.disabled;
        const rows = list.querySelectorAll("button");
        if (moved || lost) (rows[r.parent && rows.length > 1 ? 1 : 0] || roots.querySelector("button") || useBtn).focus();
      };
      useBtn.onclick = () => { finish(cur.path); close(); };
      useBtn.disabled = true;                // until the first folder is shown
      box.append(h("h2", title), note ? h("p.muted", note) : null, roots, h("p", { style: { margin: "10px 0 0" } }, crumbs), list,
        h("div.row.end", h("button", { onclick: () => { finish(null); close(); } }, "Cancel"), useBtn));
      load("", true);
    }, () => finish(null));
  });
}

export const pct = (x, d = 1) => (x === null || x === undefined || Number.isNaN(x)) ? "n/a" : (100 * x).toFixed(d) + "%";
export const num = (x) => (x === null || x === undefined) ? "n/a" : Number(x).toLocaleString("en-GB");
export const k2 = (x) => (x === null || x === undefined) ? "n/a" : (Math.abs(x) < 0.005 ? 0 : Number(x)).toFixed(2);
// "95% CI 0.71 to 0.95", or why there is no interval.
export const kci = (st) => st.ci_low === null || st.ci_low === undefined ? `95% CI ${st.ci_note || "n/a"}` : `95% CI ${k2(st.ci_low)} to ${k2(st.ci_high)}`;
export function bar(done, total, label = "Progress") {
  const w = total ? Math.round((100 * done) / total) : 0;
  return h("div.bar", { role: "progressbar", "aria-label": label, "aria-valuenow": w, "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-valuetext": `${num(done)} of ${num(total)}` }, h("i", { style: { width: w + "%" } }));
}
export function lines(text) { return (text || "").split("\n").map((s) => s.trim()).filter(Boolean); }
export function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// Highlight words people asked for (green for include terms, red for exclude terms). The matching runs on the
// plain text and each piece is escaped afterwards, so a term can never match inside "&amp;" or a tag.
export function highlight(text, inc = [], exc = []) {
  const raw = String(text ?? "");
  const terms = [...inc.map((w) => [w, "hi-in"]), ...exc.map((w) => [w, "hi-ex"])].map(([w, c]) => [String(w || "").trim(), c]).filter(([w]) => w.length > 1);
  if (!terms.length) return esc(raw);
  terms.sort((a, b) => b[0].length - a[0].length);
  // whole words only ("age" does not light up "page"), unless the term ends with * (triag* matches triage, triaging)
  const pat = (w) => "(?<![\\p{L}\\p{N}])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\*/g, "[\\p{L}\\p{N}]*")
    + (w.endsWith("*") ? "" : "(?![\\p{L}\\p{N}])");
  const rx = new RegExp("(" + terms.map(([w]) => pat(w)).join("|") + ")", "giu");
  const cls = new Map(terms.map(([w, c]) => [w.toLowerCase(), c]));
  let out = "", last = 0, m;
  while ((m = rx.exec(raw)) !== null) {
    if (!m[0]) { rx.lastIndex++; continue; }
    const lw = m[0].toLowerCase();
    const c = cls.get(lw) || [...cls.entries()].find(([w]) => w.endsWith("*") && lw.startsWith(w.slice(0, -1)))?.[1] || "hi-in";
    out += esc(raw.slice(last, m.index)) + `<mark class="${c}">${esc(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + esc(raw.slice(last));
}

// A file name Windows accepts as it is (the same rule as util.safe_filename on the server), so the name a toast
// shows is the name the file is saved under: no \ / : * ? " < > | or control characters, no dot or space at the end.
export function safeName(name) {
  // eslint-disable-next-line no-control-regex -- control characters are exactly what a file name must not hold
  const s = String(name || "").normalize("NFC").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/^[\s.]+|[\s.]+$/g, "");
  return s || "file";
}

// Titles that are not part of a person's name: skipped for first names ("Use Sam's") and for initials.
export const PERSON_TITLE = /^(dr|prof|professor|mr|mrs|ms|miss|mx|sir|dame)\.?$/i;
export function nameWords(s) {
  const all = String(s || "").split(/\s+/).filter(Boolean);
  const words = all.filter((w) => !PERSON_TITLE.test(w));
  return words.length ? words : all;
}
// "Dr Sam Patel" -> "SP"
export const initialsOf = (s) => nameWords(s).map((w) => w[0]).join("").toUpperCase().slice(0, 3);

// Downloads go through fetch, so a refusal (a file that would show votes too early, say) appears as a message
// instead of a page of raw text.
export async function download(kind) {
  let r;
  try { r = await fetch("/download/" + kind, { headers: { "X-Token": TOKEN } }); }
  catch (e) { toast(NOT_RUNNING, true); return; }
  if (!r.ok) {
    let j = {};
    try { j = await r.json(); } catch (e) { /* not JSON */ }
    toast(j.error || `The download failed (${r.status}).`, true);
    return;
  }
  const blob = await r.blob();
  const m = (r.headers.get("Content-Disposition") || "").match(/filename\*=UTF-8''([^;]+)/);
  const name = safeName(m ? decodeURIComponent(m[1]) : kind);
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
  toast(`Downloaded ${name}`);
}

export const SETS = { main: "Database searches", update: "Update search", other: "Other methods" };
export const TA_LABEL = { include: "Include", maybe: "Maybe", exclude: "Exclude" };
export const FT_LABEL = { include: "Include", exclude: "Exclude", awaiting: "Awaiting information", ongoing: "Ongoing study", not_retrieved: "Not retrieved" };
