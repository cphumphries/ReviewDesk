"""PRISMA 2020 flow diagram: the counts behind it, and the diagram as SVG."""
from __future__ import annotations

import html
from collections import Counter

from . import screening
from .project import Project


REGISTER_WORDS = ("clinicaltrials.gov", "clinicaltrials", "ictrp", "isrctn", "euctr", "eu clinical trials", "ctis",
                  "anzctr", "chictr", "drks", "ctri", "trial register", "trials register")


def is_register(name: str) -> bool:
    """A trial register rather than a bibliographic database (CENTRAL, a 'register of trials', counts as a database)."""
    n = (name or "").lower()
    return "central" not in n and "cochrane" not in n and any(w in n for w in REGISTER_WORDS)


def _number(x):
    """'1,234' as 1234; None when no number was recorded."""
    t = str(x if x is not None else "").replace(",", "").strip()
    return int(t) if t.isdigit() else None


def registers_not_imported(p: Project) -> dict:
    """{register: records found, or None if not recorded} for trial registers searched whose records were never imported
    into ReviewDesk. They come from the search log (a register search with no export file linked to it, and no file
    imported under that register's name); a register named only in Project set-up counts when the search log has no
    search of it (older projects). None is counted twice, and none is in any box of the flow diagram."""
    files = p.imports()
    imported = {str(m.get("database") or "").strip().lower() for m in files}
    linked = {m.get("search_id") for m in files if m.get("search_id")}
    out: dict = {}
    logged = set()
    for e in p.search_log():
        name = str(e.get("database") or "").strip()
        if not name or not is_register(name):
            continue
        logged.add(name.lower())
        if e.get("id") in linked or name.lower() in imported:
            continue
        n = _number(e.get("results"))
        key = next((k for k in out if k.lower() == name.lower()), name)
        out[key] = (out.get(key) or 0) + n if n is not None else out.get(key)
    for r in p.settings_ro().get("registers", []) or []:
        name = str((r or {}).get("name", "") or "").strip() if isinstance(r, dict) else ""
        low = name.lower()
        if not name or low in imported or any(low in x or x in low for x in logged | {k.lower() for k in out}):
            continue
        out[name] = _number(r.get("records"))
    return out


# words in a search log entry's source that show it is not a database search (other methods; as readiness.py reads it)
OTHER_METHOD_WORDS = ("citation", "reference list", "references", "hand search", "handsearch", "website", "web search",
                      "google", "grey literature", "gray literature", "expert")


def databases_not_imported(p: Project) -> dict:
    """{database: records found, or None if not recorded} for database searches in the search log whose records were
    never imported into ReviewDesk (no export file linked to the search, and none imported under that database's name).
    Like registers_not_imported: they are in no box of the flow diagram, so the diagram, the methods text and the
    search appendix name them rather than count them as nothing. A search that found 0 records needs no file."""
    files = p.imports()
    imported = {" ".join(str(m.get("database") or "").lower().split()) for m in files}
    linked = {m.get("search_id") for m in files if m.get("search_id")}
    out: dict = {}
    for e in p.search_log():
        name = " ".join(str(e.get("database") or "").split())
        low = name.lower()
        if (not name or is_register(name) or any(w in low for w in OTHER_METHOD_WORDS) or e.get("id") in linked
                or low in imported):
            continue
        n = _number(e.get("results"))
        if n == 0:
            continue
        key = next((k for k in out if k.lower() == low), name)
        out[key] = (out.get(key) or 0) + n if n is not None else out.get(key)
    return out


def found_words(n) -> str:
    """'11 records', '1 record', or 'number of records not recorded'."""
    return "number of records not recorded" if n is None else f"{n:,} record{'' if n == 1 else 's'}"


def counts(p: Project, blind: bool = False) -> dict:
    """The PRISMA counts. blind: as a reviewer may see them while the AI second screener is behind single screening
    (records waiting for a second look counted as excluded), so they cannot show what the AI said about a record.
    Shared: do not change the result."""
    return p.memo(("prisma_counts", blind), lambda: _counts(p, blind))


