"""A small but realistic review, made through ReviewDesk's own functions, for the reporting tests and for
tools/check_reports.py: an MSc review of AI in emergency department triage, with a project lead (CH) and a reviewer
(NH), five searches (one with gaps in the log), duplicates, a calibration round, an agreement sample, the AI second
screener behind single screening, other methods and an update search, every full-text decision (ongoing and awaiting
included), data extraction and risk of bias, some of it unfinished. No network and no real AI service: the AI's
answers are written as the AI runner writes them.

    info = build(folder)        # {"p": Project, "ids": {role: record ID}, "key": {role: study key}}
"""
from __future__ import annotations

import datetime as dt
import json

from reviewdesk import ai, integrity, review, robplan, screening, util
from reviewdesk.project import Project

TITLE = "AI in emergency department triage: prospective clinical evaluations"
TRICKY = 'AI triage <score> & "ED" flow ]]> in a prospective study\x01 of 400 adults'
AI_MARK = "AIREASON-7f3"           # in every AI reason, so a test can see an AI answer wherever it turns up

# role, title, authors, year, journal, doi, abstract
RELEVANT = [
    ("inc_taylor_1", "Machine learning triage in the emergency department: a prospective multicentre evaluation",
     "Taylor, Amelia; Singh, Raj; Brown, Li; Evans, Mark; Ng, Wei; Park, Jun; Osei, Kofi", "2025", "Annals of Emergency Medicine",
     "10.1016/j.annemergmed.2025.01.001"),
    ("inc_taylor_2", "Machine learning triage in the emergency department: twelve-month follow-up of a prospective evaluation",
     "Taylor, Amelia; Okoro, Ben", "2026", "Emergency Medicine Journal", "10.1136/emermed-2026-000002"),
    ("inc_a", "Deep learning for under-triage detection: a prospective cohort in three hospitals",
     "Kowalski, Jan; Duarte, Ana", "2024", "BMJ Open", "10.1136/bmjopen-2024-000003"),
    ("inc_b", "An interrupted time series of an AI triage tool in a paediatric emergency department",
     "Haddad, Sami; Lee, Grace", "2023", "Pediatric Emergency Care", "10.1097/pec.2023.000004"),
    ("inc_tricky", TRICKY, "O'Brien, Siobhan; Müller, Jörg", "2025", "Journal of Emergency & Critical Care", "10.1000/tricky.2025.5"),
    ("inc_vs_exc", "Natural language processing of triage notes: a prospective validation", "Okafor, Chidi; Adeyemi, Tolu",
     "2026", "Academic Emergency Medicine", "10.1111/acem.2026.000006"),
    ("exc_pop", "AI triage for adult inpatients on medical wards: a prospective study", "Fischer, Lena", "2024",
     "Journal of Hospital Medicine", "10.1002/jhm.2024.000007"),
    ("exc_design", "Retrospective evaluation of an AI triage score in emergency care", "Rossi, Marco", "2022",
     "Emergency Medicine Australasia", "10.1111/ema.2022.000008"),
    ("exc_outcome", "An AI triage tool and clinician satisfaction: a prospective survey", "Svensson, Erik", "2024",
     "International Journal of Medical Informatics", "10.1016/ijmi.2024.000009"),
    ("exc_reason_conflict", "Triage support by artificial intelligence in a simulated emergency department", "Moreau, Claire",
     "2023", "Simulation in Healthcare", "10.1097/sih.2023.000010"),
    ("awaiting", "Prospective evaluation of an AI triage assistant: conference abstract", "Novak, Petra", "2025",
     "Emergency Medicine Journal (abstracts)", "10.1136/emermed-2025-abs-011"),
    ("ongoing_resolved", "Large language model triage support in emergency departments: protocol for a stepped-wedge trial",
     "Chen, Hui; Patel, Nisha", "2026", "BMJ Open", "10.1136/bmjopen-2026-000012"),
    ("not_retrieved", "Prospective assessment of an AI triage tool in a rural emergency department", "Walsh, Fergus", "2021",
     "Rural Health Reports", "10.5555/rhr.2021.000013"),
    ("second_look", "Computer-aided triage and waiting times: a prospective before-after study", "Ivanova, Olga", "2024",
     "Emergency Nurse", "10.7748/en.2024.000014"),
]
IRRELEVANT_TOPICS = ["Statins in heart failure", "Knee arthroplasty rehabilitation", "School-based asthma education",
                     "Antibiotic prophylaxis in dental surgery", "Vitamin D in older adults", "Smoking cessation apps",
                     "Hand hygiene audits on surgical wards", "Physiotherapy after stroke", "Sleep in shift workers",
                     "Diabetic foot screening", "Prenatal iron supplements", "Hearing aids and cognition",
                     "Telehealth for depression", "Cataract surgery waiting lists", "Malaria bed nets", "Dietary salt and blood pressure",
                     "Falls prevention in care homes", "Sepsis bundles in intensive care"]


