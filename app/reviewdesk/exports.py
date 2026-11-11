"""Files to download: spreadsheets of records and decisions, RIS of the included studies, the search log,
the agreement and AI reports, and a methods summary written from what actually happened."""
from __future__ import annotations

import csv
import io

from . import VERSION, ai, prisma, screening, stats, util
from .dedup import RULE_TEXT
from .project import Project, SETS


def _csv(header: list, rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([util.csv_safe(x) for x in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def names(p: Project) -> dict:
    return {r["id"]: r["name"] for r in p.reviewers()}


def records_csv(p: Project) -> bytes:
    ta = {}
    for s in SETS:
        ta.update(screening.ta_outcomes(p, s))
    ft = screening.ft_outcomes(p)
    imps = {m["id"]: m for m in p.imports()}
    rows = []
    for r in p.records():
        o = ta.get(r["id"], {})
        f = ft.get(r["id"], {})
        rows.append([r["id"], SETS[r["set"]], r["title"], r["authors"], r["year"], r["journal"], r["doi"], r["pmid"],
                     "; ".join(sorted({imps[s]["database"] for s in r["sources"] if s in imps})),
                     o.get("status", "not open"), f.get("status", ""), f.get("reason", ""), f.get("study", "")])
    return _csv(["ID", "Record set", "Title", "Authors", "Year", "Journal", "DOI", "PMID", "Found in",
                 "Title and abstract", "Full text", "Exclusion reason", "Study"], rows)


def decisions_csv(p: Project) -> bytes:
    by = p.by_id()
    nm = names(p)
    rows = []
    for rid, rows_ in p.decisions().items():
        seen = set()
        for d in rows_:
            k = (d["r"], d["s"])
            rows.append([d["t"], nm.get(rid, rid), rid, d["r"], by.get(d["r"], {}).get("title", "")[:200],
                         screening.STAGE_NAMES.get(d["s"], d["s"]),
                         d["d"], d.get("reason", ""), d.get("note", ""), "first" if k not in seen else "change"])
            seen.add(k)
    for (rec, stage), d in sorted(p.resolutions().items()):
        # the decision the project lead recorded on a disagreement (how it was reached is not recorded)
        rows.append([d["t"], nm.get(d.get("by", ""), d.get("by", "")), d.get("by", ""), rec, by.get(rec, {}).get("title", "")[:200],
                     {"ta": "title and abstract", "ft": "full text"}.get(stage, stage), d["d"], d.get("reason", ""),
                     d.get("note", ""), "resolution recorded by the project lead"])
    rows.sort(key=lambda r: r[0])
    return _csv(["When", "Who", "Initials", "Record", "Title", "Stage", "Decision", "Reason", "Note", "Kind"], rows)


def included_ris(p: Project) -> bytes:
    by = p.by_id()
    ft = screening.ft_outcomes(p)
    out = io.StringIO()
    for rid, o in sorted(ft.items()):
        if o["status"] != "included":
            continue
        r = by.get(rid)
        if not r:
            continue
        out.write("TY  - JOUR\n")
        out.write(f"TI  - {util.one_line(r.get('title'))}\n")
        for au in [util.one_line(a) for a in (r.get("authors") or "").split(";") if a.strip()]:
            out.write(f"AU  - {au}\n")
        for tag, k in (("PY", "year"), ("JO", "journal"), ("VL", "volume"), ("IS", "issue"), ("SP", "pages"), ("DO", "doi"), ("AB", "abstract")):
            if r.get(k):
                out.write(f"{tag}  - {util.one_line(r[k])}\n")
        study = util.one_line(o.get("study"))
        out.write(f"N1  - ReviewDesk record {rid}" + (f"; study: {study}" if study else "") + "\nER  - \n\n")
    return out.getvalue().encode("utf-8")


HOW_WORDS = {"both": "both reviewers", "agreed together": "decision recorded by the project lead"}


def excluded_fulltext_csv(p: Project) -> bytes:
    from . import reporting                           # here, not at the top: reporting imports this module
    by = p.by_id()
    src = reporting.accession_sources(p)
    rows = []
    for rid, o in sorted(screening.ft_outcomes(p).items()):
        if o["status"] == "excluded":                 # reports assessed and excluded (PRISMA item 16b); not retrieved,
            r = by.get(rid) or {"id": rid, "title": rid}  # awaiting classification and ongoing are in the records spreadsheet
            # the same study label, reference and identifier as the Word list of excluded studies
            rows.append([rid, reporting.first_author_year(r), reporting.cite(r, ident=False), r.get("title", ""),
                         r.get("journal", ""), r.get("year", ""), reporting.identifier(r, src.get(rid, "")),
                         o.get("reason", "") or "No reason recorded", HOW_WORDS.get(o.get("how", ""), o.get("how", ""))])
    return _csv(["ID", "Study", "Reference", "Title", "Journal", "Year", "DOI or identifier", "Reason for exclusion",
                 "How decided"], rows)


def search_log_csv(p: Project) -> bytes:
    imps = p.imports()
    rows = []
    for e in p.search_log():
        files = [m.get("original_name") or m["filename"] for m in imps if m.get("search_id") == e["id"]]
        rows.append([e.get("database", ""), e.get("platform", ""), e.get("coverage", ""), e.get("date", ""),
                     e.get("results", ""), e.get("limits", ""), e.get("string", ""), "; ".join(files), e.get("notes", ""),
                     names(p).get(e.get("by", ""), e.get("by", ""))])
    return _csv(["Database", "Platform", "Coverage", "Date searched", "Records", "Limits and filters", "Search strategy",
                 "Export files", "Notes", "Run by"], rows)


def ai_csv(p: Project) -> bytes:
    a = ai.answers(p)
    comb = ai.combined(p)
    rows = []
    for r in p.records():
        A, B = a["A"].get(r["id"], {}), a["B"].get(r["id"], {})
        if not A and not B:
            continue
        rows.append([r["id"], r["title"][:200], comb.get(r["id"], ""), A.get("decision", ""), A.get("criterion", ""), A.get("reason", ""),
                     B.get("decision", ""), B.get("criterion", ""), B.get("reason", ""), A.get("served", ""), B.get("served", "")])
    return _csv(["ID", "Title", "AI (kept unless both exclude)", "Pass A", "Pass A criterion", "Pass A reason",
                 "Pass B", "Pass B criterion", "Pass B reason", "Pass A model as served", "Pass B model as served"], rows)


def _readable(x) -> str:
    """Audit details as 'key: value; key: value' rather than Python's dict notation."""
    if isinstance(x, dict):
        return "; ".join(f"{k.replace('_', ' ')}: {_readable(v)}" for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return ", ".join(_readable(v) for v in x)
    if isinstance(x, bool):
        return "yes" if x else "no"
    return "" if x is None else str(x)


def audit_for(p: Project, lead: bool) -> list:
    """The audit log as a member may read it. Everyone reads every entry; for someone who is not the project lead,
    the entry that ends an AI run leaves out how many of the AI's answers failed (the Overview and the AI page hide
    that number from them too: it is one of the AI's figures, which only the lead sees)."""
    out = []
    for e in p.audit_entries():
        if not lead and e.get("action") == "AI run ended" and isinstance(e.get("details"), dict) and "errors" in e["details"]:
            e = {**e, "details": {k: v for k, v in e["details"].items() if k != "errors"}}
        # A verification sample's numbers are the lead's too: how many records left on one reviewer's vote, set beside
        # that reviewer's own count, is how many of their exclusions the AI kept; and its verifier is not told how
        # many of their records are mixed in.
        if not lead and e.get("action") in VERIFY_ACTIONS and isinstance(e.get("details"), dict):
            e = {**e, "details": {k: v for k, v in e["details"].items() if k in ("set", "screened by", "reason")}}
        elif e.get("action") in VERIFY_ACTIONS and isinstance(e.get("details"), dict) and "draw" in e["details"]:
            # (the draw is logged whole, for the program: the lead reads its numbers here and its records in the data)
            e = {**e, "details": {k: v for k, v in e["details"].items() if k != "draw"}}
        out.append(e)
    return out


VERIFY_ACTIONS = ("draw the verification sample", "end the verification sample as it stands")     # (evaluation.DRAW, END)


def audit_csv(p: Project, lead: bool = True) -> bytes:
    nm = names(p)
    rows = [[e.get("t", ""), nm.get(e.get("who", ""), e.get("who", "")), e.get("action", ""), _readable(e.get("details", "")),
             e.get("version", "")] for e in audit_for(p, lead)]
    return _csv(["When", "Who", "Action", "Details", "Version"], rows)


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")


def _day(s) -> str:
    """'2026-11-04' (or a time stamp) as '4 November 2026', so that no sentence starts with a number."""
    t = str(s or "")[:10]
    try:
        y, m, d = (int(x) for x in t.split("-"))
        return f"{d} {MONTHS[m - 1]} {y}"
    except (ValueError, IndexError):
        return t or "an unrecorded date"


def _list(xs: list) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _pl(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def dedup_rules(p: Project) -> dict:
    """The duplicate rule as it was recorded when duplicates were removed (not simply today's rule): {"latest": the
    rule last used, word for word, "version": the ReviewDesk that used it ('' if not recorded), "earlier": [{"rule",
    "version", "until"}] rules used before it, oldest first, "earlier_note": a sentence for the short methods summary,
    or ''}. A project whose duplicates have not been removed yet has today's rule."""
    from . import VERSION
    dd = p.dedup_report()
    hist = [h for h in dd.get("rules") or [] if isinstance(h, dict) and h.get("rule")]
    if not hist:
        hist = [{"rule": dd.get("rule") or RULE_TEXT, "version": str(dd.get("version") or ("" if dd.get("rule") else VERSION)), "since": ""}]
    last = hist[-1]
    earlier = [{"rule": h["rule"], "version": str(h.get("version") or ""), "until": str(hist[k + 1].get("since") or "")[:10]}
               for k, h in enumerate(hist[:-1])]
    note = ""
    if earlier:
        when = earlier[-1]["until"]
        note = ("The rule changed with a newer version of ReviewDesk while the review was under way"
                + (f" (on {when})" if when else "") + ": records already being screened kept the merges made under the "
                "earlier rule, which is given word for word in the search methods appendix.")
    return {"latest": last["rule"], "version": str(last.get("version") or ""), "earlier": earlier, "earlier_note": note}


def the_reviewers(p: Project) -> str:
    """'the reviewers', or 'the reviewer' in a review one person screens alone."""
    return "the reviewer" if len([x for x in screening.plan(p, "main").get("reviewers") or [] if x]) <= 1 else "the reviewers"


def _pl0(n: int, one: str, many: str = "") -> str:
    """As _pl, but 'no pairs were' rather than '0 pairs were'."""
    return f"no {many or one + 's'}" if n == 0 else _pl(n, one, many)


class Heading(str):
    """A section heading in the methods summary: in the plain text it is a line of its own."""


class Fill(str):
    """A line only the authors can write ("[Authors to complete: ...]"): highlighted in the Word methods text."""


def calibration_table(p: Project, set_name: str, k: int):
    """The two-by-two table of calibration round k (0-based), worked out as screening.agreement worked it out when the
    round was finished (each reviewer's vote as it stood when the last of them was cast), or None."""
    sv = p.state_ro().get("sets", {}).get(set_name, {})
    rounds = sv.get("calibration_rounds") or []
    if k >= len(rounds):
        return None
    pl = screening.plan(p, set_name)
    a, b = (pl["reviewers"] + ["", ""])[:2]
    V = screening.votes(p)
    ind = screening.independent(V, "cal", rounds[k], a, b, True)      # the votes that count, as agreement() takes them
    if not ind:
        return None
    return stats.two_by_two([(x["d"] in screening.FWD, y["d"] in screening.FWD) for x, y in ind.values()])


def calibration_result(p: Project, set_name: str, k: int, cr: dict) -> dict:
    """A stored calibration result with its two-by-two table added when the votes give the same kappa, so that its
    confidence intervals can be reported in full."""
    t = calibration_table(p, set_name, k)
    if t and t["n"] == cr.get("n"):
        return dict(cr, table={c: t[c] for c in stats.CELLS})
    return dict(cr)


def ai_rule_text(sr: dict, full: bool) -> str:
    """What the AI stopping rule decided. For a reviewer who is not the lead, only the outcome: never the AI's keep
    rate or anything else worked out from its answers."""
    rule = sr.get("ai_rule") or ""
    kind = sr.get("ai_rule_kind") or ("dropped" if "could not be completed" in rule else
                                      "not_needed" if "not needed" in rule else "stopping_rule")
    if not rule:
        return ""
    if kind == "dropped":
        return f"Deviation from the protocol: {rule}."
    if kind == "not_needed":
        return f"Because kappa was below the threshold, {rule.replace('the AI step was not needed, because ', 'the AI step was not used: ')}."
    if full:
        return f"Under the pre-specified stopping rule, {rule}."
    if kind == "net":
        return ("Under the pre-specified stopping rule, the AI second screener stayed in use behind the single reviewer: "
                "every record the single reviewer excluded but the AI would keep went to the second reviewer.")
    return "Under the pre-specified stopping rule, the AI step was dropped and both reviewers screened every record."


def methods_summary(p: Project, full: bool = True) -> str:
    """A plain-text account of what was done, for the methods section and PRISMA items 8 and 16. full=False is the
    reviewers' version: no AI accuracy figures, no second-look figures, and counts that cannot show what the AI said
    about any record (the lead's version has them all)."""
    return p.memo(("methods", bool(full)), lambda: "\n".join(str(x) for x in methods_parts(p, full) if x is not None))


def methods_parts(p: Project, full: bool = True) -> list:
    """The methods summary as its lines, with each section heading a Heading (reporting.py builds on it).
    Shared: do not change the result."""
    return p.memo(("methods_parts", bool(full)), lambda: _methods_summary(p, full))


def _set_plan_text(p: Project, set_name: str, sv: dict, nm: dict) -> list:
    """How the records of one set were screened, from the plan fixed when it started and what then happened."""
    pl = screening.plan(p, set_name)
    a, b = (pl["reviewers"] + ["", ""])[:2]
    A, B = nm.get(a, a), nm.get(b, b)
    one, two = nm.get(pl["single_reviewer"], pl["single_reviewer"]), nm.get(pl["second_reviewer"], pl["second_reviewer"])
    out = []
    mode = sv.get("remainder_mode") or pl["mode"]
    if pl["conflicts"] == "discuss":
        # ReviewDesk records only the decision the project lead entered, not how it was reached
        rule = ("Votes were Include, Maybe or Exclude. When two reviewers disagreed (one sending a record on, the other "
                "excluding it), the project lead recorded the decision, and the record went forward to full text if that "
                "decision sent it on.")
    elif len([x for x in pl["reviewers"] if x]) <= 1:
        rule = (f"Votes were Include, Maybe or Exclude, and a record went forward to full text if {A or one or 'the reviewer'} "
                "voted Include or Maybe.")
    else:
        rule = ("Votes were Include, Maybe or Exclude, and a record went forward to full text if any reviewer who screened it "
                "voted Include or Maybe.")
    if not sv.get("sample_result"):
        if mode == "dual":
            out.append(f"{A} and {B} screened every record independently, without seeing each other's votes. {rule}")
        elif mode == "single":
            txt = f"{one} screened every record alone. {rule}"
            if sv.get("ai_net_active"):
                txt += (f" Every record {one} excluded that the AI second screener would keep (see below) was screened again "
                        f"by {two}.")
            out.append(txt)
    else:
        out.append(rule)
    if pl["conflicts"] == "discuss" and len(pl["reviewers"]) > 1:
        out.append(Fill("[Authors to complete: how disagreements at title and abstract were resolved before the project "
                        "lead recorded each decision (for example, by discussion between the two reviewers).]"))
    return out


def _ai_text(p: Project, set_name: str, v: dict, sr: dict, full: bool, scope: str) -> str:
    """The AI second screener for the main search: what was sent, how answers were used, and (for the lead only) how
    many answers came back, its accuracy on the agreement sample and its keep rate."""
    txt = ""
    if v:
        st_ = v.get("settings", {})

        def desc(ps):
            x = st_.get(ps, {})
            bits = []
            if x.get("reasoning_effort"): bits.append(f"reasoning {x['reasoning_effort']}")
            if x.get("temperature") is not None: bits.append(f"temperature {x['temperature']}")
            return v["models"][ps] + (f" ({', '.join(bits)})" if bits else "")
        cov = ai.coverage(p, set_name)
        sent = cov["records"] - cov["missing"]
        txt = (f"AI second screener: the records in {scope} were sent to two large language models, {desc('A')} and "
               f"{desc('B')}, through an OpenAI-compatible endpoint, each with its own prompt built from the eligibility "
               f"criteria (prompt fingerprints {v['prompts']['A']} and {v['prompts']['B']}; the exact prompts are in the "
               "file 'AI prompts and settings', to deposit with the review). A record counted as kept by the AI unless both "
               "models excluded it; a call that failed or gave an "
               "unreadable answer counted as kept. ")
        if full:
            txt += (f"Both models gave a readable answer for {cov['done']:,} of the {_pl(cov['records'], 'record')}"
                    + (f"; for {_pl(cov['failed'], 'record')} a call failed or the answer could not be read" if cov["failed"] else "")
                    + (f"; {_pl(cov['missing'], 'record was', 'records were')} not sent to both models" if cov["missing"] else "")
                    + ". ")
            cs = v["combined"]
            ci, sci = cs.get("sens_ci"), cs.get("spec_ci")
            # 1.0.0 stored the sample result without the two sample counts: fall back to the pairs the figures used,
            # and leave out "of N sample records" when that number was not recorded
            answered = v.get("sample_answered", cs.get("n", 0))
            of_n = f" of {v['sample_records']:,}" if v.get("sample_records") is not None else ""
            txt += (f"Against the two reviewers' outcome on the agreement sample, on the {answered:,}{of_n} "
                    f"sample {'record' if answered == 1 and not of_n else 'records'} both models answered, the AI's sensitivity was "
                    f"{stats.fmt_pct(cs['sensitivity'])} ({cs['tp']}/{cs['tp'] + cs['fn']}; Wilson 95% CI "
                    f"{stats.fmt_pct(ci[0]) if ci else 'n/a'} to {stats.fmt_pct(ci[1]) if ci else 'n/a'}) and its specificity "
                    f"{stats.fmt_pct(cs['specificity'])} ({cs['tn']}/{cs['tn'] + cs['fp']}; Wilson 95% CI "
                    f"{stats.fmt_pct(sci[0]) if sci else 'n/a'} to {stats.fmt_pct(sci[1]) if sci else 'n/a'}). "
                    "The sensitivity and specificity describe the AI and decided nothing. "
                    f"It kept {v['kept']:,} of the {_pl(v['records'], 'record')} "
                    + ("" if v["records"] == cov["records"] else "sent to both models ")
                    + f"in {scope} ({stats.fmt_pct(v['keep_rate'])})"
                    + (f", counting the {_pl(cov['failed'], 'record')} with a failed or unreadable answer as kept" if cov["failed"] and sent else "")
                    + ". ")
        else:
            txt += "(How many answers came back, and the AI's accuracy figures, are shown to the project lead.) "
    rule = ai_rule_text(sr, full)
    if rule:
        if full and v and _rule_kind(sr) in ("net", "stopping_rule"):
            # the keep rate, unlike the accuracy figures, decided whether the AI stayed in use
            rule = "This keep rate fed the pre-specified stopping rule: " + rule[len("Under the pre-specified stopping rule, "):]
        txt += rule + " "
    return txt + "The AI never excluded a record: every exclusion was made by a person."


def small_sample(sr: dict, a_name: str, b_name: str) -> str:
    """The caution the screen gives before the rule is applied, for the agreement sample's result: a small sample, or
    few records sent on, and what the rule then decided on the strength of kappa."""
    k = sr.get("kappa")
    if k is None or sr.get("threshold") is None:
        return ""
    single = "single screening" in (sr.get("kappa_rule") or "") or k >= sr["threshold"] - 1e-9
    decided = ("the decision to allow single screening" if single else
               "the decision that both reviewers screen every record")
    return stats.small_sample_text(sr, a_name, b_name, decided)


def second_look_text(sl: dict, one: str = "", two: str = "") -> str:
    """The second looks: records the single reviewer excluded that the AI would keep, screened again by the second
    reviewer. The sentence never starts with a number."""
    n = sl["total"]
    if not n:
        return ""
    one, two = one or "the single reviewer", two or "the second reviewer"
    it, them = ("it", "it") if n == 1 else ("they", "them")
    fwd, inc = sl["forward"], sl["included"]
    pend = sl.get("pending") or 0
    if pend:            # some second looks are still to come: never say they have all been done
        done = n - pend
        out = (f"Of the records {one} excluded, {n:,} {'was' if n == 1 else 'were'} kept by the AI and "
               f"so {'goes' if n == 1 else 'go'} to {two} for a second look, with {two} knowing that {it} had been excluded "
               f"and that the AI would keep {them}; ")
        if not done:
            return out + f"{two} has not screened {them} yet."
        out += (f"so far {done:,} {'has' if done == 1 else 'have'} been screened again ("
                + (f"{fwd:,} went to full text" if fwd else "none went to full text")
                + f"), and {pend:,} {'is' if pend == 1 else 'are'} still to be screened.")
        return out
    out = (f"Of the records {one} excluded, {n:,} {'was' if n == 1 else 'were'} kept by the AI and screened again by {two}, "
           f"who knew {it} had been excluded and that the AI would keep {them}")
    if n == 1:
        out += ("; it went to full text and " + ("was included" if inc else "was not included") if fwd else
                "; it did not go to full text")
    else:
        out += (f"; {fwd:,} of these went to full text and " + (f"{inc:,} {'was' if inc == 1 else 'were'} included" if inc
                                                                else "none was included")
                if fwd else "; none of these went to full text")
    return out + "."


def ft_disagreements(p: Project) -> dict:
    """The disagreements at full text, from what was recorded: the reports on which the two reviewers' decisions
    (each one's latest vote) differ, which of them have a decision the project lead recorded, and which are still
    open. ReviewDesk records only the decision, not how it was reached."""
    V, R = screening.votes(p), p.resolutions()
    by = p.by_id()
    req = {s: screening.ft_required(p, s) for s in SETS}
    differ, compared = [], 0
    for rid in screening.ft_outcomes(p):
        r_req = req.get((by.get(rid) or {}).get("set", "main"), req["main"])
        if len(r_req) < 2:
            continue
        status = screening.ft_outcome(rid, r_req, V, {})["status"]
        compared += status != "pending"              # both reviewers have voted on it
        if status == "conflict":
            differ.append(rid)
    resolved = [r for r in differ if (r, "ft") in R]
    return {"total": len(differ), "resolved": len(resolved), "open": len(differ) - len(resolved),
            "lead": p.lead_id(), "compared": compared}


def ft_disagreement_parts(p: Project, nm: dict) -> list:
    """What the methods and the agreement report say about disagreements at full text: how many, that the project lead
    recorded the decision on each (and how many are still open), the rule set in advance, and a line for the authors
    on how they were resolved."""
    if len(screening.ft_required(p, "main")) < 2:
        return []
    d = ft_disagreements(p)
    rule = (p.settings_ro().get("fulltext", {}).get("disagreement_default") or "").strip().rstrip(".")
    lead = nm.get(d["lead"], d["lead"])
    out = []
    said_rule = f"The rule set in advance for when the reviewers still disagree: “{rule}.”" if rule else ""
    if not d["compared"]:                             # no report has both reviewers' decisions yet
        return [said_rule] if rule else []
    if not d["total"]:
        out.append("The two reviewers' full-text decisions did not differ on any report, so there was no disagreement to "
                   "resolve." + (" " + said_rule if rule else ""))
        return out
    n = d["total"]
    txt = f"The two reviewers' decisions differed on {_pl(n, 'report')} at full text. The project lead ({lead}) recorded "
    if not d["open"]:
        txt += ("the decision on it, so none is still waiting for a decision." if n == 1 else
                "the decision on each, so none is still waiting for a decision.")
    else:
        txt += (f"the decision on {d['resolved']:,} of them; {d['open']:,} {'is' if d['open'] == 1 else 'are'} still waiting "
                "for a decision.")
    if rule:
        txt += " " + said_rule
    out.append(txt)
    out.append(Fill("[Authors to complete: how the disagreements at full text were resolved (for example, by discussion "
                    "between the two reviewers, or by a third reviewer)" + (", and how many, if any, were decided by the "
                                                                          "rule for when the reviewers still disagree"
                                                                          if rule else "") + ".]"))
    return out


def _rule_kind(sr: dict) -> str:
    rule = sr.get("ai_rule") or ""
    return sr.get("ai_rule_kind") or ("dropped" if "could not be completed" in rule else
                                      "not_needed" if "not needed" in rule else "stopping_rule")


def _methods_summary(p: Project, full: bool) -> list:
    s = p.settings_ro()
    st = p.state_ro()
    nm = names(p)
    C = prisma.counts(p, blind=not full)
    D = C["db"]
    lines = [f"Methods summary for '{s['title']}' (generated by ReviewDesk {VERSION} on {util.today()})", ""]
    lines.append(Heading("Records and de-duplication"))
    regs = D.get("registers") or {}
    dups, gone = D["duplicates"], D["other_removed"]
    lines.append(f"Database searches returned {_pl(D['identified_databases'], 'record')}"
                 + (" in the exports imported into ReviewDesk" if D.get("databases_not_imported") else "") + " ("
                 + "; ".join(f"{k} {v:,}" for k, v in D["databases"].items()) + ")"
                 + (f" and trial registers {D['identified_registers']:,} (" + "; ".join(f"{k} {v:,}" for k, v in regs.items()) + ")"
                    if D.get("identified_registers") else "") + ". Of these, "
                 + (f"{dups:,} {'was' if dups == 1 else 'were'} removed as {'a duplicate' if dups == 1 else 'duplicates'}"
                    if dups else "none was a duplicate")
                 + (f" and {_pl(gone, 'entry', 'entries')} with no title, abstract or identifier "
                    f"{'was' if gone == 1 else 'were'} set aside" if gone else "")
                 + f", leaving {_pl(D['records'], 'record')} to screen"
                 + (" (including the update search)" if D.get("update_included") else "") + ". "
                 "Duplicates were removed in ReviewDesk: " + dedup_rules(p)["latest"][:1].lower() + dedup_rules(p)["latest"][1:]
                 + (" " + dedup_rules(p)["earlier_note"] if dedup_rules(p)["earlier_note"] else ""))
    st0 = p.state_ro().get("overrides", {})
    by_hand = len(st0.get("not_dup", [])) + len(st0.get("merge", []))
    if by_hand:
        lines.append(f"{the_reviewers(p).capitalize()} also checked pairs of records flagged as possible duplicates by hand: "
                     f"{_pl0(len(st0.get('merge', [])), 'pair was', 'pairs were')} merged and "
                     f"{_pl0(len(st0.get('not_dup', [])), 'pair was', 'pairs were')} kept apart.")
    short = []
    imps = p.imports()
    for e in p.search_log():
        want = str(e.get("results") or "").replace(",", "").strip()
        got = sum(m["parse"]["records"] for m in imps if m.get("search_id") == e["id"])
        if want.isdigit() and got and int(want) != got:
            short.append(f"{e.get('database')} ({int(want):,} found, {got:,} imported)")
    if short:
        lines.append("For some searches the number of records imported differs from the number the search log records: "
                     + "; ".join(short) + ". Explain this in the methods (for example, part of an export missing).")
    ni = D.get("registers_not_imported") or {}
    if ni:
        one = len(ni) == 1
        lines.append(_list([f"{k} ({prisma.found_words(v)})" for k, v in ni.items()]) + f" {'was' if one else 'were'} also searched, but "
                     f"{'its' if one else 'their'} records were not imported into ReviewDesk, so they were not de-duplicated or "
                     "screened in ReviewDesk and are in none of the counts above or in the flow diagram.")
        lines.append(Fill(f"[Authors to complete: how the records from {_list(list(ni))} were handled outside ReviewDesk: how "
                          "they were de-duplicated and screened, by whom and whether independently, and what came of them "
                          "(PRISMA 2020 items 8 and 16a). Or import the register exports into ReviewDesk, so that they are "
                          "screened and counted with the other records.]"))
    dni = D.get("databases_not_imported") or {}
    if dni:
        # a database search logged with its number of records but no export: never silently counted as nothing
        one = len(dni) == 1
        lines.append(_list([f"{k} ({prisma.found_words(v)})" for k, v in dni.items()]) + f" {'was' if one else 'were'} "
                     f"also searched, but {'its' if one else 'their'} records were not imported into ReviewDesk, so they "
                     "were not de-duplicated or screened in ReviewDesk and are in none of the counts above or in the flow "
                     "diagram.")
        lines.append(Fill(f"[Authors to complete: what was done with the records from {_list(list(dni))}: how they were "
                          "de-duplicated and screened outside ReviewDesk, by whom and whether independently, and what came "
                          "of them (PRISMA 2020 items 8 and 16a). Or import "
                          + ("the search's export" if one else "the export of each search")
                          + " into ReviewDesk, so that its records are screened and counted with the others.]"))
    lines.append("")
    explained = [False, False]

    def ci_methods(k: dict | None = None) -> str:
        """How the kappa intervals were worked out, said once, the first time a kappa is given; and, the first time
        the reviewers agree on every record, how the interval is worked out then."""
        out = ""
        if not explained[0]:
            explained[0] = True
            out += (" Large-sample 95% confidence intervals for kappa use the standard error of Fleiss, Cohen and Everitt "
                    "(1969). " + stats.bootstrap_method_text())
        if k is not None and not explained[1] and stats.kappa_ci(k).get("gof"):
            explained[1] = True
            out += " " + stats.gof_method_text()
        return out
    ai_anywhere = any((x.get("plan_at_start") or {}).get("ai_second_screener") or x.get("ai_net_active")
                      for x in st.get("sets", {}).values())
    for set_name, heading in (("main", "Title and abstract screening"), ("update", "Title and abstract screening: update search"),
                              ("other", "Title and abstract screening: other methods")):
        sv = st.get("sets", {}).get(set_name, {})
        if not sv.get("order"):
            continue
        pl = screening.plan(p, set_name)
        a, b = (pl["reviewers"] + ["", ""])[:2]
        lines.append(Heading(heading))
        n_set = len(sv["order"])
        lines.append(f"The {_pl(n_set, 'record')} {'was' if n_set == 1 else 'were'} put into a random order (seed "
                     f"{sv.get('seed')}) before screening; nobody saw them in any relevance-ranked order.")
        if set_name == "other" and C["other"].get("sources"):
            O = C["other"]
            # which records these are; how they were screened (by whom, and whether the AI was used) follows, from the
            # plan fixed when screening started
            lines.append("They are the records found by other methods (" + "; ".join(f"{k} {v:,}" for k, v in O["sources"].items())
                         + ")" + (f", less {_pl(O['duplicates'], 'duplicate')} of records already found" if O.get("duplicates") else "")
                         + (f" and {_pl(O['other_removed'], 'entry', 'entries')} with no title, abstract or identifier"
                            if O.get("other_removed") else "")
                         + ", and they were screened on title and abstract as a set of their own.")
        cal = sv.get("calibration_results", [])
        if cal and not b:
            # one reviewer: a practice round, but no second set of votes to compare it with
            n_cal = sum(len(r) for r in (sv.get("calibration_rounds") or [])[:len(cal)])
            lines.append(f"{nm.get(a, a)} first screened {_pl(n_cal, 'record')} as a practice (calibration) round; with one "
                         "reviewer there was no agreement to measure. These practice votes were not used for any record's "
                         "outcome: the records were screened again later with the other records, and only those later votes "
                         "counted.")
            cal = []
        for k, cr in enumerate(cal, 1):
            if cr.get("n"):
                cr = calibration_result(p, set_name, k - 1, cr)
                lines.append(f"Calibration round {k}: {nm.get(a, a)} and {nm.get(b, b)} screened the same {_pl(cr['n'], 'record')} "
                             f"independently. Agreement was {stats.fmt_pct(cr['agreement'])}, "
                             f"{stats.kappa_text(cr)}." + (ci_methods(cr) if cr.get("kappa") is not None else ""))
        if cal:
            whose = "records were" if len(cal) == 1 else "rounds' records were"
            lines.append(("These practice votes were not used for the agreement sample or for any record's outcome: the "
                          f"calibration {whose} screened again later "
                          + ("with the other records outside the agreement sample" if sv.get("sample") else "with the other records")
                          + ", and only those later votes counted."))
            # ReviewDesk does not record what the reviewers did with the calibration disagreements
            lines.append(Fill("[Authors to complete: what was done with the calibration disagreements (for example, "
                              "discussed and the eligibility criteria clarified).]"))
        lines += _set_plan_text(p, set_name, sv, nm)
        sr = sv.get("sample_result")
        if sr:
            ra, rb = (sr.get("reviewers") or [a, b])[:2]
            t = sr.get("table") or {}
            lines.append(f"Both reviewers then screened a random {stats.fmt_pct(pl['sample_fraction'], 0)} of records "
                         f"({sr['n']:,}) independently, without seeing each other's votes until both had finished. Using each "
                         "reviewer's vote before the two were compared (Maybe counted as Include): both sent "
                         f"{t.get('both_yes', 0):,} on, both excluded {t.get('both_no', 0):,}, only {nm.get(ra, ra)} sent "
                         f"{t.get('only_a', 0):,} on and only {nm.get(rb, rb)} sent {t.get('only_b', 0):,} on. Agreement was "
                         f"{stats.fmt_pct(sr['agreement'])}, {stats.kappa_text(sr)}. The pre-specified threshold was "
                         f"{stats.fmt_threshold(sr['threshold'])}: {sr['kappa_rule']}."
                         + (" " + stats.threshold_text(sr, sr.get("threshold")) if stats.threshold_text(sr, sr.get("threshold")) else "")
                         + (" " + small_sample(sr, nm.get(ra, ra), nm.get(rb, rb)) if small_sample(sr, "", "") else "")
                         + (f" {sr['note']}" if sr.get("note") else "")
                         + (ci_methods(sr) if sr.get("kappa") is not None else ""))
            recount = screening.sample_recount_methods(p, set_name)      # votes that arrived after the rule was applied
            if recount:
                lines.append(recount)
            if sr.get("ai") or sr.get("ai_rule"):
                lines.append("")
                lines.append(_ai_text(p, set_name, sr.get("ai") or {}, sr, full, "the main search"))
            if sr.get("remainder"):
                lines.append(f"After the agreement sample, {sr['remainder']}.")
        elif pl.get("inherited"):
            lines.append("This set followed the outcome of the main screening plan.")
        if sv.get("ai_net_active") and not sr:
            cov = ai.coverage(p, set_name)
            if full:
                lines.append(f"The AI second screener, set up as for the main search, was sent the records in this set: both models "
                             f"gave a readable answer for {cov['done']:,} of the {cov['records']:,}"
                             + (f"; for {_pl(cov['failed'], 'record')} a call failed or the answer could not be read, and "
                                f"{'it' if cov['failed'] == 1 else 'they'} counted as kept" if cov["failed"] else "")
                             + (f"; {_pl(cov['missing'], 'record was', 'records were')} not sent to both models" if cov["missing"] else "")
                             + ". The AI never excluded a record.")
            else:
                lines.append("The AI second screener, set up as for the main search, was sent the records in this set. The AI "
                             "never excluded a record.")
        elif set_name != "main" and ai_anywhere and not sv.get("ai_net_active") and not sr:
            lines.append("The AI second screener's answers were not used for these records.")
        sl = screening.second_look_stats(p, set_name) if full else {"total": 0}
        if not full and sv.get("ai_net_active"):
            lines.append("(The second-look figures are shown to the project lead.)")
        if sl["total"]:
            lines.append(second_look_text(sl))
        from . import evaluation                      # here, not at the top: evaluation imports this module
        checked = evaluation.methods_text(p, set_name, full)
        if checked:
            lines.append(checked)
        lines.append("")
    fc = C["db"]
    req = [nm.get(x, x) for x in screening.ft_required(p, "main")]
    lines.append(Heading("Full-text review"))
    sought, nr, assessed = fc["sought"], fc["not_retrieved"], fc["assessed"]
    O = C["other"]
    if sought:
        first = (f"The full {'text' if sought == 1 else 'texts'} of {_pl(sought, 'report')} {'was' if sought == 1 else 'were'} "
                 "sought; " + (f"{nr:,} could not be retrieved and " if nr else "")
                 + (f"{assessed:,} {'was' if assessed == 1 else 'were'} assessed "
                    + (f"by {_list(req)} independently" if len(req) > 1 else f"by {_list(req)}") if assessed else
                    "none has been assessed yet" if fc.get("pending_ft") else "none was assessed") + ".")
    else:
        first = ("No record from the searches has gone to full text yet." if fc.get("pending_ta") else
                 "No record from the searches went to full text, so no full text was sought.")
    inc = C["included_reports"]
    lines.append(first
                 + (f" From other methods, {_pl(O['sought'], 'further report was', 'further reports were')} sought and "
                    f"{O['assessed']:,} {'was' if O['assessed'] == 1 else 'were'} assessed." if O.get("sought") else "")
                 + (f" In all, {_pl(inc, 'report')} of {_pl(C['included_studies'], 'study', 'studies')} "
                    f"{'was' if inc == 1 else 'were'} included" if inc else
                    " In all, no study has been included yet" if not C.get("complete") else " In all, no study was included")
                 + (f"; {_pl(C['awaiting'], 'report')} {'is' if C['awaiting'] == 1 else 'are'} awaiting classification"
                    if C["awaiting"] else "")
                 + (("; 1 report of an ongoing study (registered or under way, with no results yet) is listed but not included"
                     if C.get("ongoing") == 1 else
                     f"; {C.get('ongoing', 0):,} reports of ongoing studies (registered or under way, with no results yet) are "
                     "listed but not included") if C.get("ongoing") else "") + ".")
    ag = screening.agreement(p, "main", "fulltext")
    if ag["stats"].get("kappa") is not None:
        k = ag["stats"]
        left = ag.get("both_done", k["n"]) - k["n"]
        lines.append(f"Agreement on include versus exclude at full text: {stats.fmt_pct(k['agreement'])}, "
                     f"{stats.kappa_text(k, 'reports')}, on the {_pl(k['n'], 'report')} from every record set that both "
                     "reviewers voted to include or exclude"
                     + (f" ({_pl(left, 'report')} that either reviewer marked as awaiting classification, ongoing or not "
                        "retrieved " + ("is" if left == 1 else "are") + " left out)" if left else "") + "."
                     + (" " + k["note"] if k.get("note") else "") + ci_methods(k))
    lines += ft_disagreement_parts(p, nm)
    am = s.get("amendments") or []
    if am:
        lines.append("")
        lines.append(Heading("Changes after screening started"))
        what = {"criteria": "eligibility criteria", "question": "review question", "risk_of_bias_plan": "risk-of-bias plan"}
        from . import robplan
        plan_am = iter(robplan.amendments(p))                  # in the same order, with older wording brought up to date
        for x in am:
            if x.get("what") == robplan.KEY:
                x = next(plan_am, x)
            lines.append(f"On {_day(x.get('date'))}, {nm.get(x.get('by', ''), x.get('by', ''))} changed the "
                         f"{what.get(x.get('what'), x.get('what'))}"
                         + (f" ({'; '.join(x['changes'])})" if x.get("changes") else "")
                         + (f". Reason given: {str(x['reason']).rstrip('.')}" if x.get("reason") else "")
                         + ". Report this as an amendment to the protocol.")
    if not C.get("complete"):
        lines.append("")
        lines.append("Screening is not finished: the counts above will change.")
    lines.append("")
    lines.append("Software: ReviewDesk " + VERSION + ". Every decision, with its time and author, is kept in the project folder.")
    return lines
