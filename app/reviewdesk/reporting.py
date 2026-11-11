"""Everything a systematic review's write-up needs from ReviewDesk, and the reporting pack that holds it all.

The outputs follow PRISMA 2020 (Page MJ, et al. BMJ 2021;372:n71), PRISMA-S (Rethlefsen ML, et al. Syst Rev
2021;10:39) and the 2025 joint position statement on AI in evidence synthesis (Flemyng E, et al. Cochrane, Campbell,
JBI and CEE; JBI Evid Synth 2025;23(11):2162-2166). Each is a Word document (docx.py), a CSV or a text file, made
from what the project recorded, with marked gaps where only the authors can write.

Who may have what follows the rest of ReviewDesk (see server.download_blocked): a reviewer who is not the project
lead gets the blinded methods text and flow diagram, never the AI's answers or the files listing every vote. Every
output has a status: "ready", "later" (not yet: the work it reports is not done), "lead" (the project lead only) or
"none" (nothing to list). DOWNLOADS gives each one's why_not, which is "" only when it is ready.

ITEMS is the catalogue of every output (these and the older downloads in server.py): the Reports page, the reporting
pack and its "Read me first" all come from it, so they always agree.
"""
from __future__ import annotations

import io
import re
import zipfile

from . import VERSION, ai, certainty, evaluation, exports, prisma, review, robplan, robtools, screening, stats, util
from .docx import Cell, Document, b, fill
from .project import DamagedFile, Project, SETS

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TXT = "text/plain; charset=utf-8"
CSV = "text/csv"
ZIP = "application/zip"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
PRISMA_REF = ("Page MJ, McKenzie JE, Bossuyt PM, et al. The PRISMA 2020 statement: an updated guideline for reporting "
              "systematic reviews. BMJ 2021;372:n71.")
PRISMA_S_REF = ("Rethlefsen ML, Kirtley S, Waffenschmidt S, et al. PRISMA-S: an extension to the PRISMA statement for "
                "reporting literature searches in systematic reviews. Syst Rev 2021;10:39.")
CC_BY = "CC BY 4.0, https://creativecommons.org/licenses/by/4.0/"
PRESS_REF = ("McGowan J, Sampson M, Salzwedel DM, Cogo E, Foerster V, Lefebvre C. PRESS Peer Review of Electronic Search "
             "Strategies: 2015 guideline statement. J Clin Epidemiol 2016;75:40-6.")
AI_REF = ("Flemyng E, et al. Position statement on artificial intelligence (AI) use in evidence synthesis across "
          "Cochrane, the Campbell Collaboration, JBI and the Collaboration for Environmental Evidence 2025. "
          "JBI Evid Synth 2025;23(11):2162-2166. doi:10.11124/JBIES-25-00480")


# ================================================================================================ small helpers
def _nm(p: Project):
    names = exports.names(p)
    return lambda x: names.get(x, x) if x else ""


def _pl(n: int, one: str, many: str = "") -> str:
    return exports._pl(n, one, many)


def _finished(two: bool, done: int, total: int, one: str = "study", many: str = "studies") -> str:
    """How many studies the extractors (or the one extractor) have finished, reading right for none, one and all."""
    who = "Both extractors have" if two else "The extractor has"
    if not total:
        return ""
    if not done:
        return ((f"Neither extractor has finished {'the ' + one if total == 1 else 'any of the ' + _pl(total, one, many)} yet.")
                if two else f"The extractor has not finished {'the ' + one if total == 1 else 'any of the ' + _pl(total, one, many)} yet.")
    if done == total:
        return f"{who} finished {'the ' + one if total == 1 else 'all ' + _pl(total, one, many)}."
    return f"{who} finished {done:,} of the {_pl(total, one, many)}."


def _list(xs) -> str:
    return exports._list(list(xs))


def nice_date(s) -> str:
    """'2026-12-01' (or a time stamp) as '1 December 2026'; anything else as it is."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return str(s or "")
    return f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def _dates(ds) -> str:
    ds = sorted({str(d)[:10] for d in ds if d})
    if not ds:
        return ""
    if len(ds) == 1:
        return f"on {nice_date(ds[0])}"
    a, z = nice_date(ds[0]), nice_date(ds[-1])
    if a.split()[1:] == z.split()[1:] and len(a.split()) == 3:          # 23 and 25 September 2026
        return f"between {a.split()[0]} and {z}"
    if a.split()[2:] == z.split()[2:] and len(a.split()) == 3:          # 3 March and 25 September 2026
        return f"between {' '.join(a.split()[:2])} and {z}"
    return f"between {a} and {z}"


_INITIALS = re.compile(r"(?:[A-Z]\.?-?){1,3}|[A-Z][a-z]\.|(?:[A-Z]\.){1,3}")


_LOOSE_INITIALS = re.compile(r"[A-Z][a-z]")     # 'Jw' in a list written 'Gupta P; Santos Jw': initials, not a name


def _initials(token: str, loose: bool = False) -> str:
    """'J.W.', 'JW', 'Jw.' or 'J-W' as 'JW'; '' if the token is not initials (a surname such as 'Li' or 'Wu').
    loose: also 'Jw' with no full stop, for an author list whose other names are written with initials only."""
    if _INITIALS.fullmatch(token) or (loose and _LOOSE_INITIALS.fullmatch(token)):
        return "".join(ch for ch in token if ch.isalpha()).upper()
    return ""


def _initials_style(names: list) -> bool:
    """True when an author list (without commas) writes its names as 'Surname I': at least one other name ends in
    capitals only ('Gupta P', 'Moreau MJ') and none in a full given name. Then 'Santos Jw' is 'Santos JW'."""
    plain = [n.split() for n in names if "," not in n and len(n.split()) > 1]
    if len(plain) < 2:
        return False
    caps = [b for b in plain if _initials(b[-1])]
    return bool(caps) and all(_initials(b[-1]) or _LOOSE_INITIALS.fullmatch(b[-1]) for b in plain)


def _vancouver(name: str, loose: bool = False) -> str:
    """'Smith, John A.' as 'Smith JA'; 'Smith J.' or 'Bakker Jw.' as 'Smith J' and 'Bakker JW'; organisations as they
    are. loose: 'Santos Jw' as 'Santos JW' (an author list written with initials, see _initials_style)."""
    name = " ".join(str(name or "").split())
    if "," not in name:
        bits = name.split()
        if len(bits) > 1 and _initials(bits[-1], loose):
            return f"{' '.join(bits[:-1])} {_initials(bits[-1], loose)}"
        return name
    last, first = name.split(",", 1)
    ini = "".join(w[0] for w in re.split(r"[\s.\-]+", first) if w and w[0].isalpha()).upper()
    return f"{last.strip()} {ini}".strip()


def authors_vancouver(authors: str) -> list:
    """Every author of a record, in Vancouver style."""
    names = [" ".join(a.split()) for a in (authors or "").split(";") if a.strip()]
    loose = _initials_style(names)
    return [_vancouver(a, loose) for a in names]


def cite(r: dict, ident: bool = True, source: str = "") -> str:
    """A reference in Vancouver style: Smith J, Jones K, Lee M, et al. Title. Journal. 2020;12(3):45-67. doi:...
    ident=False leaves out the DOI or other identifier (for a table that gives it in a column of its own). source: the
    database an accession number came from (accession_sources)."""
    au = authors_vancouver(r.get("authors") or "")
    parts = []
    if au:
        parts.append(", ".join(au[:6]) + (", et al." if len(au) > 6 else "."))
    t = " ".join((r.get("title") or "").split()).rstrip(".")
    if t:
        parts.append(t if t[-1] in "?!" else t + ".")              # "Does it work?" ends without a full stop added
    j = " ".join((r.get("journal") or "").split()).rstrip(".")
    detail = str(r.get("year") or "") + (f";{r['volume']}" if r.get("volume") else "") + (f"({r['issue']})" if r.get("issue") else "") \
        + (f":{r['pages']}" if r.get("pages") else "")
    detail = detail.rstrip(".")
    if j or detail:
        parts.append(". ".join(x for x in (j, detail) if x) + ".")
    ident = identifier(r, source) if ident else ""
    if ident:
        parts.append(ident if ident.startswith("doi:") or ident.startswith("http") else ident + ".")
    return " ".join(parts) or str(r.get("id", ""))


def first_author_year(r: dict) -> str:
    """'Smith 2020': the first author's surname and the year, as reviews name studies."""
    names = [" ".join(a.split()) for a in (r.get("authors") or "").split(";") if a.strip()]
    au = names[0] if names else ""
    if "," in au:
        au = au.split(",")[0].strip()
    else:
        bits = au.split()
        if len(bits) > 1 and _initials(bits[-1], _initials_style(names)):
            au = " ".join(bits[:-1])
    au = au[:40]
    if not au:                                   # a register entry, or a report with no authors
        return str(r.get("accession") or "").strip()[:30] or util.short_text(r.get("title", ""), 30) or str(r.get("id", ""))
    return f"{au} {r.get('year', '')}".strip()


REGISTRY_ID = re.compile(r"(NCT\d{8}|ACTRN\d{14}|ISRCTN\d{8}|ChiCTR[\w-]+|DRKS\d{8}|CTRI/[\w/-]+|EUCTR[\w-]+|"
                         r"\d{4}-\d{6}-\d{2}(?:-\d{2})?|JPRN-[\w-]+|IRCT[\w-]+|PACTR\d+|KCT\d+|TCTR\d+|NL-OMON\d+|NTR\d+)",
                         re.I)


def identifier(r: dict, source: str = "") -> str:
    """How a report is identified, labelled: 'doi:…', 'PMID …', a trial registration number as it is, or 'Accession
    number … (Embase)' (source: the database the record came from, when there was only one)."""
    if r.get("doi"):
        return f"doi:{r['doi']}"
    if r.get("pmid"):
        return f"PMID {r['pmid']}"
    acc = str(r.get("accession") or "").strip()
    if acc:
        if REGISTRY_ID.fullmatch(acc):
            return acc
        return f"Accession number {acc}" + (f" ({source})" if source else "")
    return util.web_url(r.get("url"))


def accession_sources(p: Project) -> dict:
    """{record ID: the database its accession number came from}, to label accession numbers. A record keeps the
    accession number of the first copy read (de-duplication never takes it from a later copy), so it comes from the
    file of the record's first source, even when the record was also found in other databases."""
    imps = {m["id"]: m.get("database", "") for m in p.imports(include_missing=True)}
    out = {}
    for r in p.records():
        first = str(r.get("first_source") or "").split("#")[0] or ((r.get("sources") or [""])[0])
        if imps.get(first):
            out[r["id"]] = imps[first]
    return out


def _csv_bytes(header: list, rows: list) -> bytes:
    return exports._csv(header, rows)


def _short(p: Project) -> str:
    s = p.settings_ro()
    return s.get("short_title") or s.get("title") or "Review"


def _doc(p: Project, title: str, landscape: bool = False) -> Document:
    s = p.settings_ro()
    return Document(title, subtitle=f"{s.get('title') or 'Untitled review'}. Made by ReviewDesk {VERSION} on "
                                    f"{nice_date(util.today())}.", landscape=landscape, footer=f"{_short(p)}: {title}")


def _not_final(d: Document, text: str) -> None:
    d.para([b("Not final. "), text])


def _txt(lines: list) -> bytes:
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _complete(p: Project) -> bool:
    return bool(prisma.counts(p).get("complete"))


# ================================================================================================ full-text lists
def ft_groups(p: Project) -> dict:
    """{status: [record ID, ...]} for the reports at full text, in record order."""
    out: dict = {}
    for rid, o in sorted(screening.ft_outcomes(p).items()):
        out.setdefault(o["status"], []).append(rid)
    return out


def _ft_open(p: Project) -> int:
    g = ft_groups(p)
    return len(g.get("pending", [])) + len(g.get("conflict", []))


def _notes(p: Project, rid: str) -> str:
    """What the reviewers wrote about a decided report: the note agreed together, or each reviewer's own."""
    res = p.resolutions().get((rid, "ft"))
    if res:
        return res.get("note", "")
    nm = _nm(p)
    V = screening.votes(p)
    notes = {w: (x["latest"] or {}).get("note", "") for w, x in sorted(V.get("ft", {}).get(rid, {}).items())}
    notes = {w: n for w, n in notes.items() if n.strip()}
    if len(notes) > 1 and len({" ".join(n.split()).lower() for n in notes.values()}) == 1:
        return next(iter(notes.values())) + " (both reviewers)"
    return "; ".join(f"{nm(w)}: {n}" for w, n in notes.items())


# ================================================================================================ statuses
def _core_block(p: Project, me: str, kind: str) -> tuple:
    """The rules of server.download_blocked for the older downloads, for a given person: (state, reason)."""
    if kind == "ai":
        if not p.is_lead(me):
            return "lead", "Only the project lead can download the AI's answers."
        if not ai.visible(p, me):
            return "later", "The AI's answers can be downloaded once the agreement sample is finished."
        return "", ""
    if kind not in ("records", "decisions", "evaluation", "evaluation_share"):
        return "", ""
    if not p.is_lead(me):
        return "lead", ("This file shows every reviewer's decisions and, where they are recorded, how long each took, so "
                        "only the project lead can download it." if kind.startswith("evaluation") else
                        "This file shows every reviewer's decisions, so only the project lead can download it.")
    if kind != "records" and evaluation.open_sample(p):       # (the votes of a verification sample still being screened)
        return "later", evaluation.open_sample(p)
    if any(sv.get("phase") in ("calibration", "sample") for sv in p.state_ro().get("sets", {}).values()):
        return "later", ("This file shows every reviewer's decisions, so it can be downloaded once the calibration round and "
                         "agreement sample are finished.")
    n = screening.half_voted(p)
    if n:
        return "later", (f"This file shows every reviewer's decisions, and on {n:,} record{'s' if n != 1 else ''} one reviewer has "
                         "voted and the other has not yet. It can be downloaded when both have voted on every record they share.")
    return "", ""


def core_why_not(p: Project, me: str, kind: str) -> str:
    return _core_block(p, me, kind)[1]


def _ai_used(p: Project) -> bool:
    if p.settings_ro()["screening"].get("ai_second_screener"):
        return True
    if any((sv.get("plan_at_start") or {}).get("ai_second_screener") for sv in p.state_ro().get("sets", {}).values()):
        return True
    a = ai.answers(p)
    return bool(a["A"] or a["B"])


def _profile(p: Project, me: str):
    """The GRADE evidence profile from certainty.py, when that part provides one: None if it does not."""
    fn = getattr(certainty, "evidence_profile", None)
    if not fn:
        return None
    try:
        prof = fn(p, me)
    except Exception as e:                                   # noqa: BLE001 - a fault there must not stop the reports
        return {"error": f"{type(e).__name__}: {e}", "outcomes": []}
    return prof if isinstance(prof, dict) else None


def _rob_counts(p: Project) -> tuple:
    rows = review.rob_table(p)
    return len(rows), sum(1 for r in rows if r["both_done"])


def _extracted(p: Project) -> int:
    return review.progress(p)["finished"]


def status(p: Project, me: str, kind: str) -> tuple:
    """(state, reason) for any output: ready, later, lead or none."""
    closed = evaluation.blocked(p, me)      # someone screening a verification sample has no download until it is done
    if closed:
        return "later", closed
    st = p.state_ro().get("sets", {})
    has_records = bool(p.records())
    lead = p.is_lead(me)
    no_records = ("later", "Available once records have been imported and processed (Import records).")
    groups = ft_groups(p) if has_records else {}
    ft_started = bool(groups)
    ft_open = len(groups.get("pending", [])) + len(groups.get("conflict", []))

    def ft_list(status_: str, words: str):
        if not ft_started:
            return "later", "Available once reports reach full-text review."
        if groups.get(status_):
            return "ready", ""
        if ft_open:
            return "none", f"No report has been {words} so far; {ft_open:,} {'is' if ft_open == 1 else 'are'} still being assessed."
        return "none", f"No report was {words}, so there is nothing to list."

    if kind in ("records", "decisions", "ai", "summary", "prisma", "audit", "included", "excluded", "searchlog", "dedup",
                "extraction", "rob", "rob_svg", "evaluation", "evaluation_share"):
        state, why = _core_block(p, me, kind)
        if state:
            return state, why
        if kind.startswith("evaluation"):
            return ("ready", "") if any(sv.get("order") for sv in st.values()) else ("later", "Available once screening has started.")
        if kind == "ai":
            return ("ready", "") if _ai_used(p) and (ai.answers(p)["A"] or ai.answers(p)["B"]) else ("none", "The AI has not screened any records.")
        if kind in ("records", "decisions", "summary", "prisma") and not has_records:
            return no_records
        if kind == "decisions" and not any(p.decisions().values()):
            return "later", "Available once screening has started."
        if kind == "included":
            return ("ready", "") if groups.get("included") else ("later", "Available once a study is included at full text.")
        if kind == "excluded":
            return ft_list("excluded", "excluded at full text")
        if kind == "searchlog":
            return ("ready", "") if p.search_log() else ("later", "Available once a search is recorded (Plan and log searches).")
        if kind == "dedup":
            dd = p.dedup_report()
            if not dd.get("processed"):
                return "later", "Available once the imports have been processed (Import records)."
            return ("ready", "") if dd.get("merges") else ("none", "No duplicates were found, so the log is empty.")
        if kind == "extraction":
            return ("ready", "") if review.studies(p) else ("later", "Available once a study is included at full text.")
        if kind in ("rob", "rob_svg"):
            return ("ready", "") if _rob_counts(p)[0] else ("later", "Available once risk of bias is assessed for a study (Extract data).")
        return "ready", ""
    if kind in ("rep_methods", "rep_methods_txt", "rep_counts"):
        return ("ready", "") if has_records else no_records
    if kind == "rep_search":
        return ("ready", "") if p.search_log() or p.imports() else ("later", "Available once a search is recorded in the search log (Plan and log searches).")
    if kind == "rep_agreement":
        if any(sv.get("calibration_results") or sv.get("sample_result") for sv in st.values()) or _ft_agreement(p):
            return "ready", ""
        if exports.the_reviewers(p) == "the reviewer" and not review.people(p)["second"]:
            return "none", ("One person does the screening in this review, so there is no agreement between reviewers "
                            "to report.")
        return "later", "Available once a calibration round or the agreement sample is finished (Screen titles and abstracts)."
    if kind == "rep_excluded":
        return ft_list("excluded", "excluded at full text")
    if kind == "rep_awaiting":
        return ft_list("awaiting", "marked as awaiting information")
    if kind == "rep_ongoing":
        return ft_list("ongoing", "marked as an ongoing study")
    if kind == "rep_not_retrieved":
        return ft_list("not_retrieved", "recorded as not retrieved")
    if kind == "rep_included":
        if not ft_started:
            return "later", "Available once reports reach full-text review."
        return ("ready", "") if groups.get("included") else ("later", "Available once a study is included at full text.")
    if kind == "rep_characteristics":
        if not review.studies(p):
            return "later", "Available once a study is included at full text."
        if not _extracted(p):
            return "later", ("Available once both extractors have finished at least one study (Extract data)."
                             if review.people(p)["second"] else
                             "Available once the extractor has finished at least one study (Extract data).")
        return "ready", ""
    if kind == "rep_rob":
        n, done = _rob_counts(p)
        if not n:
            return "later", "Available once risk of bias is assessed for a study (Extract data, Risk of bias)."
        if not done:
            return "later", ("Available once both reviewers have finished at least one risk-of-bias assessment."
                             if len([x for x in review.rob_people(p) if x]) > 1 else
                             "Available once the reviewer has finished at least one risk-of-bias assessment.")
        return "ready", ""
    if kind == "rep_sof":
        prof = _profile(p, me)
        if prof is None:
            return "later", "Available once outcomes are rated with GRADE (Rate certainty)."
        if prof.get("error"):
            return "later", f"The GRADE ratings could not be read ({prof['error']})."
        if not prof.get("outcomes"):
            return "later", "Available once outcomes are rated with GRADE (Rate certainty)."
        return "ready", ""
    if kind == "rep_ai":
        if not lead:
            return "lead", "Only the project lead can download this, because it shows how the AI judged the records."
        if not _ai_used(p):
            return "none", "The AI second screener was not used in this review."
        if not ai.visible(p, me):
            return "later", "Available once the agreement sample is finished: until then nobody sees the AI's answers."
        return "ready", ""
    if kind == "rep_ai_prompts":
        if not lead:
            return "lead", "Only the project lead can download the AI prompts and settings."
        if not _ai_used(p) and not ai.config(p).get("fixed_prompts"):
            return "none", "The AI second screener was not used in this review."
        return "ready", ""
    if kind in ("rep_checklist", "rep_checks", "rep_pack"):
        return "ready", ""
    if kind == "certainty":
        why = _grade_workbook(p, me)[1]
        return ("ready", "") if not why else ("later", why)
    return "none", "Unknown file."