def _abstract(title: str, relevant: bool) -> str:
    if relevant:
        return (f"Background: {title}. Methods: a prospective study in the emergency department comparing an artificial "
                "intelligence triage tool with usual nurse triage. Results: under-triage fell from 12% to 7% (p < 0.05) "
                "& time to triage fell by 4 minutes. Conclusion: the tool was safe.")
    return f"Background: {title}. Methods: a randomised trial. Results: the intervention helped. Conclusion: more research is needed."


def _ris(recs: list) -> bytes:
    out = []
    for r in recs:
        out.append(f"TY  - {r.get('ty', 'JOUR')}")
        if r.get("title"):
            out.append(f"TI  - {r['title']}")
        for a in [x.strip() for x in (r.get("authors") or "").split(";") if x.strip()]:
            out.append(f"AU  - {a}")
        for tag, k in (("PY", "year"), ("JO", "journal"), ("VL", "volume"), ("IS", "issue"), ("SP", "sp"), ("EP", "ep"),
                       ("DO", "doi"), ("AN", "accession"), ("UR", "url"), ("AB", "abstract")):
            if r.get(k):
                out.append(f"{tag}  - {r[k]}")
        out.append("ER  - ")
        out.append("")
    return "\n".join(out).encode("utf-8")


def _rec(role, title, authors, year, journal, doi, relevant=True, **kw):
    return {"role": role, "title": title, "authors": authors, "year": year, "journal": journal, "doi": doi,
            "abstract": _abstract(title, relevant), "volume": "12", "issue": "3", "sp": "101", "ep": "109", "relevant": relevant, **kw}


def records() -> dict:
    """The exports, as lists of records with their role in the review."""
    rel = {x[0]: _rec(*x) for x in RELEVANT}
    irr = [_rec(f"irr_{k}", f"{t}: a randomised trial", f"Smith, John; Jones, Kate {k}", str(2015 + k % 10), "The Lancet",
                f"10.1016/irr.{k:03d}", relevant=False) for k, t in enumerate(IRRELEVANT_TOPICS)]
    medline = [r for k, r in rel.items()] + irr[:15]
    embase = [dict(r) for r in medline[:8]] + [
        _rec("exc_lang", "Triage automatisé par intelligence artificielle aux urgences : étude prospective", "Dubois, Anne",
             "2024", "Annales françaises de médecine d'urgence", "10.1007/afmu.2024.000015")] + irr[15:18] + [
        {"role": "empty", "ty": "JOUR", "year": "2020", "journal": ""}]
    ctgov = [_rec("ongoing", "AI-assisted triage in the emergency department (TRIAGE-AI): a randomised trial", "", "2026",
                  "ClinicalTrials.gov", "", accession="NCT06000001", url="https://clinicaltrials.gov/study/NCT06000001", ty="DBASE"),
             _rec("irr_reg_1", "Vitamin D in intensive care: a registered trial", "", "2025", "ClinicalTrials.gov", "",
                  relevant=False, accession="NCT05000002", ty="DBASE"),
             _rec("irr_reg_2", "Music therapy before surgery", "", "2024", "ClinicalTrials.gov", "", relevant=False,
                  accession="NCT05000003", ty="DBASE")]
    other = [dict(rel["inc_a"]),
             _rec("inc_other", "AI triage and hospital admission: a prospective cohort from Sweden", "Lindqvist, Karin", "2024",
                  "Scandinavian Journal of Trauma, Resuscitation and Emergency Medicine", "10.1186/sjtrem.2024.000016"),
             _rec("irr_oth_1", "Exercise for low back pain", "Garcia, Luis", "2019", "Spine", "10.1097/spine.2019.17", relevant=False),
             _rec("irr_oth_2", "Probiotics in infants", "Kim, Soo", "2020", "Pediatrics", "10.1542/peds.2020.18", relevant=False)]
    update = [dict(rel["inc_b"]),
              _rec("inc_update", "A prospective evaluation of AI triage in two Kenyan emergency departments", "Mbeki, Thandi; Otieno, Paul",
                   "2026", "African Journal of Emergency Medicine", "10.1016/afjem.2026.000019"),
              _rec("irr_upd_1", "Yoga for anxiety", "Rao, Priya", "2026", "Complementary Therapies", "10.1016/ct.2026.20", relevant=False)]
    return {"medline": medline, "embase": embase, "ctgov": ctgov, "other": other, "update": update}


