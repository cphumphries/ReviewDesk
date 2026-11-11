"""The whole reference review, end to end, through the HTTP API, with realistic exports from all five databases, the
update search and other methods: CH (the lead) and NH (the reviewer) each on their own computer, each running their own
ReviewDesk on the shared project folder, and the AI second screener answered by the stand-in.

Searches are logged and their exports added; records are de-duplicated (and the pairs ReviewDesk lists are checked by
hand); CH starts screening; both screen the calibration round and the agreement sample; the pre-specified rule is
applied; NH screens the rest with the AI behind her, and CH takes the second looks; the update search and other methods
are added and screened; full texts are added (PDFs in bulk, by record ID and by DOI) and assessed by both; data are
extracted and checked; risk of bias is judged with each tool and agreed; the PRISMA diagram and every download are made.

At the end the PRISMA numbers and the included studies are compared with the truth the generator knows about every
record, allowing exactly for the slips the simulated reviewers are known to have made (worked out here, independently
of ReviewDesk, from the votes they cast and the answers the AI gave)."""
from __future__ import annotations

import os
from collections import Counter

import pytest

import realistic
from helpers import (ServerProcess, SimReviewer, TruthMap, check_svg, check_xlsx, formula_cells, log_and_import, read_csv, read_json,
                     reference_plan, reference_project)
from mock_elm import MODEL_A, MODEL_B, MockELM, TEST_KEY
from reviewdesk import review as rv
from reviewdesk.parsers import parse_bytes

FWD = ("include", "maybe")


class Run:
    """Everything that happened, for the checks."""


def last_votes(sim: SimReviewer, stage: str) -> dict:
    out = {}
    for st, rid, d, _ in sim.log:
        if st == stage:
            out[rid] = d
    return out


def run_reference_review(tmp, scale: str = "normal", seed: int = 2026, order_seed: int | None = 20261104) -> Run:
    """The whole review. seed: the generator's; order_seed: the screening plan's seed for the random order (None: a new
    one, as a new project gets). The two ReviewDesks and the AI stand-in are stopped if anything fails."""
    R = Run()
    R.servers, R.elm = [], None
    try:
        return _run(R, tmp, scale, seed, order_seed)
    except BaseException:
        stop(R)
        raise


def stop(R: Run) -> None:
    for s in R.servers:
        s.stop()
    if R.elm:
        R.elm.stop()