def _grade_workbook(p: Project, me: str) -> tuple:
    """(maker, why not) for the GRADE evidence profile workbook that certainty.py makes, if it does."""
    dl = (getattr(certainty, "DOWNLOADS", {}) or {}).get("certainty")
    if not dl or getattr(certainty, "evidence_profile", None) is None:
        return None, "Available once outcomes are rated with GRADE (Rate certainty)."
    try:
        why = dl[3](p, me)
    except Exception as e:                                   # noqa: BLE001 - a fault there must not stop the reports
        why = f"The GRADE ratings could not be read ({type(e).__name__}: {e})."
    return dl[2], why


def why_not(kind: str):
    """The download's why_not(project, me): "" when it is ready, otherwise the reason in plain words."""
    def fn(p: Project, me: str) -> str:
        state, why = status(p, me, kind)
        return "" if state == "ready" else (why or "Not available yet.")
    return fn


# ================================================================================================ methods text
def _registration(p: Project) -> list:
    reg = p.settings_ro().get("registration") or {}
    if reg.get("registry") or reg.get("id"):
        txt = (f"The protocol was registered in {reg.get('registry') or 'a registry'}" + (f" ({reg['id']})" if reg.get("id") else "")
               + (f" on {nice_date(reg['date'])}" if reg.get("date") else "") + ".")
        if util.web_url(reg.get("url")):
            txt += f" It can be read at {util.web_url(reg.get('url'))}."
            return [txt]
        return [txt, ("fill", "[Authors to complete: where the review protocol can be read (for example, the registry "
                              "record's web address), or that no protocol was prepared (PRISMA 2020 item 24b). Record the "
                              "web address in Project set-up and it appears here.]")]
    return [("fill", "[Authors to complete: where the protocol was registered (register name and number) and where it can "
                     "be read, or that it was not registered (PRISMA 2020 items 24a and 24b). Record it in Project set-up and "
                     "it appears here.]")]


def _criteria(p: Project) -> list:
    c = p.settings_ro().get("criteria") or {}
    out = []
    inc = [x for x in c.get("include") or [] if str(x).strip()]
    exc = [x for x in c.get("exclude") or [] if str(x).strip()]
    rules = [x for x in c.get("rules") or [] if str(x).strip()]
    if inc:
        out += ["Studies were included if they met every one of these criteria:", ("bullets", inc)]
    if exc:
        out += ["Studies were excluded if any one of these applied:", ("bullets", exc)]
    if rules:
        out += ["Rules for borderline cases, agreed before screening:", ("bullets", rules)]
    if not out:
        out.append(("fill", "[Authors to complete: the eligibility criteria (PRISMA 2020 item 5). Write them in Project "
                            "set-up and they appear here.]"))
    out.append(("fill", "[Authors to complete: how studies were grouped for the syntheses (PRISMA 2020 item 5).]"))
    return out


def _search_parts(p: Project) -> dict:
    """The search log sorted into databases, registers, other methods and update searches."""
    imps = p.imports()
    out = {"databases": [], "registers": [], "other": [], "update": []}
    for e in p.search_log():
        files = [m for m in imps if m.get("search_id") == e["id"]]
        sets = {m.get("set") for m in files}
        if "update" in sets:
            out["update"].append((e, files))
        elif "other" in sets:
            out["other"].append((e, files))
        elif prisma.is_register(e.get("database", "")):
            out["registers"].append((e, files))
        else:
            out["databases"].append((e, files))
    return out


def _source_name(e: dict, also: str = "") -> str:
    """'MEDLINE (Ovid)'; the platform is left out when the name already gives it ('CENTRAL (Cochrane Library)').
    also: more to say in the same brackets ('WHO ICTRP (trialsearch.who.int; searched on 23 October 2026)')."""
    name = (e.get("database") or "a source").strip()
    plat = (e.get("platform") or "").strip()
    inside = [x for x in ((plat if plat and plat.lower() not in name.lower() else ""), also) if x]
    if inside and name.endswith(")"):                # 'CENTRAL (Cochrane Library; searched on …)', never two brackets
        return name[:-1] + "; " + "; ".join(inside) + ")"
    return name + (f" ({'; '.join(inside)})" if inside else "")


def _sources_text(p: Project) -> list:
    S = _search_parts(p)
    C = prisma.counts(p)
    out = []
    if S["databases"]:
        out.append(f"{_list(_source_name(e) for e, _ in S['databases'])} {'was' if len(S['databases']) == 1 else 'were'} searched "
                   f"{_dates(e.get('date') for e, _ in S['databases'])}. The full search strategies are in the search methods "
                   "appendix.")
    else:
        out.append(("fill", "[Authors to complete: the databases searched, with their platforms and the dates searched (PRISMA "
                            "2020 item 6). Record each search in the search log and it appears here.]"))
    logged = [(e, _source_name(e)) for e, _ in S["registers"]]
    typed = [str(r.get("name")).strip() for r in p.settings_ro().get("registers", []) if str(r.get("name") or "").strip()]
    typed = [r for r in typed if not any(r.lower() in name.lower() for _, name in logged)]
    regs = [_source_name(e, f"searched on {nice_date(e.get('date'))}" if e.get("date") else "") for e, _ in logged] + typed
    undated = [name for e, name in logged if not e.get("date")] + typed
    ni = C["db"].get("registers_not_imported") or {}
    if regs:
        # ReviewDesk knows only that these records were not imported: what became of them is a line for the authors in
        # the records section
        out.append(f"Trial registers were also searched: {_list(regs)}."
                   + (f" The records found in {_list(list(ni))} were not imported into ReviewDesk (see the records section)."
                      if ni else ""))
    if undated:
        out.append(("fill", f"[Authors to complete: the date {_list(undated)} {'was' if len(undated) == 1 else 'were each'} last "
                            "searched (PRISMA 2020 item 6). Record each register search in the search log (Plan and log "
                            "searches) and its date appears here.]"))
    others = sorted(C["other"].get("sources", {}).items())
    if others or S["other"]:
        dated = {}
        for e, files in S["other"]:                   # a search's date belongs to the sources its export files are from
            if e.get("date"):
                for name in {(e.get("database") or "")} | {m.get("database") or "" for m in files}:
                    dated.setdefault(name.strip().lower(), []).append(e.get("date"))
        names = [f"{k} ({_pl(v, 'record')}" + (f"; searched {_dates(dated[k.strip().lower()])}" if k.strip().lower() in dated else "") + ")"
                 for k, v in others] or [_source_name(e, f"searched {_dates([e['date']])}" if e.get("date") else "") for e, _ in S["other"]]
        out.append(f"Other methods found further records: {_list(names)}.")
        no_date = [k for k, _ in others if k.strip().lower() not in dated] if others else [
            _source_name(e) for e, _ in S["other"] if not e.get("date")]
        if no_date:
            out.append(("fill", f"[Authors to complete: when {_list(no_date)} {'was' if len(no_date) == 1 else 'were'} last "
                                "searched or consulted, and how (for example, which reference lists were checked, or the "
                                "citation index used for forward citation searching) (PRISMA 2020 item 6; PRISMA-S item 5).]"))
    if S["update"]:
        out.append(f"An update search was run {_dates(e.get('date') for e, _ in S['update'])} in "
                   f"{_list(sorted({e.get('database', '') for e, _ in S['update']}))}.")
    else:
        planned = (p.settings_ro().get("dates") or {}).get("update_search")
        if planned:
            out.append(f"An update search is planned for {nice_date(planned)}.")
    return out


def _extraction_text(p: Project) -> list:
    sts = review.studies(p)
    nm = _nm(p)
    if not sts:
        if prisma.counts(p)["complete"]:
            return ["No study was included, so no data were extracted."]
        return ["No studies have been included yet, so no data have been extracted."]
    f = review.form(p)
    ppl = review.people(p, f)
    flds = review.fields(f)
    secs = [s.get("title", "") for s in f.get("sections", []) if s.get("fields")]
    both = [x["label"] for x in flds if x["mode"] == "both"]
    first, second, lead = nm(ppl["first"]), nm(ppl["second"]), nm(p.lead_id())
    out = [f"Data were extracted in ReviewDesk on a form of {_pl(len(flds), 'item')} in {_pl(len(secs), 'section')} "
           f"({_list(secs)})" + (f", revision {f['rev']} of the form" if f.get("rev") else ", ReviewDesk's general template") + "."]
    if ppl["second"]:
        # recorded: who extracted, who checked and what they changed, and the value the lead entered where the two
        # differed; not how the answers were checked or how differences were settled (the authors' lines below)
        txt = (f"{first} extracted every item for each study. {second} checked {first}'s answers and corrected any they "
               "judged wrong")
        if both:
            txt += (f", and extracted {'this item' if len(both) == 1 else 'these items'} independently: {_list(both)}. Neither "
                    "saw the other's answers to the independently extracted items until both had finished the study")
        txt += f". Where the two differed, the project lead ({lead}) recorded the final value."
        out.append(txt)
    else:
        out.append(f"{first} extracted every item alone (no second extractor was named).")
    done = corrected = differ = changed = settled = 0
    for s in sts:
        st = review.study_state(p, s["key"], p.lead_id())
        if not st["both_done"]:
            continue
        done += 1
        for c in st["comparison"]:
            if c["status"] == "differs":
                if c["mode"] == "both":
                    differ += 1
                else:
                    corrected += 1
            elif c["status"] == "changed":
                changed += 1
            if c["status"] in review.DIFFER and c["agreed"]:
                settled += 1
    if ppl["second"]:
        txt = _finished(True, done, len(sts), "included study", "included studies")
        if done:
            txt += (f" The second extractor corrected {exports._pl0(corrected, 'answer')}" + (f" and the two differed on "
                    f"{exports._pl0(differ, 'independently extracted answer')}" if both else "")
                    + (f"; {_pl(changed, 'answer was', 'answers were')} changed by the first extractor after the check" if changed else "")
                    + ". " + _settled(corrected + differ + changed, settled))
        out.append(txt)
        out.append(("fill", f"[Authors to complete: how {second} checked the answers (for example, against the full text of "
                            "each report), and how differences between the two extractors were resolved before the project "
                            "lead recorded the final value (for example, by discussion).]"))
    out.append(("fill", "[Authors to complete: whether study investigators were contacted to obtain or confirm data, and how "
                        "(PRISMA 2020 item 9); and any assumptions made about missing or unclear information (item 10b).]"))
    return out


# Each built-in tool: its name in a sentence, the studies it is for, and a short citation (or where it is published when
# there is no paper). A tool the project lead defined is cited as the lead gave it.
TOOL_WORDS = {"rob2": ("RoB 2", "randomised trials in which individual participants were randomised to parallel groups",
                       "Sterne JAC, et al. BMJ 2019;366:l4898"),
              "rob2_cluster": ("RoB 2 for cluster-randomised trials", "cluster-randomised trials",
                               "Eldridge S, et al. RoB 2 for cluster-randomized trials, version of 18 March 2021, riskofbias.info"),
              "rob2_crossover": ("RoB 2 for crossover trials", "randomised crossover trials",
                                 "Higgins JPT, Li T, Sterne J. RoB 2 for crossover trials, preliminary version of 18 March 2021, "
                                 "riskofbias.info"),
              "robins2": ("ROBINS-I V2", "non-randomised studies of interventions with a comparison group",
                          "a draft tool published at riskofbias.info"),
              "robins_ba": ("ROBINS-I 2016", "non-randomised studies of interventions, including uncontrolled before-after "
                            "studies and interrupted time series as section 25.5 of the Cochrane Handbook describes",
                            "Sterne JA, et al. BMJ 2016;355:i4919; Cochrane Handbook for Systematic Reviews of Interventions, "
                            "chapter 25"),
              "robins_e": ("ROBINS-E", "non-randomised studies of exposures", "Higgins JPT, et al. Environ Int 2024;186:108602"),
              "quadas2": ("QUADAS-2", "diagnostic test accuracy studies", "Whiting PF, et al. Ann Intern Med 2011;155:529-36"),
              "probast_ai": ("PROBAST+AI", "studies that develop or evaluate prediction models, including AI models",
                             "Moons KGM, et al. BMJ 2025;388:e082505"),
              "quips": ("QUIPS", "prognostic factor studies", "Hayden JA, et al. Ann Intern Med 2013;158:280-6")}
TOOL_VERSION = {k: t["version"] for k, t in robtools.TOOLS.items() if t.get("version")}


def _tool_words(p: Project, key: str) -> tuple:
    """(name, the studies it is for, citation) for any tool, built-in or the lead's."""
    if key in TOOL_WORDS:
        return TOOL_WORDS[key]
    t = review.tool_def(p, key)
    if t.get("custom"):
        return (f"{t['name']}, {robtools.version_words(t.get('version_given')) or 'version not given'}", "", t.get("cite", ""))
    return (t["name"], t.get("for", ""), t.get("cite", ""))


def _settled(differed: int, settled: int) -> str:
    """'The project lead recorded a decision on the difference.', '... on each of the 3 differences.', '... on 2 of the
    3 differences; 1 is still open.' What ReviewDesk records is the lead's decision, not how it was reached."""
    if not differed:
        return "There were no differences to settle."
    if settled == differed:
        return ("The project lead recorded a decision on the difference." if differed == 1 else
                f"The project lead recorded a decision on each of the {differed:,} differences.")
    return (f"The project lead recorded a decision on {settled:,} of the {differed:,} differences; "
            f"{differed - settled:,} {'is' if differed - settled == 1 else 'are'} still open.")


def _lc(s: str) -> str:
    """A reason the lead typed, inside a sentence: its first letter in lower case unless the first word is an acronym."""
    w = (s or "").split(" ", 1)[0]
    return s[:1].lower() + s[1:] if w[:1].isupper() and w[1:2].islower() else s


def _design_item(p: Project, d: dict, reasons: dict | None = None) -> str:
    """One design of the plan with its tool and citation (or why it has no assessment), as a list item."""
    if d.get("none"):
        why = d.get("reason") or (reasons or {}).get(d.get("id"), "")
        return f"{d['name']}: no risk-of-bias assessment" + (f" ({_lc(why).rstrip('.')})." if why else ".")
    name, _, ref = _tool_words(p, d["tool"])
    t = review.tool_def(p, d["tool"])
    note = f"; ReviewDesk records {t['domains_from']}" if t.get("domains_from") else ""
    ref = str(ref or "").rstrip()
    ref = ref[:-1] if ref.endswith(".") and not ref.endswith("et al.") else ref    # inside brackets: no full stop
    return (f"{d['name']}: {name} ({ref}{note})" + (", a tool defined in ReviewDesk by the labels of its items only"
                                                    if t.get("custom") else "") + ".")


def _plan_text(p: Project) -> list:
    """The risk-of-bias plan as it stood when screening started (or when it was recorded), then each amendment with
    its reason, then the plan as amended."""
    nm = _nm(p)
    pl = robplan.plan(p)
    if not pl.get("recorded"):
        return [("fill", "[Authors to complete: the risk-of-bias tool used for each study design the review includes, as the "
                         "protocol set it (PRISMA 2020 item 11). Record the risk-of-bias plan in Project set-up and it "
                         "appears here.]")]
    when = nice_date(str(pl["recorded"])[:10])
    ams = robplan.amendments(p)
    if pl.get("before_screening"):
        out = [f"The risk-of-bias plan, recorded in ReviewDesk on {when} before screening started, set the tool for each "
               "study design:"]
    else:
        st = robplan.started_on(p)
        out = [f"The risk-of-bias plan, which sets the tool for each study design, was recorded in ReviewDesk on {when}, "
               f"after screening had started{f' (on {nice_date(st[:10])})' if st else ''}:"]
    reasons = {d["id"]: d.get("reason", "") for d in pl.get("designs") or []}
    original = robplan.history(p)["original"] if ams else pl.get("designs") or []
    if original is None:                             # amendments from before their designs were kept: their own words
        out.append(("bullets", [f"{robplan._former(str(x))}." for x in ams[0].get("before") or []]))
    else:
        out.append(("bullets", [_design_item(p, d, reasons) for d in original]))
    if not pl.get("before_screening"):
        out.append(("fill", "[Authors to complete: confirm that this plan matches the registered protocol, or say how it "
                            "differs and why (PRISMA 2020 items 11 and 24c). It was recorded in ReviewDesk only after "
                            "screening had started.]"))
    if ams:
        out.append(f"The plan was amended after screening started, as {'a protocol amendment' if len(ams) == 1 else 'protocol amendments'}. "
                   + " ".join(f"On {nice_date(str(x.get('date', ''))[:10])}, {nm(x.get('by', ''))} "
                              f"{'; '.join(x.get('changes') or ['changed it'])} (reason: {_lc(str(x.get('reason', ''))).rstrip('.')})."
                              for x in ams))
        out.append("As amended, the plan gives:")
        out.append(("bullets", [_design_item(p, d) for d in pl.get("designs") or []]))
    return out


def _rob_text(p: Project) -> list:
    R = review.rob_rows(p)
    nm = _nm(p)
    out = _plan_text(p)
    if not R["defs"]:
        if not review.studies(p) and prisma.counts(p)["complete"]:
            out.append("No study was included, so no risk-of-bias assessment was made.")
        else:
            out.append("No risk-of-bias assessments have been recorded yet: the rest of this section fills in once they are.")
        return out
    by_tool: dict = {}
    no_design = 0
    stale: dict = {}
    for (s, aid), d in R["defs"].items():
        by_tool.setdefault(d["tool"], []).append(s)
        no_design += not d.get("design")
        why = robplan.superseded(p, d)
        if why:
            now = robplan.design(p, d.get("design"))
            then = (f"the plan now gives {review.tool_def(p, now['tool']).get('short', now['tool'])}"
                    if now and not now.get("none") else "the plan now gives these studies no assessment" if now
                    else "the plan no longer has this design")
            tn, dn = _tool_words(p, d["tool"])[0], robplan._mid(d.get("design_name") or d.get("design"))
            k = (tn if dn.lower() in tn.lower() else f"{tn}, for {dn}", then)
            stale[k] = stale.get(k, 0) + 1
    bits = []
    for tool in [k for k in robtools.TOOLS if k in by_tool] + [k for k in by_tool if k not in robtools.TOOLS]:
        name = _tool_words(p, tool)[0]
        n, k = len(by_tool[tool]), len(set(by_tool[tool]))
        bits.append(f"{name} ({_pl(n, 'result')} in {_pl(k, 'study', 'studies')})")
    total, n_st = len(R["defs"]), len({s for s, _ in R["defs"]})
    n_stale = sum(stale.values())
    how = (", each with the tool chosen when it was added, before the risk-of-bias plan was recorded" if no_design == total
           else ", each with the tool the plan gives for the study's design" if not no_design and not n_stale else "")
    out.append(f"Risk of bias was assessed for {_pl(total, 'result')} in {_pl(n_st, 'study', 'studies')}, named in the "
               f"risk-of-bias tables{how}. By tool: {_list(bits)}.")
    if 0 < no_design < total:
        out.append(f"Of these, {_pl(no_design, 'result was', 'results were')} assessed before the plan was recorded, with "
                   "the tool chosen at the time" + ("." if n_stale else "; the others with the tool the plan gives for the "
                                                    "study's design."))
    if n_stale:
        who = (f"Of these, {_pl(n_stale, 'result was', 'results were')}" if n_stale < total else
               "It was" if total == 1 else f"All {total:,} were")
        out.append(f"{who} assessed with a tool the plan gave before it was amended: "
                   + "; ".join(f"{t} ({_pl(n, 'result')}; {then})" for (t, then), n in stale.items())
                   + "." + (" The others were assessed with the tool the plan gives for the study's design." if
                            total - n_stale - no_design > 0 else ""))
    out.append(("fill", "[Authors to complete: which results were assessed for risk of bias, and why those, for example "
                        "the results of the outcomes rated with GRADE (PRISMA 2020 item 11).]"))
    a, b_ = (review.rob_people(p) + ["", ""])[:2]
    if a and b_:
        out.append(f"{nm(a)} and {nm(b_)} judged every domain independently, without seeing each other's judgements until "
                   f"both had finished the assessment. Where they differed, the project lead ({nm(p.lead_id())}) recorded "
                   "the agreed judgement.")
        out.append(("fill", "[Authors to complete: how differences between the two reviewers' judgements were resolved, for "
                            "example by discussion between them or by a third reviewer; and whether the reviewers used each "
                            "tool's signalling questions or guidance (PRISMA 2020 item 11).]"))
    elif a:
        out.append(f"{nm(a)} judged every domain.")
    pairs = same = overall = overall_same = differed = settled = finished = 0
    changed = []
    for s in review.studies(p):
        for v in review.rob_view(p, s["key"], p.lead_id(), settled_only=True):
            if not v["both_done"]:
                continue
            finished += 1
            mine = []
            for d in v["domains"]:
                if d.get("a") and d.get("b"):
                    pairs += 1
                    same += d["a"] == d["b"]
                    if d["d"] == "O":
                        overall += 1
                        overall_same += d["a"] == d["b"]
                    if d["a"] != d["b"]:
                        differed += 1
                        settled += bool(d.get("agreed"))
                if d.get("override"):
                    mine.append("overall" if d["d"] == "O" else "overall applicability" if d["d"] == "AO" else d["d"])
            if mine:
                changed.append((s["label"], mine))
    if a and b_ and pairs:
        out.append(f"Both reviewers finished {_pl(finished, 'assessment')}. They gave the same judgement for {same:,} of "
                   f"{_pl(pairs, 'domain')} ({stats.fmt_pct(same / pairs)})" + (f", and the same overall judgement for "
                   f"{overall_same:,} of {_pl(overall, 'result')}" if overall else "") + ". " + _settled(differed, settled))
    if changed:
        n = sum(len(x) for _, x in changed)
        out.append(f"The project lead changed {_pl(n, 'judgement')} on which both reviewers had agreed ("
                   + "; ".join(f"{lab}: {_list(xs)}" for lab, xs in changed) + "), with the reason for each given under the "
                   "risk-of-bias tables.")
    versions = [robtools.TOOLS[t]["version"] for t in robtools.TOOLS if t in by_tool and robtools.TOOLS[t].get("version")]
    out.append(("fill", "[Authors to complete: " + "; ".join(versions + ["any changes made to a tool"]) + " (PRISMA 2020 item 11).]"))
    return out