STRATEGY = """1  exp Emergency Service, Hospital/
2  (emergency adj2 (department* or room* or unit*)).tw,kf.
3  1 or 2
4  exp Triage/ or triag*.tw,kf.
5  exp Artificial Intelligence/ or ("machine learning" or "deep learning" or "large language model*" or "natural language processing").tw,kf.
6  3 and 4 and 5
7  limit 6 to yr="2010 -Current"
8  7 not (animals/ not humans/)"""


def _ago(days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _write_ai(p: Project, recs: list, roles: dict) -> None:
    """The AI's answers for these records, written as ai.Runner writes them (chained and signed)."""
    keys = ai.current_keys(p)
    for ps in ("A", "B"):
        path = p.path("ai", f"answers_{ps}@{p.dev()}.jsonl")
        chain = integrity.last_hash(path)
        with open(path, "a", encoding="utf-8") as f:
            for r in recs:
                role = roles.get(r["id"], "")
                relevant = not role.startswith(("irr", "empty"))
                dec = "INCLUDE" if relevant else "EXCLUDE"
                if role == "irr_3" and ps == "B":
                    dec = "MAYBE"                  # one irrelevant record the AI keeps: a second look for the lead
                res = {"decision": dec, "criterion": "none" if relevant else "population", "reason": f"{AI_MARK} {ps} {role}",
                       "usage": {"prompt": 900, "completion": 20}, "served": keys[ps][0] + "-2026-01", "latency": 1.2}
                if role == "irr_5" and ps == "A":
                    res = {"decision": "ERROR", "criterion": "", "reason": f"{AI_MARK} Unreachable: no answer", "latency": 0}
                res.update({"id": r["id"], "rh": ai.record_hash(r), "pass": ps, "model": keys[ps][0], "prompt": keys[ps][1],
                            "settings": keys[ps][2], "when": util.now(), "engine": ai.ENGINE})
                res["t"] = res["when"]
                res[integrity.H] = integrity.row_hash(chain, res)
                res[integrity.K] = integrity.sign(res)
                chain = res[integrity.H]
                f.write(json.dumps(res, ensure_ascii=False) + "\n")


def _ai_run(p: Project, recs: list, roles: dict, label: str) -> None:
    keys = ai.current_keys(p)
    p.audit("CH", "AI run started", {"label": label, "records_checked": len(recs), "records_sent": len(recs),
                                     "calls": 2 * len(recs), "retries": 0,
                                     "models": {ps: keys[ps][0] for ps in "AB"}, "prompts": {ps: keys[ps][1] for ps in "AB"},
                                     "endpoint": "https://elm.edina.ac.uk/api/v1"})
    _write_ai(p, recs, roles)
    p.audit("CH", "AI run ended", {"label": label, "done": 2 * len(recs), "errors": 1, "stopped": False, "adaptations": {}})


def _screen_all(p: Project, roles: dict, special: dict) -> None:
    """Every open title-and-abstract task, for both people, until none is left (a second look appears only once the
    single reviewer has voted)."""
    for _ in range(6):
        did = False
        for who in ("NH", "CH"):
            for t in screening.tasks_for(p, who):
                if t["task"] == "fulltext" or not t["left"]:
                    continue
                stage = "cal" if t["task"] == "calibration" else "ta"
                for q in screening.queue(p, who, t["task"], t["set"]):
                    if q["done"]:
                        continue
                    role = roles.get(q["id"], "")
                    relevant = not role.startswith(("irr", "empty"))
                    d = "include" if relevant else "exclude"
                    if who == "NH" and q["id"] in special.get("nh_maybe", ()):
                        d = "maybe"
                    if role == "second_look" and t["task"] == "screen" and who == "NH":
                        d = "exclude"
                    if t["task"] == "second":
                        d = "include" if relevant else "exclude"
                    p.add_decision(who, q["id"], stage, d)
                    did = True
        if not did:
            return


FT = {  # role: (NH decision, NH reason, CH decision, CH reason, study, note)
    "inc_taylor_1": ("include", "", "include", "", "Taylor 2025", ""),
    "inc_taylor_2": ("include", "", "include", "", "Taylor 2025", ""),
    "inc_vs_exc": ("include", "", "exclude", "Wrong outcomes", "Okafor 2026", ""),
    "inc_other": ("include", "", "include", "", "Lindqvist 2024", ""),
    "inc_update": ("include", "", "include", "", "Mbeki 2026", ""),
    "exc_pop": ("exclude", "Wrong population", "exclude", "Wrong population", "", ""),
    "exc_design": ("exclude", "Wrong study design", "exclude", "Wrong study design", "", ""),
    "exc_outcome": ("exclude", "Wrong outcomes", "exclude", "Wrong outcomes", "", ""),
    "exc_reason_conflict": ("exclude", "Wrong population", "exclude", "Wrong study design", "", ""),
    "exc_lang": ("exclude", "Not in English", "exclude", "Not in English", "", ""),
    "awaiting": ("awaiting", "", "awaiting", "", "", "Authors e-mailed for the full results; no reply yet"),
    "ongoing": ("ongoing", "", "ongoing", "", "", "Registered; recruiting until 2027"),
    "ongoing_resolved": ("awaiting", "", "ongoing", "", "", ""),
    "not_retrieved": ("not_retrieved", "", "not_retrieved", "", "", "Not held by the library; inter-library loan failed"),
    "second_look": ("exclude", "Wrong outcomes", "exclude", "Wrong outcomes", "", ""),
}


# The protocol's risk-of-bias plan: RoB 2 for randomised trials (the cluster version for cluster trials), ROBINS-I V2 for
# studies with concurrent controls, ROBINS-I (2016) for uncontrolled before-after and interrupted time series studies,
# PROBAST+AI for accuracy, and no assessment for case series (with the reason).
PLAN = {"designs": [{"id": "rct", "name": "Randomised trials (individually randomised, parallel-group)", "tool": "rob2"},
                    {"id": "cluster_rct", "name": "Cluster-randomised trials", "tool": "rob2_cluster"},
                    {"id": "nrsi", "name": "Non-randomised studies of interventions, with a comparison group", "tool": "robins2"},
                    {"id": "before_after", "name": "Uncontrolled before-after studies", "tool": "robins_ba"},
                    {"id": "its", "name": "Interrupted time series", "tool": "robins_ba"},
                    {"id": "prediction", "name": "Prediction model studies (developing or validating a model, including AI "
                                                 "models)", "tool": "probast_ai"},
                    {"name": "Case series", "none": True, "reason": "Included for context only; not used in any synthesis"}],
        "tools": {}}


def build(folder: str) -> dict:
    p = Project.create(folder, TITLE, "Colin Hawthorne", "CH")
    p.update_settings("CH", {
        "reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
        "short_title": "AI triage review",
        "question": "In emergency departments, how do AI triage tools evaluated prospectively compare with usual triage?",
        "criteria": {"include": ["Prospective clinical evaluation of an AI triage tool", "Emergency department patients",
                                 "Reports time to triage, under-triage, over-triage or accuracy"],
                     "exclude": ["Retrospective or simulation-only studies", "Not in English"],
                     "rules": ["Conference abstracts with enough data are included"]},
        "screening": {"reviewers": ["NH", "CH"], "mode": "sample_then_single", "calibration_size": 6, "sample_fraction": 0.3,
                      "kappa_threshold": 0.5, "single_reviewer": "NH", "second_reviewer": "CH", "ai_second_screener": True,
                      "ai_keep_rate_stop": 0.9, "seed": 4242},
        "registers": [{"name": "ANZCTR", "records": 4}],
        "registration": {"registry": "PROSPERO", "id": "CRD420261234567", "date": _ago(60), "url": "https://www.crd.york.ac.uk/prospero/"},
        "known_studies": [{"title": RELEVANT[0][1]}, {"doi": "10.9999/not-in-the-records"}],
        "dates": {"update_search": _ago(-90)},
        # the protocol's rule for disagreements that remain (a new project leaves it for the lead to write)
        "fulltext": {"disagreement_default": "Discuss; if still not agreed, include the study and report the disagreement."},
    })
    robplan.save_plan(p, "CH", PLAN)
    R = records()
    log = {}
    for key, db, plat, n, extra in (
            ("medline", "MEDLINE", "Ovid", len(R["medline"]), {"coverage": "1946 to the present", "limits": "Humans; 2010 to the present"}),
            ("embase", "Embase", "Ovid", len(R["embase"]), {"coverage": "1974 to the present", "limits": "None"}),
            ("cinahl", "CINAHL", "", 57, {}),                                  # gaps in the log, and no export linked
            ("ctgov", "ClinicalTrials.gov", "clinicaltrials.gov", len(R["ctgov"]), {"limits": "None", "string": "triage AND (artificial intelligence OR machine learning)"}),
            ("other", "Citation searching (backward and forward)", "Web of Science", len(R["other"]), {"limits": "None", "string": "References of included studies, and studies citing them"}),
            ("update", "MEDLINE", "Ovid", len(R["update"]), {"coverage": "1946 to the present", "limits": "Humans; 2010 to the present"})):
        e = p.save_search("NH", {"database": db, "platform": plat, "date": _ago(3 if key == "update" else 40 + len(log)),
                                 "results": str(n), "string": extra.pop("string", STRATEGY), **extra})
        log[key] = e["id"]
    for key, name, db, set_ in (("medline", "medline.ris", "MEDLINE", "main"), ("embase", "embase.ris", "Embase", "main"),
                                ("ctgov", "ctgov.ris", "ClinicalTrials.gov", "main"), ("other", "citations.ris", "Citation searching", "other")):
        p.add_import("NH", name, _ris(R[key]), {"set": set_, "database": db, "platform": "", "search_date": _ago(40),
                                                "search_id": log[key], "notes": "", "part": ""})
    p.process_imports("CH")
    roles = {}

    def learn():
        by_title = {}
        for k, recs in R.items():
            for r in recs:
                if r.get("title"):
                    by_title.setdefault(" ".join(r["title"].split()).lower(), r["role"])
        for r in p.records():
            roles[r["id"]] = by_title.get(" ".join(r["title"].split()).lower(), "")
    learn()
    screening.start(p, "CH", "main")
    _ai_run(p, [r for r in p.records()], roles, "all records")
    sv = p.state_ro()["sets"]["main"]
    cal = sv["calibration_rounds"][0]
    cal_odd = next(r for r in cal if roles[r].startswith("irr"))
    sample_odd = next(r for r in sv["sample"] if roles[r].startswith("irr"))
    _screen_all(p, roles, {"nh_maybe": {cal_odd}})
    screening.finish_calibration(p, "CH", "main")
    _screen_all(p, roles, {"nh_maybe": {sample_odd}})
    screening.apply_sample_decision(p, "CH", "main")
    _screen_all(p, roles, {})
    screening.start(p, "CH", "other")
    _screen_all(p, roles, {})
    p.add_import("NH", "medline update.ris", _ris(R["update"]), {"set": "update", "database": "MEDLINE", "platform": "Ovid",
                                                                  "search_date": _ago(3), "search_id": log["update"], "notes": "", "part": ""})
    p.process_imports("CH")
    learn()
    have = ai.answers(p)["A"]
    _ai_run(p, [r for r in p.records() if r["id"] not in have], roles, "update search records")
    screening.start(p, "CH", "update")
    _screen_all(p, roles, {})
    # ------------------------------------------------------------------------------------------ full texts
    ids = {}
    for rid, role in roles.items():
        ids.setdefault(role, rid)
    for rid in sorted(screening.ft_outcomes(p)):
        role = roles.get(rid, "")
        nd, nr, cd, cr, study, note = FT.get(role) or (("include", "", "include", "", "", "") if not role.startswith("irr")
                                                       else ("exclude", "Wrong intervention", "exclude", "Wrong intervention", "", ""))
        p.add_decision("NH", rid, "ft", nd, nr, note, study if nd == "include" else "")
        p.add_decision("CH", rid, "ft", cd, cr, note, study if cd == "include" else "")
    p.add_resolution("CH", ids["inc_vs_exc"], "ft", "include", "", "Time to triage is reported in the supplement", "Okafor 2026")
    p.add_resolution("CH", ids["exc_reason_conflict"], "ft", "exclude", "Wrong study design", "Simulation only")
    p.add_resolution("CH", ids["ongoing_resolved"], "ft", "ongoing", "", "Protocol only; results expected in 2027")
    # ------------------------------------------------------------------------------------------ extraction
    f = review.form(p)
    review.save_form(p, "CH", {**{k: v for k, v in f.items() if k in ("name", "sections")}, "template": "generic", "first": "NH", "second": "CH"})
    key = {s["label"]: s["key"] for s in review.studies(p)}
    of = {ids[r]: review.study_of(p).get(ids[r]) for r in ("inc_a", "inc_b", "inc_tricky", "inc_taylor_1", "inc_vs_exc")}
    k_taylor, k_a, k_b, k_tricky, k_okafor = (of[ids[r]] for r in ("inc_taylor_1", "inc_a", "inc_b", "inc_tricky", "inc_vs_exc"))
    outcome = lambda eff: [{"outcome": "Time to triage", "measure": "Minutes, median", "group": "AI", "n": "200", "result": "11",
                            "effect": eff, "where": "Table 2"}]

    def extract(k, country="United Kingdom", design="Cohort study", eff="-4 min (-6 to -2)", n="400", setting="Three emergency departments"):
        for fid, v in (("author_year", "as reported"), ("country", country), ("design", design), ("setting", setting), ("n", n),
                       ("population", "Adults attending the emergency department"), ("intervention", "AI triage tool"),
                       ("comparator", "Usual nurse triage"), ("funding", "Public funding; no conflicts declared"), ("notes", "")):
            review.save_value(p, "NH", k, fid, v, "p. 3")
        review.save_value(p, "NH", k, "outcomes", outcome(eff))

    def check(k, eff="-4 min (-6 to -2)", corrections=None):
        for fid, v in (corrections or {}).items():
            review.save_value(p, "CH", k, fid, v, "p. 4")
        review.save_value(p, "CH", k, "outcomes", outcome(eff))
        fa = review.answers(p)["values"]["NH"][k]
        review.set_done(p, "CH", k, True, review.checked_sig(review.form(p), fa))

    extract(k_taylor, design="Cluster randomised trial", n="1250")
    review.set_done(p, "NH", k_taylor, True)
    check(k_taylor)
    extract(k_a, setting="Values < 5 & > 2 were excluded")
    review.set_done(p, "NH", k_a, True)
    check(k_a, eff="-5 min (-7 to -3)")
    review.agree_value(p, "CH", k_a, "outcomes", outcome("-5 min (-7 to -3)"))
    extract(k_b, design="Interrupted time series")
    review.set_done(p, "NH", k_b, True)
    check(k_b, corrections={"country": "Ireland"})                    # left for the lead to settle
    extract(k_okafor, design="Cohort study")
    review.set_done(p, "NH", k_okafor, True)
    check(k_okafor)
    extract(k_tricky, setting=TRICKY)
    review.set_done(p, "NH", k_tricky, True)                           # the second extractor has not checked it yet
    # ------------------------------------------------------------------------------------------ risk of bias
    def assess(k, design, label, nh, ch=None, finish_ch=True):
        review.define_assessment(p, "NH", k, design, label)
        aid = next(a for (s, a), d in review.rob_rows(p)["defs"].items() if s == k and d["label"] == label)
        t = review.tool_def(p, review.rob_rows(p)["defs"][(k, aid)]["tool"])
        for who, js in (("NH", nh), ("CH", ch if ch is not None else nh)):
            if who == "CH" and not finish_ch:
                break
            for code, _ in t["domains"]:
                review.judge(p, who, k, aid, code, js.get(code, review.scale_for(t, code)[0]), f"Support for {code} <&>")
            review.rob_done(p, who, k, aid, True)
        return aid
    aid = assess(k_taylor, "cluster_rct", "Time to triage", {"D2": "Some concerns", "O": "Some concerns"},
                 {"D2": "Low risk", "O": "Some concerns"})
    review.rob_agree(p, "CH", k_taylor, aid, "D2", "Some concerns")
    assess(k_a, "nrsi", "Under-triage", {"D1": "Serious risk", "O": "Serious risk"})
    assess(k_b, "its", "Time to triage (interrupted time series)", {"D1": "Moderate risk", "O": "Moderate risk"})
    assess(k_okafor, "prediction", "Accuracy against expert triage", {"D4": "High", "O": "High"})
    assess(k_tricky, "rct", "Time to triage", {}, finish_ch=False)
    return {"p": p, "ids": ids, "roles": roles, "key": {"taylor": k_taylor, "a": k_a, "b": k_b, "tricky": k_tricky, "okafor": k_okafor},
            "folder": folder}


def fake_profile(p, me) -> dict:
    """A GRADE evidence profile in the shape certainty.evidence_profile gives (for testing the Summary of findings)."""
    def it(f, name, rating, change, x=""):
        return {"id": f, "name": name, "rating": rating, "change": change, "explanation": x}
    dom = lambda r1, c1, e1: [it("rob", "Risk of bias", r1, c1, e1), it("inconsistency", "Inconsistency", "Not serious", 0),
                              it("indirectness", "Indirectness", "Not serious", 0),
                              it("imprecision", "Imprecision", "Serious", -1, "Few events <10 & wide CI"),
                              it("publication", "Publication bias", "Undetected", 0)]
    base = {"kind": "Effect of an intervention", "kind_id": "effect", "comparison": "AI-supported triage compared with usual nurse triage",
            "synthesis": "narrative", "synthesis_text": "Narrative synthesis", "importance_id": "critical", "both_done": True,
            "statement_check": {"ok": True, "why": "", "suggestion": "", "kept": False, "reason": ""}, "statement_mismatch": False,
            "cautions": [], "upgrade_justification": "", "problems": [], "meaning": ""}

    def foot(o):
        o["footnotes"] = [{"item": x["name"], "rating": x["rating"], "text": x["explanation"]}
                          for x in o["domains"] + o["upgrades"] if x["explanation"]]
        return o
    return {"method_text": "The certainty of the evidence for each main outcome was rated with GRADE.\n\nRatings were "
                           "narrative where results were not pooled (Murad et al. 2017).",
            "outcomes": [
                foot(dict(base, name="Time to triage", importance="Critical", measure="Minutes from arrival to triage", studies=3,
                     participants="1,850", design="1 randomised, 2 non-randomised", start="High",
                     domains=dom("Serious", -1, "Two studies at serious risk"),
                     upgrades=[it("large_effect", "Large effect", "No", 0)], certainty="Low", symbols="⊕⊕◯◯", level=2,
                     effect="4 minutes shorter (2 to 6 shorter)", statement="AI triage may shorten time to triage.", complete=True)),
                foot(dict(base, name="Under-triage", importance="Critical", measure="Proportion under-triaged", studies=2,
                     participants="900", design="non-randomised", start="Low", domains=dom("Not serious", 0, ""),
                     upgrades=[it("large_effect", "Large effect", "Upgraded one level", 1, "RR 0.4")], certainty="Moderate",
                     symbols="⊕⊕⊕◯", level=3, effect="RR 0.58 (0.40 to 0.83)", statement="AI triage may reduce under-triage.",
                     statement_mismatch=True, statement_check={
                         "ok": False, "why": "Moderate certainty is worded with 'probably' or 'likely'; this says 'may'.",
                         "suggestion": "AI triage probably reduces under-triage.", "kept": False, "reason": ""},
                     complete=False))]}


GRADE_INTERVENTION = "AI-supported triage compared with usual nurse triage"
GRADE_TEST = "The AI triage tool against expert triage"


def grade(info: dict) -> dict:
    """GRADE ratings for the review that build() made, done as the team does them: the lead defines each outcome, both
    reviewers rate it independently and finish it, and the lead agrees what is left. For tools/check_reports.py (the
    Summary of findings and evidence profile, for both people) and the GRADE tests. Called after build(), separately,
    so that build() itself (and every test that uses it) is unchanged. Every outcome ends finished and agreed:

    - Time to triage: an intervention outcome from the randomised trial (low certainty);
    - a test: the AI triage tool against expert triage, with its accuracy figures (a pre-test probability of 20% from
      the included studies and 40% from an audit, pooled sensitivity and specificity) and one outcome each for true
      positives, false negatives, true negatives and false positives (GRADE guidance 21), worded with the suggested
      natural-frequency statements;
    - Under-triage: a statement worded for moderate certainty at low certainty, kept with the lead's reason;
    - Over-triage: both reviewers rated imprecision as no serious concerns, and the lead changed it, with the reason.

    Returns {outcome name: outcome ID}."""
    from reviewdesk import certainty
    p, key = info["p"], info["key"]

    def define(name, importance, kind, studies, comparison, measure="", result=""):
        return certainty.define(p, "CH", {"name": name, "importance": importance, "kind": kind, "comparison": comparison,
                                          "measure": measure, "synthesis": "narrative", "result": result,
                                          "studies": [key[s] for s in studies]})["o"]

    def rate(oid, start, downs, effect, statement):
        """Both reviewers give the same ratings (downs: {reason to rate down: (rating, explanation)}) and finish."""
        for who in ("NH", "CH"):
            certainty.rate(p, who, oid, "start", start)
            for f in certainty.DOMAIN_IDS:
                v, x = downs.get(f, (0, ""))
                certainty.rate(p, who, oid, f, v, x)
            certainty.rate(p, who, oid, "effect", effect)
            certainty.rate(p, who, oid, "statement", statement)
            certainty.set_done(p, who, oid, True)

    ids = {}
    # an intervention outcome, from the randomised trial: 4 − 1 − 1, low
    name = "Time to triage"
    ids[name] = define(name, "critical", "effect", ["taylor"], GRADE_INTERVENTION, "Minutes from arrival to triage")
    rate(ids[name], "rct", {"rob": (-1, "Some concerns about deviations from the intended triage in the one trial"),
                            "imprecision": (-1, "One trial; the interval runs from 6 minutes shorter to no difference")},
         "1 trial, 1,250 patients; median 4 minutes shorter (2 to 6 shorter)",
         "AI-supported triage may reduce time to triage compared with usual nurse triage.")
    # a test, with its figures, and one outcome for each kind of result (GRADE guidance 21)
    results = [("True positives (urgent patients the tool identifies)", "critical", "tp"),
               ("False negatives (urgent patients the tool misses)", "critical", "fn"),
               ("True negatives (other patients the tool correctly leaves)", "important", "tn"),
               ("False positives (other patients the tool flags as urgent)", "important", "fp")]
    for name, importance, result in results:
        ids[name] = define(name, importance, "accuracy", ["okafor"], GRADE_TEST, "Per 1,000 patients tested", result)
    certainty.set_accuracy(p, "CH", {"comparison": GRADE_TEST, "condition": "high acuity (triage category 1 or 2)",
                                     "threshold": "Triage category 1 or 2 by the tool", "mode": "pooled", "interval": "ci",
                                     "prev": [{"p": "20", "source": "the median of the included studies"},
                                              {"p": "40", "source": "a regional audit of emergency departments"}],
                                     "sens": {"est": "0.86", "lo": "0.79", "hi": "0.91"},
                                     "spec": {"est": "0.81", "lo": "0.76", "hi": "0.85"}})
    for name, _, result in results:
        downs = {"rob": (-1, "High risk of bias in the analysis domain (PROBAST+AI)")}
        if result == "fn":                       # few urgent patients were missed: the interval is wide
            downs["imprecision"] = (-1, "Few false negatives; the interval would change how the tool is used")
        level = certainty.compute("dta", {f: downs.get(f, (0,))[0] for f in certainty.DOMAIN_IDS}, {})["level"]
        d = {**dict(certainty.outcomes(p))[ids[name]], "_acc": certainty.accuracy_figures(p, {"comparison": GRADE_TEST})}
        rate(ids[name], "dta", downs, "1 study, 400 patients; sensitivity 0.86 and specificity 0.81 against expert triage",
             certainty.statements(d, level)[0]["text"])
    # a statement worded for another certainty, kept with the lead's reason
    name = "Under-triage"
    ids[name] = define(name, "critical", "effect", ["a"], GRADE_INTERVENTION, "Proportion of patients under-triaged")
    rate(ids[name], "nrs_robins", {"rob": (-2, "Serious risk of bias from confounding (ROBINS-I)")},
         "1 study, 400 patients; under-triage 5.1% with the tool and 8.4% without",
         "AI-supported triage probably reduces under-triage compared with usual nurse triage.")
    certainty.keep_statement(p, "CH", ids[name], "The registered protocol words a single large cohort's finding this "
                                                  "way; the authors keep its wording and report the difference")
    # a rating both reviewers gave identically, changed by the lead with the reason
    name = "Over-triage"
    ids[name] = define(name, "important", "effect", ["b"], GRADE_INTERVENTION, "Proportion of patients over-triaged")
    rate(ids[name], "nrs_robins", {"rob": (-2, "Serious risk of bias: no comparison group in the time series"),
                                   "inconsistency": (-1, "Over-triage rose in one period and fell in another")},
         "1 interrupted time series, 400 patients; no consistent change in over-triage",
         "The evidence is very uncertain about the effect of AI-supported triage on over-triage compared with usual "
         "nurse triage.")
    certainty.agree(p, "CH", ids[name], "imprecision", -1, "Fewer than 30 over-triage events in the one study",
                    why="Both reviewers missed how few events the one study recorded")
    return ids


def build_open_sample(folder: str) -> dict:
    """The same review stopped part-way through the agreement sample: the AI has screened every record, NH has
    voted on every sample record and CH on none. Nothing may yet show NH's votes or the AI's answers."""
    p = Project.create(folder, TITLE, "Colin Hawthorne", "CH")
    p.update_settings("CH", {
        "reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
        "question": "In emergency departments, how do AI triage tools evaluated prospectively compare with usual triage?",
        "criteria": {"include": ["Prospective clinical evaluation of an AI triage tool"], "exclude": ["Not in English"], "rules": []},
        "screening": {"reviewers": ["NH", "CH"], "mode": "sample_then_single", "calibration_size": 0, "sample_fraction": 0.3,
                      "kappa_threshold": 0.5, "single_reviewer": "NH", "second_reviewer": "CH", "ai_second_screener": True, "seed": 99}})
    robplan.save_plan(p, "CH", PLAN)
    R = records()
    e = p.save_search("NH", {"database": "MEDLINE", "platform": "Ovid", "date": _ago(5), "results": str(len(R["medline"])), "string": STRATEGY})
    p.add_import("NH", "medline.ris", _ris(R["medline"]), {"set": "main", "database": "MEDLINE", "platform": "Ovid", "search_date": _ago(5),
                                                          "search_id": e["id"], "notes": "", "part": ""})
    p.process_imports("CH")
    by_title = {" ".join(r["title"].split()).lower(): r["role"] for r in R["medline"]}
    roles = {r["id"]: by_title.get(" ".join(r["title"].split()).lower(), "") for r in p.records()}
    screening.start(p, "CH", "main")
    _ai_run(p, p.records(), roles, "all records")
    for rid in p.state_ro()["sets"]["main"]["sample"]:
        p.add_decision("NH", rid, "ta", "exclude" if roles[rid].startswith("irr") else "include")
    return {"p": p, "roles": roles, "folder": folder}