def _run(R: Run, tmp, scale: str, seed: int, order_seed: int | None) -> Run:
    R.review = review = realistic.Review(seed, scale)
    folder_parent = str(tmp / "OneDrive - UoE")
    R.elm = elm = MockELM(decide=review.ai_decider()).start()
    R.servers.append(ServerProcess(str(tmp / "home-CH")))
    R.servers.append(ServerProcess(str(tmp / "home-NH")))
    ch, nh = R.servers[0].client("CH"), R.servers[1].client("NH")
    R.ch, R.nh = ch, nh
    # ----------------------------------------------------------------------------------------------- set-up
    eligible_articles = [w for w in review.works if w.kind == "article" and w.ft == "include" and w.set == "main" and w.doi][:3]
    plan = reference_plan(**({"seed": order_seed} if order_seed else {}))
    folder = reference_project(ch, folder_parent, screening=plan,
                               known_studies=[{"title": w.title, "doi": w.doi, "pmid": ""} for w in eligible_articles])
    R.folder = folder
    ch.post("/api/user", {"elm_url": elm.url, "elm_key": TEST_KEY, "my_name": "Colin Hawthorne"})
    nh.open_project(folder)
    nh.be("NH")
    # ----------------------------------------------------------------------------------------------- searches and imports
    R.imports = log_and_import(ch, review, sets=("main",))
    assert not any(e.get("mismatch") for e in ch.get("/api/searchlog")["entries"])
    R.process_main = ch.process()["summary"]
    truth = TruthMap(review, folder)
    R.truth = truth
    R.hand_merged, R.kept_apart = merge_by_hand(ch, truth)
    truth.refresh()
    # ----------------------------------------------------------------------------------------------- screening the main search
    ch.post("/api/screening/start", {"set": "main"})
    sv = read_json(os.path.join(folder, "state", "state.json"))["sets"]["main"]
    R.seed = sv["seed"]
    R.order, R.sample = sv["order"], set(sv["sample"])
    R.calibration = set(sv["calibration_rounds"][0])
    elm.fail("unreadable", when=lambda call: call.model == MODEL_B and call.title in R.unreadable)
    R.unreadable = {truth.by_id[r]["title"] for r in R.order[5:7]}          # two records the AI cannot answer: kept
    st = ch.run_ai(wait=600)
    assert st["message"] == "Finished", st
    sims = {"NH": SimReviewer(nh, "NH", truth, realistic.Errors("NH", seed, {"cal": 0.06, "ta": 0.03, "ft": 0.03}),
                              max_wrong={"sample": 1}),
            "CH": SimReviewer(ch, "CH", truth, realistic.Errors("CH", seed, {"cal": 0.04, "ta": 0.0, "ft": 0.02}))}
    R.sims = sims
    for who in ("NH", "CH"):
        sims[who].screen("calibration")
    ch.post("/api/screening/calibration", {"set": "main"})
    sims["NH"].screen("sample")
    R.blind_mid_sample = nh.get("/api/agreement", scope="sample")       # NH has finished, CH has not
    sims["CH"].screen("sample")
    R.agreement = ch.get("/api/agreement", scope="sample")
    R.applied = ch.post("/api/screening/apply", {"set": "main"})["sets"]["main"]
    if R.applied["remainder_mode"] == "single":
        sims["NH"].screen("screen")
        sims["CH"].screen("second")
    else:
        for who in ("NH", "CH"):
            sims[who].screen("screen")
    # ----------------------------------------------------------------------------------------------- the update search
    R.imports.update(log_and_import(ch, review, sets=("update",)))
    R.process_update = ch.process()["summary"]
    truth.refresh()
    st = ch.run_ai(wait=600)
    assert st["message"] == "Finished", st
    ch.post("/api/screening/start", {"set": "update"})
    if R.applied["remainder_mode"] == "single":
        sims["NH"].screen("screen", "update")
        sims["CH"].screen("second", "update")
    else:
        for who in ("NH", "CH"):
            sims[who].screen("screen", "update")
    # ----------------------------------------------------------------------------------------------- other methods (NH adds them)
    R.imports.update(log_and_import(nh, review, sets=("other",)))
    ch.process()
    truth.refresh()
    ch.post("/api/screening/start", {"set": "other"})
    for who in ("NH", "CH"):
        sims[who].screen("screen", "other")
    # ----------------------------------------------------------------------------------------------- full texts
    R.pdfs = add_pdfs(ch, nh, truth)
    for who in ("NH", "CH"):
        sims[who].fulltext()
    R.resolved = {}
    for x in ch.get("/api/conflicts")["conflicts"]:
        assert x["stage"] == "ft", x                          # "forward": no disagreements to discuss at title and abstract
        d, reason, study = SimReviewer.right_ft(truth.of(x["id"]))
        ch.resolve(x["id"], "ft", d, reason=reason, study=study, note="Settled together at the weekly meeting")
        R.resolved[x["id"]] = d
    # ----------------------------------------------------------------------------------------------- extraction and risk of bias
    ch.post("/api/extract/form", {"template": "generic", "first": "NH", "second": "CH"})
    R.studies = ch.get("/api/extract")["studies"]
    extract_and_appraise(R, ch, nh, truth)
    # ----------------------------------------------------------------------------------------------- the reports
    R.prisma = ch.get("/api/prisma")
    R.prisma_nh = nh.get("/api/prisma")
    R.downloads = {k: ch.download(k) for k in ("records", "decisions", "included", "excluded", "searchlog", "ai", "audit", "prisma",
                                               "summary", "dedup", "missing_ris", "missing_csv", "extraction", "rob", "rob_svg")}
    R.refused_nh = {k: nh.download(k) for k in ("records", "decisions", "ai")}
    R.integrity = {"CH": ch.get("/api/integrity")["report"], "NH": nh.get("/api/integrity")["report"]}
    R.ai_view = ch.get("/api/ai")
    R.ai_view_nh = nh.get("/api/ai")
    truth.refresh()
    R.records = {r["id"]: r for r in truth.records}
    return R