def _certainty_text(p: Project, me: str) -> list:
    prof = _profile(p, me)
    if prof and not prof.get("error") and (prof.get("method_text") or prof.get("outcomes")):
        out = [x for x in str(prof.get("method_text") or "").split("\n\n") if x.strip()]
        outs = prof.get("outcomes") or []
        rated = [o for o in outs if o.get("complete")]
        if outs:
            # grouped by certainty, names in sentence case: "low for time to triage; very low for under-triage and …"
            # an outcome not yet finished is named apart, never with a bracket after its name (names have their own)
            by: dict = {}
            for o in outs:
                c = (o.get("certainty") or "").lower() if o.get("complete") else "not yet finished"
                by.setdefault(c, []).append(_o_text(o))
            order = ["high", "moderate", "low", "very low"]
            parts = [(f"{c} for {_list(by[c])}" if c in order else f"{c}: {_list(by[c])}")
                     for c in sorted(by, key=lambda c: order.index(c) if c in order else 9)]
            n, done = len(outs), len(rated)
            out.append(f"The certainty of the evidence was rated for {_pl(n, 'outcome')}"
                       + (f" ({done:,} of them finished)" if 0 < done < n else
                          (" (not finished yet)" if n == 1 else " (none of them finished yet)") if not done else "")
                       + ": " + "; ".join(parts) + ".")
        return out
    return [("fill", "[Authors to complete: how the certainty of the evidence was assessed, for example with GRADE (PRISMA 2020 "
                     "item 15). This fills in once outcomes are rated on the Rate certainty (GRADE) page.]")]


def methods_sections(p: Project, me: str) -> tuple:
    """(sections, closing): sections are (heading, [paragraph]) where a paragraph is text, ("bullets", [text]) or
    ("fill", text). Built on exports.methods_parts, which this keeps word for word."""
    full = p.is_lead(me)
    parts = exports.methods_parts(p, full)
    found: list = []
    cur = None
    closing = []
    for x in parts[1:]:                     # the first line is the summary's own title
        if x is None:
            continue
        if isinstance(x, exports.Heading):
            cur = (str(x), [])
            found.append(cur)
        elif not x.strip():
            continue
        elif isinstance(x, exports.Fill) and cur is not None:
            cur[1].append(("fill", str(x)))
        elif x.startswith(("Software: ReviewDesk", "Screening is not finished")):
            closing.append(x)
        elif cur is not None:
            cur[1].append(x)
    amend = [s for s in found if s[0] == "Changes after screening started"]
    found = [s for s in found if s[0] != "Changes after screening started"]
    out = [("Protocol and registration", _registration(p)), ("Eligibility criteria", _criteria(p)),
           ("Information sources and searches", _sources_text(p))]
    out += found
    out += [("Data extraction", _extraction_text(p)), ("Risk of bias in included studies", _rob_text(p)),
            ("Certainty of the evidence", _certainty_text(p, me))]
    out += amend
    return out, closing


def methods_docx(p: Project, me: str) -> bytes:
    sections, closing = methods_sections(p, me)
    d = _doc(p, "Methods text")
    d.note("ReviewDesk wrote this from what was recorded in the project. Edit it into the methods section of your review in "
           "your own words, check every number against the PRISMA flow diagram, and complete the highlighted lines.")
    if not p.is_lead(me):
        d.note("This is the version for reviewers: the AI's figures (how many answers came back, its accuracy and how many "
               "records it kept) and the second-look figures are in the project lead's version.")
    if not _complete(p):
        _not_final(d, "Screening is not finished, so the numbers will still change.")
    for head, paras in sections:
        d.heading(head, 1)
        for x in paras:
            if isinstance(x, tuple) and x[0] == "bullets":
                d.bullets(x[1])
            elif isinstance(x, tuple) and x[0] == "fill":
                d.fill_in(x[1])
            else:
                d.para(x)
    full, to_add = methods_references(sections)
    if full or to_add:
        d.heading("References for the methods named above", 1)
        for x in full:
            d.para(x)
        if to_add:
            d.fill_in(to_add)
    for x in closing:
        d.note(x)
    return d.to_bytes()


# Sources the methods text cites by author and year. ReviewDesk prints in full the references it holds word for word
# and asks the authors to add the others to their reference list: it does not print a citation it cannot vouch for.
_CITED = (("Fleiss, Cohen and Everitt", "Fleiss, Cohen and Everitt (1969)", ""),
          ("Donner and Eliasziw", "Donner and Eliasziw (1992)", stats.GOF_REF),
          ("Schünemann et al. 2019", "Schünemann et al. (2019), GRADE guidelines 18", ""),
          ("Schünemann et al. 2020", "Schünemann et al. (2020), GRADE guidelines 21", ""),
          ("Zeng et al. 2022", "Zeng et al. (2022), GRADE guidance 34", ""),
          ("Balshem et al.", "", certainty.GRADE_REFS), ("Murad et al.", "", certainty.GRADE_REFS),
          ("Santesso et al.", "", certainty.GRADE_REFS))


def methods_references(sections: list) -> tuple:
    """(references ReviewDesk gives in full, a fill-in naming the others, or "") for the sources the methods text
    cites by author and year, so that none is left without its reference."""
    def flat(x):
        if isinstance(x, str):
            return x
        if isinstance(x, (list, tuple)):
            return " ".join(flat(y) for y in x)
        return ""
    text = " ".join(flat(paras) for _, paras in sections).replace("(", "").replace(")", "")
    full, add = [], []
    for seen, name, ref in _CITED:
        if seen.replace("(", "").replace(")", "") in text:
            if ref and ref not in full:
                full.append(ref)
            elif not ref and name not in add:
                add.append(name)
    fill = ("[Authors to complete: add to your reference list the " + ("source" if len(add) == 1 else "sources")
            + " cited above by author and year: " + "; ".join(add) + ".]") if add else ""
    return full, fill


def methods_txt(p: Project, me: str) -> bytes:
    sections, closing = methods_sections(p, me)
    s = p.settings_ro()
    lines = [f"Methods text for '{s.get('title', '')}' (made by ReviewDesk {VERSION} on {util.today()})", ""]
    if not p.is_lead(me):
        lines += ["This is the version for reviewers: the AI's figures (how many answers came back, its accuracy and how "
                  "many records it kept) and the second-look figures are in the project lead's version.", ""]
    for head, paras in sections:
        lines += [head.upper(), ""]
        for x in paras:
            if isinstance(x, tuple) and x[0] == "bullets":
                lines += [f"  - {y}" for y in x[1]]
            elif isinstance(x, tuple):
                lines.append(x[1])
            else:
                lines.append(x)
            lines.append("")
    full, to_add = methods_references(sections)
    if full or to_add:
        lines += ["REFERENCES FOR THE METHODS NAMED ABOVE", ""] + [y for x in full for y in (x, "")]
        if to_add:
            lines += [to_add, ""]
    lines += closing
    return _txt(lines)


# ================================================================================================ search appendix
def _missing(e: dict, part: str = "databases") -> list:
    """What the search log lacks for one search. The years covered are asked for bibliographic databases only (not
    for registers, citation searching or hand-searching)."""
    out = []
    database = part in ("databases", "update") and not prisma.is_register(e.get("database", ""))
    if not (e.get("platform") or "").strip():
        out.append("the platform or interface used (PRISMA-S item 1)")
    if database and not (e.get("coverage") or "").strip():
        out.append("the years the database covers")
    if not (e.get("date") or "").strip():
        out.append("the date searched (PRISMA-S item 13)")
    if not str(e.get("results") or "").strip():
        out.append("the number of records found (PRISMA-S item 15)")
    if not (e.get("string") or "").strip():
        out.append("the full search strategy, exactly as run (PRISMA-S item 8)")
    if not (e.get("limits") or "").strip():
        out.append("the limits and filters used, or 'none' (PRISMA-S items 9 and 10)")
    return out


def _entry(d: Document, p: Project, e: dict, files: list, part: str = "databases") -> None:
    nm = _nm(p)
    d.heading(_source_name(e), 2)
    got = sum(m["parse"]["records"] for m in files)
    want = str(e.get("results") or "").replace(",", "").strip()
    rows = [("Database or source", e.get("database", "")), ("Platform or interface", e.get("platform", "")),
            ("Years covered", e.get("coverage", "")), ("Date searched", nice_date(e.get("date", ""))),
            ("Records found", f"{int(want):,}" if want.isdigit() else want),
            ("Records imported", (f"{got:,} from {_list(m.get('original_name') or m['filename'] for m in files)}" if files
                                  else "No export file is linked to this search")),
            ("Limits and filters", e.get("limits", "")), ("Run by", nm(e.get("by", "")))]
    if e.get("notes"):
        rows.append(("Notes", e["notes"]))
    if part not in ("databases", "update") or prisma.is_register(e.get("database", "")):
        rows = [x for x in rows if x[0] != "Years covered" or x[1]]       # asked for bibliographic databases only
    d.fields([(k, v or "Not recorded") for k, v in rows])
    if want.isdigit() and files and int(want) != got:
        d.para([b("Check: "), f"the search found {_pl(int(want), 'record')} but {got:,} {'was' if got == 1 else 'were'} imported. Explain the difference "
                              "(for example, a part of the export missing)."])
    d.heading("Search strategy, exactly as run", 3)
    if (e.get("string") or "").strip():
        d.pre(e["string"])
    miss = _missing(e, part)
    if miss:
        d.fill_in("[Missing from the search log: " + "; ".join(miss) + ". Add it in the search log (Plan and log searches).]")


def search_docx(p: Project, me: str) -> bytes:
    S = _search_parts(p)
    C = prisma.counts(p, blind=not p.is_lead(me))
    d = _doc(p, "Search methods appendix (PRISMA-S)")
    d.note("The searches as recorded in ReviewDesk's search log, in the order of the PRISMA-S checklist (" + PRISMA_S_REF
           + ") Highlighted lines are things the log has no information for: add them before you submit.")
    all_ = [(e, f, part) for part in ("databases", "registers", "other", "update") for e, f in S[part]]
    words = {"databases": "Database", "registers": "Trial register", "other": "Other method", "update": "Update search"}
    d.heading("Sources searched", 1)
    if all_:
        d.table(["Source", "Platform", "Kind", "Date searched", "Records found"],
                [[e.get("database", ""), e.get("platform", ""), words[part], nice_date(e.get("date", "")),
                  str(e.get("results") or "")] for e, f, part in all_], widths=[3, 2, 1.6, 1.8, 1.2], compact=True,
                caption="Every search in the search log")
    else:
        d.fill_in("[No search has been recorded in the search log yet (Plan and log searches).]")
    d.heading("Databases (PRISMA-S items 1, 2, 8, 9, 13 and 15)", 1)
    if S["databases"]:
        d.fill_in("[Authors to complete: whether each database was searched on its own, or several were searched together "
                  "on one platform (for example, MEDLINE and Embase at once on Ovid), and if so which (PRISMA-S item 2).]")
        for e, files in S["databases"]:
            _entry(d, p, e, files)
    else:
        d.fill_in("[No database search is recorded.]")
    d.heading("Study registries (PRISMA-S item 3)", 1)
    typed = [r for r in p.settings_ro().get("registers", []) if str(r.get("name") or "").strip()]
    ni = C["db"].get("registers_not_imported") or {}
    if S["registers"]:
        for e, files in S["registers"]:
            _entry(d, p, e, files, "registers")
            if not files and str(e.get("database") or "").strip() in ni:
                d.para("The records this search found were not imported into ReviewDesk, so they are in none of the flow "
                       "diagram's boxes (a footnote names them).")
    logged = [_source_name(e).lower() for e, _ in S["registers"]]
    for r in typed:
        name = str(r["name"]).strip()
        if any(name.lower() in x for x in logged):
            continue
        d.heading(name, 2)
        found = prisma.found_words(ni[name]) if name in ni else prisma.found_words(prisma._number(r.get("records")))
        d.para(f"Recorded in Project set-up as searched ({found} found)"
               + ("; the records were not imported into ReviewDesk, so they are in none of the flow diagram's boxes" if name in ni
                  else "") + ". The search is not in the search log.")
        d.fill_in(f"[Missing from the search log for {name}: the platform or interface used (PRISMA-S item 1), the date "
                  "searched (PRISMA-S item 13), the number of records found (PRISMA-S item 15), the search strategy exactly as "
                  "run (PRISMA-S item 8) and any limits used (PRISMA-S item 9). Record the search in the search log (Plan "
                  "and log searches) and it appears here.]")
    if not S["registers"] and not typed:
        d.fill_in("[No trial register search is recorded. If registers were searched, record each search in the search log.]")
    d.heading("Online resources, citation searching, contacts and other methods (PRISMA-S items 4 to 7)", 1)
    others = sorted(C["other"].get("sources", {}).items())
    if others:
        d.table(["Source", "Records found"], [[k, f"{v:,}"] for k, v in others], widths=[4, 1], landscape=False,
                caption="Records found by other methods (imported into ReviewDesk)")
    for e, files in S["other"]:
        _entry(d, p, e, files, "other")
    d.fill_in("[Authors to complete: any websites or online resources browsed (item 4), how citations were searched, "
              "forwards and backwards (item 5), any study authors or experts contacted (item 6), and any other methods "
              "(item 7).]")
    d.heading("Search filters and prior work (PRISMA-S items 10 and 11)", 1)
    d.fill_in("[Authors to complete: whether published search filters were used, as designed or changed, with a citation for "
              "each (item 10); and whether searches from earlier work were reused or adapted, with a citation (item 11).]")
    d.heading("Updates (PRISMA-S item 12)", 1)
    if S["update"]:
        d.para(f"The searches were re-run {_dates(e.get('date') for e, _ in S['update'])}:")
        for e, files in S["update"]:
            _entry(d, p, e, files, "update")
    else:
        planned = (p.settings_ro().get("dates") or {}).get("update_search")
        d.para("No update search is recorded yet." + (f" One is planned for {nice_date(planned)}." if planned else ""))
    d.heading("Dates of searches (PRISMA-S item 13)", 1)
    dated = [(e, part) for e, f, part in all_ if e.get("date")]
    d.para(f"The searches in the log were run {_dates(e.get('date') for e, _ in dated)}." if dated else "No search date is recorded.")
    undated = [_source_name(e) for e, f, part in all_ if not e.get("date")] + [
        str(r["name"]).strip() for r in typed if not any(str(r["name"]).strip().lower() in x for x in logged)]
    if undated:
        d.fill_in(f"[Authors to complete: the date {_list(undated)} {'was' if len(undated) == 1 else 'were each'} searched "
                  "(PRISMA-S item 13).]")
    d.heading("Peer review of the searches (PRISMA-S item 14)", 1)
    ws = p.worksheet()
    ticks = [x for x in (ws.get("press") or []) if x]
    if ws.get("press"):
        d.para(f"On the search worksheet, {len(ticks):,} of the {_pl(len(ws['press']), 'prompt')} for checking the strategy "
               f"{'is' if len(ticks) == 1 else 'are'} ticked (ReviewDesk's own prompts, covering the areas of PRESS 2015: "
               f"{PRESS_REF.rstrip('.')}).")
    d.fill_in("[Authors to complete: who peer reviewed the search strategies, and how (for example, a librarian using the "
              "PRESS 2015 guideline).]")
    d.heading("Total records and de-duplication (PRISMA-S items 15 and 16)", 1)
    dd = p.dedup_report()
    files = dd.get("files", [])
    if files:
        imps = {m["id"]: m for m in p.imports(include_missing=True)}
        rows = [[f.get("database", ""), SETS.get(f.get("set"), f.get("set", "")),
                 imps.get(f.get("import"), {}).get("original_name") or f.get("filename", ""), f"{f['read']:,}",
                 f"{f['duplicates']:,}", f"{f['empty']:,}", f"{f.get('new', 0):,}"] for f in files]
        tot = [sum(f[k] for f in files) for k in ("read", "duplicates", "empty")] + [sum(f.get("new", 0) for f in files)]
        rows.append([Cell("Total", bold=True), "", ""] + [Cell(f"{x:,}", bold=True) for x in tot])
        d.table(["Source", "Records set", "File", "Records read", "Duplicates removed", "Set aside (no title, abstract or identifier)",
                 "New records kept"], rows, compact=True, caption="Records read from each export, and duplicates removed")
        dni = {**(C["db"].get("databases_not_imported") or {}), **(C["db"].get("registers_not_imported") or {})}
        if dni:
            one = len(dni) == 1
            d.para(f"Not in this table: {_list([f'{k} ({prisma.found_words(v)} found)' if v is not None else f'{k} ({prisma.found_words(v)})' for k, v in dni.items()])}. "
                   f"{'Its' if one else 'Their'} records were not imported into ReviewDesk, so they were not de-duplicated or "
                   "screened in ReviewDesk and are in none of these totals or of the flow diagram's boxes.")
            d.fill_in(f"[Authors to complete: what was done with the records from {_list(list(dni))} (PRISMA-S item 16).]")
        dr = exports.dedup_rules(p)
        d.para(f"Duplicates were removed in ReviewDesk{(' ' + dr['version']) if dr['version'] else ''}, which reads the files in "
               "a fixed order (MEDLINE first, as its records are the fullest) and keeps the first copy of each record, leaving "
               f"{_pl(len(p.records()), 'unique record')}. The rule, word for word: {dr['latest']}")
        for e in dr["earlier"]:                  # a rule used earlier in the same review (records already being screened kept its merges)
            d.para("An earlier rule was used" + (f" until {nice_date(e['until'])}" if e["until"] else "")
                   + (f", with ReviewDesk {e['version']}" if e["version"] else ", with an earlier version of ReviewDesk")
                   + ". Records that were already being screened when the rule changed kept the merges made under it. "
                   f"That rule, word for word: {e['rule']}")
        ov = p.state_ro().get("overrides", {})
        if ov.get("merge") or ov.get("not_dup"):
            d.para(f"The reviewers also checked by hand pairs flagged as possible duplicates: {exports._pl0(len(ov.get('merge', [])), 'pair was', 'pairs were')} "
                   f"merged and {exports._pl0(len(ov.get('not_dup', [])), 'pair was', 'pairs were')} kept apart. Every merge is listed in the "
                   "de-duplication log (CSV).")
    else:
        d.para("The imports have not been processed yet, so there are no de-duplication figures.")
    unlinked = [m for m in p.imports() if not m.get("search_id")]
    if unlinked:
        d.heading("Exports not linked to a search", 2)
        d.para("These files were imported without being linked to a search in the log, so their search strategy and date are "
               "not recorded here: " + "; ".join(f"{m.get('original_name') or m['filename']} ({m.get('database', '')}, "
                                                 f"{_pl(m['parse']['records'], 'record')})" for m in unlinked) + ".")
    return d.to_bytes()


