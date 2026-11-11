"""The timeline infographic: a well-formed SVG with every step of the project's plan in order, the branch at the
agreement rule only for plans with an agreement sample, the AI only when it is planned, statuses in words, every
piece of text escaped, and a version for the guides that needs no project. Also its route and downloads."""
import copy
import http.client
import json
import os
import re
import threading
import xml.etree.ElementTree as ET

import pytest

from reviewdesk import readiness, server, timeline, workflow
from reviewdesk.project import Project

NS = "{http://www.w3.org/2000/svg}"
REF = {
    "title": "Example review", "short_title": "Example",
    "reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"}, {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
    "screening": {"reviewers": ["NH", "CH"], "calibration_size": 100, "mode": "sample_then_single", "sample_fraction": 0.2,
                  "kappa_threshold": 0.8, "single_reviewer": "NH", "second_reviewer": "CH", "ai_second_screener": True,
                  "ai_keep_rate_stop": 0.5},
    "fulltext": {"mode": "dual"}, "registration": {}, "dates": {},
}


def plan(**screening):
    s = copy.deepcopy(REF)
    s["screening"].update(screening)
    return s


def texts(svg: str) -> list:
    """Every line of text in the picture, in order (each <text>, with its <tspan>s), as the reader sees it."""
    root = ET.fromstring(svg)
    return ["".join(t.itertext()) for t in root.iter(NS + "text")]


def joined(svg: str) -> str:
    return " ".join(texts(svg))


VARIANTS = ("landscape", "portrait")
PLANS = {"sample_ai": plan(), "sample_no_ai": plan(ai_second_screener=False), "dual": plan(mode="dual"),
         "single_ai": plan(mode="single"), "single": plan(mode="single", ai_second_screener=False),
         "no_calibration": plan(calibration_size=0)}


# ------------------------------------------------------------------------------------------------ the picture
@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("name", sorted(PLANS))
def test_svg_parses_and_has_every_step_in_order(name, variant):
    s = PLANS[name]
    svg = timeline.render(timeline.model(s, 6000), variant)
    root = ET.fromstring(svg)                                  # well-formed XML
    assert root.tag == NS + "svg" and root.get("viewBox")
    all_text = joined(svg)
    steps = workflow.steps_for(s, 6000)
    for st in steps:
        assert st["title"] in all_text, st["title"]
    for _, phase in workflow.PHASES:
        assert phase in all_text, phase
    # the numbers run 1..n, each step's number before its title, in order
    m = timeline.model(s, 6000)
    assert [x["n"] for x in m["steps"]] == list(range(1, len(steps) + 1))
    assert [x["id"] for x in m["steps"]] == [x["id"] for x in steps]
    for k in range(1, len(steps) + 1):
        assert str(k) in texts(svg)


def test_steps_follow_the_plan_numbers():
    svg = timeline.render(timeline.model(plan(calibration_size=150, sample_fraction=0.25, kappa_threshold=0.7), 4000))
    t = joined(svg)
    assert "150 records" in t
    assert "25% of the records (about 1,000)" in t
    assert "at least 0.70" in t and "kappa 0.70 or more" in t and "kappa below 0.70" in t
    assert "Nadine Hayes (NH), alone" in t                     # who screens alone
    assert "records NH excludes that the AI would keep go to CH" in t
    assert "over 50% of the records" in t


@pytest.mark.parametrize("variant", VARIANTS)
def test_branch_only_for_a_sample_then_single_plan(variant):
    with_branch = joined(timeline.render(timeline.model(plan(), 100), variant))
    assert "Yes" in texts(timeline.render(timeline.model(plan(), 100), variant))
    assert "kappa 0.80 or more" in with_branch and "kappa below 0.80" in with_branch
    assert "Apply the agreement rule" in with_branch
    assert "Either way, the records sent on go to step" in with_branch
    for mode in ("dual", "single"):
        t = joined(timeline.render(timeline.model(plan(mode=mode), 100), variant))
        assert "kappa" not in t.lower()
        assert "Apply the agreement rule" not in t and "Agreement sample" not in t
        assert "Either way" not in t