def merge_by_hand(ch, truth: TruthMap) -> tuple:
    """The lead checks each pair ReviewDesk lists: the same report is merged, different reports are kept apart."""
    same, differ = [], []
    for x in ch.get("/api/dedup")["possible"]:
        a, b = truth.of(x["a"]["id"]), truth.of(x["b"]["id"])
        (same if a.wid == b.wid else differ).append((x["a"]["id"], x["b"]["id"]))
    done = set()
    for a, b in same:
        if b not in done and a not in done:
            ch.post("/api/dedup/merge", {"keep": a, "drop": b})
            done.add(b)
    if differ:
        ch.post("/api/dedup/notdup", {"pairs": [list(p) for p in differ]})
    return same, differ


def add_pdfs(ch, nh, truth: TruthMap) -> dict:
    """Most PDFs in bulk with the record ID in the file name (as saved from the 'PDFs still to find' list); every fourth
    as downloaded from the publisher (found by its DOI and title); NH adds some. None for reports that cannot be found."""
    out = Counter()
    rows = ch.get("/api/fulltext")["rows"]
    for k, row in enumerate(rows):
        w = truth.of(row["id"])
        if w.ft == "not_retrieved":
            continue
        c = nh if k % 3 == 0 else ch
        data = realistic.pdf_for(w, pages=9)
        if k % 4 == 1 and w.doi and row["doi"] == w.doi:
            r = c.upload("/api/fulltext/bulk", data, filename=f"{w.first_author} {w.year} - full text.pdf")
        else:
            r = c.upload("/api/fulltext/bulk", data, filename=f"{row['id']} {w.first_author} {w.year}.pdf")
        out[r["result"]] += 1
        if r["result"] == "waiting":
            c.post("/api/fulltext/assign", {"name": r["name"], "record": row["id"]})
        elif r["result"] == "already":                         # one PDF holding two reports of a study
            c.upload("/api/fulltext/upload", data + b"\n% second copy", record=row["id"])
        else:
            assert r["result"] == "placed" and r["record"] == row["id"], r
    return out