def _counts(p: Project, blind: bool = False) -> dict:
    dd = p.dedup_report()
    files = dd.get("files", [])
    by = p.by_id()
    C = {"db": {}, "other": {}, "checks": []}
    # ---------------------------------------------------------------------------------- databases and registers
    db_files = [f for f in files if f["set"] in ("main", "update")]
    per_db, per_reg = Counter(), Counter()
    for f in db_files:
        (per_reg if is_register(f["database"]) else per_db)[f["database"]] += f["read"]
    # registers searched whose records were not imported: they were never de-duplicated or screened in ReviewDesk, so
    # they stay out of every box of the diagram and are named in a footnote instead
    not_imported = registers_not_imported(p)
    manual_merges = sum(1 for m in dd.get("merges", []) if m.get("from_record") and by.get(m["kept"], {}).get("set") in ("main", "update"))
    D = C["db"]
    D["databases"] = dict(per_db)
    D["identified_databases"] = sum(per_db.values())
    D["registers"] = dict(per_reg)
    D["identified_registers"] = sum(per_reg.values())
    D["registers_not_imported"] = not_imported
    D["databases_not_imported"] = databases_not_imported(p)
    D["duplicates"] = sum(f["duplicates"] for f in db_files)     # includes merges the review team made by hand
    D["automation"] = 0
    D["other_removed"] = sum(f["empty"] for f in db_files)
    D["manual_merges"] = manual_merges
    D["update_included"] = any(f["set"] == "update" for f in db_files)
    _screening_counts(p, D, ("main", "update"), blind)
    # ---------------------------------------------------------------------------------- other methods
    o_files = [f for f in files if f["set"] == "other"]
    per_src = Counter()
    for f in o_files:
        per_src[f["database"]] += f["read"]
    O = C["other"]
    O["sources"] = dict(per_src)
    O["identified"] = sum(per_src.values())
    O["duplicates"] = sum(f["duplicates"] for f in o_files)
    O["other_removed"] = sum(f["empty"] for f in o_files)
    _screening_counts(p, O, ("other",), blind)
    # ---------------------------------------------------------------------------------- included
    fto = screening.ft_outcomes(p)
    inc = [r for r, o in fto.items() if o["status"] == "included"]
    studies = {(fto[r].get("study") or r).strip().lower() for r in inc}
    C["included_reports"] = len(inc)
    C["included_studies"] = len(studies)
    C["awaiting"] = sum(1 for o in fto.values() if o["status"] == "awaiting")
    C["ongoing"] = sum(1 for o in fto.values() if o["status"] == "ongoing")
    # ---------------------------------------------------------------------------------- arithmetic checks
    # each check is on the numbers the diagram shows (registers that were not imported are in no box)
    if D["ft_included"] + O["ft_included"] != C["included_reports"]:
        C["checks"].append(f"Reports included from the two columns ({D['ft_included'] + O['ft_included']:,}) do not equal the "
                           f"reports of included studies ({C['included_reports']:,}).")
    for key, X in (("Databases", D), ("Other methods", O)):
        ident = X.get("identified_databases", X.get("identified", 0)) + X.get("identified_registers", 0)
        if not (ident or X["records"]):
            continue
        sums = [(ident - X["duplicates"] - X["other_removed"], X["records"], "identified minus removed", "records in the set"),
                (X["screened"] + X["pending_ta"], X["records"], "screened plus not yet screened", "records"),
                (X["ta_excluded"] + X["sought"], X["screened"], "excluded plus sought for retrieval", "screened"),
                (X["not_retrieved"] + X["assessed"] + X["pending_ft"], X["sought"], "not retrieved plus assessed plus still open", "sought"),
                (X["ft_excluded"] + X["ft_included"] + X["awaiting"] + X["ongoing"], X["assessed"],
                 "excluded plus included plus awaiting plus ongoing", "assessed")]
        for got, want, a, b in sums:
            if got != want:
                C["checks"].append(f"{key}: {a} ({got:,}) does not equal {b} ({want:,}).")
        gone = X.get("not_in_records") or []
        if gone:
            C["checks"].append(f"{key}: {len(gone):,} screened record{'s are' if len(gone) != 1 else ' is'} not in the records "
                               f"({', '.join(gone[:10])}{'…' if len(gone) > 10 else ''}). See Data problems on the Overview.")
    # finished when nothing is left to screen; reports awaiting classification and ongoing studies are final categories
    C["complete"] = all(X["pending_ta"] == 0 and X["pending_ft"] == 0 for X in (D, O))
    return C