# ================================================================================================ agreement
def _ft_agreement(p: Project) -> dict:
    ag = screening.agreement(p, "main", "fulltext")
    return ag if ag["complete"] and ag["stats"].get("n") else {}


def _two_by_two(d: Document, t: dict, A: str, B: str, yes: str = "sent on", no: str = "excluded", caption: str = "") -> None:
    by, oa, ob, bn = t.get("both_yes", 0), t.get("only_a", 0), t.get("only_b", 0), t.get("both_no", 0)
    n = by + oa + ob + bn
    rows = [[Cell(f"{A}: {yes}", bold=True), f"{by:,}", f"{oa:,}", f"{by + oa:,}"],
            [Cell(f"{A}: {no}", bold=True), f"{ob:,}", f"{bn:,}", f"{ob + bn:,}"],
            [Cell("Total", bold=True), f"{by + ob:,}", f"{oa + bn:,}", f"{n:,}"]]
    d.table(["", f"{B}: {yes}", f"{B}: {no}", "Total"], rows, widths=[2.4, 1.5, 1.5, 1], landscape=False, caption=caption)


def agreement_docx(p: Project, me: str) -> bytes:
    nm = _nm(p)
    lead = p.is_lead(me)
    d = _doc(p, "Screening agreement")
    d.note("Cohen's kappa measures how far two reviewers agree beyond what chance alone would give: 1 is perfect agreement and "
           "0 is no better than chance. Each kappa has two 95% confidence intervals: one from the large-sample standard "
           "error of Fleiss, Cohen and Everitt (1969), and a percentile bootstrap interval. " + stats.bootstrap_method_text()
           + " Each reviewer's vote is the one they cast before the two were compared; at title and abstract, Maybe counts as "
           "sending a record on.")
    gof_said = [False]

    def gof_note(k: dict) -> str:
        """How the interval is worked out when the reviewers agree on every record: said once, where it first applies."""
        if gof_said[0] or not stats.kappa_ci(k).get("gof"):
            return ""
        gof_said[0] = True
        return " " + stats.gof_method_text()
    st = p.state_ro().get("sets", {})
    for set_name, label in SETS.items():
        sv = st.get(set_name, {})
        if not sv.get("order"):
            continue
        pl = screening.plan(p, set_name)
        A, B = [nm(x) for x in (pl["reviewers"] + ["", ""])[:2]]
        d.heading(f"Title and abstract screening: {label.lower()}", 1)
        before = len(d._blocks)
        cal = sv.get("calibration_results") or []
        if cal:
            crs = [exports.calibration_result(p, set_name, k, c) for k, c in enumerate(cal)]
            d.heading("Calibration rounds" if len(crs) > 1 else "Calibration round", 2)
            d.para(f"{A} and {B} screened the same records independently. These votes were practice: they were not used "
                   "for the agreement sample or for any record's outcome, and the calibration records were screened again "
                   "later with the other records" + (" outside the agreement sample" if sv.get("sample") else "") + ".")
            # nothing records what the reviewers did with the calibration disagreements
            d.fill_in("[Authors to complete: what was done with the calibration disagreements (for example, discussed and "
                      "the eligibility criteria clarified).]")
            d.table(["Round", "Records", "Agreement", "Cohen's kappa (95% CI: large-sample; bootstrap)"],
                    [[str(k), f"{c.get('n') or 0:,}", stats.fmt_pct(c.get("agreement")), stats.kappa_cell(c)]
                     for k, c in enumerate(crs, 1)],
                    widths=[0.8, 1, 1.2, 3], landscape=False,
                    caption="Agreement in each calibration round" if len(crs) > 1 else "Agreement in the calibration round")
            for k, c in enumerate(crs, 1):
                d.para(f"Round {k}: agreement {stats.fmt_pct(c.get('agreement'))}, {stats.kappa_text(c)}." + gof_note(c))
        elif sv.get("phase") == "calibration":
            d.para("The calibration round is not finished yet.")
        sr = sv.get("sample_result")
        if sr:
            ra, rb = [nm(x) for x in (sr.get("reviewers") or pl["reviewers"] + ["", ""])[:2]]
            d.heading("Agreement sample", 2)
            d.para(f"Both reviewers screened a random {stats.fmt_pct(pl['sample_fraction'], 0)} of the records "
                   f"({sr['n']:,}) independently, without seeing each other's votes until both had finished.")
            _two_by_two(d, sr.get("table") or {}, ra, rb, caption="Agreement sample: each reviewer's own vote")
            thr = stats.threshold_text(sr, sr.get("threshold"))
            d.para(f"Agreement was {stats.fmt_pct(sr['agreement'])}, {stats.kappa_text(sr)}." + (f" {thr}" if thr else "")
                   + (f" {sr['note']}" if sr.get("note") else "") + gof_note(sr))
            small = exports.small_sample(sr, ra, rb)
            if small:                                # as the screen warned before the rule was applied
                d.para([b("Caution. "), small])
            d.para(f"The rule set in advance: if kappa reached {stats.fmt_threshold(sr['threshold'])}, one reviewer could screen the rest. "
                   f"Here {sr['kappa_rule']}" + (f"; {sr['remainder']}" if sr.get("remainder") else "") + ".")
            recount = screening.sample_recount_methods(p, set_name)      # votes that arrived after the rule was applied
            if recount:
                d.para(recount)
            if sr.get("ai_rule"):
                d.para([b("AI second screener. "), exports.ai_rule_text(sr, lead)
                        + (" Its accuracy on the agreement sample is in the AI second screener report." if lead else "")])
        elif sv.get("sample"):
            ag = screening.agreement(p, set_name, "sample")
            d.heading("Agreement sample", 2)
            d.para(f"The agreement sample ({_pl(ag['records'], 'record')}) is not finished: {A} has screened {ag['done_a']:,} "
                   f"and {B} {ag['done_b']:,}. The figures appear here once both have finished it and the rule has been applied.")
        if (sv.get("remainder_mode") or pl["mode"]) == "dual" and sv.get("phase") == "screening":
            ag = screening.agreement(p, set_name, "remainder")
            outside = bool(sv.get("sample"))
            if ag["complete"] and ag["stats"].get("n"):
                d.heading("The remaining records, screened by both" if outside else "Both reviewers screened every record", 2)
                _two_by_two(d, ag["stats"], A, B, caption="Records outside the agreement sample" if outside else
                            "Records screened by both reviewers")
                d.para(f"Agreement was {stats.fmt_pct(ag['stats']['agreement'])}, {stats.kappa_text(ag['stats'])}, on "
                       f"{_pl(ag['stats']['n'], 'record')}. {ag['note']}" + (f" {ag['stats']['note']}" if ag["stats"].get("note") else "")
                       + gof_note(ag["stats"]))
            elif sv.get("phase") == "screening":
                d.para("Both reviewers are screening these records; their agreement appears here once both have screened "
                       "every one.")
        if len(d._blocks) == before:
            mode = sv.get("remainder_mode") or pl["mode"]
            one = nm(pl.get("single_reviewer"))
            d.para(f"{one} screened these records alone" + (" (as the main screening plan decided)" if pl.get("inherited") else "")
                   + (", with the AI second screener behind" if sv.get("ai_net_active") else "")
                   + ", so there is no agreement between reviewers to report." if mode == "single" else
                   "There are no agreement figures for these records yet.")
    ag = _ft_agreement(p)
    d.heading("Full-text review", 1)
    if ag:
        A, B = [nm(x) for x in ag["reviewers"]]
        _two_by_two(d, ag["stats"], A, B, yes="include", no="exclude", caption="Full texts: include versus exclude")
        left = ag.get("left_out", ag.get("both_done", ag["stats"]["n"]) - ag["stats"]["n"])
        d.para(f"Agreement was {stats.fmt_pct(ag['stats']['agreement'])}, {stats.kappa_text(ag['stats'], 'reports')}, on "
               f"{_pl(ag['stats']['n'], 'report')}. {ag['note']}"
               + (f" Left out for that reason: {_pl(left, 'report')}." if left else "")
               + (f" {ag['stats']['note']}" if ag["stats"].get("note") else "") + gof_note(ag["stats"]))
        for x in exports.ft_disagreement_parts(p, exports.names(p)):       # what was recorded, then the authors' line
            if isinstance(x, exports.Fill):
                d.fill_in(str(x))
            else:
                d.para(x)
    else:
        d.para("Full-text agreement appears here once both reviewers have assessed every report sent to full text.")
    return d.to_bytes()


# ================================================================================================ lists of studies
def _report_rows(p: Project, ids: list, notes: bool = True, ident: bool = False) -> list:
    by = p.by_id()
    src = accession_sources(p) if ident else {}
    out = []
    for rid in ids:
        r = by.get(rid) or {"id": rid, "title": rid}
        row = [first_author_year(r), " ".join((r.get("title") or "").split()), r.get("journal", ""),
               identifier(r, src.get(rid, "")) if ident else (f"doi:{r['doi']}" if r.get("doi") else "")]
        if notes:
            row.append(_notes(p, rid))
        out.append(row)
    return out


def excluded_docx(p: Project, me: str) -> bytes:
    groups = ft_groups(p)
    fto = screening.ft_outcomes(p)
    ids = groups.get("excluded", [])
    order = list(p.settings_ro().get("ft_reasons", []))
    by_reason: dict = {}
    for rid in ids:
        by_reason.setdefault(fto[rid].get("reason") or "No reason recorded", []).append(rid)
    reasons = [r for r in order if r in by_reason] + sorted(r for r in by_reason if r not in order)
    d = _doc(p, "Studies excluded at full text, with reasons")
    d.para(f"This list has the {_pl(len(ids), 'report')} that {'was' if len(ids) == 1 else 'were'} assessed in full and "
           f"excluded. Each is listed under the one reason for exclusion {exports.the_reviewers(p)} recorded for it; the reasons are in "
           "the order set in Project set-up (PRISMA 2020 item 16b).")
    if _ft_open(p):
        _not_final(d, f"Still being assessed: {_pl(_ft_open(p), 'report')}, so this list will change.")
    d.table(["Reason for exclusion", "Reports"], [[r, f"{len(by_reason[r]):,}"] for r in reasons]
            + [[Cell("Total", bold=True), Cell(f"{len(ids):,}", bold=True)]], widths=[4, 1], landscape=False,
            caption="Reports excluded at full text, by reason")
    by = p.by_id()
    src = accession_sources(p)
    for r in reasons:
        d.heading(f"{r} ({len(by_reason[r]):,})", 2)
        # the full reference, as in the list of included studies, and its identifier in a column of its own
        rows = [[first_author_year(by.get(rid) or {"id": rid}), cite(by.get(rid) or {"id": rid}, ident=False),
                 identifier(by.get(rid) or {}, src.get(rid, ""))] for rid in by_reason[r]]
        d.table(["Study", "Reference", "DOI or identifier"], rows, widths=[1.3, 4.9, 3.0], compact=True,
                caption=f"Excluded: {r}")
    d.note("The references are as the databases exported them: check each one against the report itself (for example, "
           "accents and macrons in names, which some databases drop), and use your reference manager for the final list.")
    return d.to_bytes()


def _simple_list(p: Project, me: str, status_: str, title: str, intro: str, ident_head: str = "DOI or identifier") -> bytes:
    ids = ft_groups(p).get(status_, [])
    d = _doc(p, title)
    d.para(intro)
    if _ft_open(p):
        _not_final(d, f"Still being assessed: {_pl(_ft_open(p), 'report')}, so this list may change.")
    d.para(f"This list has {_pl(len(ids), 'report')}.")
    d.table(["Study", "Report", "Journal or source", ident_head, "Notes from the reviewers"],
            _report_rows(p, ids, notes=True, ident=True), widths=[1.6, 3.4, 1.6, 1.9, 2.3], compact=True, caption=title)
    return d.to_bytes()


def awaiting_docx(p: Project, me: str) -> bytes:
    return _simple_list(p, me, "awaiting", "Studies awaiting classification",
                        "Reports whose eligibility could not be decided from what is available, for example because the "
                        "authors were asked for information and have not yet replied. They are neither included nor "
                        "excluded. PRISMA 2020 asks for them to be reported (item 16a); Cochrane reviews list them as "
                        "studies awaiting classification.")


def ongoing_docx(p: Project, me: str) -> bytes:
    return _simple_list(p, me, "ongoing", "Ongoing studies",
                        "Registered or ongoing studies with no results yet, such as trial register entries. They are listed "
                        "here, not included (PRISMA 2020 item 16a). Check each again when the review is updated.")


def not_retrieved_docx(p: Project, me: str) -> bytes:
    return _simple_list(p, me, "not_retrieved", "Reports not retrieved",
                        "Reports sent to full-text review whose full text could not be found, through the library, the "
                        "authors or inter-library loan. They are the 'Reports not retrieved' box of the PRISMA flow diagram "
                        "(item 16a).")


def included_docx(p: Project, me: str) -> bytes:
    sts = review.studies(p)
    by = p.by_id()
    n_rep = sum(len(s["reports"]) for s in sts)
    d = _doc(p, "Included studies and their reports")
    d.para(f"The review includes {_pl(len(sts), 'study', 'studies')}, in {_pl(n_rep, 'report')}. "
           f"Reports given the same study name at full text are one study; the study is named as {exports.the_reviewers(p)} named it, or "
           "by its first report's first author and year (PRISMA 2020 items 16a and 17).")
    if _ft_open(p):
        _not_final(d, f"Still being assessed: {_pl(_ft_open(p), 'report')}, so this list may change.")
    d.table(["Study", "Reports", "ReviewDesk record IDs"], [[s["label"], f"{len(s['reports']):,}", ", ".join(s["reports"])] for s in sts],
            widths=[2.5, 1, 3], landscape=False, caption="Included studies")
    src = accession_sources(p)
    for s in sts:
        d.heading(s["label"], 2)
        d.bullets([cite(by.get(r) or {"id": r}, source=src.get(r, "")) + f" [{r}]" for r in s["reports"]], numbered=True)
    d.note("The references are as the databases exported them: check each one against the report itself (for example, "
           "accents and macrons in names, which some databases drop), and use your reference manager for the final list. "
           "The included reports are also in a RIS file for your reference manager (included reports.ris).")
    return d.to_bytes()


# ================================================================================================ characteristics
def _value_text(v, fl: dict) -> str:
    if isinstance(v, list):
        return review._show(v, fl)
    return "" if v is None else str(v)


def characteristics_docx(p: Project, me: str) -> bytes:
    f = review.form(p)
    sts = review.studies(p)
    lead = p.lead_id()
    d = _doc(p, "Characteristics of included studies", landscape=True)
    states = {s["key"]: review.study_state(p, s["key"], lead) for s in sts}
    done = [s for s in sts if states[s["key"]]["both_done"]]
    two = bool(review.people(p, f)["second"])
    if two:
        d.para("The values agreed for each included study: what both extractors gave, what the second extractor confirmed, "
               "or, where they differed, the value the project lead recorded (PRISMA 2020 item 17). "
               + _finished(True, len(done), len(sts)))
    else:
        d.para("The values recorded for each included study by the one extractor (no second extractor was named; PRISMA "
               "2020 item 17). " + _finished(False, len(done), len(sts)))
    unfinished = [s["label"] for s in sts if not states[s["key"]]["both_done"]]
    if unfinished:
        _not_final(d, f"Not finished by {'both extractors' if two else 'the extractor'} yet: {_list(unfinished)}.")
    k = 0

    def value(s, fl):
        st = states[s["key"]]
        if not st["both_done"]:
            return Cell("(not finished)", fill="F3F4F6")
        c = next((c for c in st["comparison"] if c["f"] == fl["id"]), None)
        if not c or c["status"] == "empty":
            return ""
        if c["final"] is None:
            return Cell("[to agree]", fill="FBEFC7")
        return _value_text(c["final"], fl)

    for sec in f.get("sections", []):
        plain = [x for x in sec.get("fields", []) if x["type"] != "table"]
        for start in range(0, len(plain), 7):
            chunk = plain[start:start + 7]
            k += 1
            d.table(["Study"] + [x["label"] for x in chunk], [[Cell(s["label"], bold=True)] + [value(s, x) for x in chunk] for s in sts],
                    compact=True, caption=f"Table {k}. {sec.get('title', '')}" + (" (continued)" if start else ""))
        for t in [x for x in sec.get("fields", []) if x["type"] == "table"]:
            cols = t.get("columns", [])
            rows = []
            for s in sts:
                st = states[s["key"]]
                c = next((c for c in st["comparison"] if c["f"] == t["id"]), None) if st["both_done"] else None
                v = c["final"] if c else None
                if not st["both_done"]:
                    rows.append([Cell(s["label"], bold=True), Cell("(not finished)", fill="F3F4F6")] + [""] * (len(cols) - 1))
                elif c and v is None:
                    rows.append([Cell(s["label"], bold=True), Cell("[to agree]", fill="FBEFC7")] + [""] * (len(cols) - 1))
                else:
                    for r in (v if isinstance(v, list) else []):
                        rows.append([Cell(s["label"], bold=True)] + [str(r.get(col["id"], "")) for col in cols])
            k += 1
            d.table(["Study"] + [col["label"] for col in cols], rows, compact=True, caption=f"Table {k}. {t['label']}")
    d.note(("[to agree]: the extractors gave different answers and the project lead has not yet recorded the agreed value. "
            if two else "") + "Every answer, and where in each report it was found, is in the data extraction workbook (Excel).")
    return d.to_bytes()


# ================================================================================================ risk of bias
ROB_FILL = {0: "D8EFE2", 0.5: "E6F2EA", -0.5: "E9EDF1", 1: "FBEFC7", 2: "F6D5DB", 3: "E7B9C1", -1: "E9EDF1"}