def extract_and_appraise(R, ch, nh, truth: TruthMap) -> None:
    """NH extracts every study and CH checks it; both extract the outcome data (they differ on one result in every third
    study); both judge risk of bias with the tool the study's design calls for (CH more severely on the first domain in
    every second study); CH records what was agreed."""
    R.extract_differs, R.rob_differs = [], []
    first, second = R.sims["NH"], R.sims["CH"]
    for k, s in enumerate(R.studies):
        key = s["key"]
        w = truth.of(key)
        n = str(200 + 37 * k)
        table = [{"outcome": "Time to triage", "measure": "median minutes", "group": "AI", "n": n, "result": "4", "effect": "", "where": "Table 2"},
                 {"outcome": "Under-triage", "measure": "%", "group": "AI", "n": n, "result": "5.1", "effect": "RR 0.61 (0.45 to 0.83)", "where": "Table 3"}]
        first.extract(key, {"author_year": s["label"], "country": getattr(w, "country", "") or "Not reported", "design": w.design or "Other",
                            "n": n, "setting": "Emergency department", "population": "Adults presenting for triage",
                            "intervention": "AI triage support", "comparator": "Usual nurse triage", "outcomes": table}, where="p. 4")
        theirs = [dict(table[0]), dict(table[1], result="5.4")] if k % 3 == 0 else table
        if k % 3 == 0:
            R.extract_differs.append(key)
        out = second.check(key, answers={"outcomes": theirs})
        for c in out["state"]["comparison"]:
            if c["status"] in ("differs", "changed"):
                ch.post("/api/extract/agree", {"key": key, "f": c["f"], "v": c["a"]})
        design = getattr(w, "rob_design", "") or "before_after"
        made = ch.post("/api/rob/define", {"key": key, "design": design, "label": "Under-triage, AI versus usual triage"})["rob"][0]
        aid, tool = made["a"], made["tool"]
        t = rv.TOOLS[tool]
        sc = lambda code: rv.scale_for(t, code)                          # noqa: E731 - each domain's own scale
        first.judge(key, aid, tool, lambda code: sc(code)[1] if code == "O" else sc(code)[0], "NH: see the methods section")
        second.judge(key, aid, tool, lambda code: sc(code)[1] if code == "O" or (code == "D1" and k % 2 == 0) else sc(code)[0],
                     "CH: see the methods section")
        view = ch.get("/api/extract/study", key=key)["rob"][0]
        for d in view["domains"]:
            if d.get("a") != d.get("b"):
                ch.post("/api/rob/agree", {"key": key, "a": aid, "d": d["d"], "j": d["severer"]})
                R.rob_differs.append((key, d["d"]))


def expected(R) -> dict:
    """What the PRISMA numbers must be, worked out from the truth, the votes the reviewers cast and the AI's answers,
    without ReviewDesk."""
    t, nh, ch = R.truth, R.sims["NH"], R.sims["CH"]
    ta_nh, ta_ch = last_votes(nh, "ta"), last_votes(ch, "ta")
    ai = {}
    for call in R.elm.calls:          # the AI's last answer for each record (as sent: two reports can share a title) and model
        if call.decision in ("INCLUDE", "MAYBE", "EXCLUDE") or call.decision == "unreadable":
            ai[(call.title, call.pubtype, call.model)] = call.decision

    def keep(rid):
        r = t.by_id[rid]
        sent = (r["title"], r.get("pubtype") or "not given")
        return not (ai.get(sent + (MODEL_A,)) == "EXCLUDE" and ai.get(sent + (MODEL_B,)) == "EXCLUDE")
    single = R.applied["remainder_mode"] == "single"
    fwd = {}
    for rid, r in t.by_id.items():
        if r["set"] == "other" or rid in R.sample or not single:
            fwd[rid] = ta_nh.get(rid) in FWD or ta_ch.get(rid) in FWD
        else:
            fwd[rid] = ta_nh.get(rid) in FWD or (keep(rid) and ta_ch.get(rid) in FWD)
    ft_nh, ft_ch = last_votes(nh, "ft"), last_votes(ch, "ft")
    ft = {}
    for rid, f in fwd.items():
        if not f:
            continue
        a, b = ft_nh[rid], ft_ch[rid]
        if a[0] == b[0] and (a[0] != "exclude" or a[1] == b[1]):
            ft[rid] = a
        else:
            ft[rid] = SimReviewer.right_ft(t.of(rid))
    out = {}
    for col, sets in (("db", ("main", "update")), ("other", ("other",))):
        ids = [rid for rid, r in t.by_id.items() if r["set"] in sets]
        f = [rid for rid in ids if fwd[rid]]
        out[col] = {"records": len(ids), "screened": len(ids), "ta_excluded": len(ids) - len(f), "sought": len(f),
                    "not_retrieved": sum(1 for r in f if ft[r][0] == "not_retrieved"),
                    "assessed": sum(1 for r in f if ft[r][0] in ("include", "exclude", "awaiting")),
                    "ft_excluded": sum(1 for r in f if ft[r][0] == "exclude"), "ft_included": sum(1 for r in f if ft[r][0] == "include"),
                    "awaiting": sum(1 for r in f if ft[r][0] == "awaiting"),
                    "ft_reasons": dict(Counter(ft[r][1] for r in f if ft[r][0] == "exclude")), "pending_ta": 0, "pending_ft": 0}
    inc = [r for r, v in ft.items() if v[0] == "include"]
    out["included_reports"] = len(inc)
    out["included_studies"] = sorted({ft[r][2] for r in inc})
    out["fwd"], out["ft"], out["keep"] = fwd, ft, keep
    # an eligible study is lost only when every one of its reports was excluded by a person's slip: at title and abstract
    # (NH excluded it and the AI would not keep it, or the second look excluded it too) or by both at full text
    included = {ft[r][2] for r in inc}
    reports: dict = {}
    for rid in t.by_id:
        w = t.of(rid)
        if w.ft == "include":
            reports.setdefault(w.study_label, []).append(rid)
    out["lost"] = sorted(label for label in reports if label not in included)
    out["lost_why"] = {label: [("title and abstract" if not fwd[r] else f"full text: {ft[r]}") for r in reports[label]]
                       for label in out["lost"]}
    return out


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    R = run_reference_review(tmp_path_factory.mktemp("e2e"), "normal")
    yield R
    stop(R)