@pytest.mark.parametrize("variant", VARIANTS)
def test_ai_appears_only_when_planned(variant):
    has = joined(timeline.render(timeline.model(plan(), 100), variant))
    assert "Run the AI second screener" in has and "AI second screener" in has
    for s in (plan(ai_second_screener=False), plan(mode="dual"), plan(mode="single", ai_second_screener=False),
              plan(mode="dual", ai_second_screener=True)):          # dual screening never uses the AI
        t = joined(timeline.render(timeline.model(s, 100), variant))
        assert not re.search(r"\bAI\b", t), t


def test_ai_card_for_single_screening_does_not_mention_a_sample():
    m = timeline.model(plan(mode="single"), 100)
    ai = next(x for x in m["steps"] if x["id"] == "ai")
    assert "sample" not in " ".join(ai["facts"]).lower()
    assert "Only the project lead sees their answers." in ai["facts"][0]


@pytest.mark.parametrize("variant", VARIANTS)
def test_text_from_the_project_is_escaped(variant):
    nasty = '<script>alert("x")</script> & \'quotes\' "too" \x07'
    s = copy.deepcopy(REF)
    s["title"] = nasty
    s["reviewers"] = [{"id": "CH", "name": 'Colin "<b>" & Co', "role": "lead"}, {"id": "NH", "name": "Nadine <img src=x>", "role": "reviewer"}]
    s["registration"] = {"registry": "PROSPERO & <OSF>", "id": 'CRD"><script>1</script>', "date": "2026-11-20"}
    s["dates"] = {"start": "<script>soon</script>", "searches": "Dec & Jan", "update_search": "", "end": ""}
    svg = timeline.render(timeline.model(s, 10, {"setup": "done"}, title=nasty), variant)
    assert "<script" not in svg and "<img" not in svg and "<b>" not in svg
    assert "\x07" not in svg
    root = ET.fromstring(svg)                                  # still well-formed
    t = " ".join("".join(x.itertext()) for x in root.iter())
    assert '<script>alert("x")</script> & \'quotes\' "too"' in t        # shown as typed, as text
    assert "Nadine <img src=x>" in t and "PROSPERO & <OSF>" in t and "Dec & Jan" in t
    assert "&amp;" in svg and "&lt;script&gt;" in svg and "&quot;" in svg


def test_control_characters_and_lone_surrogates_are_dropped():
    s = copy.deepcopy(REF)
    s["title"] = "Bad \x00\x1b bytes \ud800 here"
    svg = timeline.render(timeline.model(s, 0, title=s["title"]))
    svg.encode("utf-8")                                        # no lone surrogates left
    ET.fromstring(svg)


@pytest.mark.parametrize("variant", VARIANTS)
def test_statuses_are_shown_in_words(variant):
    ids = [x["id"] for x in workflow.steps_for(REF, 100)]
    st = {i: "later" for i in ids}
    st.update({ids[0]: "done", ids[1]: "done", ids[2]: "now", ids[3]: "blocked"})
    svg = timeline.render(timeline.model(REF, 100, st), variant)
    t = texts(svg)
    for word in ("Done", "Now", "Something is missing", "Later"):
        assert word in t, word
    assert t.count("Done") >= 3                                 # two steps and the key
    assert "You are here" in t                                  # the phase of the first step that is now or blocked
    m = timeline.model(REF, 100, st)
    assert m["here"] == "search"
    assert [ph["status"] for ph in m["phases"]][:2] == ["done", "here"]


def test_no_statuses_no_status_words():
    t = texts(timeline.render(timeline.model(REF, 100, None)))
    assert "Now" not in t and "Later" not in t and "You are here" not in t


