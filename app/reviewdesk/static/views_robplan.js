// The risk-of-bias plan: for each study design the review includes, the tool used (Project set-up and the guided
// set-up), and the form for a tool the project lead defines. The plan is saved on its own (/api/rob/plan): once
// screening has started, a change needs its reason and is recorded as a protocol amendment.
import { api, h, toast, modal, confirmBox, lines } from "./lib.js";

const NONE = "__none";
const NEW_TOOL = "__new";
let seq = 0;
const nice = (iso) => { const d = new Date(String(iso || "").slice(0, 10) + "T12:00:00"); return isNaN(d) ? String(iso || "") : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }); };
const copy = (x) => JSON.parse(JSON.stringify(x));
// A tool's version in a sentence, without "version" twice when the lead typed the word (robtools.version_words).
const versionWords = (v) => { const x = String(v || "").trim().replace(/^version\b[\s:.,-]*/i, "").trim(); return x ? `version ${x}` : "no version given"; };

// What the plan says, for anyone (the reviewers see this; the lead sees it above the editor too).
export function planStatus(d, nameOf = (x) => x) {
  const pl = d.plan || {};
  if (d.recorded) {
    return h("p", h("strong", "Recorded "), `on ${nice(pl.recorded)}${pl.recorded_by ? ` by ${nameOf(pl.recorded_by)}` : ""}, `,
      pl.before_screening ? "before screening started." : "after screening had started: the methods text asks you to confirm it matches the registered protocol.");
  }
  return h("p", h("strong", "Not recorded yet. "), d.started ? "It is needed before anyone can add a risk-of-bias assessment." : "It is needed before screening starts, as the eligibility criteria are.");
}

export function planList(d) {
  const pl = d.plan || {};
  if (!(pl.designs || []).length) return h("p.muted", "No study designs in the plan yet.");
  return h("ul.rp-list", pl.designs.map((x) => h("li", h("strong", x.name), ": ",
    x.none ? `no risk-of-bias assessment (${x.reason})` : (d.tools[x.tool] || {}).name || x.tool)));
}

export function amendmentList(d, nameOf = (x) => x) {
  if (!(d.amendments || []).length) return null;
  return h("details.rp-amend", h("summary.small", `Changed after screening started (${d.amendments.length === 1 ? "1 protocol amendment" : d.amendments.length + " protocol amendments"})`),
    h("ul.small", d.amendments.map((a) => h("li", `${nice(a.date)}, ${nameOf(a.by)}: ${(a.changes || []).join("; ")}. Reason: ${a.reason}`))));
}