def test_every_export_was_read_and_logged_as_its_database_wrote_it(reference):
    R = reference
    log = {e["database"]: e for e in R.ch.get("/api/searchlog")["entries"] if e.get("notes") != "Update search"}
    assert {"MEDLINE", "Embase", "CINAHL", "Scopus", "CENTRAL (Cochrane Library)", "Citation searching", "NEJM AI"} <= set(log)
    imps = {m["id"]: m for m in R.ch.get("/api/imports")["imports"]}
    for iid, e in R.imports.items():
        m = imps[iid]
        assert m["parse"]["records"] == e.records and m["processed"] and not m["missing"]
    assert {imps[i]["parse"]["encoding"] for i in imps} >= {"utf-8", "cp1252"}
    main_records = sum(e.records for e in R.review.files("main"))
    assert 380 <= main_records <= 520                       # about 400 records from the five databases
    assert {imps[i]["added_by"] for i in imps} == {"CH", "NH"}


def test_duplicates_were_removed_by_the_rule_and_by_hand_without_losing_a_report(reference):
    R = reference
    t = R.truth
    works = Counter(w.wid for w in t.work.values())
    assert max(works.values()) == 1                          # every report is one record
    for rid in t.by_id:
        assert t.of(rid) is not None
    unique = {w for e in R.review.exports for w in e.wids if w}
    assert len(t.by_id) == len(unique)
    assert R.kept_apart                                      # the look-alikes (two-word surnames now merge by the rule)
    dd = R.ch.get("/api/dedup")
    for mg in dd["merges"]:
        if mg.get("from_import"):
            assert t.of(mg["kept"]).wid == R.imports[mg["from_import"]].wids[mg["from_index"]]


def test_screening_followed_the_registered_plan(reference):
    R = reference
    assert len(R.calibration) == 100 and not R.calibration & R.sample
    assert len(R.sample) == round(len(R.order) * 0.2)
    assert R.blind_mid_sample["stats"] == {} and R.blind_mid_sample["disagreements"] == []
    sr = R.applied["sample_result"]
    nh, ch = last_votes(R.sims["NH"], "ta"), last_votes(R.sims["CH"], "ta")
    from reviewdesk import stats
    want = stats.kappa(stats.two_by_two([(nh[r] in FWD, ch[r] in FWD) for r in R.sample]))
    assert sr["kappa"] == pytest.approx(want["kappa"]) and sr["agreement"] == pytest.approx(want["agreement"])
    # the seed is chosen so that the sample reaches the threshold and the AI second screener is used
    assert sr["kappa"] >= 0.8 and R.applied["remainder_mode"] == "single" and R.applied["ai_net_active"]
    assert sr["ai_rule_kind"] == "net" and sr["ai"]["keep_rate"] <= 0.5
    assert R.ai_view["validation"]["failed"] == 2 and R.ai_view["visible"] and not R.ai_view_nh["visible"]
    assert all(k["record"] and k["A"] in ("INCLUDE", "MAYBE") for k in R.ai_view["known"])