def test_outside_steps_dates_and_registration():
    s = copy.deepcopy(REF)
    s["registration"] = {"registry": "PROSPERO", "id": "CRD420261234567", "date": "2026-11-20", "url": ""}
    s["dates"] = {"start": "2026-11-02", "searches": "late November 2026", "update_search": "2027-04", "end": "2027-08-31"}
    for variant in VARIANTS:
        t = joined(timeline.render(timeline.model(s, 0), variant))
        assert t.count("Done outside ReviewDesk") >= 3              # register, synthesis and the key
        assert "PROSPERO CRD420261234567, 20 November 2026" in t
        for d in ("2 November 2026", "late November 2026", "April 2027", "31 August 2027"):
            assert d in t, d
    t = joined(timeline.render(timeline.model(REF, 0), "landscape"))
    assert "No registration number recorded yet." in t


def test_glossary_matches_the_plan():
    t = joined(timeline.svg_generic())
    for term in ("Calibration round", "Agreement sample", "AI second screener", "GRADE", "PRISMA 2020"):
        assert term in t
    t = joined(timeline.render(timeline.model(plan(mode="dual", calibration_size=0), 0)))
    assert "Calibration round" not in t and "Agreement sample" not in t


@pytest.mark.parametrize("variant", VARIANTS)
def test_generic_version_needs_no_project(variant):
    svg = timeline.svg_generic(variant)
    ET.fromstring(svg)
    t = joined(svg)
    assert "100 records" in t and "20% of the records" in t and "at least 0.80" in t
    assert "Reviewer A, alone" in t and "AI second screener" in t
    assert "Done" not in texts(svg) and "Later" not in texts(svg)
    assert "ReviewDesk 1." not in t                              # no version or date: the guides' pictures stay put
    for st in workflow.steps_for(timeline.GENERIC_SETTINGS, 0):
        assert st["title"] in t


def test_landscape_fits_a_page():
    """The generic landscape picture keeps to about the shape of A-series paper, so it prints on one page."""
    svg = timeline.svg_generic("landscape")
    w, h = (float(x) for x in re.search(r'width="([\d.]+)" height="([\d.]+)"', svg).groups())
    assert 1.25 <= w / h <= 1.6


def test_unknown_layout_is_refused():
    with pytest.raises(ValueError):
        timeline.svg_generic("square")


def test_no_external_resources():
    for variant in VARIANTS:
        svg = timeline.svg_generic(variant)
        assert "href=" not in svg.replace('url(#tl', "") and "http://www.w3.org/2000/svg" in svg
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", svg)
        assert "Plex" in svg and "Serif4" in svg and "sans-serif" in svg


def test_planned_dates_read_as_people_write_them():
    assert timeline._when("2026-11-02") == "2 November 2026"
    assert timeline._when("2027-04") == "April 2027"
    assert timeline._when("late November 2026") == "late November 2026"
    assert timeline._when("2026-13-40") == "2026-13-40"


def test_wrap_never_overflows():
    lines = timeline._wrap("A very long sentence with a supercalifragilisticexpialidociousword in it", 120, 13)
    assert all(timeline._tw(x, 13) <= 120 for x in lines)
    assert "".join(lines).replace("-", "").replace(" ", "") == "Averylongsentencewithasupercalifragilisticexpialidociouswordinit"


# ------------------------------------------------------------------------------------------------ with a project
@pytest.fixture
def proj(tmp_path):
    p = Project.create(str(tmp_path / "rev"), "Triage <review> & more", "Colin Hawthorne", "CH")
    p.update_settings("CH", {"short_title": "Triage review",
                             "reviewers": [{"id": "CH", "name": "Colin Hawthorne", "role": "lead"},
                                           {"id": "NH", "name": "Nadine Hayes", "role": "reviewer"}],
                             "screening": {"reviewers": ["NH", "CH"], "single_reviewer": "NH", "second_reviewer": "CH",
                                           "ai_second_screener": True},
                             "registration": {"registry": "PROSPERO", "id": "CRD42026000001", "date": "", "url": ""},
                             "dates": {"start": "", "searches": "2026-12", "update_search": "", "end": ""}})
    return p