// The editor. d: what /api/rob/plan gives. Returns {node, dirty(), save(), state()}; save() resolves to true when saved
// (or nothing needed saving), false when something is missing (it says what). draft: an earlier editor's state(), so
// that ticks not yet recorded survive the page being drawn again (used only while the recorded plan is unchanged).
export function planEditor(d, { nameOf = (x) => x, onSaved = null, draft = null } = {}) {
  const fixed = d.started && d.recorded;
  const used = d.used || {};
  const src = d.plan || { designs: [], tools: {} };
  const tools = copy(src.tools || {});                       // the lead's own tools, as they will be saved
  const std = new Map(d.standard.map((x) => [x.id, x]));
  const have = new Map((src.designs || []).map((x) => [x.id, x]));
  // a row per standard design (ticked when the plan has it), then the lead's own designs
  const rows = d.standard.map((x) => {
    const was = have.get(x.id);
    return { id: x.id, name: x.name, std: true, on: !!was, tool: was ? (was.none ? NONE : was.tool) : x.tool, reason: was ? was.reason || "" : "" };
  });
  for (const x of src.designs || []) if (!std.has(x.id)) rows.push({ id: x.id, name: x.name, std: false, on: true, tool: x.none ? NONE : x.tool, reason: x.reason || "" });
  let dirty = !d.recorded && (src.designs || []).length > 0;  // a draft (from before 1.3.0) still has to be recorded
  const base = JSON.stringify([d.recorded || false, src]);
  const mark = () => { dirty = true; };
  const box = h("div.rp");
  const reason = h("textarea#rp-reason", { rows: 3, "aria-describedby": "rp-reason-help" });
  reason.addEventListener("input", mark);
  if (draft && draft.dirty && draft.base === base) {
    rows.splice(0, rows.length, ...copy(draft.rows));
    for (const k of Object.keys(tools)) delete tools[k];
    Object.assign(tools, copy(draft.tools));
    reason.value = draft.reason;
    dirty = true;
  }
  const toolInfo = (k) => (tools[k] ? { ...tools[k], custom: true, help: "A tool you defined: reviewers use the official tool's own questions and guidance.", link: tools[k].link ? [tools[k].name, tools[k].link] : null } : d.tools[k] || {});

  const toolSelect = (r, label) => {
    const sel = h("select", { "aria-label": label },
      h("optgroup", { label: "Built-in tools" }, d.builtin.map((k) => h("option", { value: k, selected: r.tool === k, title: `For ${d.tools[k].for}` }, d.tools[k].short))),
      Object.keys(tools).length ? h("optgroup", { label: "Tools you defined" }, Object.keys(tools).map((k) => h("option", { value: k, selected: r.tool === k }, tools[k].name))) : null,
      h("optgroup", { label: "Other" }, h("option", { value: NONE, selected: r.tool === NONE, title: "Give the reason in the box below: it is reported in the methods" }, "No risk-of-bias assessment"),
        h("option", { value: NEW_TOOL }, "Define another tool…")));
    sel.addEventListener("change", () => {
      if (sel.value === NEW_TOOL) {
        sel.value = r.tool;
        editTool(null, (k) => { r.tool = k; r.on = true; mark(); draw(); box.querySelector(`[data-row="${CSS.escape(r.id)}"] select`)?.focus(); });
        return;
      }
      r.tool = sel.value; r.on = true; mark(); draw();
      box.querySelector(`[data-row="${CSS.escape(r.id)}"] ${r.tool === NONE ? "input.rp-reason" : "select"}`)?.focus();
    });
    return sel;
  };

  const rowNode = (r) => {
    const id = `rp-${++seq}`;
    // ticked or unticked (by mouse or with Space), the row is drawn again: the keyboard stays on its tick box
    const tick = r.std ? h("input", { type: "checkbox", id, checked: r.on, onchange: (e) => { r.on = e.target.checked; mark(); draw();
      box.querySelector(`[data-row="${CSS.escape(r.id)}"] input[type=checkbox]`)?.focus(); } }) : null;
    const info = r.tool && r.tool !== NONE ? toolInfo(r.tool) : null;
    const name = r.std ? h("label", { for: id }, r.name)
      : h("input.rp-name", { type: "text", value: r.name, "aria-label": "Study design", placeholder: "e.g. Qualitative studies", oninput: (e) => { r.name = e.target.value; mark(); } });
    return h("div.rp-row" + (r.on ? ".on" : ""), { "data-row": r.id, role: "group", "aria-label": r.name || "New study design" },
      h("div.rp-design", tick, name),
      r.on ? h("div.rp-tool", toolSelect(r, `Tool for ${r.name || "this design"}`),
        r.tool === NONE ? h("input.rp-reason", { type: "text", value: r.reason, "aria-label": `Why ${r.name || "these studies"} have no risk-of-bias assessment`,
          placeholder: "Why these studies have no assessment (reported in the methods)", oninput: (e) => { r.reason = e.target.value; mark(); } })
          : info ? h("p.small.muted.rp-help", h("strong.rp-tname", `${info.short || info.name || r.tool}. `), info.help || "",
            info.link ? [" ", h("a", { href: info.link[1], target: "_blank" }, info.link[0])] : null) : null) : null,
      !r.std ? h("button.link.small.rp-remove", { "aria-label": `Remove ${r.name || "this design"}`, onclick: () => { rows.splice(rows.indexOf(r), 1); mark(); draw(); } }, "Remove") : null);
  };

  const toolsBlock = () => {
    const ks = Object.keys(tools);
    return h("section.rp-tools", h("h3", "Tools you defined"),
      h("p.small.muted", "For any tool ReviewDesk does not have built in, such as a JBI checklist, CASP, the Newcastle-Ottawa Scale, MMAT, AMSTAR 2, ROBIS or the EPOC criteria. You type only its name, version, citation and the labels of its items or domains: reviewers answer the official tool's questions alongside."),
      ks.length ? h("ul.rp-list", ks.map((k) => {
        const inUse = rows.some((r) => r.on && r.tool === k);
        return h("li", h("strong", tools[k].name), ` (${versionWords(tools[k].version)}; ${tools[k].items.length} item${tools[k].items.length === 1 ? "" : "s"})`,
          used[k] ? h("span.small.muted", ` — used by ${used[k]} assessment${used[k] === 1 ? "" : "s"}`) : null, " ",
          h("button.link.small", { "aria-label": `Edit ${tools[k].name}`, onclick: () => editTool(k, () => { mark(); draw(); }) }, "Edit"), " ",
          !used[k] ? h("button.link.small", { "aria-label": `Remove ${tools[k].name}`, onclick: async () => {
            if (inUse && !(await confirmBox(`Remove ${tools[k].name}?`, "A study design in the plan uses it: that design will need another tool.", "Remove it", true))) return;
            delete tools[k];
            rows.forEach((r) => { if (r.tool === k) r.tool = ""; });
            mark(); draw();
          } }, "Remove") : null);
      })) : null,
      h("button", { onclick: () => editTool(null, () => { mark(); draw(); }) }, "Define a tool"));
  };

  // the form for a tool the lead defines (k: its key, or null for a new one); done(key) once it is kept
  const editTool = (k, done) => modal((mb, close) => {
    const was = k ? copy(tools[k]) : { name: "", version: "", citation: "", link: "", items: [], scale: { preset: "ynu" }, overall: { use: false, same: true, rule: false } };
    const locked = !!(k && used[k]);
    const items = (was.items || []).map((x) => ({ ...x }));
    if (!items.length) items.push({ id: "", label: "" }, { id: "", label: "" });
    const F = { name: h("input#rt-name", { type: "text", value: was.name }), version: h("input#rt-version", { type: "text", value: was.version, placeholder: "e.g. 2020 edition, or the date on the checklist" }),
      citation: h("textarea#rt-cite", { rows: 2, value: was.citation }), link: h("input#rt-link", { type: "text", value: was.link || "", inputmode: "url", placeholder: "https://" }) };
    const itemsBox = h("div");
    const drawItems = () => itemsBox.replaceChildren(h("ol.rp-items", items.map((it, i) => h("li",
      h("input", { type: "text", value: it.label, "aria-label": `Item ${i + 1}`, oninput: (e) => (it.label = e.target.value) }),
      !locked ? h("button.link.small", { "aria-label": `Remove item ${i + 1}`, onclick: () => { items.splice(i, 1); drawItems(); itemsBox.querySelector("input")?.focus(); } }, "Remove") : null))),
    !locked ? h("button.small", { onclick: () => { items.push({ id: "", label: "" }); drawItems(); [...itemsBox.querySelectorAll("input")].pop()?.focus(); } }, "Add an item") : null);
    drawItems();
    const scaleBox = (s, name, legend) => {
      const st = { preset: s.preset || "ynu", answers: (s.answers || []).join("\n"), ordered: s.ordered !== false };
      const own = h("textarea", { rows: 4, value: st.answers, disabled: locked, "aria-label": `${legend}: your own answers, one per line`, oninput: (e) => (st.answers = e.target.value) });
      const ord = h("input", { type: "checkbox", checked: st.ordered, disabled: locked, onchange: (e) => { st.ordered = e.target.checked; sync(); } });
      const node = h("fieldset", h("legend", legend),
        Object.entries(d.presets).map(([v, p]) => h("label.inline", h("input", { type: "radio", name, value: v, checked: st.preset === v, disabled: locked, onchange: () => { st.preset = v; sync(); } }), h("span", p.label))),
        h("label.inline", h("input", { type: "radio", name, value: "own", checked: st.preset === "own", disabled: locked, onchange: () => { st.preset = "own"; sync(); } }), h("span", "My own list")),
        h("div.rp-own", own, h("label.inline", ord, h("span", "These answers are in order, from least to most concern"))));
      const sync = () => { node.querySelector(".rp-own").hidden = st.preset !== "own"; if (onSync) onSync(); };
      let onSync = null;
      return { node, st, sync, set onchange(f) { onSync = f; } };
    };
    const items_ = scaleBox(was.scale || {}, `rt-scale-${seq}`, "The answer for each item");
    const ov = { use: !!(was.overall || {}).use, same: (was.overall || {}).same !== false, rule: !!(was.overall || {}).rule };
    const ovScale = scaleBox(was.overall && !was.overall.same ? was.overall : { preset: "lmh" }, `rt-oscale-${seq}`, "The overall judgement's answers");
    const useBox = h("input", { type: "checkbox", checked: ov.use, disabled: locked, onchange: (e) => { ov.use = e.target.checked; drawOverall(); } });
    const overallBox = h("div");
    // on the items' own answers, in order, the rule is ticked unless the lead unticks it (an overall judgement of "Yes"
    // beside an item answered "No" is otherwise never questioned); a tool that already had an overall judgement keeps its choice
    let ruleChosen = !!(k && (was.overall || {}).use);
    const drawOverall = () => {
      const ordered = items_.st.preset !== "own" || items_.st.ordered;
      if (!ordered || !ov.same) ov.rule = false;
      else if (!ruleChosen) ov.rule = true;
      overallBox.replaceChildren(!ov.use ? null : h("div",
        h("label.inline", h("input", { type: "radio", name: `rt-same-${seq}`, checked: ov.same, disabled: locked, onchange: () => { ov.same = true; drawOverall(); } }), h("span", "On the same answers as the items")),
        h("label.inline", h("input", { type: "radio", name: `rt-same-${seq}`, checked: !ov.same, disabled: locked, onchange: () => { ov.same = false; drawOverall(); } }), h("span", "On its own answers")),
        !ov.same ? ovScale.node : null,
        h("label.inline", h("input", { type: "checkbox", checked: ov.rule, disabled: locked || !ov.same || !ordered, onchange: (e) => { ov.rule = e.target.checked; ruleChosen = true; } }),
          h("span", "The overall judgement is never less severe than the worst item")),
        h("p.small.muted", ov.same && ordered ? "Ticked at first: ReviewDesk then checks, when a reviewer finishes, that the overall judgement is not less severe than any item, as it does for the built-in tools. Untick it only if the tool allows that." : "Only for an overall judgement on the items' own answers, when those are in order.")));
      if (!ov.same) ovScale.sync();
    };
    items_.onchange = drawOverall;
    items_.sync();
    drawOverall();
    mb.classList.add("wide");
    mb.append(h("h2", k ? `Edit ${was.name}` : "Define a tool"),
      h("p.muted", "Type only the tool's name, the labels of its items or domains, and its answers, never its questions or guidance: several tools' wording is copyright, and reviewers use the official tool alongside ReviewDesk. Reviewers then judge it exactly like a built-in tool: independently, without seeing each other's answers until both have finished, and you agree any differences."),
      locked ? h("div.callout.warn", h("p.small", `${was.name} is already used by ${used[k]} assessment${used[k] === 1 ? "" : "s"}, so its items, answers and overall judgement are fixed. You can still correct its name, version, citation, link and the labels of its items.`)) : null,
      h("div.grid2", h("label.f", { for: "rt-name" }, h("span", "Name (required)"), F.name), h("label.f", { for: "rt-version" }, h("span", "Version (required)"), F.version)),
      h("label.f", { for: "rt-cite" }, h("span", "Citation (required), as the methods text should print it"), F.citation),
      h("label.f", { for: "rt-link" }, h("span", "Link to the official tool (optional)"), F.link),
      h("fieldset", h("legend", "Items or domains (labels only)"), itemsBox),
      items_.node,
      h("fieldset", h("legend", "Overall judgement"), h("label.inline", useBox, h("span", "The tool has an overall judgement")), overallBox),
      h("div.row.end", h("button", { onclick: close }, "Cancel"), h("button.primary", { onclick: () => {
        const list = items.map((x) => ({ id: x.id, label: x.label.trim() })).filter((x) => x.label);
        const need = !F.name.value.trim() ? [F.name, "Give the tool a name."] : !F.version.value.trim() ? [F.version, "Give the version used."]
          : !F.citation.value.trim() ? [F.citation, "Give its citation."] : !list.length ? [itemsBox.querySelector("input"), "Add at least one item or domain."]
            : F.link.value.trim() && !/^https?:\/\//i.test(F.link.value.trim()) ? [F.link, "The link should start with https://."]
              : items_.st.preset === "own" && lines(items_.st.answers).length < 2 ? [items_.node.querySelector("textarea"), "Give at least two answers, one per line."] : null;
        if (need) { toast(need[1], true); need[0]?.focus(); return; }
        if (Object.entries(tools).some(([kk, t]) => kk !== k && t.name.trim().toLowerCase() === F.name.value.trim().toLowerCase())) { toast("You already have a tool with that name.", true); F.name.focus(); return; }
        const scale = (st) => (st.preset === "own" ? { preset: "own", answers: lines(st.answers), ordered: st.ordered } : { preset: st.preset });
        const def = { ...was, name: F.name.value.trim(), version: F.version.value.trim(), citation: F.citation.value.trim(), link: F.link.value.trim(), items: list,
          scale: locked ? was.scale : scale(items_.st),
          overall: locked ? was.overall : ov.use ? { use: true, same: ov.same, rule: ov.rule, ...(ov.same ? {} : scale(ovScale.st)) } : { use: false } };
        const key = k || "x_" + Array.from(crypto.getRandomValues(new Uint8Array(4))).map((b) => b.toString(16).padStart(2, "0")).join("");
        tools[key] = def;
        close();
        done(key);
      } }, k ? "Keep the changes" : "Add the tool")));
    F.name.focus();
  });

  const draw = () => {
    const on = rows.filter((r) => r.on);
    box.replaceChildren(
      fixed ? h("div.callout.warn", h("p", "Screening has started, so the plan is fixed. A change now is a protocol amendment: give the reason below. It is dated in the audit log and reported in the methods (PRISMA 2020 item 24c).")) : null,
      (src.legacy && (src.legacy.tools.length || src.legacy.other)) && !d.recorded ? h("div.callout", h("p.small", h("strong", src.legacy.in_use ? "The risk-of-bias assessments already in this project use these tools: " : "Before ReviewDesk 1.3.0 you named these tools: "),
        [...src.legacy.tools, src.legacy.other].filter(Boolean).join("; ") + (src.legacy.in_use ? ". ReviewDesk has ticked the study designs they are usually used for. Check them against your protocol, untick any it does not name, add any it does, and record the plan."
          : ". ReviewDesk has ticked the designs they fit. Check them against your protocol, add any it names, and record the plan."))) : null,
      h("div.rp-rows", { role: "group", "aria-label": "Study designs and their tools" }, h("div.rp-head", { "aria-hidden": "true" }, h("span", "Study design (tick each one your protocol includes)"), h("span", "Tool")), rows.map(rowNode)),
      h("button", { onclick: () => { rows.push({ id: `new-${++seq}`, name: "", std: false, on: true, tool: "", reason: "" }); mark(); draw(); [...box.querySelectorAll("input.rp-name")].pop()?.focus(); } }, "Add another study design"),
      toolsBlock(),
      fixed ? h("div.wzf.rp-why", h("label", { for: "rp-reason" }, "Why the plan changes (required)"), reason,
        h("small#rp-reason-help", "Reported in the methods as an amendment to the protocol, with the date.")) : null,
      h("p.small.muted", `${on.length} study design${on.length === 1 ? "" : "s"} in the plan.`));
  };
  draw();

  // explicit: the plan's own Save button (the guided set-up saves it with the rest of its screen, when there is any)
  const save = async (explicit = false) => {
    const on = rows.filter((r) => r.on);
    if (!dirty && d.recorded) { if (explicit) toast("The plan had not changed."); return true; }
    if (!on.length) {
      if (!d.recorded && !explicit) return true;              // nothing chosen yet: the set-up checks say it is needed
      toast("Tick at least one study design for the plan: each design your protocol says the review includes.", true);
      box.querySelector("input[type=checkbox]")?.focus();
      return false;
    }
    const bad = on.find((r) => !r.name.trim()) || on.find((r) => !r.tool) || on.find((r) => r.tool === NONE && r.reason.trim().length < 5);
    if (bad) {
      const el = box.querySelector(`[data-row="${CSS.escape(bad.id)}"]`);
      toast(!bad.name.trim() ? "Give the study design a name." : !bad.tool ? `Choose the tool for ${bad.name}.` : `Say why ${bad.name} have no risk-of-bias assessment.`, true);
      (el && el.querySelector(!bad.name.trim() ? "input.rp-name" : bad.tool === NONE ? "input.rp-reason" : "select"))?.focus();
      return false;
    }
    if (fixed && reason.value.trim().length < 10) { toast("Say why the plan changes: it is a protocol amendment.", true); reason.focus(); return false; }
    const plan = { designs: on.map((r) => ({ id: r.id, name: r.name.trim(), tool: r.tool === NONE ? "" : r.tool, none: r.tool === NONE, reason: r.tool === NONE ? r.reason.trim() : "" })), tools };
    try {
      const out = await api("/api/rob/plan", { plan, reason: reason.value.trim() });
      dirty = false;
      toast(!out.changed ? "The plan had not changed." : out.amendment ? "Plan changed: recorded as a protocol amendment, with your reason." : d.recorded ? "Plan saved." : "Risk-of-bias plan recorded.");
      if (onSaved) onSaved(out);
      return true;
    } catch (e) { toast(e.message, true); return false; }
  };
  const state = () => ({ base, rows: copy(rows), tools: copy(tools), reason: reason.value, dirty });
  return { node: box, dirty: () => dirty, save, nameOf, state };
}