def _screening_counts(p: Project, X: dict, sets: tuple, blind: bool = False) -> None:
    by = p.by_id()
    X["records"] = sum(1 for r in by.values() if r["set"] in sets)
    ta = {}
    for s in sets:
        if blind:
            ta.update({r: {"status": st} for r, st in screening.statuses_blind_to_ai(p, s).items()})
        else:
            ta.update(screening.ta_outcomes(p, s))
    X["screened"] = sum(1 for o in ta.values() if o["status"] in ("forward", "excluded"))
    X["ta_excluded"] = sum(1 for o in ta.values() if o["status"] == "excluded")
    # Counted from the records themselves, so that the sums below are real checks: a record with a screening outcome
    # that is not (or no longer) in the records shows up as a difference, never as a negative number.
    X["pending_ta"] = sum(1 for o in ta.values() if o["status"] in ("pending", "conflict", "not_open")) + \
        sum(1 for r in by.values() if r["set"] in sets and r["id"] not in ta)
    X["not_in_records"] = sorted(r for r in ta if r not in by or by[r]["set"] not in sets)
    fwd = [r for r, o in ta.items() if o["status"] == "forward"]
    fto = screening.ft_outcomes(p)
    X["sought"] = len(fwd)
    X["not_retrieved"] = sum(1 for r in fwd if fto.get(r, {}).get("status") == "not_retrieved")
    assessed = [r for r in fwd if fto.get(r, {}).get("status") in ("included", "excluded", "awaiting", "ongoing")]
    X["assessed"] = len(assessed)
    X["ft_reasons"] = dict(Counter(fto[r].get("reason") or "No reason recorded" for r in assessed if fto[r]["status"] == "excluded"))
    X["ft_excluded"] = sum(X["ft_reasons"].values())
    X["ft_included"] = sum(1 for r in assessed if fto[r]["status"] == "included")
    X["awaiting"] = sum(1 for r in assessed if fto[r]["status"] == "awaiting")
    X["ongoing"] = sum(1 for r in assessed if fto[r]["status"] == "ongoing")
    X["pending_ft"] = sum(1 for r in fwd if fto.get(r, {}).get("status") in ("pending", "conflict"))


# ================================================================================================ the diagram
SOURCE = "Adapted from Page MJ, et al. BMJ 2021;372:n71 (CC BY 4.0, https://creativecommons.org/licenses/by/4.0/)."


def _wrap(text: str, width_px: float, size: int = 12, hang: int = 2) -> list:
    per = max(10, int(width_px / (size * 0.52)))
    out = []
    for para in text.split("\n"):
        ind = len(para) - len(para.lstrip(" "))
        words, line = para.split(), " " * ind
        for w in words:
            if len(line) + len(w) + 1 > per and line.strip():
                out.append(line.rstrip())
                line = " " * (ind + hang) + w
            else:
                line = (line + " " + w) if line.strip() else line + w
        out.append(line.rstrip())
    return out


def svg(p: Project, blind: bool = False) -> str:
    return p.memo(("prisma_svg", blind), lambda: _svg(p, blind))


