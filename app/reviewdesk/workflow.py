"""The steps of a systematic review in ReviewDesk, in the order they happen.

One list, used by the guided set-up, the "what is missing" checks (readiness.py), the timeline page and
infographic (timeline.py) and the user guides, so they always agree. Each step says who does it and what it
involves; steps_for() keeps the steps this project's plan uses and fills in its numbers (the calibration size, the
share in the agreement sample, the kappa needed, who screens alone, and so on). Who each step needs comes from
screening.roles, the one set of rules for who a screening plan needs: a plan with one reviewer has no calibration
round, and its steps say that one person does the work."""
from __future__ import annotations

from . import screening

# phase ids, in order, with the words shown for them
PHASES = [("plan", "Plan"), ("search", "Search"), ("screen", "Screen titles and abstracts"), ("fulltext", "Full texts"),
          ("extract", "Extract and appraise"), ("synthesise", "Synthesise and rate certainty"), ("report", "Report")]

# when: which plans use the step. "always"; "sample" (agreement sample, then one reviewer if kappa is high enough);
# "ai" (the AI second screener is planned); "update" (an update search is planned or has records)
STEPS = [
    {"id": "setup", "phase": "plan", "when": "always", "route": "setup", "who": "lead",
     "title": "Set up the project",
     "what": "Write the review question and eligibility criteria, add the team, fix the screening plan and record the "
             "risk-of-bias plan (the tool for each study design)."},
    {"id": "register", "phase": "plan", "when": "always", "route": "setup", "who": "lead", "outside": True,
     "title": "Register the protocol",
     "what": "Register the review (for example in PROSPERO or on OSF) before the final searches are run, and record "
             "its registration number here."},
    {"id": "searches", "phase": "search", "when": "always", "route": "searches", "who": "searcher",
     "title": "Run and log the searches",
     "what": "Run the final search in every database, as close together as possible, and log each one: database, "
             "platform, date, number of records and the full search strategy."},
    {"id": "import", "phase": "search", "when": "always", "route": "import", "who": "anyone",
     "title": "Import the results",
     "what": "Export the results from each database and add the files, each linked to its search in the log."},
    {"id": "duplicates", "phase": "search", "when": "always", "route": "duplicates", "who": "lead",
     "title": "Remove duplicates",
     "what": "Add the imported files to the records, which removes duplicates, then check the pairs ReviewDesk flags as possible duplicates."},
    {"id": "calibration", "phase": "screen", "when": "calibration", "route": "screening", "who": "both",
     "title": "Calibration round",
     "what": "Both reviewers screen the same {calibration} records, then discuss every disagreement. These votes do "
             "not count towards agreement."},
    {"id": "sample", "phase": "screen", "when": "sample", "route": "screening", "who": "both",
     "title": "Agreement sample",
     "what": "Both reviewers screen a random {sample_pct} of the records{sample_about} independently, without "
             "seeing each other's votes."},
    {"id": "ai", "phase": "screen", "when": "ai", "route": "ai", "who": "lead",
     "title": "Run the AI second screener",
     "what": "The lead runs the two AI models on every record before the agreement sample is finished. Nobody sees "
             "their answers until it is."},
    {"id": "agreement_rule", "phase": "screen", "when": "sample", "route": "screening", "who": "lead",
     "title": "Apply the agreement rule",
     "what": "If Cohen's kappa on the sample is at least {kappa}, {single} screens the rest alone{ai_clause}; "
             "otherwise both reviewers screen everything."},
    {"id": "screen", "phase": "screen", "when": "always", "route": "screening", "who": "screeners",
     "title": "Screen the remaining records",
     "what": "{remainder_text}"},
    {"id": "get_fulltexts", "phase": "fulltext", "when": "always", "route": "fulltext", "who": "anyone",
     "title": "Get the full texts",
     "what": "Find a PDF for every record sent on: open-access copies, the library, or by asking the authors. Record "
             "any that cannot be found as not retrieved."},
    {"id": "fulltext", "phase": "fulltext", "when": "always", "route": "fulltext", "who": "fulltext",
     "title": "Assess the full texts",
     "what": "{fulltext_text} Record a reason for every exclusion{ft_settle}."},
    {"id": "citations", "phase": "fulltext", "when": "always", "route": "import", "who": "anyone",
     "title": "Citation searching and other sources",
     "what": "Check the reference lists of included studies (and studies citing them) and any other planned sources; "
             "import what they find as records from other methods and screen them the same way."},
    {"id": "extract", "phase": "extract", "when": "always", "route": "extract", "who": "extractors",
     "title": "Extract data",
     "what": "{extract_text}"},
    {"id": "rob", "phase": "extract", "when": "always", "route": "extract", "who": "both",
     "title": "Assess risk of bias",
     "what": "{rob_text}"},
    {"id": "update_search", "phase": "extract", "when": "always", "route": "import", "who": "searcher",
     "title": "Re-run the searches",
     "what": "Before the analysis, run the searches again unchanged{update_when}, import the new records as the "
             "update search and screen them the same way."},
    {"id": "synthesis", "phase": "synthesise", "when": "always", "route": "", "who": "team", "outside": True,
     "title": "Synthesise the results",
     "what": "Analyse the extracted data, outside ReviewDesk (for example a meta-analysis in R, or a narrative "
             "synthesis following SWiM)."},
    {"id": "certainty", "phase": "synthesise", "when": "always", "route": "certainty", "who": "both",
     "title": "Rate the certainty of the evidence",
     "what": "Rate each main outcome with GRADE: risk of bias, inconsistency, indirectness, imprecision and "
             "publication bias."},
    {"id": "report", "phase": "report", "when": "always", "route": "report", "who": "lead",
     "title": "Report the review",
     "what": "Download the PRISMA 2020 flow diagram, the tables and the methods summary for the write-up and the "
             "appendix."},
]