def test_project_svg_uses_readiness_statuses(proj, monkeypatch):
    def check(p, me):
        steps = [dict(s, status="later", missing=[], warnings=[]) for s in workflow.steps_for(p.settings_ro(), len(p.records()))]
        steps[0]["status"] = "done"
        steps[1]["status"] = "now"
        return {"steps": steps, "missing": 0}
    monkeypatch.setattr(readiness, "check", check)
    for variant in VARIANTS:
        svg = timeline.svg(proj, "CH", variant)
        t = texts(svg)
        assert "Done" in t and "Now" in t and "You are here" in t
        assert "Triage <review> & more" in joined(svg) and "<review>" not in svg
        assert "PROSPERO CRD42026000001" in joined(svg) and "December 2026" in joined(svg)


def test_api_returns_steps_phases_statuses_and_svgs(proj):
    d = timeline.h_timeline(proj, "CH", {}, {})
    json.dumps(d)                                               # it can be sent
    assert [s["n"] for s in d["steps"]] == list(range(1, len(d["steps"]) + 1))
    assert [ph["id"] for ph in d["phases"]] == [pid for pid, _ in workflow.PHASES]
    assert set(d["statuses"]) == {s["id"] for s in d["steps"]}
    assert all(s["status"] in ("done", "now", "blocked", "later") for s in d["steps"])
    assert d["branch"] is True and d["ai"] is True
    ET.fromstring(d["svg"])
    ET.fromstring(d["svg_portrait"])
    assert 'data-variant="landscape"' in d["svg"] and 'data-variant="portrait"' in d["svg_portrait"]
    scr = next(s for s in d["steps"] if s["id"] == "screen")
    assert scr["branch"]["yes"]["who"] == "Nadine Hayes (NH), alone"
    light = timeline.h_timeline(proj, "CH", {"svg": ["0"]}, {})
    assert "svg" not in light and "svg_portrait" not in light and light["steps"] == d["steps"]


def test_downloads_are_registered(proj):
    assert server.ROUTES_GET["/api/timeline"]
    for kind, name in (("timeline_svg", "steps of the review.svg"), ("timeline_svg_portrait", "steps of the review (portrait).svg")):
        fname, ctype, make, why_not = server.MODULE_DOWNLOADS[kind]
        assert fname == name and ctype == "image/svg+xml"
        assert why_not(proj, "NH") == ""
        ET.fromstring(make(proj, "NH").decode("utf-8"))


# ------------------------------------------------------------------------------------------------ over HTTP
@pytest.fixture(scope="module")
def running(tmp_path_factory):
    os.environ["REVIEWDESK_HOME"] = str(tmp_path_factory.mktemp("home"))
    httpd, url = server.serve(0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], url.split("t=")[1], tmp_path_factory.mktemp("projects")
    httpd.shutdown()


def call(port, tok, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers={"Host": f"127.0.0.1:{port}", "X-Token": tok, "Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), data


def test_route_and_download_over_http(running):
    port, tok, parent = running
    st, _, _ = call(port, tok, "POST", "/api/project/create", {"parent": str(parent), "name": "t", "title": "HTTP review",
                                                               "lead_name": "Ada Lovelace", "lead_id": "AL"})
    assert st == 200
    # a new project has no short title until the lead gives one (the guided set-up's first screen asks for it)
    st, _, _ = call(port, tok, "POST", "/api/settings", {"changes": {"short_title": "HTTP review"}})
    assert st == 200
    st, _, data = call(port, tok, "GET", "/api/timeline")
    assert st == 200
    d = json.loads(data)
    assert d["steps"] and d["svg"].startswith("<svg") and d["svg_portrait"].startswith("<svg")
    st, hd, data = call(port, tok, "GET", "/download/timeline_svg")
    assert st == 200 and hd["Content-Type"] == "image/svg+xml"
    assert "steps%20of%20the%20review.svg" in hd["Content-Disposition"] and "HTTP%20review%20-%20" in hd["Content-Disposition"]
    ET.fromstring(data.decode("utf-8"))
    st, hd, _ = call(port, tok, "GET", "/download/timeline_svg_portrait")
    assert st == 200 and "%28portrait%29.svg" in hd["Content-Disposition"]
    st, _, _ = call(port, "wrong", "GET", "/download/timeline_svg")
    assert st == 403