def rob_docx(p: Project, me: str) -> bytes:
    rows = review.rob_table(p)
    d = _doc(p, "Risk of bias in included studies", landscape=True)
    two = len([x for x in review.rob_people(p) if x]) > 1
    d.para(("The final judgement for each domain of each result assessed: the judgement both reviewers gave, or, where they "
            "differed, the one the project lead recorded (PRISMA 2020 item 18), on each tool's own scale. " if two else
            "The judgement for each domain of each result assessed, by the one reviewer who assessed risk of bias (PRISMA "
            "2020 item 18), on each tool's own scale. ") + "The colours repeat the words; they are never the only signal.")
    open_ = sum(1 for r in rows if not r["both_done"])
    if open_:
        _not_final(d, f"{'Both reviewers have' if two else 'The reviewer has'} yet to finish {_pl(open_, 'assessment')}.")
    wrong = [r for r in rows if r["problems"]]
    if wrong:
        _not_final(d, f"For {_pl(len(wrong), 'assessment')} the agreed overall judgement does not follow the tool's rule; "
                      "each is named under its table. The project lead agrees a new overall judgement (Extract data, Risk of "
                      "bias).")
    odd = [r for r in rows if r["warning"]]
    if odd:
        _not_final(d, f"In {_pl(len(odd), 'assessment')}, the study design (and so the tool) does not fit the design "
                      "recorded in data extraction; each is named under its table. Check the design.")
    old_tool = [r for r in rows if r.get("superseded")]
    if old_tool:
        _not_final(d, f"In {_pl(len(old_tool), 'assessment')}, the tool is one the risk-of-bias plan no longer gives for the "
                      "study's design: the plan was amended after the assessment was added. Each is named under its table.")
    k = 0
    pl = robplan.plan(p)
    if pl.get("recorded"):
        k += 1
        plan_rows = []
        ams = robplan.amendments(p)
        changed, gone = robplan.changes_by_design(p)
        tname = lambda key: _tool_words(p, key)[0] if key else "no risk-of-bias assessment"     # noqa: E731
        for x in pl.get("designs") or []:
            if x.get("none"):
                row = [Cell(x["name"], bold=True), "No risk-of-bias assessment", x.get("reason", "")]
            else:
                name, _, ref = _tool_words(p, x["tool"])
                row = [Cell(x["name"], bold=True), name, ref]
            plan_rows.append(row + ([changed.get(x["id"], "")] if ams else []))
        for x in gone:                               # designs the amendments removed: their tool may still appear below
            plan_rows.append([Cell(x["name"]), tname(x.get("tool")) if not x.get("none") else "No risk-of-bias assessment",
                              _tool_words(p, x["tool"])[2] if x.get("tool") else "", changed.get(x["id"]) or "Removed"])
        d.heading("The risk-of-bias plan", 1)
        d.table(["Study design", "Tool", "Citation, or why there is no assessment"] + (["Changed by amendment"] if ams else []),
                plan_rows, compact=True, caption=f"Table {k}. The tool for each study design",
                notes=[f"Recorded on {nice_date(str(pl['recorded'])[:10])}, "
                       + ("before screening started." if pl.get("before_screening") else "after screening had started.")
                       + (f" Changed since by {_pl(len(ams), 'protocol amendment')}, {'each ' if len(ams) > 1 else ''}dated in the last column and "
                          "explained in the methods text." if ams else "")])
    for tool, t in review._tool_order(p, rows):
        mine = [r for r in rows if r["tool"] == tool]
        k += 1
        name, design, _ = _tool_words(p, tool)
        d.heading(f"{name} ({design})" if design and not name.startswith("RoB 2 for") else name, 1)
        cols = review._columns(t, mine)
        head = ["Study", "Study design", "Result assessed"] + [("Overall" if c == "O" else c) for c, _ in cols]
        body, marks, notes_ = [], [], []
        for r in mine:
            cells = [Cell(r["study"], bold=True), r["design_name"] or "(not recorded)", r["label"]]
            js = {c: j for c, _, j in r["domains"]}
            over = {c: (was, why) for c, _, was, _, why in r["overrides"]}
            for code, cname in cols:
                j = js.get(code)
                if code not in js:
                    cells.append(Cell("—", fill="F3F4F6"))
                elif not j:                              # every domain needs a judgement: a blank one is not agreed yet
                    cells.append(Cell("(not yet agreed)" if r["both_done"] else "(not finished)", fill="F3F4F6"))
                else:
                    tag = ""
                    if code in over:
                        tag = f" [{_letter(len(marks))}]"
                        was, why = over[code]
                        marks.append(f"[{_letter(len(marks))}] {r['study']} ({r['label']}), {code} {cname}: both reviewers "
                                     f"judged “{was}”; the project lead recorded “{j}”. Reason: {why}")
                    cells.append(Cell(j + tag, fill=ROB_FILL.get(_band(robtools.sev(t, j)), "")))
            body.append(cells)
        key = [f"{c}: {n}" for c, n in cols if c not in robtools.OVERALL_CODES]
        key += ["Overall: the overall judgement" if c == "O" else f"{c}: {n.lower()}" for c, n in cols if c in robtools.OVERALL_CODES]
        notes_.append(("Items: " if t.get("custom") else "Domains: ") + "; ".join(key) + ".")
        notes_.append("Judgements: " + "; ".join(robtools.every_judgement(t, [c for c, _ in cols])) + ".")
        if t.get("domains_from"):
            notes_.append(f"ReviewDesk records {t['domains_from']}.")
        if not any(c in robtools.OVERALL_CODES for c, _ in t["domains"]):
            notes_.append(f"{t.get('short', t['name'])} has no overall judgement, so none is given; report the "
                          f"{'items' if t.get('custom') else 'domains'}.")
        if t.get("custom"):
            notes_.append(f"{t['name']}, {robtools.version_words(t.get('version_given')) or 'version not given'}: "
                          f"{robtools.end_sentence(t.get('cite', ''))} A tool defined by the project lead; ReviewDesk "
                          "recorded the judgement for each item.")
        notes_ += [[b(m.split("] ", 1)[0] + "] "), m.split("] ", 1)[1]] for m in marks]
        notes_ += [[b(f"Check {r['study']} ({r['label']}): "), " ".join(r["problems"])] for r in mine if r["problems"]]
        notes_ += [[b(f"Check the design of {r['study']} ({r['label']}): "), r["warning"]] for r in mine if r["warning"]]
        notes_ += [[b(f"Check the tool of {r['study']} ({r['label']}): "), r["superseded"]] for r in mine if r.get("superseded")]
        d.table(head, body, compact=True, caption=f"Table {k}. {name}", notes=notes_, repeat=3)
    if not rows:
        d.para("No study was included, so no risk-of-bias assessment was made." if not review.studies(p)
               and prisma.counts(p)["complete"] else "No risk-of-bias assessments have been recorded yet.")
    d.note("The traffic-light figure (risk of bias summary.svg) and every judgement with its support"
           + (", from both reviewers," if two else "") + " are in the risk-of-bias files in the reporting pack.")
    return d.to_bytes()


def _band(sv):
    """The table colour for a severity: the nearest band (none for a scale with no order)."""
    if sv is None:
        return None
    return min(ROB_FILL, key=lambda k: (abs(k - sv), -k))


# ================================================================================================ GRADE
NOTHING = {"", "none", "no", "not present", "not applicable", "n/a", "not used", "no serious", "not serious"}


def _stars(c: str) -> str:
    x = (c or "").strip().lower()
    for word, s in (("very low", "⊕◯◯◯"), ("low", "⊕⊕◯◯"), ("moderate", "⊕⊕⊕◯"), ("high", "⊕⊕⊕⊕")):
        if x.startswith(word):
            return f"{s} {c}"
    return c or ""


POPULATION = re.compile(r"\b(patients?|participants?|people|persons?|adults?|child(ren)?|infants?|adolescents?|women|men|"
                        r"residents?|individuals?|population|students?|workers?|carers?|neonates?|older)\b", re.I)


def _population(p: Project) -> str:
    """The inclusion criterion that names who the review is about (the first one that names a group of people), or ""
    when none does. The summary of findings says where it came from, so the authors can check it."""
    for x in (p.settings_ro().get("criteria") or {}).get("include") or []:
        t = " ".join(str(x).split())
        if t and POPULATION.search(t):
            return t
    return ""


def _o_title(o: dict) -> str:
    """An outcome's name at the start of a cell or sentence (sentence case, as everywhere in the GRADE outputs)."""
    return str(o.get("title") or certainty.name_title(o.get("name", "")))


def _o_text(o: dict) -> str:
    """An outcome's name inside a sentence."""
    return str(o.get("name_text") or certainty.name_text(o.get("name", "")))


def _is_accuracy(o: dict) -> bool:
    return str(o.get("kind_id") or "") == "accuracy" or "accuracy" in str(o.get("kind") or "").lower()


def _sof_header(o: dict, p: Project | None = None, group: list | None = None) -> list:
    """The lines above a summary of findings table: the review question, the population (from the eligibility
    criteria), the setting, and the intervention and comparison (for test accuracy, the index test and reference
    standard) from the outcome's comparison. For test accuracy also the target condition, the threshold, the
    sensitivity and specificity, and the pre-test probability with its source (GRADE guidance 21). Anything ReviewDesk
    cannot know is a highlighted gap."""
    gap = lambda what: [fill(f"[Authors to complete: {what}]")]
    comp = " ".join(str(o.get("comparison") or "").split())
    acc = _is_accuracy(o)
    first, second = str(o.get("intervention") or "").strip(), str(o.get("control") or "").strip()
    if comp and not (first and second):
        for word in ((" against ", " versus ", " compared with ") if acc else (" compared with ", " versus ", " against ")):
            if word in comp:
                first, second = [x.strip() for x in comp.split(word, 1)]
                break
    first, second = certainty.name_title(first), certainty.name_title(second)
    question = " ".join(str((p.settings_ro() if p else {}).get("question") or "").split())
    population = str(o.get("population") or "").strip() or (_population(p) if p else "")
    source = "; from the eligibility criteria" if "(" in population else " (from the eligibility criteria)"
    rows = ([("Review question", question)] if question else []) + [
        ("Patient or population", (population.rstrip(".") + source) if population and not o.get("population")
         else population or gap("the patients or population")),
        ("Setting", str(o.get("setting") or "").strip() or gap("the setting (for example, the countries and kinds of "
                                                                 "hospital the studies come from)"))]
    if acc:
        a = o.get("accuracy") or {}
        miss = gap("enter it under Accuracy figures on the Rate certainty (GRADE) page")
        # "pooled" only when an outcome using these figures pools its results (GRADE's figures are otherwise the one
        # study's, or the figures the review team entered)
        src = certainty.figures_source(group or [o])
        pooled = ("" if a.get("interval") != "95% CI" or a.get("mode") == "direct" else
                  "Pooled " if src == "pooled" else "")
        own = ("" if pooled or a.get("interval") != "95% CI" or a.get("mode") == "direct" else
               " (from the single study)" if src == "single" else " (as entered by the review team; not pooled)")
        rows += [("Index test", first or certainty.name_title(comp) or gap("the test or tool")),
                 ("Reference standard", second or gap("the reference standard")),
                 ("Target condition", certainty.name_title(a.get("condition") or "") or miss)]
        if a.get("threshold"):
            rows.append(("Threshold", a["threshold"]))
        if a.get("sens"):
            rows.append((f"{pooled}sensitivity".capitalize() + own, a["sens"]))
        if a.get("spec"):
            rows.append((f"{pooled}specificity".capitalize() + own, a["spec"]))
        prev = a.get("prevalence") or []
        rows.append(("Pre-test probability (prevalence)" if len(prev) < 2 else "Pre-test probabilities (prevalence)",
                     "; ".join(f"{certainty._pct(x['p'])}%, from {x['source']}" for x in prev) or miss))
    else:
        rows += [("Intervention", first or certainty.name_title(comp) or gap("the intervention")),
                 ("Comparison", second or gap("the comparison"))]
    return rows


_SHORT_HYPHEN = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z0-9]{1,2}\b)")


def _whole(s: str) -> str:
    """'ROBINS-I', 'QUADAS-2': a non-breaking hyphen, so that a narrow table cell never leaves 'I' alone on a line."""
    return _SHORT_HYPHEN.sub("\u2011", str(s or ""))


def _studies_cell(o: dict, start: str = "") -> list:
    """'8 studies', the design, the starting certainty (when given) and the participants, a line each."""
    n = o.get("studies") or 0
    out = [f"{n} {'study' if n == 1 else 'studies'}" if n else "No studies chosen yet"] + ([_whole(o["design"])] if o.get("design") else [])
    if start:
        out.append(start)
    if o.get("participants"):
        out.append(f"Participants: {o['participants']}")
    return out


def _notes_of(o: dict) -> list:
    """(reason, rating, explanation) for every footnote of an outcome: the profile's own footnotes when it gives them,
    otherwise every GRADE domain and upgrade whose rating is explained."""
    if isinstance(o.get("footnotes"), list):
        return [(str(f.get("item") or ""), str(f.get("rating") or ""), str(f.get("text") or "").strip())
                for f in o["footnotes"] if isinstance(f, dict) and str(f.get("text") or "").strip()]
    out = []
    for dm in list(o.get("domains") or []) + list(o.get("upgrades") or []):
        x = str(dm.get("explanation") or dm.get("footnote") or "").strip()
        if x:
            out.append((str(dm.get("name") or ""), str(dm.get("rating") or ""), x))
    return out


