"""The methodologist's re-check of the reporting outputs (ReviewDesk 1.3.0): every sentence states what ReviewDesk
recorded, and anything else is a highlighted line for the authors.

M1: no claim of a step that was not recorded (discussions, how disagreements were settled, how register records were
handled, checking against the reports); I1: perfect agreement; I2: what the AI figures decided; I3: how the records
from other methods were screened; I7: the excluded studies cited in full; I9: the read-me and the checklist agree;
I10: an accession number names its database; I11: responsibility and other AI use; and the style points (brackets,
sentences that start with a number, initials, labels)."""
import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

import pytest

import demo_review
from reviewdesk import exports, reporting, screening, stats

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return demo_review.build(str(tmp_path_factory.mktemp("recheck") / "AI triage"))


def _ptext(p) -> str:
    return "".join((el.text or "") if el.tag == W + "t" else "\n" if el.tag == W + "br" else "" for el in p.iter())


def body(data: bytes) -> list:
    """(style, text, highlighted) for each paragraph of a .docx outside tables, in order."""
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    out = []
    for p in root.find(W + "body"):
        if p.tag != W + "p":
            continue
        st = p.find(f"{W}pPr/{W}pStyle")
        out.append((st.get(W + "val") if st is not None else "", _ptext(p), p.find(f".//{W}highlight") is not None))
    return out


def text(data: bytes) -> str:
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    return "\n".join(_ptext(p) for p in root.iter(W + "p"))


def highlighted(data: bytes, words: str) -> bool:
    """True when the paragraph that has these words is a highlighted line for the authors."""
    return any(h for _, t, h in body(data) if words in t)


def section(data: bytes, head: str) -> list:
    """The paragraphs under one heading of the methods text."""
    out, on = [], False
    for style, t, _ in body(data):
        if style.startswith("Heading"):
            on = t == head
        elif on:
            out.append(t)
    return out


MINE = ("Information sources and searches", "Records and de-duplication", "Title and abstract screening",
        "Title and abstract screening: update search", "Title and abstract screening: other methods", "Full-text review",
        "Data extraction")


# ------------------------------------------------------------------------------------------------ M1
def test_nothing_claims_a_step_that_was_not_recorded(demo):
    p = demo["p"]
    for me in ("CH", "NH"):
        docs = {"methods": reporting.methods_docx(p, me), "agreement": reporting.agreement_docx(p, me),
                "characteristics": reporting.characteristics_docx(p, me)}
        alltext = "\n".join(text(d) for d in docs.values()) + exports.methods_summary(p, me == "CH")
        for claim in ("discussed every disagreement", "settled by the two reviewers together", "settled together",
                      "were handled outside it", "against the reports", "settled by agreement", "they decided together"):
            assert claim not in alltext, claim
        # "after discussion" is gone from ReviewDesk's own sentences; it stays only inside the authors' lines
        for name in ("characteristics", "methods"):
            for _, t, hl in body(docs[name]):
                if "after discussion" in t:
                    assert hl or "Risk of bias" in t or "judgement" in t, t       # the risk-of-bias text is rob-plan's
        m = docs["methods"]
        # what was recorded is stated, and the rest is a highlighted line for the authors
        assert highlighted(m, "what was done with the calibration disagreements")
        assert highlighted(m, "how the disagreements at full text were resolved")
        assert highlighted(m, "how the records from ANZCTR were handled outside ReviewDesk")
        assert highlighted(m, "checked the answers (for example, against the full text of each report)")
        assert highlighted(docs["agreement"], "what was done with the calibration disagreements")
        assert highlighted(docs["agreement"], "how the disagreements at full text were resolved")
        assert "The records found in ANZCTR were not imported into ReviewDesk (see the records section)." in text(m)
        d = exports.ft_disagreements(p)
        assert {k: d[k] for k in ("total", "resolved", "open", "lead")} == {"total": 3, "resolved": 3, "open": 0, "lead": "CH"}
        assert d["compared"] >= d["total"]
        assert ("The two reviewers' decisions differed on 3 reports at full text. The project lead (Colin Hawthorne) "
                "recorded the decision on each, so none is still waiting for a decision.") in text(m)
        assert "Where the two differed, the project lead (Colin Hawthorne) recorded the final value." in text(m)
        assert ("where they differed, the value the project lead recorded (PRISMA 2020 item 17)"
                in text(docs["characteristics"]))