def test_the_ai_never_excluded_a_record(reference):
    R = reference
    exp = expected(R)
    nh, ch = last_votes(R.sims["NH"], "ta"), last_votes(R.sims["CH"], "ta")
    for rid, f in exp["fwd"].items():
        if not f:
            assert nh.get(rid) == "exclude" or ch.get(rid) == "exclude"
        if nh.get(rid) in FWD:
            assert f
    second = {rid for st, rid, _, _ in R.sims["CH"].log if st == "ta" and rid not in R.sample and R.truth.by_id[rid]["set"] != "other"}
    assert second == {rid for rid in exp["fwd"] if R.truth.by_id[rid]["set"] != "other" and rid not in R.sample
                      and nh.get(rid) == "exclude" and exp["keep"](rid)}


def test_the_prisma_numbers_match_the_truth_allowing_for_the_known_slips(reference):
    R = reference
    exp = expected(R)
    C = R.prisma["counts"]
    assert C["checks"] == [] and C["complete"]
    for col in ("db", "other"):
        for k, v in exp[col].items():
            assert C[col][k] == v, (col, k, C[col][k], v, "screening seed", R.seed)
    assert C["included_reports"] == exp["included_reports"]
    assert C["included_studies"] == len(exp["included_studies"])
    per_db = Counter()
    for e in R.review.exports:
        if e.set in ("main", "update"):
            per_db[e.database] += e.records
    assert C["db"]["databases"] == dict(per_db)
    assert C["db"]["identified_databases"] - C["db"]["duplicates"] - C["db"]["other_removed"] == C["db"]["records"]


def test_the_included_studies_are_the_eligible_ones_except_those_lost_to_known_slips(reference):
    R = reference
    exp = expected(R)
    truth = R.review.eligible_studies()
    got = sorted(s["label"] for s in R.studies)
    assert got == exp["included_studies"]
    assert sorted(truth - set(got)) == exp["lost"], exp["lost_why"]     # every eligible study missed is explained by slips
    nh, ch = last_votes(R.sims["NH"], "ta"), last_votes(R.sims["CH"], "ta")
    for label, why in exp["lost_why"].items():                  # ... and each slip is one the reviewers are known to have made
        for rid in [r for r in R.truth.by_id if R.truth.of(r).study_label == label]:
            if not exp["fwd"][rid]:
                assert nh.get(rid) == "exclude" and (not exp["keep"](rid) or ch.get(rid) == "exclude")
    assert len(exp["lost"]) <= 1
    wrongly = set(got) - truth
    assert not wrongly                                         # nothing ineligible was included after the disagreements were settled
    reports = R.prisma["counts"]["included_reports"]
    assert reports > len(got)                                  # some studies have more than one report


def test_full_texts_were_added_and_every_report_assessed(reference):
    R = reference
    assert R.pdfs["placed"] >= 0.6 * sum(R.pdfs.values())
    rows = R.ch.get("/api/fulltext")["rows"]
    for row in rows:
        w = R.truth.of(row["id"])
        assert row["pdf"] is (w.ft != "not_retrieved"), row
        assert row["status"] in ("included", "excluded", "awaiting", "not_retrieved")
    assert R.ch.get("/api/fulltext/missing")["missing"] == []
    assert R.ch.get("/api/conflicts")["conflicts"] == []


