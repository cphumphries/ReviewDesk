"""Bugs found while building 1.3, each with the test that would have caught it."""
import pytest

from reviewdesk import server
from reviewdesk.project import ProjectError


@pytest.fixture
def lead(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWDESK_HOME", str(tmp_path / "home"))
    httpd, _ = server.serve(0)
    httpd.server_close()
    server.h_create({}, {"parent": str(tmp_path), "name": "proj", "title": "A review", "lead_name": "Ada Lovelace", "lead_id": "AL"})
    return server.APP.project


@pytest.mark.parametrize("body", [{"stage": "ft", "decision": "maybe"}, {"stage": "ta", "decision": "awaiting"},
                                  {"stage": "ft", "decision": "exclude", "reason": "Not a reason we use"},
                                  {"stage": "x", "decision": "include"}, {"stage": "ft", "decision": "include", "record": ""}])
def test_an_agreed_decision_must_be_one_the_stage_allows(lead, body):
    """A full-text 'maybe' was once saved for good and then broke the full-text page, the Overview and PRISMA."""
    with pytest.raises(server.ApiError):
        server.h_resolve({}, {"record": "R00001", **body})
    assert lead.resolutions() == {}


def test_a_reviewer_is_told_the_right_reason_for_the_ai_download(lead, monkeypatch):
    monkeypatch.setattr(lead, "is_lead", lambda who: False)
    assert server.download_blocked(lead, "ai") == "Only the project lead can download the AI's answers."


def test_the_lead_cannot_change_their_own_initials(lead):
    """The lead once changed CH to CX and was then no longer recognised on their own computer."""
    with pytest.raises(ProjectError):
        lead.update_settings("AL", {"reviewers": [{"id": "AX", "name": "Ada Lovelace", "role": "lead"}]})
    assert [r["id"] for r in lead.reviewers()] == ["AL"]


def test_someone_removed_before_screening_leaves_the_screening_plan(lead):
    lead.update_settings("AL", {"reviewers": [{"id": "AL", "name": "Ada Lovelace", "role": "lead"},
                                              {"id": "GH", "name": "Grace Hopper", "role": "reviewer"}],
                                "screening": {"reviewers": ["AL", "GH"], "single_reviewer": "GH", "second_reviewer": "AL"}})
    lead.update_settings("AL", {"reviewers": [{"id": "AL", "name": "Ada Lovelace", "role": "lead"}]})
    sc = lead.settings_ro()["screening"]
    assert sc["reviewers"] == ["AL"] and sc["single_reviewer"] == "" and sc["second_reviewer"] == "AL"