def notes(p: Project, blind: bool = False) -> list:
    """The footnotes under the diagram, in order (the last is the template's source and licence)."""
    C = counts(p, blind)
    D, O = C["db"], C["other"]
    n = lambda x: f"{x:,}"
    has_other = O["identified"] > 0 or O["records"] > 0
    ni = D.get("registers_not_imported") or {}
    sets = p.state_ro().get("sets", {})
    out = []
    if ni:
        out.append("* Registers also searched, whose records were not imported into ReviewDesk, so they are in none of "
                   "these numbers: " + ", ".join(f"{k} ({found_words(v)} found)" if v is not None else f"{k} ({found_words(v)})"
                                                for k, v in ni.items()) + ".")
    dni = D.get("databases_not_imported") or {}
    if dni:
        out.append("† Database searches also run, whose records were not imported into ReviewDesk, so they are in none of "
                   "these numbers: " + ", ".join(f"{k} ({found_words(v)} found)" if v is not None else f"{k} ({found_words(v)})"
                                                for k, v in dni.items()) + ".")
    ai_net = any(sv.get("ai_net_active") for sv in sets.values())
    out.append("** Every record excluded was excluded by a person; no automation tool excluded any record"
               + (" (the AI second screener only decided which of a single reviewer's exclusions a second reviewer looked at "
                  "again)" if ai_net else "") + ".")
    if D.get("update_included"):
        out.append("Database counts include the update search.")
    if has_other:
        extra = ["records screened and excluded on title and abstract"]
        if O["duplicates"] or O["other_removed"]:
            extra.insert(0, "records removed before screening")
        out.append("Other methods: the boxes for " + " and for ".join(extra) + " are not in the PRISMA 2020 template's "
                   "other-methods column. ReviewDesk removed records already found by the database searches (or found twice) "
                   "and screened the rest on title and abstract before their full texts were sought, so these boxes show "
                   "what happened to them.")
        out.append(f"Reports of included studies: {n(D['ft_included'])} from databases and registers and "
                   f"{n(O['ft_included'])} from other methods.")
    if not C.get("complete"):
        pend_ta = D["pending_ta"] + O["pending_ta"]
        pend_ft = D["pending_ft"] + O["pending_ft"]
        out.append("Not finished: " + " and ".join(x for x in (
            f"{n(pend_ta)} record{'' if pend_ta == 1 else 's'} not yet screened" if pend_ta else "",
            f"{n(pend_ft)} report{'' if pend_ft == 1 else 's'} still being assessed" if pend_ft else "") if x)
                   + " are in no box below the identification boxes yet.")
    out.append(SOURCE)
    return out