def test_extraction_and_risk_of_bias_were_finished_and_agreed(reference):
    R = reference
    x = R.ch.get("/api/extract")
    assert x["studies"] and all(s["done_first"] and s["done_second"] and s["to_agree"] == 0 for s in x["studies"])
    assert all(s["rob"]["assessments"] == 1 and s["rob"]["both_done"] == 1 for s in x["studies"])
    assert R.extract_differs and R.rob_differs
    designs = {R.truth.of(s["key"]).rob_design for s in x["studies"] if getattr(R.truth.of(s["key"]), "rob_design", "")}
    assert designs >= {"rct", "nrsi", "prediction"} and designs & {"before_after", "its"}
    sheets = check_xlsx(R.downloads["rob"][2])
    names = {"rct": "RoB 2", "cluster_rct": "RoB 2 cluster trials", "nrsi": "ROBINS-I V2", "before_after": "ROBINS-I 2016",
             "its": "ROBINS-I 2016", "prediction": "PROBAST+AI"}
    assert {names[d] for d in designs} | {"Both reviewers", "Plan and tools"} <= set(sheets)
    for name in {names[d] for d in designs}:
        for row in sheets[name][1:]:
            assert all("(not yet agreed)" not in c and "(not finished)" not in c for c in row)
    ex = check_xlsx(R.downloads["extraction"][2])
    head, *rows = ex["Extraction"]
    assert len(rows) == len(x["studies"]) and all(r[head.index("Number of participants")] for r in rows)


def test_every_download_opens_and_reviewers_get_only_what_they_may_see(reference):
    R = reference
    for kind, (st, hd, body) in R.downloads.items():
        assert st == 200, kind
        if kind in ("records", "decisions", "excluded", "searchlog", "ai", "audit", "dedup", "missing_csv"):
            rows = read_csv(body)
            assert formula_cells(rows) == [], kind
        elif kind in ("extraction", "rob"):
            check_xlsx(body)
        elif kind in ("prisma", "rob_svg"):
            check_svg(body)
        elif kind in ("included", "missing_ris"):
            if body.strip():
                parse_bytes(body, "x.ris")
    inc = parse_bytes(R.downloads["included"][2], "included.ris")[0]
    assert len(inc) == R.prisma["counts"]["included_reports"]
    for kind, (st, _, body) in R.refused_nh.items():
        assert st == 403, kind
    assert "sensitivity" in R.prisma["summary"] and "sensitivity" not in R.prisma_nh["summary"]
    s = R.downloads["summary"][2].decode("utf-8")
    seed = R.ch.summary()["settings"]["screening"]["seed"]
    for words in (f"(seed {seed})", "Calibration round 1:", "Cohen's kappa", "The pre-specified threshold was 0.80",
                  "AI second screener: the records in the main search were sent to two large language models",
                  "Both models gave a readable answer for",
                  "screened again by the second reviewer", "This set followed the outcome of the main screening plan."):
        assert words in s, words


def test_both_computers_find_the_project_data_intact(reference):
    R = reference
    for who, rep in R.integrity.items():
        assert rep["problems"] == [], (who, rep["problems"], "screening seed", R.seed)
        assert rep["records_sealed"] == len(R.truth.by_id)
    devs = {e["computer"] for e in R.ch.get("/api/audit")["entries"]}
    assert len(devs) == 2


@pytest.mark.slow
def test_the_whole_review_at_full_scale(tmp_path):
    """About 3,340 records after de-duplication, the size of a real review of this kind, the update search re-run unchanged."""
    R = run_reference_review(tmp_path, "full")
    try:
        exp = expected(R)
        C = R.prisma["counts"]
        assert C["checks"] == [] and C["complete"]
        assert 3300 <= C["db"]["records"] <= 3700
        for col in ("db", "other"):
            for k, v in exp[col].items():
                assert C[col][k] == v, (col, k)
        assert sorted(s["label"] for s in R.studies) == exp["included_studies"]
        assert sorted(R.review.eligible_studies() - set(exp["included_studies"])) == exp["lost"]
        for who, rep in R.integrity.items():
            assert rep["problems"] == [], who
    finally:
        stop(R)