def _name(s: dict, rid: str, unset: str) -> str:
    r = next((x for x in s.get("reviewers") or [] if x.get("id") == rid), None)
    return (r or {}).get("name") or rid or unset


def plan_numbers(s: dict, n_records: int = 0, main: dict | None = None) -> dict:
    """The numbers and names the step texts use, from the project's settings (s) and the number of records the
    agreement sample is drawn from (n_records: the database searches' records only, as in timeline.sample_base).
    main: the main record set's state, when known: once screening has started, the plan fixed then is the one in
    force, and a calibration round that was drawn is a step of the review whatever the plan says now."""
    sc = s.get("screening") or {}
    main = main or {}
    started = main.get("phase") not in (None, "not_started") and isinstance(main.get("plan_at_start"), dict)
    ro = screening.roles(s, main, main["plan_at_start"] if started else None)
    pl = ro["plan"]
    mode = pl.get("mode", "sample_then_single")
    frac = float(sc.get("sample_fraction") or 0.2)
    ai = bool(sc.get("ai_second_screener")) and mode != "dual"
    single = _name(s, pl.get("single_reviewer") or "", "one reviewer")
    second = _name(s, sc.get("second_reviewer") or "", "the second reviewer")
    cal = int(pl.get("calibration_size") or 0) if mode != "single" else 0
    if main.get("calibration_rounds") and (main.get("phase") == "calibration" or main.get("calibration_results")):
        cal = len(main["calibration_rounds"][0])   # a round was drawn and is open, or was finished: the review did it
    elif started:
        cal = 0                                 # none was drawn, or one that could not be finished was ended unused
    if mode == "dual":
        remainder = "Both reviewers screen every record independently."
    elif mode == "single":
        remainder = f"{single} screens every record" + (
            f"; any record {single} excludes that the AI would keep goes to {second}." if ai else ".")
    else:
        remainder = (f"{single[:1].upper() + single[1:]} or both reviewers screen the rest, as the agreement rule decided"
                     + (f"; with the AI second screener, any record {single} excludes that the AI would keep goes to "
                        f"{second}." if ai else "."))
    ft_dual = (s.get("fulltext") or {}).get("mode", "dual") == "dual"
    one = ro["one"]                             # one person does every stage
    if ft_dual and len(ro["ft"]) == 2:
        ft_text = "Both reviewers assess every full text independently."
    elif ro["ft"]:
        ft_text = f"{_name(s, ro['ft'][0], 'One reviewer')} assesses every full text alone."
    else:
        ft_text = "Both reviewers assess every full text independently." if ft_dual else "One reviewer assesses every full text."
    first_x, second_x = ro["extract"]
    if first_x and not second_x and (one or ro["people"] < 2):     # (with two people, the lead may name a second)
        extract = (f"{_name(s, first_x, 'One reviewer')} extracts every study's details and outcome data alone, with no "
                   "second extractor to check them.")
    else:
        extract = ("One reviewer extracts each study's details and the other checks them; outcome data are extracted by "
                   "both independently and compared.")
    if one or len(ro["raters"]) == 1:
        rob = (f"{_name(s, ro['raters'][0] if ro['raters'] else '', 'One reviewer')} judges each result alone, with the "
               "tool the risk-of-bias plan gives for its study design.")
    else:
        rob = ("Both reviewers judge each result, independently, with the tool the risk-of-bias plan gives for its study "
               "design, then agree.")
    upd = ((s.get("dates") or {}).get("update_search") or "").strip()
    return {
        "mode": mode, "ai": ai, "calibration": cal, "one": one, "extract_text": extract, "rob_text": rob,
        "sample_pct": f"{round(frac * 100)}%", "sample_n": f"{max(1, round(n_records * frac)):,}" if n_records else "",
        "sample_about": f" (about {max(1, round(n_records * frac)):,})" if n_records else "",
        "kappa": f"{float(sc.get('kappa_threshold') or 0.8):.2f}", "single": single, "second": second,
        "ai_clause": f", with the AI second screener behind them (records they exclude that the AI would keep go to {second})" if ai else "",
        "remainder_text": remainder,
        "fulltext_text": ft_text, "ft_settle": "" if len(ro["ft"]) == 1 or not ft_dual else ", and settle disagreements together",
        "update_when": f" ({upd})" if upd else "",
    }


def _applies(step: dict, s: dict, nums: dict) -> bool:
    w = step["when"]
    if w == "always":
        return True
    if w == "calibration":
        return nums["calibration"] > 0
    if w == "sample":
        return nums["mode"] == "sample_then_single"
    if w == "ai":
        return nums["ai"]
    return True


# who does a step that two reviewers do, when one person does every stage (the words the timeline shows)
ONE_WHO = {"both": "one reviewer", "extractors": "one reviewer", "fulltext": "one reviewer", "screeners": "one reviewer"}


def steps_for(s: dict, n_records: int = 0, main: dict | None = None) -> list:
    """The steps this project's plan uses, in order, with their texts filled in. Each is a copy:
    {"id", "phase", "phase_title", "title", "what", "who", "route", "outside"}. main: as for plan_numbers."""
    nums = plan_numbers(s, n_records, main)
    phase_title = dict(PHASES)
    out = []
    for st in STEPS:
        if not _applies(st, s, nums):
            continue
        x = dict(st)
        x["what"] = st["what"].format(**nums)
        x["phase_title"] = phase_title[st["phase"]]
        x["outside"] = bool(st.get("outside"))
        if nums["one"] and x["who"] in ONE_WHO:
            x["who"] = ONE_WHO[x["who"]]
        out.append(x)
    return out


STEP_IDS = [s["id"] for s in STEPS]