def _svg(p: Project, blind: bool = False) -> str:
    C = counts(p, blind)
    D, O = C["db"], C["other"]
    n = lambda x: f"{x:,}"
    has_other = O["identified"] > 0 or O["records"] > 0
    ni = D.get("registers_not_imported") or {}
    # The template's box counts the records identified. Registers searched but not imported are in no box (a footnote,
    # *, names them), so when there are any the count says it is of the records imported.
    db_lines = "Records identified from:\n" + "".join(f"  {k} (n = {n(v)})\n" for k, v in D["databases"].items()) \
        + (f"Databases, total (n = {n(D['identified_databases'])} imported)†\n" if D.get("databases_not_imported") else
           f"Databases, total (n = {n(D['identified_databases'])})\n") \
        + "".join(f"  {k} (n = {n(v)})\n" for k, v in D["registers"].items()) \
        + (f"Registers (n = {n(D['identified_registers'])} imported)*" if ni else
           f"Registers (n = {n(D['identified_registers'])})")
    removed = ("Records removed before screening:\n"
               f"  Duplicate records removed (n = {n(D['duplicates'])})\n"
               f"  Records marked as ineligible by automation tools (n = {n(D['automation'])})\n"
               f"  Records removed for other reasons (n = {n(D['other_removed'])})")
    reasons = lambda X: ("Reports excluded:\n" + ("".join(f"  {k} (n = {n(v)})\n" for k, v in sorted(X["ft_reasons"].items(), key=lambda kv: -kv[1])) or "  none\n")).rstrip()
    show_aw, show_on = C["awaiting"] > 0, C.get("ongoing", 0) > 0

    def outflows(X) -> list:
        """What leaves "Reports assessed for eligibility": excluded with reasons, awaiting classification, ongoing."""
        return ([reasons(X)] + ([f"Reports awaiting classification\n(n = {n(X['awaiting'])})"] if show_aw else [])
                + ([f"Reports of ongoing studies\n(n = {n(X['ongoing'])})"] if show_on else []))
    cols = {"band": (12, 26), "dbm": (50, 330), "dbs": (410, 300), "om": (750, 250), "os": (1030, 240)}
    W = 1290 if has_other else 730
    rows = []          # (band, dbm text, dbs text or [texts], om text, os text or [texts])
    o_removed = None
    if has_other and (O["duplicates"] or O["other_removed"]):
        o_removed = ("Records removed before screening:\n" + f"  Duplicates (n = {n(O['duplicates'])})\n"
                     + (f"  Removed for other reasons (n = {n(O['other_removed'])})" if O["other_removed"] else "")).rstrip()
    rows.append(("Identification", db_lines, removed,
                 ("Records identified from:\n" + "".join(f"  {k} (n = {n(v)})\n" for k, v in O["sources"].items())).rstrip() if has_other else None,
                 o_removed))
    rows.append(("Screening", f"Records screened\n(n = {n(D['screened'])})", f"Records excluded**\n(n = {n(D['ta_excluded'])})",
                 f"Records screened (title and abstract)\n(n = {n(O['screened'])})" if has_other else None,
                 f"Records excluded**\n(n = {n(O['ta_excluded'])})" if has_other else None))
    rows.append(("Screening", f"Reports sought for retrieval\n(n = {n(D['sought'])})", f"Reports not retrieved\n(n = {n(D['not_retrieved'])})",
                 f"Reports sought for retrieval\n(n = {n(O['sought'])})" if has_other else None,
                 f"Reports not retrieved\n(n = {n(O['not_retrieved'])})" if has_other else None))
    rows.append(("Screening", f"Reports assessed for eligibility\n(n = {n(D['assessed'])})", outflows(D),
                 f"Reports assessed for eligibility\n(n = {n(O['assessed'])})" if has_other else None,
                 outflows(O) if has_other else None))
    rows.append(("Included", f"Studies included in review\n(n = {n(C['included_studies'])})\nReports of included studies\n(n = {n(C['included_reports'])})",
                 None, None, None))
    GAP = 12
    hgt = lambda t, w: len(_wrap(t, w - 20)) * 16 + 20

    def stack(t, w) -> list:
        if not t:
            return []
        return [hgt(x, w) for x in (t if isinstance(t, list) else [t])]
    out, y = [], 70
    boxes = []
    for band, a, b, c, d in rows:
        texts = [(cols["dbm"], a), (cols["dbs"], b), (cols["om"], c), (cols["os"], d)]
        need = [sum(stack(t, w)) + GAP * max(0, len(stack(t, w)) - 1) for (x, w), t in texts]
        h = max(need + [44])
        boxes.append((band, y, h, texts))
        y += h + 34
    foot = []
    for x in notes(p, blind):
        foot += _wrap(x, W - cols["dbm"][0] - 20, 11, hang=0)
    H = y + 6 + 15 * len(foot) + 12
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="Arial, Helvetica, sans-serif" font-size="12">')
    out.append('<defs><marker id="ar" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#333"/></marker></defs>')
    out.append(f'<rect width="{W}" height="{H}" fill="#fff"/>')
    out.append(f'<rect x="{cols["dbm"][0]}" y="18" width="{cols["dbs"][0] + cols["dbs"][1] - cols["dbm"][0]}" height="36" rx="6" fill="#fde9a9" stroke="#b8860b"/>')
    out.append(f'<text x="{(cols["dbm"][0] + cols["dbs"][0] + cols["dbs"][1]) / 2}" y="41" text-anchor="middle" font-weight="bold">Identification of studies via databases and registers</text>')
    if has_other:
        out.append(f'<rect x="{cols["om"][0]}" y="18" width="{cols["os"][0] + cols["os"][1] - cols["om"][0]}" height="36" rx="6" fill="#e3e3e3" stroke="#777"/>')
        out.append(f'<text x="{(cols["om"][0] + cols["os"][0] + cols["os"][1]) / 2}" y="41" text-anchor="middle" font-weight="bold">Identification of studies via other methods</text>')
    # bands
    spans = {}
    for band, y0, h, _ in boxes:
        a, b = spans.get(band, (y0, y0 + h))
        spans[band] = (min(a, y0), max(b, y0 + h))
    for band, (a, b) in spans.items():
        out.append(f'<rect x="{cols["band"][0]}" y="{a}" width="{cols["band"][1]}" height="{b - a}" rx="5" fill="#cfe2f3" stroke="#6d9eeb"/>')
        cy = (a + b) / 2
        out.append(f'<text x="{cols["band"][0] + 17}" y="{cy}" text-anchor="middle" font-weight="bold" transform="rotate(-90 {cols["band"][0] + 17} {cy})">{band}</text>')

    def box(x, w, y0, h, text):
        lines = _wrap(text, w - 20)
        out.append(f'<rect x="{x}" y="{y0}" width="{w}" height="{h}" fill="#fff" stroke="#333"/>')
        for k, line in enumerate(lines):
            sp = len(line) - len(line.lstrip(" "))
            out.append(f'<text x="{x + 10 + sp * 3}" y="{y0 + 22 + 16 * k}">{html.escape(line.strip())}</text>')

    def arrow(x1, y1, x2, y2):
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#333" stroke-width="1.3" marker-end="url(#ar)"/>')

    def side(main_x, main_w, x, w, y0, h, t):
        """The box (or stack of boxes) to the right of a main box, each with an arrow from the main box."""
        hs = stack(t, w)
        if not hs:
            return
        hs[0] += h - (sum(hs) + GAP * (len(hs) - 1))       # the first box takes up any spare height
        yy = y0
        for k, tx in enumerate(t if isinstance(t, list) else [t]):
            box(x, w, yy, hs[k], tx)
            arrow(main_x + main_w, yy + hs[k] / 2, x, yy + hs[k] / 2)
            yy += hs[k] + GAP

    prev = {}
    for band, y0, h, texts in boxes:
        (mx, mw), (sx, sw), (ox, ow), (qx, qw) = [t[0] for t in texts]
        if texts[0][1]:
            box(mx, mw, y0, h, texts[0][1])
        side(mx, mw, sx, sw, y0, h, texts[1][1])
        if texts[2][1]:
            box(ox, ow, y0, h, texts[2][1])
            side(ox, ow, qx, qw, y0, h, texts[3][1])
        if "dbm" in prev:
            arrow(mx + mw / 2, prev["dbm"], mx + mw / 2, y0)
        if texts[2][1] and "om" in prev:
            arrow(ox + ow / 2, prev["om"], ox + ow / 2, y0)
        prev["dbm"] = y0 + h
        if texts[2][1]:
            prev["om"] = y0 + h
        if band == "Included" and has_other and "om" in prev:
            ox_c = cols["om"][0] + cols["om"][1] / 2
            out.append(f'<polyline points="{ox_c},{prev["om"]} {ox_c},{y0 + h / 2} {mx + mw},{y0 + h / 2}" fill="none" stroke="#333" stroke-width="1.3" marker-end="url(#ar)"/>')
    for k, line in enumerate(foot):
        out.append(f'<text x="{cols["dbm"][0]}" y="{y + 6 + 15 * k}" font-size="11" fill="#444">{html.escape(line)}</text>')
    out.append("</svg>")
    return "\n".join(out)