def test_open_full_text_disagreements_are_counted_and_the_lead_is_named(demo, monkeypatch):
    p = demo["p"]
    real = p.resolutions()
    gone = sorted(real)[0]
    assert gone[1] == "ft"
    monkeypatch.setattr(p, "resolutions", lambda: {k: v for k, v in real.items() if k != gone})
    d = exports.ft_disagreements(p)
    assert d["total"] == 3 and d["resolved"] == 2 and d["open"] == 1
    words = " ".join(str(x) for x in exports.ft_disagreement_parts(p, exports.names(p)))
    assert "recorded the decision on 2 of them; 1 is still waiting for a decision." in words
    # before any report has both reviewers' decisions, only the rule set in advance is said
    monkeypatch.setattr(exports, "ft_disagreements", lambda p_: {"total": 0, "resolved": 0, "open": 0, "lead": "CH",
                                                                 "compared": 0})
    parts = exports.ft_disagreement_parts(p, exports.names(p))
    assert len(parts) == 1 and parts[0].startswith("The rule set in advance for when the reviewers still disagree: “")


def test_a_rule_for_title_and_abstract_disagreements_says_only_what_was_recorded(demo, monkeypatch):
    p = demo["p"]
    real = screening.plan

    def plan(p_, s):
        return dict(real(p_, s), conflicts="discuss")
    monkeypatch.setattr(screening, "plan", plan)
    out = exports._set_plan_text(p, "other", {}, exports.names(p))
    joined = " ".join(str(x) for x in out)
    assert "they decided together" not in joined and "the project lead recorded the decision" in joined
    assert any(isinstance(x, exports.Fill) and "how disagreements at title and abstract were resolved" in x for x in out)


def test_decisions_and_exclusions_name_who_recorded_a_resolution(demo):
    p = demo["p"]
    rows = list(csv.reader(io.StringIO(exports.decisions_csv(p).decode("utf-8-sig"))))
    res = [r for r in rows[1:] if r[-1].startswith("resolution")]
    assert res and all(r[1] == "Colin Hawthorne" and r[-1] == "resolution recorded by the project lead" for r in res)
    assert not any(r[1] == "Agreed together" for r in rows)
    ex = list(csv.reader(io.StringIO(exports.excluded_fulltext_csv(p).decode("utf-8-sig"))))
    assert {r[-1] for r in ex[1:]} <= {"both reviewers", "decision recorded by the project lead"}


# ------------------------------------------------------------------------------------------------ I1
def test_perfect_agreement_is_reported_with_the_goodness_of_fit_interval_and_the_sent_on_counts(demo):
    p = demo["p"]
    a = text(reporting.agreement_docx(p, "CH"))
    other = a.split("Title and abstract screening: other methods")[1].split("Full-text review")[0]
    assert ("Cohen's kappa 1.00 (the reviewers agreed on all 3 records, each sending 1 on; neither the large-sample method "
            "nor the percentile bootstrap gives an interval when the reviewers agree on every record, but the goodness-of-fit "
            "method of Donner and Eliasziw gives a 95% CI of") in other
    assert "Donner and Eliasziw (1992), which assumes that the two reviewers send records on at the same" in other
    assert "no confidence interval can be given" not in a and "))" not in a


def test_a_small_agreement_sample_is_flagged_where_the_rule_rested_on_it(demo):
    p = demo["p"]
    sr = p.state_ro()["sets"]["main"]["sample_result"]
    assert sr["n"] < stats.SMALL_SAMPLE
    want = ("The agreement sample was small: 11 records, and Nadine Hayes sent on 8 of them and Colin Hawthorne 7 (Include "
            "or Maybe). With so few records, kappa can come out high or low largely by chance, and the decision to allow "
            "single screening rested on it.")
    assert exports.small_sample(sr, "Nadine Hayes", "Colin Hawthorne") == want
    for me in ("CH", "NH"):
        assert want in exports.methods_summary(p, me == "CH")
        assert any(t == "Caution. " + want for _, t, _ in body(reporting.agreement_docx(p, me)))


# ------------------------------------------------------------------------------------------------ I2, I3
def test_the_ai_paragraph_says_what_decided_nothing_and_what_fed_the_stopping_rule(demo):
    p = demo["p"]
    m = exports.methods_summary(p, True)
    assert "These figures describe the AI and decided nothing" not in m
    assert "The sensitivity and specificity describe the AI and decided nothing." in m
    assert re.search(r"This keep rate fed the pre-specified stopping rule: the AI kept [\d.]+% of records, within the \d+% "
                     r"limit", m)
    t = text(reporting.ai_docx(p, "CH"))
    assert "The sensitivity and specificity describe the AI; they decided nothing" in t
    assert "Unlike the accuracy figures, this keep rate decided something: it fed the pre-specified stopping rule" in t
    nh = exports.methods_summary(p, False)
    assert "keep rate" not in nh and "decided nothing" not in nh                  # the reviewer sees neither figure