def _letter(k: int) -> str:
    return "abcdefghijklmnopqrstuvwxyz"[k % 26] + ("" if k < 26 else str(k // 26))


def _said_cell(o: dict) -> list:
    """The plain-language statement, flagged (highlighted) while its wording does not match the certainty."""
    said = [o.get("statement", "")]
    if o.get("statement_mismatch"):              # never let a wording that does not match the certainty pass
        chk = o.get("statement_check") or {}
        why = str(chk.get("why") or "").strip()
        if "does not match" not in why:
            why = ("This wording does not match the certainty of the evidence. " + why).strip()
        said += ["\n", fill(f"[Check: {why}" + (f" Suggested: “{chk['suggestion']}”" if chk.get("suggestion") else "")
                             + "]")]
    return said


def _certainty_cell(o: dict, marks: list, lines: bool = False) -> str:
    """'⊕⊕◯◯ Low (a, b)'; with lines, the symbols on a line of their own so that a narrow column never splits them."""
    c = _stars(o.get("certainty", ""))
    if lines and " " in c:
        c = c.replace(" ", "\n", 1)
    return c + (f" ({', '.join(marks)})" if marks else "") + ("" if o.get("complete") else "\nNot finished")


def _sof_notes(o: dict, notes: list) -> list:
    """The outcome's footnotes added to notes ('a. Time to triage, risk of bias (serious): …'); returns their marks."""
    marks = []
    for name, rating, x in _notes_of(o):
        marks.append(_letter(len(notes)))
        notes.append(f"{_letter(len(notes))}. {_o_title(o)}, {name[:1].lower() + name[1:]}"
                     + (f" ({rating[:1].lower() + rating[1:]})" if rating else "") + f": {x}")
    return marks


def _sof_effect_table(d: Document, t: int, mine: list) -> None:
    notes, rows = [], []
    for o in mine:
        marks = _sof_notes(o, notes)
        outcome = [b(_o_title(o))]
        if o.get("measure"):
            outcome.append("\n" + str(o["measure"]))
        if o.get("importance"):
            outcome.append("\n" + f"Importance: {str(o['importance']).lower()}")
        rows.append([Cell(outcome), "\n".join(_studies_cell(o)), o.get("effect", ""), _certainty_cell(o, marks),
                     Cell(_said_cell(o))])
    head = ["Outcome", "Studies and participants", "Effect", "Certainty of the evidence (GRADE)", "What this means"]
    d.table(head, rows, widths=_fit_widths(head, rows, [2.2, 2.2, 2.6, 1.9, 3.2]), compact=True, keep_rows=True,
            caption=f"Summary of findings {t} (PRISMA 2020 item 22)", notes=notes or None)


def _sof_accuracy_table(d: Document, t: int, mine: list) -> None:
    """A summary of findings for test accuracy as GRADE guidance 21 (part 2) lays it out: for each kind of result
    (true positives, false negatives, true negatives, false positives), the number per 1,000 people tested at each
    pre-test probability, the participants (studies) and the certainty; then what it means."""
    order = {r: k for k, r in enumerate(certainty.ROW_IDS)}
    mine = sorted(mine, key=lambda o: min([order.get(r["id"], 9) for r in (o.get("accuracy") or {}).get("rows") or []] or [9]))
    first = next((o.get("accuracy") for o in mine if (o.get("accuracy") or {}).get("prevalence")), None) or {}
    prev = first.get("prevalence") or []
    ivl = first.get("interval") or "95% CI"
    k = max(1, len(prev))
    head = (["Test result"] + ([f"Per 1,000 people tested ({ivl}), pre-test probability {certainty._pct(x['p'])}%"
                                for x in prev] or ["Per 1,000 people tested"])
            + ["Number of participants (studies)", "Certainty of the evidence (GRADE)"])
    ncol = len(head)
    notes, rows = [], []
    for o in mine:
        marks = _sof_notes(o, notes)
        a = o.get("accuracy") or {}
        n = o.get("studies") or 0
        who = (f"{o['participants']} " if o.get("participants") else "") + f"({n} {'study' if n == 1 else 'studies'})"
        cert = _certainty_cell(o, marks)
        rows.append([Cell([b(_o_title(o))] + ([f" · importance: {str(o['importance']).lower()}"] if o.get("importance") else []),
                          span=ncol, fill="F3F4F6")])
        if not a.get("rows"):
            rows.append([Cell(b(a.get("result_name") or "Accuracy")),
                         Cell([o.get("effect", "")] + (["\n", fill(f"[{a['missing']}]")] if a.get("missing") else []), span=k),
                         who, cert])
        for rw in a.get("rows") or []:
            vals = [v for v in rw.get("values") or []][:len(prev)]
            label = Cell([b(rw["name"]), "\n" + rw["who"][:1].upper() + rw["who"][1:]])
            if prev and all(vals):
                rows.append([label] + vals + [who, cert])
            else:
                rows.append([label, Cell([fill("[Not yet given: " + (a.get("missing") or certainty.ACC_MISSING) + "]")],
                                         span=k), who, cert])
        rows.append([Cell([b("What this means: ")] + _said_cell(o), span=ncol)])
    d.table(head, rows, widths=_fit_widths(head, rows, [2.6] + [1.6] * k + [1.5, 1.7]), compact=True, keep_rows=True,
            caption=f"Summary of findings {t} (PRISMA 2020 item 22): test accuracy (GRADE guidance 21)",
            notes=(notes or []) + ["Numbers per 1,000 people tested: of every 1,000 people tested, the number with the "
                                   "condition is the pre-test probability times 1,000; true positives are those of them "
                                   "the test finds and false negatives those it misses; true negatives and false "
                                   "positives are the same for the people without the condition."
                                   + (" " + certainty.COMPLEMENT_NOTE if first and first.get("mode") != "direct" else "")])


def _fit_widths(head: list, rows: list, want: list) -> list:
    """Column widths (in twips, for a landscape page) that give every column at least the room its longest word needs
    (docx.min_widths) and share what is left by the wanted proportions, so no word is ever broken across lines."""
    from . import docx as _docx
    need = _docx.min_widths(head, [r for r in rows if len(r) == len(head)], compact=True)
    spare = _docx.LANDSCAPE_WIDTH - sum(need)
    if spare <= 0 or len(want) != len(need):
        return want
    return [n + spare * w / sum(want) for n, w in zip(need, want)]


def _evidence_profile_table(d: Document, outs: list) -> None:
    """The GRADE evidence profile: one row of ratings per outcome, with the effect on a row of its own beneath, so
    that every column is wide enough for its longest word (no word, and no GRADE symbol, is split across lines)."""
    names, notes = [], []
    for o in outs:
        for dm in o.get("domains") or []:
            if dm.get("name") and dm["name"] not in names:
                names.append(dm["name"])

    def mark(o: dict, *pairs) -> str:
        """' (b, c)': a footnote for each (text, what) that has a text."""
        got = []
        for text, what in pairs:
            if text:
                notes.append(f"{_o_title(o)}, {what[:1].lower() + what[1:]}: {text}")
                got.append(_letter(len(notes) - 1))
        return f" ({', '.join(got)})" if got else ""

    head = ["Outcome", "Studies, design and participants"] + names + ["Other considerations", "Certainty"]
    rows = []
    for o in outs:
        st = o.get("start_item") or {}
        start = (f"Starting certainty: {str(o.get('start') or '').lower()}" if o.get("start") else "")
        start += mark(o, (str(st.get("override") or ""), "starting certainty")) if start else ""
        cells = [Cell([b(_o_title(o))] + ([f"\n{o['importance']}"] if o.get("importance") else [])),
                 "\n".join(_studies_cell(o, start))]
        by = {dm.get("name"): dm for dm in o.get("domains") or []}
        for n in names:
            dm = by.get(n) or {}
            cells.append((dm.get("rating") or "") + mark(o, (str(dm.get("explanation") or dm.get("footnote") or ""), n),
                                                          (str(dm.get("override") or ""), n)))
        ups = [u for u in o.get("upgrades") or [] if str(u.get("rating") or "").strip().lower() not in NOTHING
               or u.get("override")]
        why = ""
        if o.get("upgrade_justification"):
            why = next((f["text"] for f in o.get("footnotes") or [] if isinstance(f, dict) and f.get("text")
                        and str(f.get("item", "")).startswith("Why rating up")), "") or str(o["upgrade_justification"])
        parts = []
        for k, u in enumerate(ups):                  # the reason why rating up is justified goes with the first upgrade
            parts.append(f"{u.get('name', '')}: {u.get('rating', '')}"
                         + mark(o, (str(u.get("explanation") or u.get("footnote") or ""), u.get("name", "")),
                                (str(u.get("override") or ""), u.get("name", "")),
                                (why if k == 0 else "", "why rating up is justified")))
        other = "; ".join(parts) or ("None" if o.get("complete") else "")      # blank, as the other cells, until rated
        if why and not ups:
            other += mark(o, (why, "why rating up is justified"))
        cells.append(other)
        cells.append(_certainty_cell(o, [], lines=True))
        rows.append(cells)
        effect = [str(o.get("effect") or "")] + certainty.accuracy_lines(o)
        # the outcome is named, so the row still reads on its own when a page break parts it from the ratings above
        rows.append([Cell([b(f"Effect for {_o_text(o)}: "), "\n".join(x for x in effect if x) or "not yet agreed"],
                          span=len(head))])
    d.heading("Evidence profile", 1)
    d.table(head, rows, widths=_fit_widths(head, rows, [2.3, 2.7] + [1.25] * len(names) + [1.8, 1.1]),
            compact=True, keep_rows=True, caption="GRADE evidence profile (PRISMA 2020 items 15 and 22)",
            notes=[f"{_letter(k)}. {n}" for k, n in enumerate(notes)] + [GRADE_KEY])


GRADE_KEY = ("GRADE: Grading of Recommendations Assessment, Development and Evaluation. ⊕⊕⊕⊕ high, ⊕⊕⊕◯ moderate, ⊕⊕◯◯ "
             "low, ⊕◯◯◯ very low certainty.")


def sof_docx(p: Project, me: str, prof: dict | None = None) -> bytes:
    prof = prof if prof is not None else (_profile(p, me) or {"outcomes": []})
    outs = prof.get("outcomes") or []
    d = _doc(p, "Summary of findings and evidence profile", landscape=True)
    for x in str(prof.get("method_text") or "").split("\n\n"):
        if x.strip():
            d.para(x.strip())
    open_ = [_o_text(o) for o in outs if not o.get("complete")]
    if open_:
        one = len(open_) == 1
        _not_final(d, f"The outcome{'' if one else 's'} {_list(open_)} {'is' if one else 'are'} not finished on the Rate "
                      "certainty (GRADE) page.")
    flagged = [_o_text(o) for o in outs if o.get("statement_mismatch")]
    if flagged:
        one = len(flagged) == 1
        _not_final(d, f"The plain-language statement{'' if one else 's'} for {_list(flagged)} "
                      f"{'does' if one else 'do'} not match the certainty of the evidence: "
                      f"{'it is' if one else 'each is'} flagged in the summary of findings. Reword "
                      f"{'it' if one else 'each'}, or record why {'it is' if one else 'each is'} kept (Rate certainty).")
    # one summary of findings table for each comparison (or test and reference standard)
    groups: list = []
    for o in outs:
        key = (" ".join(str(o.get("comparison") or "").split()).lower(), _is_accuracy(o))
        g = next((g for g in groups if g[0] == key), None)
        if g is None:
            groups.append((key, [o]))
        else:
            g[1].append(o)
    for t, (key, mine) in enumerate(groups, 1):
        comp = " ".join(str(mine[0].get("comparison") or "").split())
        title = "Summary of findings" + (f": {certainty.name_title(comp)}" if comp else "")
        d.heading(title, 1)
        d.table(None, [[Cell(k, bold=True), v] for k, v in _sof_header(mine[0], p, mine)], widths=[1, 4],
                caption=f"Summary of findings {t}: who and what it is about")
        if key[1]:
            _sof_accuracy_table(d, t, mine)
        else:
            _sof_effect_table(d, t, mine)
    _evidence_profile_table(d, outs)
    d.note("The sources of GRADE's wording used in ReviewDesk: " + certainty.GRADE_REFS)
    return d.to_bytes()


# ================================================================================================ AI second screener
def _ai_runs(p: Project) -> list:
    """Every AI run from the audit log: start and end, what was sent, and what happened. A run with no end in the log
    was interrupted (ReviewDesk was closed, or the computer went to sleep): its calls are counted from the answers it
    saved."""
    runs, open_ = [], {}
    for e in p.audit_entries():
        act, det = e.get("action"), e.get("details") or {}
        if act == "AI run started":
            r = {"start": e.get("t", ""), "label": det.get("label", ""), "checked": det.get("records_checked", det.get("records")),
                 "sent": det.get("records_sent"), "calls": det.get("calls"), "retries": det.get("retries"),
                 "models": det.get("models") or {}, "prompts": det.get("prompts") or {}, "endpoint": det.get("endpoint", ""),
                 "end": "", "done": None, "errors": None, "stopped": None, "problem": "", "interrupted": False}
            runs.append(r)
            open_[(e.get("who"), det.get("label"))] = r
        elif act == "AI run ended":
            r = open_.pop((e.get("who"), det.get("label")), None)
            if r:
                r.update({"end": e.get("t", ""), "done": det.get("done"), "errors": det.get("errors"), "stopped": det.get("stopped"),
                          "adaptations": det.get("adaptations") or {}, "problem": det.get("problem") or ""})
    left = [r for r in runs if not r["end"]]
    if left and ai.Runner.status(p).get("running"):
        left[-1]["running"] = True                           # the run going now
        left = left[:-1]
    if left:
        rows = [d for ps in "AB" for d in p._read_all(p.path("ai", f"answers_{ps}*.jsonl"), "ai" + ps)]
        starts = sorted(util.ts(r["start"]) for r in runs)
        for r in left:
            t0 = util.ts(r["start"])
            t1 = next((t for t in starts if t > t0), float("inf"))
            mine = [d for d in rows if t0 <= util.ts(d.get("t") or d.get("when") or "") < t1]
            r.update({"interrupted": True, "done": len(mine),
                      "errors": sum(1 for d in mine if d.get("decision") in ("ERROR", "UNPARSED"))})
    return runs


def _settings_words(st: dict) -> str:
    bits = []
    if st.get("reasoning_effort"):
        bits.append(f"reasoning effort {st['reasoning_effort']}")
    for k, w in (("temperature", "temperature"), ("top_p", "top_p"), ("top_k", "top_k")):
        if st.get(k) is not None:
            bits.append(f"{w} {st[k]}")
    bits.append(f"at most {st.get('max_tokens'):,} tokens per answer" if st.get("max_tokens") else "")
    bits.append("JSON reply format asked for" if st.get("json_mode") else "plain-text reply read for its JSON")
    return "; ".join(x for x in bits if x)


def _acc_row(label: str, x: dict) -> list:
    def ci(v):
        return f"{stats.fmt_pct(v[0])} to {stats.fmt_pct(v[1])}" if v else "n/a"
    return [Cell(label, bold=True), f"{x.get('n', 0):,}", f"{stats.fmt_pct(x.get('sensitivity'))} ({ci(x.get('sens_ci'))})",
            f"{stats.fmt_pct(x.get('specificity'))} ({ci(x.get('spec_ci'))})",
            f"{x.get('tp', 0)}/{x.get('tp', 0) + x.get('fn', 0)}", f"{x.get('fp', 0)}/{x.get('fp', 0) + x.get('tn', 0)}"]


SET_WORDS = {"main": "from the database searches", "update": "from the update search", "other": "found by other methods"}


def _run_rows(runs: list) -> list:
    rows = []
    for r in runs:
        when = lambda t: (t or "")[:16].replace("T", " ")
        if r["sent"] is not None:
            sent = f"{r['sent']:,}"
        elif r["checked"] is not None:
            sent = f"{r['checked']:,} checked (how many were sent was not recorded)"
        else:
            sent = "not recorded"
        calls = f"{r['done']:,}" if r["done"] is not None else "not recorded"
        if r["done"] is not None and r.get("retries"):
            calls += f" ({r['retries']:,} {'was a retry' if r['retries'] == 1 else 'were retries'})"
        if r.get("running"):
            outcome = "still running"
        elif r["interrupted"]:
            calls += " (counted from the answers saved)" if r["done"] is not None else ""
            outcome = "interrupted (ReviewDesk was closed or the computer slept)"
        elif r.get("problem"):
            outcome = f"stopped: {r['problem']}"
        else:
            outcome = "stopped early" if r["stopped"] else "finished" if r["stopped"] is not None else "not recorded"
        rows.append([when(r["start"]), when(r["end"]) or ("still running" if r.get("running") else
                                                         "interrupted" if r["interrupted"] else "not recorded"), r["label"],
                     sent, calls, f"{r['errors']:,}" if r["errors"] is not None else "not recorded", outcome])
    return rows


def ai_docx(p: Project, me: str) -> bytes:
    c = ai.config(p)
    nm = _nm(p)
    v = ai.validate(p, "main")
    pr = ai.prompts(p)
    runs = _ai_runs(p)
    base = next((r["endpoint"] for r in reversed(runs) if r.get("endpoint")), "") or ai.endpoint()[0]
    elm = ai.is_elm(base)
    sv = p.state_ro().get("sets", {}).get("main", {})
    sr = sv.get("sample_result") or {}
    pl = screening.plan(p, "main")
    a = ai.answers(p)
    whens = sorted(x.get("when", "") for ps in "AB" for x in a[ps].values() if x.get("when"))
    models = {ps: c["passes"][ps]["model"] for ps in "AB"}
    d = _doc(p, "AI second screener report")
    d.note("For the methods section and appendix. It is structured to cover the elements of the joint position statement on "
           "AI use in evidence synthesis (" + AI_REF + "): it names the tool and the models, says what the AI was used for "
           "and how its answers were used, shows how well it did on this review's agreement sample, gives the exact prompts, "
           "and covers funding, interests, data protection, limitations, the authors' responsibility and any other use of "
           "AI, with highlighted lines for what only the authors can write. Whether the review meets the statement is for "
           "the authors to judge. For the project lead only: it shows how the AI judged the records.")
    # 1 ----------------------------------------------------------------------------------------- the tool
    d.heading("1. The tool, the models and when they were used", 1)
    # v["served"] counts records: for each record, the model its current answer from that pass says it came from
    # (readable or not; a reply that is a retry replaces the earlier one)
    served = {ps: ", ".join(f"{k} (named in its replies for {_pl(n, 'record')})" for k, n in sorted(v["served"][ps].items()) if k)
              or "not reported" for ps in "AB"}
    hosted = all(m.startswith(ai.LOCAL_PREFIXES) for m in models.values())
    where = (f"The University of Edinburgh's ELM service ({base})" + (", with models that ELM lists as University-hosted."
                                                                      if hosted else ".") if elm else
             f"An OpenAI-compatible service at {base or '(not recorded)'}.")
    d.fields([("Software", f"ReviewDesk {VERSION} (free, open-source software under the MIT licence), AI screening engine "
                           f"'{ai.ENGINE}'"),
              ("Pass A model", f"{models['A']}; as the service reported it: {served['A']}"),
              ("Pass A settings", _settings_words(v["settings"]["A"])),
              ("Pass B model", f"{models['B']}; as the service reported it: {served['B']}"),
              ("Pass B settings", _settings_words(v["settings"]["B"])),
              ("Service", where),
              ("Prompts fixed", nice_date(c.get("prompts_fixed_at")) + " (when screening started)" if c.get("prompts_fixed_at")
               else "Not fixed yet (they are fixed when screening starts)"),
              ("Set-up locked", nice_date(c.get("locked_at")) + " (when the agreement rule was applied)" if c.get("locked_at") else "Not yet"),
              ("Answers dated", _dates(whens).replace("on ", "", 1) if whens else "No answers yet")],
             caption="The tool and the models")
    d.fill_in("[Authors to complete: who runs this AI service, and where it processed the data sent to it (see section 8).]")
    if runs:
        d.table(["Started", "Finished", "What was run", "Records sent", "Calls made", "Failed calls", "Outcome"],
                _run_rows(runs), widths=[1.5, 1.5, 1.4, 1.2, 1.5, 0.9, 1.9], compact=True, landscape=False,
                caption="Every AI run (from the audit log)",
                notes=["Records sent: records with at least one call in that run (records both models had already answered "
                       "were checked but not sent again). A retry is a call for a record whose earlier answer failed or could "
                       "not be read. Failed calls: calls that failed or whose answer could not be read (they count as kept)."])
    # 2 ----------------------------------------------------------------------------------------- purpose
    d.heading("2. Purpose, and the parts of the review it affected", 1)
    cov = {s_: ai.coverage(p, s_) for s_ in SETS if any(r["set"] == s_ for r in p.records())}
    used = {s_: x for s_, x in cov.items() if x["records"] - x["missing"] > 0}
    unused = {s_: x for s_, x in cov.items() if s_ not in used}
    sets_state = p.state_ro().get("sets", {})
    net = [s_ for s_ in cov if sets_state.get(s_, {}).get("ai_net_active")]
    idle = [s_ for s_ in used if s_ not in net]
    d.para("The AI was used at one stage only: title and abstract screening, as a second screener behind one reviewer "
           "screening alone. It did not read full texts, and it was not used for data extraction, risk of bias or the "
           "certainty of the evidence.")
    failed = sum(x["failed"] for x in used.values())
    missing = sum(x["missing"] for x in used.values())
    if used:
        d.para("Both models gave a readable answer for " + _list(f"{x['done']:,} of the {_pl(x['records'], 'record')} {SET_WORDS[s_]}"
                                                               for s_, x in used.items())
               + (f" ({_pl(failed, 'record')} had a failed or unreadable answer from at least one model, which counted as kept)"
                  if failed else "")
               + (f"; {_pl(missing, 'record was', 'records were')} not sent to both models" if missing else "") + ".")
    if cov:
        d.para((("Its answers were used for the records " + _list(SET_WORDS[s_] for s_ in net) + ", to choose which of the "
                 "single reviewer's exclusions had a second look.") if net else "Its answers were not used for any record.")
               + (" Its answers for the records " + _list(SET_WORDS[s_] for s_ in idle) + " were not used." if idle and net else "")
               + (" The AI was not sent the " + _list(f"{_pl(x['records'], 'record')} {SET_WORDS[s_]}" for s_, x in unused.items())
                  + "." if unused else ""))
    # 3 ----------------------------------------------------------------------------------------- how answers were used
    d.heading("3. How the AI's answers were used", 1)
    one, two = nm(pl.get("single_reviewer")), nm(pl.get("second_reviewer"))
    d.bullets([f"Two large language models, {models['A']} and {models['B']}, each screened the records described in section 2, "
               "each with its own prompt built from the review question and eligibility criteria.",
               "A record counted as kept by the AI unless both models excluded it. A call that failed, or an answer that "
               "could not be read, counted as kept.",
               f"The AI never excluded a record: every exclusion was made by a person. Every record {one or 'the single reviewer'} "
               f"excluded that the AI would keep was screened again by {two or 'the second reviewer'} (a second look).",
               "The AI's answers were hidden from both reviewers until the agreement sample was finished, and were then shown "
               "only to the project lead.",
               f"Stopping rule, set in advance: if the AI kept more than {stats.fmt_pct(pl['ai_keep_rate_stop'], 0)} of the "
               "records, the AI step would be dropped and both reviewers would screen every record."])
    if sr.get("ai_rule"):
        d.para([b("What happened: "), sr["ai_rule"][:1].upper() + sr["ai_rule"][1:] + "."])
    # 4 ----------------------------------------------------------------------------------------- evaluation
    d.heading("4. Justification, and how well it did in this review", 1)
    d.fill_in("[Authors to complete: why the AI second screener was used (for example, to make single screening safer), and "
              "any published evaluation of this approach or of these models for screening.]")
    answered = v.get("sample_answered", v["combined"].get("n", 0))      # 1.0.0 recorded neither sample count
    of_n = f" of the {v['sample_records']:,}" if v.get("sample_records") is not None else ""
    d.para(f"The AI was checked against {v['reference']}. The sensitivity and specificity describe the AI; they decided "
           "nothing, because the AI never excludes. Sensitivity is the share of records the reviewers sent on that the AI "
           "kept; specificity the share of records the reviewers excluded that the AI would exclude. The 95% confidence "
           "intervals are by the Wilson score method. "
           f"Each row uses the sample records that screener answered: both models together, the {answered:,}{of_n} "
           f"sample {'records' if of_n or answered != 1 else 'record'} both answered; each model alone, the sample records it answered "
           f"({v['A'].get('n', 0):,} for pass A and {v['B'].get('n', 0):,} for pass B).")
    gone = (v["sample_records"] - answered) if v.get("sample_records") is not None else 0
    if gone:
        d.para(f"Of the sample records, {gone:,} {'has' if gone == 1 else 'have'} no readable answer from both models and "
               f"{'is' if gone == 1 else 'are'} left out of the figures for both models together; in screening, "
               f"{'it counts' if gone == 1 else 'they count'} as kept.")
    d.table(["Screener", "Sample records", "Sensitivity (95% CI)", "Specificity (95% CI)", "Kept of those sent on",
             "Kept of those excluded"],
            [_acc_row("Both models (kept unless both exclude)", v["combined"]), _acc_row(f"Pass A alone ({models['A']})", v["A"]),
             _acc_row(f"Pass B alone ({models['B']})", v["B"])], widths=[3, 1.1, 2.1, 2.1, 1.3, 1.3], compact=True,
            landscape=False, caption="Accuracy on the agreement sample")
    main_cov = cov.get("main") or {"records": 0, "done": 0, "failed": 0, "missing": 0}
    by_id = p.by_id()
    readable = [r for r in (set(a["A"]) & set(a["B"])) if a["A"][r]["decision"] in ai.LABELS and a["B"][r]["decision"] in ai.LABELS
                and (by_id.get(r) or {}).get("set") == "main"]
    differ = sum(1 for r in readable if (a["A"][r]["decision"] == "EXCLUDE") != (a["B"][r]["decision"] == "EXCLUDE"))
    if v["records"]:
        kind = exports._rule_kind(sr) if sr.get("ai_rule") else ""
        d.para(f"Of the {_pl(main_cov['records'], 'record')} from the database searches, the AI kept {v['kept']:,}"
               + (f" ({stats.fmt_pct(v['keep_rate'])})" if v["records"] == main_cov["records"] else
                  f" of the {v['records']:,} that both models were sent ({stats.fmt_pct(v['keep_rate'])})")
               + (f", counting the {_pl(main_cov['failed'], 'record')} with a failed or unreadable answer as kept" if main_cov["failed"] else "")
               + "." + (" Unlike the accuracy figures, this keep rate decided something: it fed the pre-specified stopping "
                        "rule (section 3)." if kind in ("net", "stopping_rule") else "")
               + f" Of the {_pl(len(readable), 'record')} both models answered, one model would exclude and the other keep "
               f"{_pl(differ, 'record')}.")
    if v.get("missed"):
        by = p.by_id()
        d.para("Records the reviewers sent on that the AI would not have kept:")
        d.bullets([f"{r}: {(by.get(r) or {}).get('title', '')}" for r in v["missed"]])
    known = ai.known_check(p)
    if known:
        d.table(["Known eligible study", "In the records", "Pass A", "Pass B"],
                [[k["known"].get("title") or (f"doi:{k['known']['doi']}" if k["known"].get("doi") else f"PMID {k['known'].get('pmid', '')}"),
                  k.get("record") or "not found", k.get("A") or "not run", k.get("B") or "not run"] for k in known],
                widths=[4, 1.3, 1.2, 1.2], compact=True, landscape=False,
                caption="Known studies check: eligible studies known before screening, which the AI should keep")
    # 5 ----------------------------------------------------------------------------------------- prompts and data
    d.heading("5. Prompts, inputs and outputs", 1)
    d.para("What was sent, for every record: the system prompt below (the review question and eligibility criteria, with how "
           "to decide) and one message with the record's title, year, journal, publication type and abstract (an abstract "
           "over 6,000 characters was cut short). Nothing else was sent: no author names, no full texts and no reviewer's "
           "vote.")
    d.para(f"Each prompt's fingerprint is the first 16 characters of the SHA-256 hash of the prompt, a line '----' and the "
           f"message template (UTF-8). Pass A: {ai.fingerprint(pr['A'])}; pass B: {ai.fingerprint(pr['B'])}. An answer "
           "counts only if it was made with these prompts, the models and settings above, and the record's text as it is now.")
    d.heading(f"Pass A prompt ({models['A']})", 2)
    d.pre(pr["A"])
    d.heading(f"Pass B prompt ({models['B']})", 2)
    d.pre(pr["B"])
    d.heading("The message sent with each record", 2)
    d.pre(ai.USER)
    d.para("Outputs: every answer (each model's decision, the criterion it named and its reason, for each record) is in "
           "'AI decisions.csv' in the project lead's reporting pack: deposit it with the review. The answers are also kept in "
           "the project folder (ai/answers_*.jsonl), each with the model as served, the prompt fingerprint and the time.")
    # 6 ----------------------------------------------------------------------------------------- human checks
    d.heading("6. How people checked the AI's answers", 1)
    sl = screening.second_look_stats(p, "main")
    ft_people = screening.ft_required(p, "main")
    d.para("No record was excluded by the AI. Every exclusion at title and abstract was a reviewer's decision"
           + (", and at full text both reviewers assessed every report." if len(ft_people) > 1 else
              f", and at full text {nm(ft_people[0]) if ft_people else 'a reviewer'} assessed every report."))
    if sl["total"]:
        d.para(exports.second_look_text(sl, one, two))
    elif sv.get("ai_net_active"):
        d.para("No record has gone to a second look yet.")
    checked = evaluation.methods_text(p, "main", True)        # the records neither the reviewer nor the AI kept
    if checked:
        d.para(checked)
    elif sv.get("ai_net_active"):
        d.para("No verification sample has been drawn, so there is so far no measure of how many of the records the single "
               "reviewer excluded, and the AI would not keep either, a second reviewer would have sent on. (The project lead "
               "can draw one on the screening page once the single reviewer has screened every record.)")
    # 7, 8, 9 ------------------------------------------------------------------------------------ funding, data, limitations
    d.heading("7. Funding and interests", 1)
    d.para("ReviewDesk received no funding: Chris Humphries wrote it in his own time, and it is free, open-source software "
           "(MIT licence). Its prompts are ReviewDesk's own, filled in with this review's question and eligibility criteria. "
           "The AI models belong to their developers; ReviewDesk has no link with them.")
    d.fill_in("[Authors to complete: who paid for the AI service used in this review (for example, the University's "
              "licence, a grant or the authors), and whether the funder had any role in the review.]")
    d.fill_in("[Authors to complete: any interest an author has in ReviewDesk, its prompts or the AI models or service (for "
              "example, having written them), or state that there is none.]")
    d.heading("8. Data protection, confidentiality, licensing and ethics", 1)
    d.para(f"For each record, the AI service ({base or 'not recorded'}) was sent the record's title, year, journal, "
           "publication type and abstract, with the prompts in section 5. ReviewDesk sent no author names, no full texts and "
           "no reviewer's votes.")
    d.fill_in("[Authors to complete: where the service processed the data, whether it kept the data or used them to train "
              "models, and the data protection and confidentiality terms that applied (for example, the University's "
              "agreement for the service).]")
    d.fill_in("[Authors to complete: whether the database licences and publishers' terms allowed titles and abstracts to be "
              "sent to an AI service, and how copyright was respected.]")
    d.fill_in("[Authors to complete: whether ethics approval or other permission was needed for this use of AI, and how any "
              "records that are not public (for example, unpublished reports from authors) were handled.]")
    d.heading("9. Limitations and possible biases", 1)
    d.para("Points to consider (keep those that apply):")
    d.bullets([f"The accuracy figures come from {_pl(answered, 'record')}; the confidence intervals show how "
               "uncertain they are, and the sample is too small to show whether the AI did worse for any one kind of record.",
               "Large language models can carry biases from the text they learned from: for example, they may judge records "
               "less favourably when they come from some countries or settings, are about less-studied groups, are written "
               "in less fluent English, or have short or missing abstracts. If the AI wrongly excluded records of one kind "
               "more often, those records would less often get a second look when the single reviewer excluded them, so the "
               "single reviewer's mistakes on them would less often be caught. The AI could not remove a record from the "
               "review by itself.",
               "The AI read titles and abstracts only, as the reviewers did at this stage.",
               "A service can update a model without changing its name; the names above are what the service reported with "
               "each answer.",
               "The reference standard is the two reviewers' screening, which can itself be wrong."])
    d.fill_in("[Authors to complete: the limitations of using the AI in this review, including any sign of bias in its "
              "answers and its likely effect on the results.]")
    # 10 ---------------------------------------------------------------------------------------- responsibility, other AI
    # The position statement's first principle: the authors are responsible for their evidence synthesis, including the
    # decision to use AI and its outputs. ReviewDesk knows only its own AI step, so both are lines for the authors.
    d.heading("10. Responsibility, and any other use of AI", 1)
    d.fill_in("[Authors to complete: a statement that the authors are ultimately responsible for this evidence synthesis, "
              "including the decision to use AI, the human oversight of its outputs and their effect on the review, and "
              "that its use met legal and ethical standards.]")
    d.fill_in("[Authors to complete: any other use of AI or automation in this review that made or suggested judgements "
              "(for example, in searching, data extraction or risk of bias), with the name, version and dates, the purpose "
              "and the parts of the review it affected, the justification, any interests and the limitations, as this report "
              "gives them for the AI second screener; any other use of AI your journal asks you to declare (for example, "
              "for writing or translation); or state that no other AI was used.]")
    d.heading("Reference", 1)
    d.para(AI_REF)
    return d.to_bytes()


def ai_prompts_txt(p: Project, me: str) -> bytes:
    c = ai.config(p)
    pr = ai.prompts(p)
    s = p.settings_ro()
    lines = ["AI second screener: the exact prompts and settings", f"Review: {s.get('title', '')}",
             f"Made by ReviewDesk {VERSION} on {util.today()}", "",
             "These are the prompts the two AI models were given, word for word, and the message sent with each record. "
             "Report them with the review (2025 position statement on AI in evidence synthesis: share prompts, inputs and outputs).",
             (f"The prompts were fixed when screening started ({nice_date(c['prompts_fixed_at'])}): later changes to the "
              "question or criteria do not change them." if c.get("prompts_fixed_at") else
              "The prompts are not fixed yet: they are fixed when screening starts, and until then they change if the "
              "review question or eligibility criteria change."), "",
             "Fingerprint: the first 16 characters of the SHA-256 hash (in hexadecimal) of the prompt, a new line, '----', "
             "a new line and the message template, in UTF-8.", ""]
    for ps in "AB":
        st = ai.settings_for(c["passes"][ps])
        lines += [f"Pass {ps}", f"  Model: {c['passes'][ps]['model']}", f"  Settings: {_settings_words(st)}",
                  f"  Fingerprint: {ai.fingerprint(pr[ps])}", ""]
    for ps in "AB":
        lines += [f"----- Pass {ps} prompt (system message), exactly as sent -----", pr[ps], "----- end -----", ""]
    lines += ["----- Message sent with each record (user message template; {fields} are filled in) -----", ai.USER,
              "----- end -----", "", "An abstract over 6,000 characters is cut short and ends ' [abstract truncated]'. A missing "
              "value is sent as 'not given'; a missing abstract as 'No abstract available.'."]
    return ("\n".join(lines) + "\n").encode("utf-8")


# ================================================================================================ PRISMA checklist
CHECKLIST = [
    ("TITLE", [("1", "Title", "Identify the report as a systematic review.")]),
    ("ABSTRACT", [("2", "Abstract", "See the PRISMA 2020 for Abstracts checklist.")]),
    ("INTRODUCTION", [("3", "Rationale", "Describe the rationale for the review in the context of existing knowledge."),
                      ("4", "Objectives", "Provide an explicit statement of the objective(s) or question(s) the review addresses.")]),
    ("METHODS", [
        ("5", "Eligibility criteria", "Specify the inclusion and exclusion criteria for the review and how studies were grouped for the syntheses."),
        ("6", "Information sources", "Specify all databases, registers, websites, organisations, reference lists and other sources searched or consulted to identify studies. Specify the date when each source was last searched or consulted."),
        ("7", "Search strategy", "Present the full search strategies for all databases, registers and websites, including any filters and limits used."),
        ("8", "Selection process", "Specify the methods used to decide whether a study met the inclusion criteria of the review, including how many reviewers screened each record and each report retrieved, whether they worked independently, and if applicable, details of automation tools used in the process."),
        ("9", "Data collection process", "Specify the methods used to collect data from reports, including how many reviewers collected data from each report, whether they worked independently, any processes for obtaining or confirming data from study investigators, and if applicable, details of automation tools used in the process."),
        ("10a", "Data items", "List and define all outcomes for which data were sought. Specify whether all results that were compatible with each outcome domain in each study were sought (e.g. for all measures, time points, analyses), and if not, the methods used to decide which results to collect."),
        ("10b", "Data items", "List and define all other variables for which data were sought (e.g. participant and intervention characteristics, funding sources). Describe any assumptions made about any missing or unclear information."),
        ("11", "Study risk of bias assessment", "Specify the methods used to assess risk of bias in the included studies, including details of the tool(s) used, how many reviewers assessed each study and whether they worked independently, and if applicable, details of automation tools used in the process."),
        ("12", "Effect measures", "Specify for each outcome the effect measure(s) (e.g. risk ratio, mean difference) used in the synthesis or presentation of results."),
        ("13a", "Synthesis methods", "Describe the processes used to decide which studies were eligible for each synthesis (e.g. tabulating the study intervention characteristics and comparing against the planned groups for each synthesis (item #5))."),
        ("13b", "Synthesis methods", "Describe any methods required to prepare the data for presentation or synthesis, such as handling of missing summary statistics, or data conversions."),
        ("13c", "Synthesis methods", "Describe any methods used to tabulate or visually display results of individual studies and syntheses."),
        ("13d", "Synthesis methods", "Describe any methods used to synthesize results and provide a rationale for the choice(s). If meta-analysis was performed, describe the model(s), method(s) to identify the presence and extent of statistical heterogeneity, and software package(s) used."),
        ("13e", "Synthesis methods", "Describe any methods used to explore possible causes of heterogeneity among study results (e.g. subgroup analysis, meta-regression)."),
        ("13f", "Synthesis methods", "Describe any sensitivity analyses conducted to assess robustness of the synthesized results."),
        ("14", "Reporting bias assessment", "Describe any methods used to assess risk of bias due to missing results in a synthesis (arising from reporting biases)."),
        ("15", "Certainty assessment", "Describe any methods used to assess certainty (or confidence) in the body of evidence for an outcome.")]),
    ("RESULTS", [
        ("16a", "Study selection", "Describe the results of the search and selection process, from the number of records identified in the search to the number of studies included in the review, ideally using a flow diagram."),
        ("16b", "Study selection", "Cite studies that might appear to meet the inclusion criteria, but which were excluded, and explain why they were excluded."),
        ("17", "Study characteristics", "Cite each included study and present its characteristics."),
        ("18", "Risk of bias in studies", "Present assessments of risk of bias for each included study."),
        ("19", "Results of individual studies", "For all outcomes, present, for each study: (a) summary statistics for each group (where appropriate) and (b) an effect estimate and its precision (e.g. confidence/credible interval), ideally using structured tables or plots."),
        ("20a", "Results of syntheses", "For each synthesis, briefly summarise the characteristics and risk of bias among contributing studies."),
        ("20b", "Results of syntheses", "Present results of all statistical syntheses conducted. If meta-analysis was done, present for each the summary estimate and its precision (e.g. confidence/credible interval) and measures of statistical heterogeneity. If comparing groups, describe the direction of the effect."),
        ("20c", "Results of syntheses", "Present results of all investigations of possible causes of heterogeneity among study results."),
        ("20d", "Results of syntheses", "Present results of all sensitivity analyses conducted to assess the robustness of the synthesized results."),
        ("21", "Reporting biases", "Present assessments of risk of bias due to missing results (arising from reporting biases) for each synthesis assessed."),
        ("22", "Certainty of evidence", "Present assessments of certainty (or confidence) in the body of evidence for each outcome assessed.")]),
    ("DISCUSSION", [
        ("23a", "Discussion", "Provide a general interpretation of the results in the context of other evidence."),
        ("23b", "Discussion", "Discuss any limitations of the evidence included in the review."),
        ("23c", "Discussion", "Discuss any limitations of the review processes used."),
        ("23d", "Discussion", "Discuss implications of the results for practice, policy, and future research.")]),
    ("OTHER INFORMATION", [
        ("24a", "Registration and protocol", "Provide registration information for the review, including register name and registration number, or state that the review was not registered."),
        ("24b", "Registration and protocol", "Indicate where the review protocol can be accessed, or state that a protocol was not prepared."),
        ("24c", "Registration and protocol", "Describe and explain any amendments to information provided at registration or in the protocol."),
        ("25", "Support", "Describe sources of financial or non-financial support for the review, and the role of the funders or sponsors in the review."),
        ("26", "Competing interests", "Declare any competing interests of review authors."),
        ("27", "Availability of data, code and other materials", "Report which of the following are publicly available and where they can be found: template data collection forms; data extracted from included studies; data used for all analyses; analytic code; any other materials used in the review.")]),
]
CHECKLIST_ITEMS = [n for _, items in CHECKLIST for n, _, _ in items]


def checklist_where(p: Project, me: str) -> dict:
    """{item: text}: the ReviewDesk files that cover each item (partly, where marked), for this person."""
    lead = p.is_lead(me)
    sof = getattr(certainty, "evidence_profile", None) is not None
    reg = p.settings_ro().get("registration") or {}
    ai_used = _ai_used(p)
    lead_note = "" if lead else " (in the project lead's pack)"
    m = {
        "5": "Methods text: Eligibility criteria (partly: add how studies were grouped for the syntheses)",
        "6": "Methods text: Information sources and searches; Search methods appendix (PRISMA-S)",
        "7": "Search methods appendix (PRISMA-S): full strategies, limits and filters; Search log (CSV)",
        "8": "Methods text: Title and abstract screening, Full-text review; Screening agreement"
             + ("; AI second screener report (automation tool" + ("" if lead else "; in the project lead's pack") + ")"
                if ai_used else ""),
        "9": "Methods text: Data extraction (partly: add any contact with study investigators)",
        "10a": "Data extraction workbook (Excel), Form sheet: every item extracted (partly: define the outcomes)",
        "10b": "Data extraction workbook (Excel), Form sheet (partly: add any assumptions about missing information)",
        "11": "Methods text: Risk of bias in included studies (the tool for each study design, with its citation, and how "
              "the reviewers worked" + ("; partly: add the version of each tool used" if robplan.recorded(p) else
                                         "; partly: record the risk-of-bias plan in Project set-up") + "); Risk of bias "
              "tables (Word): the risk-of-bias plan",
        "15": "Methods text: Certainty of the evidence" + ("; Summary of findings and evidence profile" if sof else ""),
        "16a": "PRISMA flow diagram (SVG, or PNG from the Reports page); PRISMA counts (CSV); Methods text; Studies awaiting "
               "classification; Ongoing studies; Reports not retrieved",
        "16b": "Studies excluded at full text, with reasons",
        "17": "Included studies and their reports; Characteristics of included studies",
        "18": "Risk of bias tables (Word); Risk of bias figure (SVG); Risk of bias workbook (Excel)",
        "19": "Data extraction workbook (Excel): results tables (partly)",
        "22": "Summary of findings and evidence profile" if sof else "",
        "24a": (f"Registration recorded in Project set-up: {reg.get('registry', '')} {reg.get('id', '')}".strip()
                + "; Methods text: Protocol and registration") if (reg.get("registry") or reg.get("id")) else "Methods text: Protocol and registration (to complete)",
        "24b": "Methods text: Protocol and registration" + (" (partly: a highlighted line asks where the protocol can be read)"
                                                            if not util.web_url(reg.get("url")) else ""),
        "24c": ("Methods text: Changes after screening started (partly: the changes to the criteria, question and "
                "risk-of-bias plan recorded in ReviewDesk; explain each one)") if p.settings_ro().get("amendments") else "",
        "27": "Reporting pack: data extraction workbook (form and data), all decisions and the audit log" + lead_note
              + (", AI prompts and answers" + lead_note if ai_used else "") + "; say where they are deposited",
    }
    if ai_used:
        m["23c"] = "AI second screener report" + lead_note + ": limitations and possible biases (partly, to complete)"
    return {k: v for k, v in m.items() if v}


def checklist_docx(p: Project, me: str) -> bytes:
    where = checklist_where(p, me)
    d = _doc(p, "PRISMA 2020 checklist", landscape=True)
    d.note("The 27 items of the PRISMA 2020 checklist, with the ReviewDesk files that cover each one. Items with nothing in the "
           "fourth column are for the authors to write.")
    rows = []
    for section, items in CHECKLIST:
        rows.append([Cell(section, bold=True, fill="F3F4F6", span=5)])
        for n, topic, text in items:
            rows.append([topic, Cell(n, bold=True), text, where.get(n, ""), ""])
    d.table(["Section and topic", "Item", "Checklist item", "Where ReviewDesk reports it", "Location where reported (page or section)"],
            rows, widths=[1.6, 0.55, 5.2, 3.4, 1.8], compact=True, caption="PRISMA 2020 checklist", keep_rows=True)
    d.para([b("The authors must complete and check this checklist. "),
            "ReviewDesk filled in the fourth column for the items its files cover, from what was recorded in this project. It "
            "cannot know where each item is in your manuscript: check every entry against the final report, and give the page "
            "or section where each item is reported. Where ReviewDesk says 'partly', the rest is for the authors to write."])
    d.note("Adapted from the PRISMA 2020 checklist: the sections, topics and checklist items are quoted word for word from "
           + PRISMA_REF + " doi: 10.1136/bmj.n71, licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); "
           "ReviewDesk added the column saying where its files report each item. For the abstract, use the PRISMA 2020 "
           "for Abstracts checklist.")
    return d.to_bytes()


# ================================================================================================ PRISMA counts, data checks
# The stage of the boxes that leave "Reports assessed for eligibility" (excluded with reasons, awaiting classification,
# ongoing, and included from that column): assessed - excluded - awaiting - ongoing = included.
OUTFLOWS = "Reports assessed for eligibility (outflows)"


def counts_csv(p: Project, me: str) -> bytes:
    C = prisma.counts(p, blind=not p.is_lead(me))
    D, O = C["db"], C["other"]
    A, B = "Identification of studies via databases and registers", "Identification of studies via other methods"
    rows = []
    for k, v in D["databases"].items():
        rows.append([A, "Identification", f"Records identified from: {k}", v])
    rows.append([A, "Identification", "Databases, total, imported" if D.get("databases_not_imported") else "Databases, total",
                 D["identified_databases"]])
    for k, v in D["registers"].items():
        rows.append([A, "Identification", f"Records identified from: {k}", v])
    ni = D.get("registers_not_imported") or {}
    # as the diagram labels it: when some registers' records were not imported, the box counts the records imported
    rows.append([A, "Identification", "Registers, imported" if ni else "Registers", D["identified_registers"]])
    rows += [[A, "Identification", "Duplicate records removed", D["duplicates"]],
             [A, "Identification", "Records marked as ineligible by automation tools", D["automation"]],
             [A, "Identification", "Records removed for other reasons", D["other_removed"]]]

    show_aw, show_on = C["awaiting"] > 0, C.get("ongoing", 0) > 0
    has_other = bool(O["identified"] or O["records"])

    def screening_rows(part, X, screened="Records screened"):
        out = [[part, "Screening", screened, X["screened"]], [part, "Screening", "Records excluded", X["ta_excluded"]],
               [part, "Screening", "Reports sought for retrieval", X["sought"]],
               [part, "Screening", "Reports not retrieved", X["not_retrieved"]],
               [part, "Screening", "Reports assessed for eligibility", X["assessed"]]]
        for k, v in sorted(X["ft_reasons"].items(), key=lambda kv: -kv[1]):
            out.append([part, OUTFLOWS, f"Reports excluded: {k}", v])
        # what else leaves "Reports assessed for eligibility": assessed - excluded - awaiting - ongoing = included
        if show_aw:
            out.append([part, OUTFLOWS, "Reports awaiting classification", X["awaiting"]])
        if show_on:
            out.append([part, OUTFLOWS, "Reports of ongoing studies", X["ongoing"]])
        if has_other:
            out.append([part, OUTFLOWS, "Reports included (from this column)", X["ft_included"]])
        return out
    rows += screening_rows(A, D)
    if O["identified"] or O["records"]:
        for k, v in O["sources"].items():
            rows.append([B, "Identification", f"Records identified from: {k}", v])
        if O["duplicates"]:
            rows.append([B, "Identification", "Duplicates", O["duplicates"]])
        if O["other_removed"]:
            rows.append([B, "Identification", "Removed for other reasons", O["other_removed"]])
        rows += screening_rows(B, O, "Records screened (title and abstract)")
    rows += [["Included", "Included", "Studies included in review", C["included_studies"]],
             ["Included", "Included", "Reports of included studies", C["included_reports"]]]
    for k, v in list((D.get("databases_not_imported") or {}).items()) + list(ni.items()):
        rows.append([A, "Not in the diagram (searched, not imported into ReviewDesk)", f"Records found in: {k}",
                     "" if v is None else v])
    for part, X in ((A, D), (B, O)):
        if X.get("pending_ta") or X.get("pending_ft"):
            rows += [[part, "Not in the diagram (still open)", "Records not yet screened", X["pending_ta"]],
                     [part, "Not in the diagram (still open)", "Reports still being assessed", X["pending_ft"]]]
    return _csv_bytes(["Part of the diagram", "Stage", "Box", "n"], rows)


def checks_txt(p: Project, me: str) -> bytes:
    s = p.settings_ro()
    nm = _nm(p)
    try:
        rep = p.integrity_report()
    except DamagedFile as e:
        rep = {"problems": [{"kind": "damaged", "file": e.what, "text": str(e)}], "accepted": [], "checked": util.now(),
               "sealed": "", "records_sealed": 0}
    lines = [f"Data checks for '{s.get('title', '')}'",
             f"Checked by ReviewDesk {VERSION} on {nice_date(rep.get('checked'))} at {str(rep.get('checked', ''))[11:16]}, "
             f"on the computer of {nm(me) or 'an unknown person'}.", ""]
    lines.append("ReviewDesk fingerprints every decision, record and PDF when it is saved, and checks the project folder against "
                 "these fingerprints. Anything changed outside ReviewDesk, or lost by the sync service, is reported here.")
    if rep.get("sealed"):
        lines.append(f"The records were sealed when screening started ({nice_date(rep['sealed'])}): "
                     f"{_pl(rep.get('records_sealed', 0), 'record')}.")
    lines.append("")
    probs = rep.get("problems") or []
    if not probs:
        lines.append("Result: no problems found. The decisions, records and PDFs that were checked match what ReviewDesk recorded. (The "
                     "checks are for accidents and sync faults: the user guide says what they can and cannot notice.)")
    else:
        lines.append(f"Result: {_pl(len(probs), 'problem')} found. Open Data checks in ReviewDesk to put each one right.")
        for k, pr in enumerate(probs, 1):
            lines += ["", f"{k}. {pr.get('text', '')}", f"   File: {pr.get('file', '')}"]
    acc = rep.get("accepted") or []
    if acc:
        lines += ["", f"Differences recorded as correct ({len(acc):,}):"]
        for a in acc:
            lines.append(f"- {nice_date(a.get('t'))}, {nm(a.get('by', ''))}: {a.get('text', '')} Reason given: {a.get('reason', '')}")
    return _txt(lines)


# ================================================================================================ the catalogue
GROUPS = [
    ("figures", "Tables and figures for the review", "1 Tables and figures",
     "The flow diagram's companions: tables to put in the results section or its supplementary material."),
    ("methods", "Methods text", "2 Methods text",
     "Text to edit into your methods section, the screening and AI reports behind it, and the PRISMA checklist filled in "
     "where ReviewDesk can."),
    ("appendix", "Appendix and deposit files", "3 Appendix and deposit files",
     "For the appendix, and to deposit with the review (for example on OSF), so others can check what was done."),
    ("evaluation", "For a study of the screening itself", "",
     "Not for the review's own report, and not in the reporting pack: for a study of how the review was screened (how "
     "accurate it was, how long it took, what each step added). The file names who cast each vote and, when the "
     "project records times, how long each vote took, so treat it as data about the people in the team."),
]

# Each item: group, title, what it is for, the PRISMA items it supports, its files [(kind, format)], and whether it is
# shown in the list on the Reports page (the flow diagram and the short methods summary have panels of their own).
# "pack": False keeps an item out of the reporting pack (it is downloaded on its own, deliberately).
ITEMS = [
    {"group": "figures", "title": "PRISMA flow diagram", "ui": False, "prisma": "PRISMA 2020 item 16a",
     "what": "The PRISMA 2020 flow diagram of records and reports at each stage, as an SVG picture, which opens in Word "
             "(Insert, Pictures) and in web browsers. A PNG copy is not in the pack: download it from the Reports page "
             "(Download picture (PNG)).",
     "files": [("prisma", "Picture (SVG)")]},
    {"group": "figures", "title": "Studies excluded at full text, with reasons", "prisma": "PRISMA 2020 item 16b",
     "what": "Every report excluded after reading it in full, grouped by the reason, with its first author, year, title, journal and DOI.",
     "files": [("rep_excluded", "Word"), ("excluded", "Spreadsheet (CSV)")]},
    {"group": "figures", "title": "Included studies and their reports", "prisma": "PRISMA 2020 items 16a and 17",
     "what": "Each included study with every report linked to it, as references; and the same reports for your reference manager.",
     "files": [("rep_included", "Word"), ("included", "Reference manager file (RIS)")]},
    {"group": "figures", "title": "Characteristics of included studies", "prisma": "PRISMA 2020 items 17, 10a, 10b and 19",
     "what": "One row per study with the values agreed on the extraction form; the workbook has every answer and where it was found.",
     "files": [("rep_characteristics", "Word"), ("extraction", "Workbook (Excel)")]},
    {"group": "figures", "title": "Risk of bias in included studies", "prisma": "PRISMA 2020 items 11 and 18",
     "what": "The risk-of-bias plan (the tool for each study design), a table for each tool with each domain's final "
             "judgement on the tool's own scale and a key, the traffic-light figure, and both reviewers' judgements with "
             "their support; any judgement the project lead changed is marked with its reason.",
     "files": [("rep_rob", "Word"), ("rob_svg", "Figure (SVG)"), ("rob", "Workbook (Excel)")]},
    {"group": "figures", "title": "Summary of findings and evidence profile (GRADE)", "prisma": "PRISMA 2020 items 15 and 22", "sof": True,
     "what": "The certainty of the evidence for each outcome rated with GRADE, with the reasons for each rating; the "
             "workbook has the evidence profile, its footnotes and both reviewers' ratings.",
     "files": [("rep_sof", "Word"), ("certainty", "Workbook (Excel)")]},
    {"group": "methods", "title": "Methods text", "prisma": "PRISMA 2020 items 5, 6, 8, 9, 11, 15, 16a, 24a and 24b",
     "what": "What was done, from the records and decisions: registration, criteria, searches, screening, full texts, data "
             "extraction, risk of bias and certainty, with highlighted lines for what only you can write.",
     "files": [("rep_methods", "Word"), ("rep_methods_txt", "Text file")]},
    {"group": "methods", "title": "Short methods summary", "ui": False, "prisma": "PRISMA 2020 items 8 and 16a",
     "what": "The short methods summary shown on the Reports page, as plain text.", "files": [("summary", "Text file")]},
    {"group": "methods", "title": "Screening agreement", "prisma": "PRISMA 2020 item 8",
     "what": "Each calibration round, the agreement sample's two-by-two table with percentage agreement and Cohen's kappa "
             "(95% CI), what the rule set in advance decided, and agreement at full text.",
     "files": [("rep_agreement", "Word")]},
    {"group": "methods", "title": "AI second screener report", "prisma": "PRISMA 2020 item 8; the 2025 position statement on AI in evidence synthesis",
     "what": "The models and settings, every run, how the AI's answers were used, its accuracy on the agreement sample, the "
             "exact prompts, funding and interests, data protection, and limitations and possible biases, with lines to "
             "complete; the prompts also as plain text.",
     "files": [("rep_ai", "Word"), ("rep_ai_prompts", "Prompts and settings (text)")], "lead_only": True},
    {"group": "methods", "title": "PRISMA 2020 checklist", "prisma": "Every PRISMA 2020 item",
     "what": "All 27 items, with the ReviewDesk file that covers each one filled in, and a column for where you report it.",
     "files": [("rep_checklist", "Word")]},
    {"group": "appendix", "title": "Search methods appendix (PRISMA-S)", "prisma": "PRISMA 2020 items 6 and 7; PRISMA-S items 1 to 16",
     "what": "Every search with its database, platform, dates, limits, number of records and the full strategy word for "
             "word; registers, other methods, update searches and de-duplication. It says what the log is missing.",
     "files": [("rep_search", "Word"), ("searchlog", "Search log (CSV)")]},
    {"group": "appendix", "title": "Studies awaiting classification", "prisma": "PRISMA 2020 item 16a",
     "what": "Reports whose eligibility could not be decided (for example, waiting for the authors to reply).",
     "files": [("rep_awaiting", "Word")]},
    {"group": "appendix", "title": "Ongoing studies", "prisma": "PRISMA 2020 item 16a",
     "what": "Registered or ongoing studies with no results yet, such as trial register entries: listed, not included.",
     "files": [("rep_ongoing", "Word")]},
    {"group": "appendix", "title": "Reports not retrieved", "prisma": "PRISMA 2020 item 16a",
     "what": "Reports whose full text could not be found: the 'Reports not retrieved' box of the flow diagram.",
     "files": [("rep_not_retrieved", "Word")]},
    {"group": "appendix", "title": "PRISMA counts", "prisma": "PRISMA 2020 item 16a",
     "what": "Every number in the flow diagram, labelled as in the diagram, for checking or for redrawing it.",
     "files": [("rep_counts", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "De-duplication log", "prisma": "PRISMA-S item 16",
     "what": "Every duplicate removed: which record was kept and the rule that matched them.", "files": [("dedup", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "Every record, with its outcome", "prisma": "PRISMA 2020 item 27", "lead_only": True,
     "what": "Every record with where it was found and its outcome at each stage.", "files": [("records", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "Every decision by every reviewer", "prisma": "PRISMA 2020 item 27", "lead_only": True,
     "what": "Every vote and agreed decision, with who made it and when.", "files": [("decisions", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "The AI's answers", "prisma": "PRISMA 2020 item 27; the 2025 position statement (share the outputs)",
     "lead_only": True, "what": "Each model's answer and reason for every record it screened.", "files": [("ai", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "Audit log", "prisma": "PRISMA 2020 item 27",
     "what": "What each person did in ReviewDesk, and when.", "files": [("audit", "Spreadsheet (CSV)")]},
    {"group": "appendix", "title": "Data checks report", "prisma": "PRISMA 2020 item 27 (it shows nothing was changed outside ReviewDesk)",
     "what": "The result of ReviewDesk's checks that no decision, record or PDF was changed outside ReviewDesk.",
     "files": [("rep_checks", "Text file")]},
    {"group": "evaluation", "title": "Evaluation data", "lead_only": True, "pack": False,
     "prisma": "Not a PRISMA item: for a methods study (see RAISE and the 2025 position statement on AI in evidence synthesis)",
     "what": "One row per record (who had to screen it, each stage's outcome, both AI models' answers and what they cost), "
             "one row per vote (with screening times, when the project records them), the verification sample, and the "
             "plan and its results as data; a read-me in the zip describes every column. The team's copy shows people by "
             "their initials and holds the audit log; the copy for sharing shows them as Reviewer A, B, C and leaves it out.",
     "files": [("evaluation", "Team's copy (zip)"), ("evaluation_share", "Copy for sharing (zip)")]},
]

MAKERS = {
    "rep_methods": ("methods text.docx", DOCX, methods_docx),
    "rep_methods_txt": ("methods text.txt", TXT, methods_txt),
    "rep_search": ("search methods appendix (PRISMA-S).docx", DOCX, search_docx),
    "rep_agreement": ("screening agreement.docx", DOCX, agreement_docx),
    "rep_excluded": ("studies excluded at full text.docx", DOCX, excluded_docx),
    "rep_awaiting": ("studies awaiting classification.docx", DOCX, awaiting_docx),
    "rep_ongoing": ("ongoing studies.docx", DOCX, ongoing_docx),
    "rep_not_retrieved": ("reports not retrieved.docx", DOCX, not_retrieved_docx),
    "rep_included": ("included studies and their reports.docx", DOCX, included_docx),
    "rep_characteristics": ("characteristics of included studies.docx", DOCX, characteristics_docx),
    "rep_rob": ("risk of bias tables.docx", DOCX, rob_docx),
    "rep_sof": ("summary of findings (GRADE).docx", DOCX, lambda p, me: sof_docx(p, me)),
    "rep_ai": ("AI second screener report.docx", DOCX, ai_docx),
    "rep_ai_prompts": ("AI prompts and settings.txt", TXT, ai_prompts_txt),
    "rep_checklist": ("PRISMA 2020 checklist.docx", DOCX, checklist_docx),
    "rep_counts": ("PRISMA counts.csv", CSV, counts_csv),
    "rep_checks": ("data checks report.txt", TXT, checks_txt),
}


def _items(p: Project, me: str = "") -> list:
    """The outputs this project has, each with the PRISMA items it supports. The methods text's items are those the
    PRISMA checklist (checklist_where) points to it for, so the read-me and the checklist always agree: item 24c, for
    example, only when amendments are recorded."""
    sof = getattr(certainty, "evidence_profile", None) is not None
    where = checklist_where(p, me or p.lead_id())
    out = []
    for x in ITEMS:
        if not (sof or not x.get("sof")):
            continue
        if x["title"] == "Methods text":
            nums = [n for n in CHECKLIST_ITEMS if "Methods text" in where.get(n, "")]
            x = dict(x, prisma="PRISMA 2020 items " + _list(nums)) if nums else x
        out.append(x)
    return out


def file_name(kind: str) -> str:
    if kind in MAKERS:
        return MAKERS[kind][0]
    if kind == "rep_pack":
        return "reporting pack.zip"
    if kind in (getattr(certainty, "DOWNLOADS", {}) or {}):
        return certainty.DOWNLOADS[kind][0]
    from . import server
    return server.DOWNLOADS[kind][0]


def make(p: Project, me: str, kind: str) -> bytes:
    """The bytes of any output, as this person may have it (the older downloads too)."""
    if kind in MAKERS:
        return MAKERS[kind][2](p, me)
    if kind == "certainty":
        maker = _grade_workbook(p, me)[0]
        if maker is None:
            raise ValueError("The GRADE part of ReviewDesk makes no workbook.")
        return maker(p, me)
    lead = p.is_lead(me)
    if kind == "prisma":
        return prisma.svg(p, blind=not lead).encode("utf-8")
    if kind == "summary":
        return exports.methods_summary(p, full=lead).encode("utf-8")
    from . import server
    if kind == "dedup":
        return server.dedup_csv(p)
    return server.DOWNLOADS[kind][2](p)


# ================================================================================================ the reporting pack
def _fmt_words(fmt: str) -> str:
    """'Word' as 'Word document', 'Spreadsheet (CSV)' as 'spreadsheet, CSV'."""
    if fmt == "Word":
        return "Word document"
    return (fmt[:1].lower() + fmt[1:]).replace(" (", ", ").rstrip(")")


def pack(p: Project, me: str) -> bytes:
    """One .zip with every output this person may have, and a 'Read me first.txt' saying what each is for."""
    s = p.settings_ro()
    lead = p.is_lead(me)
    nm = _nm(p)
    folders = {g: f for g, _, f, _ in GROUPS}
    got, missing = [], []
    items = [it for it in _items(p, me) if it.get("pack", True)]
    for it in items:
        for kind, fmt in it["files"]:
            state, why = status(p, me, kind)
            label = f"{it['title']} ({_fmt_words(fmt)})"
            if state != "ready":
                missing.append((label, why))
                continue
            try:
                data = make(p, me, kind)
            except Exception as e:                       # noqa: BLE001 - one file's fault must not lose the rest
                missing.append((label, f"It could not be made ({type(e).__name__}: {e}). Download it on its own from the "
                                       "Reports page to see the problem."))
                continue
            got.append((f"{folders[it['group']]}/{file_name(kind)}", data, it, fmt))
    lines = [f"Reporting pack for '{s.get('title', '')}'",
             f"Made by ReviewDesk {VERSION} on {nice_date(util.today())} for {nm(me) or me} "
             f"({'the project lead' if lead else 'a reviewer'}).", "",
             "WHAT THIS IS", "",
             "Every file ReviewDesk can make for writing up the review, as things stood when the pack was made. ReviewDesk "
             "fills in what the project recorded: check every file before you use it, and complete the highlighted parts "
             "in the Word documents, which only the authors can write.", ""]
    if not _complete(p):
        lines += ["Screening is not finished, so the numbers in these files will still change. Make a new pack when it is.", ""]
    if not lead:
        lines += ["This is a reviewer's pack. It leaves out the files only the project lead may have: those listing every "
                  "reviewer's votes, and the AI's answers. Its methods text and flow diagram are the versions for reviewers. "
                  "The project lead's pack has everything.", ""]
    lines += ["THE FILES", ""]
    for g, title, folder, _ in GROUPS:
        mine = [x for x in got if x[2]["group"] == g]
        if not mine:
            continue
        lines += [f"Folder '{folder}': {title.lower()}", ""]
        for it in [x for x in items if x["group"] == g]:
            files = [x for x in mine if x[2] is it]
            if not files:
                continue
            pr = it["prisma"] if it["prisma"][:2].isupper() else it["prisma"][:1].lower() + it["prisma"][1:]
            lines += [f"  {it['title']}", f"    {it['what']}", f"    Supports {pr}."]
            lines += [f"    - {path.split('/', 1)[1]}  ({_fmt_words(fmt)})" for path, _, _, fmt in files]
            lines.append("")
    if missing:
        lines += ["NOT INCLUDED, AND WHY", ""]
        for label, why in missing:
            lines += [f"  {label}", f"    {why}", ""]
    lines += ["THE GUIDELINES", "", f"  PRISMA 2020: {PRISMA_REF} The checklist's items are quoted, and the flow "
              f"diagram is adapted from its template, under its licence ({CC_BY}).", f"  PRISMA-S: {PRISMA_S_REF}",
              f"  PRESS 2015 (the areas the search worksheet's own prompts cover): {PRESS_REF}",
              f"  GRADE: {certainty.GRADE_REFS}",
              f"  AI in evidence synthesis: {AI_REF}", ""]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Read me first.txt", "\n".join(lines).rstrip() + "\n")
        for path, data, _, _ in got:
            z.writestr(path, data)
    return buf.getvalue()


# ================================================================================================ the Reports page
def h_reporting(p: Project, me: str, q, body) -> dict:
    """What the Reports page lists: every output, grouped, with whether this person can download it now."""
    lead = p.is_lead(me)
    groups = []
    ready = 0
    for g, title, _, intro in GROUPS:
        items = []
        for it in _items(p, me):
            if it["group"] != g:
                continue
            files = []
            for kind, fmt in it["files"]:
                state, why = status(p, me, kind)
                ready += state == "ready" and it.get("pack", True)       # (the pack's count: what the pack will hold)
                files.append({"kind": kind, "format": fmt, "state": state, "why": why})
            if it.get("ui", True):
                items.append({"title": it["title"], "what": it["what"], "prisma": it["prisma"], "lead_only": bool(it.get("lead_only")),
                              "files": files})
        groups.append({"id": g, "title": title, "intro": intro, "items": items})
    return {"is_lead": lead, "complete": _complete(p) if p.records() else False, "groups": groups, "ready": ready,
            "summary_state": status(p, me, "summary"), "closed": evaluation.blocked(p, me),
            "closed_title": evaluation.blocked_title(p, me)}


API_GET = {"/api/reporting": h_reporting}
API_POST = {}
DOWNLOADS = {kind: (name, mime, fn, why_not(kind)) for kind, (name, mime, fn) in MAKERS.items()}
DOWNLOADS["rep_pack"] = ("reporting pack.zip", ZIP, pack, why_not("rep_pack"))