def test_the_records_from_other_methods_are_said_to_be_screened_as_they_were(demo):
    p = demo["p"]
    for me in ("CH", "NH"):
        part = section(reporting.methods_docx(p, me), "Title and abstract screening: other methods")
        joined = " ".join(part)
        assert "like the database records" not in joined
        assert joined.startswith("The 3 records were put into a random order")
        assert "They are the records found by other methods (Citation searching 4)" in joined
        assert "Nadine Hayes and Colin Hawthorne screened every record independently" in joined
        assert "The AI second screener's answers were not used for these records." in joined


# ------------------------------------------------------------------------------------------------ I7, I10, S10, S11
def test_the_excluded_studies_are_cited_in_full_with_an_identifier(demo):
    p = demo["p"]
    by = p.by_id()
    data = reporting.excluded_docx(p, "CH")
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    tables = [t for t in root.iter(W + "tbl")][1:]
    src = reporting.accession_sources(p)
    seen = 0
    for t in tables:
        rows = [[_ptext(c) for c in tr.findall(W + "tc")] for tr in t.findall(W + "tr")]
        assert rows[0] == ["Study", "Reference", "DOI or identifier"]
        for study, ref, ident in rows[1:]:
            rid = next(r for r, o in screening.ft_outcomes(p).items() if o["status"] == "excluded"
                       and reporting.cite(by[r], ident=False) == ref)
            assert study == reporting.first_author_year(by[rid])
            assert by[rid]["title"].rstrip(".") in ref and (str(by[rid].get("year") or "") in ref)
            if by[rid].get("doi") or by[rid].get("pmid") or by[rid].get("accession"):
                assert ident and ident == reporting.identifier(by[rid], src.get(rid, ""))
            seen += 1
    assert seen == sum(1 for o in screening.ft_outcomes(p).values() if o["status"] == "excluded")
    # the CSV uses the same study labels and identifiers as the Word list
    rows = list(csv.reader(io.StringIO(exports.excluded_fulltext_csv(p).decode("utf-8-sig"))))
    assert rows[0][:3] == ["ID", "Study", "Reference"] and "DOI or identifier" in rows[0]
    for r in rows[1:]:
        assert r[1] == reporting.first_author_year(by[r[0]]) and "," not in r[1]
        assert r[rows[0].index("DOI or identifier")] == reporting.identifier(by[r[0]], src.get(r[0], ""))


class _Stub:
    """Just enough of a project for accession_sources."""

    def __init__(self, imports, records):
        self._i, self._r = imports, records

    def imports(self, include_missing=False):
        return self._i

    def records(self):
        return self._r


def test_an_accession_number_is_labelled_with_the_database_it_came_from():
    imps = [{"id": "I1", "database": "Embase"}, {"id": "I2", "database": "Scopus"}, {"id": "I3", "database": "CENTRAL"}]
    recs = [{"id": "R1", "accession": "2020458163", "sources": ["I1", "I2"], "first_source": "I1#4"},
            {"id": "R2", "accession": "CN-02557980", "sources": ["I3"]},
            {"id": "R3", "accession": "2-s2.0-85736279560", "sources": ["I2", "I1"], "first_source": "I2#0"}]
    src = reporting.accession_sources(_Stub(imps, recs))
    # found in two databases: the number is the first copy's (de-duplication never takes it from a later copy)
    assert src == {"R1": "Embase", "R2": "CENTRAL", "R3": "Scopus"}
    assert reporting.identifier(recs[0], src["R1"]) == "Accession number 2020458163 (Embase)"
    assert reporting.cite({"title": "A trial", "journal": "BMJ", "year": "2026", "accession": "2020458163"},
                          source="Embase").endswith("Accession number 2020458163 (Embase).")


def test_initials_written_without_full_stops_are_capitals_in_a_list_written_with_initials():
    assert reporting.authors_vancouver("Gupta P; Moreau M; Santos Jw; Chen T") == ["Gupta P", "Moreau M", "Santos JW", "Chen T"]
    assert reporting.authors_vancouver("Romano MJ.; Bakker Jw.; Jansen A.") == ["Romano MJ", "Bakker JW", "Jansen A"]
    # a list of full names keeps a two-letter given name as it is
    assert reporting.authors_vancouver("Wang Li; Zhang Wei") == ["Wang Li", "Zhang Wei"]
    assert reporting.authors_vancouver("Wang, Li; Zhang, Wei") == ["Wang L", "Zhang W"]
    assert reporting.first_author_year({"authors": "Santos Jw; Gupta P", "year": "2023"}) == "Santos 2023"
    c = reporting.cite({"authors": "Gupta P; Moreau M; Santos Jw", "title": "A trial", "journal": "EMJ", "year": "2023"})
    assert c.startswith("Gupta P, Moreau M, Santos JW. A trial.")


# ------------------------------------------------------------------------------------------------ I9
def test_the_read_me_and_the_checklist_agree_on_what_the_methods_text_covers(demo, monkeypatch):
    p = demo["p"]
    for me in ("CH", "NH"):
        item = next(x for x in reporting._items(p, me) if x["title"] == "Methods text")
        where = reporting.checklist_where(p, me)
        nums = [n for n in reporting.CHECKLIST_ITEMS if "Methods text" in where.get(n, "")]
        assert item["prisma"] == "PRISMA 2020 items " + exports._list(nums)
        assert "24c" not in item["prisma"] and "24b" in item["prisma"]
    z = zipfile.ZipFile(io.BytesIO(reporting.pack(p, "CH")))
    readme = z.read("Read me first.txt").decode()
    assert "Supports PRISMA 2020 items 5, 6, 8, 9, 11, 15, 16a, 24a and 24b." in readme
    # with an amendment recorded, the methods text has a section for it, and both say 24c
    real = p.settings_ro
    monkeypatch.setattr(p, "settings_ro", lambda: dict(real(), amendments=[{"date": "2026-10-01", "by": "CH",
                                                                            "what": "criteria"}]))
    item = next(x for x in reporting._items(p, "CH") if x["title"] == "Methods text")
    assert item["prisma"].endswith("24a, 24b and 24c") and reporting.checklist_where(p, "CH").get("24c")


# ------------------------------------------------------------------------------------------------ I11
def test_the_ai_report_asks_for_responsibility_and_any_other_ai_use(demo):
    p = demo["p"]
    data = reporting.ai_docx(p, "CH")
    assert "10. Responsibility, and any other use of AI" in text(data)
    assert highlighted(data, "the authors are ultimately responsible for this evidence synthesis, including the decision "
                             "to use AI")
    assert highlighted(data, "any other use of AI or automation in this review that made or suggested judgements")
    assert highlighted(data, "or state that no other AI was used")


# ------------------------------------------------------------------------------------------------ style
def test_no_sentence_starts_with_a_number_and_no_brackets_are_doubled(demo):
    p = demo["p"]
    docs = {"methods": reporting.methods_docx(p, "CH"), "methods NH": reporting.methods_docx(p, "NH"),
            "agreement": reporting.agreement_docx(p, "CH"), "excluded": reporting.excluded_docx(p, "CH"),
            "awaiting": reporting.awaiting_docx(p, "CH"), "ongoing": reporting.ongoing_docx(p, "CH"),
            "not retrieved": reporting.not_retrieved_docx(p, "CH"), "included": reporting.included_docx(p, "CH"),
            "characteristics": reporting.characteristics_docx(p, "CH"), "ai": reporting.ai_docx(p, "CH")}
    for name, data in docs.items():
        paras = body(data)
        if name.startswith("methods"):
            paras = [x for head in MINE for x in [("", t, False) for t in section(data, head)]]
            assert paras
        for style, t, _ in paras:
            if style in ("Title", "Subtitle", "Caption", "Code") or style.startswith("Heading"):
                continue
            assert not re.match(r"\d", t), (name, t)
            if style != "ListParagraph":                  # a reference has "Journal. 2026;37(4)"
                assert not re.search(r"[.!?] \d", t), (name, t)
            assert ") (" not in t and "((" not in t and "))" not in t, (name, t)
    summary = exports.methods_summary(p, True)
    assert "were put into a random order" in summary and "Records (" not in summary
    assert "no pairs were" in summary or "0 pairs" not in summary
    m = text(docs["methods"])
    assert "The 36 records were put into a random order" in m
    for me in ("CH", "NH"):                               # the checklist's column for a reviewer, too
        for item, where in reporting.checklist_where(p, me).items():
            assert ") (" not in where and "))" not in where, (me, item, where)
    assert "AI second screener report (automation tool; in the project lead's pack)" in reporting.checklist_where(p, "NH")["8"]


def test_percentages_and_counts_read_the_same_way(demo):
    p = demo["p"]
    a = text(reporting.agreement_docx(p, "CH"))
    assert "Wilson 95% CI for percentage agreement is" in a and "to 100%)" not in a and "100%" not in a.replace("100.0%", "")
    ai_text = text(reporting.ai_docx(p, "CH"))
    assert "named in its replies for" in ai_text and " answers)" not in ai_text
    assert "Its answers were used for the records from the database searches" in ai_text
    assert "Its answers were used, to choose" not in ai_text
