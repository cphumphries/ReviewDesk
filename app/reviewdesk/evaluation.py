"""Evaluating a way of screening: the verification sample, and the data an evaluation needs.

The verification sample (from 1.3.1)
  When one reviewer screens most records alone (with or without the AI second screener behind them), the records
  they exclude leave the review on one person's vote; with the AI behind, on one person's vote and the AI's failure to
  object. Nothing else in the review ever looks at those records again, so nothing measures how many of them
  should have gone on. The verification sample does:
    population   every record outside the agreement sample that is excluded on the single reviewer's vote alone at
                 the moment of the draw (with the AI behind: excluded by them and not kept by the AI, since anything
                 the AI would keep has gone to a second look)
    sample       a random number of them, chosen by the lead before the draw
    decoys       a random number of records the single reviewer sent on (and that the verifier has not yet seen at
                 any stage), mixed in so that the verifier cannot take it that every record in front of them was
                 excluded; their votes on these change nothing
    verifier     someone in the project other than the single reviewer (by default the second reviewer), who is
                 shown each record as in any screening, with no earlier decision and no sign of which kind it is
  It is drawn once per record set, by the lead, after the single reviewer has voted on every record: the seed is
  the set's own seed plus VERIFY_SEED_STEP, so the draw cannot be repeated until it comes out differently. A sampled
  record the verifier would send on (Include or Maybe) goes to full text (screening.ta_outcome): the check can only
  add records to the review, never remove one. Drawing it makes the project need ReviewDesk 1.3.1 (versions.py): an
  older version does not know these votes and would count such a record as excluded.
  The result is the share of the sample the verifier would send on, with a 95% confidence interval (Wilson), and
  what became of those at full text; projected to the records not sampled, it estimates how many records a second
  reviewer would have sent on, and how many studies the review is likely to have missed, by screening this way.

  What keeps it blind (each of these is enforced on the server, and has a test):
    - Nothing moves while the verifier votes. Their votes send no record on until the sample is finished (they have
      voted on every record of it, or the lead has ended it): otherwise every count in the project, and the list of
      reports at full text, would answer "was that one excluded before?" after each Include.
    - Its votes are fixed the moment it is finished. Before that the verifier may change any vote (they have seen
      nothing); afterwards the pages open to them again, so a vote written later is refused, and never counts if one
      arrives all the same (screening.verify_votes; votes.csv marks such a row).
    - The verifier is shown nothing that lists records by what became of them until they have finished: the
      full-text pages and PDFs (the records mixed in are there; the sampled ones are not), data extraction and
      certainty (they list included studies), the audit log (it names records whose PDFs were added) and every
      download (blocked; the server refuses these routes, see server.VERIFIER_CLOSED). The lead is not exempt.
      The same goes for the smaller things that say a record reached full text: whether it has a PDF, the link to a
      free copy that was looked for (server.record_view), the data checks of the PDFs and their repairs, whose
      answers differ for a record that has one (server.h_integrity), and the AI's answers on known studies
      (server.h_ai: no sampled record is one the AI kept).
    - A sample is drawn once, and stays drawn. Every draw is logged whole in the audit log of the lead who drew it,
      and so is the end of a sample a lead ended. The log only grows, while the state is rewritten all the time and
      older copies of it can be put back; so the latest draw in the log IS the sample (screening.verify_logged),
      whatever the state holds: the same records in the same order for the same verifier, ended if it was ended.
      Putting back a state from before the draw, or from before the end, changes nothing and opens nothing, and so
      does the lead role moving to someone else. Only when the log holds no draw is the state's entry used; and
      should both have lost it, the pages stay closed to whoever has votes on a sample until a lead has drawn it
      again (screening.verify_duty).
    - The vote that finishes it has to be meant: the server takes the vote on the last record only with "finish",
      which the page sends after asking (a second tab does not know how many records are left); the check and the
      write are made under one lock, so two votes at the same moment cannot both pass as "not the last".
    - Nobody is given the figures, or the sample's votes, before it is finished: not the lead either (the downloads
      that list every vote wait). Only the lead is given them afterwards.
    - Only the lead is given the numbers the sample was drawn with. How many records left on the single reviewer's
      vote alone, set beside their own count of exclusions, is how many of them the AI kept, which no reviewer may
      learn; so reviewers are told that a sample was drawn, who screens it and whether it is finished, and its
      verifier how many records they have to screen.
  What it cannot do: someone who assesses full texts may have seen the records mixed in on the full-text list
  before the draw (the draw form says so, and proposes someone else when there is someone), and anyone with the
  project folder can open its files. (An entry written into the state by hand still cannot make a vote count that
  no draw would: only a record that left on one other person's vote is ever sent on, screening.ta_outcome.)
  A verifier who screens one sample on two computers leaves it to the two clocks which of their votes came first
  (the rows of one computer are in the order written; two computers' rows are put together by their time stamps):
  a sample should be screened on one computer, which the guide says. A sample its verifier cannot finish would keep those pages closed to them and
  its votes without effect for good, so the lead can end it as it stands (end), which every output then says.

The evaluation download (lead only)
  One zip of plain files holding everything ReviewDesk knows that an evaluation of the screening needs: one row per
  record (who had to screen it, every stage's outcome, both AI models' answers and what they cost), one row per
  vote (with the time it took, when the project records times), the verification sample, the plan and its results
  as data, and the audit log. README.txt in the zip describes every column. Notes and the AI's written reasons are
  left out (they are in the other downloads); people appear by their initials and their part in the plan.
  The copy for sharing is the same data with each person shown as Reviewer A, B, C ... (the same letter throughout)
  and without the audit log, whose entries name people in their details: a team that has promised to publish its
  data without names should not have to edit files by hand to keep the promise.
"""
from __future__ import annotations

import copy
import io
import json
import random
import zipfile

from . import VERSION, ai, exports, screening, stats, timing, util, versions
from .project import Project, ProjectError, SETS
from .screening import FWD, latest

VERIFY_SEED_STEP = 1_000_003
DRAW = "draw the verification sample"
RNG = ("Python random.Random(seed): the eligible record IDs sorted in ascending order and shuffled, the first [size] taken; "
       "then the same for the records to mix in; then the two together sorted and shuffled, which is the order shown")


def _name(p: Project, rid: str) -> str:
    return (p.reviewer(rid) or {}).get("name") or rid or "nobody"


def candidates(p: Project, set_name: str) -> list:
    """Who could screen a set's verification sample: anyone in the project but the person whose exclusions it checks."""
    single = screening.plan(p, set_name).get("single_reviewer")
    return [r["id"] for r in p.reviewers() if r["id"] != single]


def cautions(p: Project, set_name: str) -> dict:
    """{candidate: why they are not the best person to screen the sample} (nobody listed: no reason known). The
    records mixed in are already at full text, where someone who assesses full texts may have seen them; and the
    project lead can look up what was decided on any record."""
    pl = screening.plan(p, set_name)
    ft, lead = set(screening.ft_people(pl)), p.lead_id()
    out = {}
    for w in candidates(p, set_name):
        why = []
        if w in ft:
            why.append("assesses full texts, so may already have seen the records mixed in on the full-text list")
        if w == lead:
            why.append("is the project lead, who can look up what was decided on any record")
        if why:
            out[w] = f"{_name(p, w)} " + " and ".join(why) + "."
    return out


def default_verifier(p: Project, set_name: str) -> str:
    """Who the draw form proposes: the one person there is no caution about, if there is exactly one; otherwise the
    second reviewer; otherwise the only candidate; otherwise nobody (the lead chooses)."""
    pl = screening.plan(p, set_name)
    can = candidates(p, set_name)
    best = [w for w in can if w not in cautions(p, set_name)]
    if len(best) == 1:
        return best[0]
    if pl.get("second_reviewer") in can:
        return pl["second_reviewer"]
    return can[0] if len(can) == 1 else ""


def pools(p: Project, set_name: str, verifier: str = "") -> dict:
    """What a verification sample would be drawn from now: {"waiting": records the single reviewer has still to
    screen, "population": the records excluded on their vote alone, "decoys": records they sent on that this verifier
    has not seen} (IDs in ascending order)."""
    sv, pl = screening._prep(p, set_name)
    o = screening.ta_outcomes(p, set_name)
    V = screening.votes(p)
    single = pl.get("single_reviewer")
    outside = [r for r in sv.get("order", []) if r not in sv["_sample_set"]]
    # (votes of a verification sample do not count as "seen": a sample lost with an older copy of the state must come
    # out the same when it is drawn again)
    seen = lambda r: bool(verifier) and bool(latest(V, "ta", r, verifier) or latest(V, "ft", r, verifier))      # noqa: E731
    return {"waiting": sum(1 for r in outside if o[r]["status"] == "pending" and single in (o[r].get("missing") or [])),
            "population": sorted(r for r in outside if o[r]["status"] == "excluded" and o[r].get("how") == screening.ALONE),
            "decoys": sorted(r for r in outside if o[r]["status"] == "forward" and o[r].get("required") == [single]
                             and o[r].get("how") != screening.VERIFIED_ON and not seen(r))}


def cannot_draw(p: Project, set_name: str) -> dict:
    """{} when a verification sample can be drawn for this set now; otherwise {"why": plain words, "later": True if
    it will become possible (so the page says what it is waiting for), False if this set never has one}."""
    sv, pl = screening._prep(p, set_name)
    if sv.get("phase") in (None, "not_started"):
        return {"why": "Screening of this set has not started.", "later": False}
    mode = sv.get("remainder_mode") or pl["mode"]
    if sv.get("phase") != "screening":
        return ({"why": "The agreement sample comes first: a verification sample is drawn when one reviewer has screened the "
                        "rest alone.", "later": True} if pl["mode"] == "sample_then_single" else
                {"why": "Both reviewers screen every record of this set.", "later": False})
    if mode != "single":
        return {"why": "Both reviewers screen every record of this set, so no record leaves on one person's vote and "
                       "there is nothing for a verification sample to check.", "later": False}
    if sv["_verify"]:
        return {"why": "The verification sample of this set has been drawn (it is drawn once).", "later": False}
    if not candidates(p, set_name):
        return {"why": "A verification sample needs a second person in the project to screen it (Project set-up, People).",
                "later": True}
    pool = pools(p, set_name)
    single = _name(p, pl.get("single_reviewer"))
    if pool["waiting"]:
        n = pool["waiting"]
        return {"why": f"{single} still has {exports._pl(n, 'record')} to screen. The verification sample is drawn from "
                       "every record they exclude, so it can be drawn when they have finished.", "later": True}
    if not pool["population"]:
        return {"why": f"No record of this set was excluded on {single}'s vote alone, so there is nothing to sample.",
                "later": False}
    return {}


def draw(p: Project, user: str, set_name: str, size: int, decoys: int = 0, reviewer: str = "") -> dict:
    """Lead only, once per set: draw the verification sample and give it to its verifier."""
    p.require_lead(user)
    if set_name not in SETS:
        raise ProjectError("Unknown record set.")
    with p.lock:
        st = p.state()
        stop = cannot_draw(p, set_name)
        if stop:
            raise ProjectError(stop["why"])
        sv = st["sets"][set_name]
        pl = screening.plan(p, set_name)
        single = pl.get("single_reviewer")
        who = reviewer or default_verifier(p, set_name)
        if who not in candidates(p, set_name):
            raise ProjectError("Choose who screens the verification sample: someone in the project other than "
                               f"{_name(p, single)}, whose exclusions it checks.")
        pool = pools(p, set_name, who)
        N, M = len(pool["population"]), len(pool["decoys"])
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= N:
            raise ProjectError(f"The sample must be between 1 and {N:,} records: {exports._pl(N, 'record was', 'records were')} "
                               f"excluded on {_name(p, single)}'s vote alone" + (f" ({N:,} checks every one)." if N > 1 else "."))
        if not isinstance(decoys, int) or isinstance(decoys, bool) or not 0 <= decoys <= M:
            raise ProjectError(f"The number of records mixed in must be between 0 and {M:,}: {_name(p, single)} sent on "
                               f"{exports._pl(M, 'record')} that {_name(p, who)} has not seen yet.")
        seed = int(sv.get("seed") or 0) + VERIFY_SEED_STEP
        rng = random.Random(seed)
        a = pool["population"][:]
        rng.shuffle(a)
        b = pool["decoys"][:]
        rng.shuffle(b)
        sample, decoy = a[:size], b[:decoys]
        order = sorted(sample + decoy)
        rng.shuffle(order)
        sv["verify"] = {"drawn": util.now(), "by": user, "reviewer": who, "single_reviewer": single, "seed": seed,
                        "size": size, "decoys": decoys, "population": N, "decoy_pool": M,
                        "ai_net": bool(sv.get("ai_net_active")), "sample": sample, "decoy": decoy, "order": order,
                        "rng": RNG, "version": VERSION}
        # first the project file (an older ReviewDesk must stop before it can meet these votes), then the sample
        p.need_version(user, versions.FEATURES["verification sample"])
        p.save_state(st, user)
        # logged whole ("draw"): if the state ever loses the sample, the lead's log still holds it, so it stays
        # drawn as it was (screening.verify_logged). The log's readers are shown the numbers only (exports.audit_for).
        p.audit(user, DRAW, {"set": set_name, "sample": size, "of": N, "mixed in": decoys, "screened by": who, "seed": seed,
                             "draw": copy.deepcopy(sv["verify"])})
        return sv["verify"]


def _ft_counts(p: Project, recs: list) -> dict:
    fto = screening.ft_outcomes(p)
    out = {"included": 0, "excluded": 0, "pending": 0, "conflict": 0, "other": 0}
    for r in recs:
        s = (fto.get(r) or {}).get("status", "pending")
        out[s if s in out else "other"] += 1
    return out


def _share(k: int, n: int, rest: int) -> dict:
    """k of n, with its 95% interval and what that share would be of the `rest` records not looked at."""
    if not n:
        return {"k": k, "n": n, "rate": None, "ci": None, "projected": None}
    lo, hi = stats.wilson(k, n)
    lo, hi = (0.0 if k == 0 else lo), (1.0 if k == n else hi)          # (exactly, not 0.9999999999999999)
    rate = k / n
    return {"k": k, "n": n, "rate": rate, "ci": [lo, hi],
            "projected": {"records": rate * rest, "low": lo * rest, "high": hi * rest}}


def progress(p: Project, set_name: str) -> dict | None:
    """How far a set's verification sample has got: {"reviewer", "total", "done", "finished", "ended"}, or None."""
    sv, pl = screening._prep(p, set_name)
    vf = sv["_verify"]
    if not vf:
        return None
    done = len(sv["_verify_votes"])
    return {"reviewer": vf["reviewer"], "total": len(vf["order"]), "done": done, "ended": bool(vf["ended"]),
            "finished": not sv["_verify_open"]}


def verification(p: Project, set_name: str, me: str) -> dict | None:
    """A set's verification sample as this person may see it (None: nothing to show them).
    Everyone in the project sees that a sample was drawn, who screens it and whether it is finished. Its verifier also
    sees how many records it has and how many they have done. The lead also sees what could be drawn, the numbers the
    sample was drawn with and, once it is finished, the figures. Nobody but the lead is given a number from which the
    AI's answers could be worked out (how many records left on the one vote). Shared: do not change the result."""
    return p.memo(("verification", set_name, me), lambda: _verification(p, set_name, me))


def _verification(p: Project, set_name: str, me: str) -> dict | None:
    sv, pl = screening._prep(p, set_name)
    lead = p.is_lead(me)
    vf = sv["_verify"]
    if not vf:
        if not lead:
            return None
        stop = cannot_draw(p, set_name)
        # votes of a sample that neither the state nor the lead's audit log holds any more (it can only be drawn again)
        by = p.by_id()
        lost = sum(1 for r in screening.votes(p).get("vs", {}) if (by.get(r) or {}).get("set", "main") == set_name)
        if stop and not stop["later"] and not lost:
            return None
        out = {"drawn": False, "why_not": stop.get("why", ""), "single_reviewer": pl.get("single_reviewer"),
               "candidates": candidates(p, set_name), "reviewer": default_verifier(p, set_name), "lost_votes": lost}
        if not stop:
            pool = pools(p, set_name, out["reviewer"])
            out.update(population=len(pool["population"]), decoy_pool=len(pool["decoys"]), ai_net=bool(sv.get("ai_net_active")),
                       decoy_pools={w: len(pools(p, set_name, w)["decoys"]) for w in out["candidates"]},
                       cautions=cautions(p, set_name))
        return out
    g = progress(p, set_name)
    out = {"drawn": True, "drawn_at": vf["drawn"], "reviewer": vf["reviewer"],
           "single_reviewer": vf["single_reviewer"] or pl.get("single_reviewer"), "complete": g["finished"], "ended": g["ended"]}
    if lead or me == vf["reviewer"]:
        out.update(total=g["total"], done=g["done"])
    if lead:
        out.update(size=len(vf["sample"]), decoys=len(vf["decoy"]), population=vf["population"], ai_net=vf["ai_net"],
                   seed=vf["seed"], ended_why=(vf["ended"] or {}).get("reason", ""), from_log=sv["_verify_logged"])
        if g["finished"]:
            out["results"] = results(p, set_name)
    return out


def results(p: Project, set_name: str) -> dict | None:
    """The figures of a set's verification sample, from the votes now on file (None if none was drawn). Not for
    anyone but the lead, and not before the sample is finished (verification() and the download guards see to both).
    A sample the lead ended as it stood gives figures for the records screened; the rest count as not checked."""
    sv, pl = screening._prep(p, set_name)
    vf = sv["_verify"]
    if not vf:
        return None
    mine = sv["_verify_votes"]                    # the votes that count: none written after the sample was finished
    yes = lambda r: mine[r]["d"] in FWD           # noqa: E731
    voted = [r for r in vf["sample"] if r in mine]
    sent = [r for r in voted if yes(r)]
    N = vf["population"]
    rest = max(0, N - len(voted))
    ft = _ft_counts(p, sent)
    settled = bool(sent) and ft["pending"] == 0 and ft["conflict"] == 0
    dvoted = [r for r in vf["decoy"] if r in mine]
    out = {"population": N, "not_sampled": rest, "complete": not sv["_verify_open"], "ended": bool(vf["ended"]),
           "drawn": {"sample": len(vf["sample"]), "mixed_in": len(vf["decoy"])},
           "sent_on": _share(len(sent), len(voted), rest), "sent_on_records": sent, "full_text": ft,
           "decoys": {"n": len(dvoted), "sent_on": sum(1 for r in dvoted if yes(r))}}
    # studies missed: only once every record sent on has its full-text decision (none sent on: none to wait for)
    if voted and (settled or not sent):
        out["included"] = _share(ft["included"], len(voted), rest)
    return out


def _pct_ci(x: dict) -> str:
    return f"{stats.fmt_pct(x['rate'])}; 95% confidence interval {stats.fmt_pct(x['ci'][0])} to {stats.fmt_pct(x['ci'][1])}"


def methods_text(p: Project, set_name: str, full: bool) -> str:
    """The verification sample in the words of a methods section ('' if none was drawn). full: the lead's copy, with
    the numbers it was drawn with and, once it is finished, its figures. Otherwise what was done, without any number
    (how many records left on one vote would show how many of the single reviewer's exclusions the AI kept)."""
    sv, pl = screening._prep(p, set_name)
    vf = sv["_verify"]
    if not vf:
        return ""
    one, two = _name(p, vf["single_reviewer"] or pl.get("single_reviewer")), _name(p, vf["reviewer"])
    n, d, N = len(vf["sample"]), len(vf["decoy"]), vf["population"]
    left_how = (f"excluded by {one} and not kept by the AI second screener" if vf["ai_net"] else f"excluded on {one}'s vote alone")
    start = (f"To check the records that left the review on one reviewer's vote, a verification sample was drawn on "
             f"{exports._day(vf['drawn'])}, after {one} had screened every record: ")
    blind = ("without seeing any earlier decision" + (" or which kind each record was" if d else "")
             + "; any sampled record they would send on went to full text once the sample was finished.")
    R = results(p, set_name)
    if not full:
        text = (start + f"a random sample of the records {left_how}"
                + (f", mixed with records taken at random from those {one} had sent on" if d else "")
                + f". {two} screened them " + blind)
        if R["ended"]:                          # (without how far it had got: the sample's size is the lead's to know)
            by, why = _name(p, vf["ended"]["by"]), vf["ended"]["reason"].strip().rstrip(".")
            text += (f" The sample was ended as it stood by {by} on {exports._day(vf['ended']['at'])}, before {two} had "
                     "screened every record of it" + (f" ({why})" if why else "") + "; the records not screened count as not checked.")
        return text + (" (The verification sample's figures are shown to the project lead.)" if R["complete"] else
                       " The sample is still being screened.")
    text = (start + (f"all {N:,} records" if n == N and N > 1 else f"{exports._pl(n, 'record')} taken at random (seed {vf['seed']}) from the {N:,}")
            + f" {left_how}"
            + (f", mixed with {exports._pl(d, 'record')} taken at random from those {one} had sent on" if d else "")
            + f". {two} screened {'it' if n + d == 1 else 'them'} " + blind)
    if not R["complete"]:
        g = progress(p, set_name)
        return text + f" So far {two} has screened {g['done']:,} of the {g['total']:,}; nothing is sent on until the sample is finished."
    s = R["sent_on"]
    if R["ended"]:
        g = progress(p, set_name)
        by, why = _name(p, vf["ended"]["by"]), vf["ended"]["reason"].strip().rstrip(".")
        text += (f" The sample was ended as it stood by {by} on {exports._day(vf['ended']['at'])}, when {two} had screened "
                 f"{g['done']:,} of the {g['total']:,} records" + (f" ({why})" if why else "")
                 + "; the figures are for the records screened, and the rest count as not checked.")
    if not s["n"]:
        return text + " None of the sampled records had been screened, so there are no figures."
    text += (f" {two} would have sent on {s['k']:,} of the {s['n']:,} ({_pct_ci(s)})"
             + (f", and {R['decoys']['sent_on']:,} of the {R['decoys']['n']:,} records {one} had sent on" if R["decoys"]["n"] else "") + ".")
    ft = R["full_text"]
    if s["k"]:
        waiting = ft["pending"] + ft["conflict"]
        parts = [f"{ft['included']:,} {'was' if ft['included'] == 1 else 'were'} included"] if ft["included"] or not waiting else []
        if ft["excluded"]:
            parts.append(f"{ft['excluded']:,} excluded")
        if ft["other"]:
            parts.append(f"{ft['other']:,} not retrieved, awaiting classification or ongoing")
        if waiting:
            parts.append(f"{waiting:,} {'is' if waiting == 1 else 'are'} still to be decided")
        text += f" At full text, of {'that record' if s['k'] == 1 else 'those ' + format(s['k'], ',') + ' records'}, {exports._list(parts)}."
    inc = R.get("included")
    if inc and R["not_sampled"]:
        text += (f" Among the {R['not_sampled']:,} such records not checked, that suggests about "
                 f"{round(s['projected']['records']):,} a second reviewer would have sent on (95% confidence interval "
                 f"{round(s['projected']['low']):,} to {round(s['projected']['high']):,}) and about "
                 f"{round(inc['projected']['records']):,} that would have been included (95% confidence interval "
                 f"{round(inc['projected']['low']):,} to {round(inc['projected']['high']):,}; Wilson interval for the share, "
                 "applied to the records not checked).")
    elif inc and not R["not_sampled"]:
        text += " Every such record was checked, so nothing is left to estimate."
    return text


END = "end the verification sample as it stands"


def end(p: Project, user: str, set_name: str, reason: str) -> dict:
    """Lead only: end a verification sample that cannot be finished (its verifier has left, say), as it stands. Its
    votes then take effect as for a finished sample, the records not screened count as not checked, and every output
    says that it was ended and why. Until a sample is finished or ended its votes send nothing on and the pages that
    show earlier decisions stay closed to its verifier, so there must be a way out."""
    p.require_lead(user)
    if set_name not in SETS:
        raise ProjectError("Unknown record set.")
    reason = " ".join(str(reason or "").split())
    if len(reason) < 3:
        raise ProjectError("Say why the sample cannot be finished: the reason goes into the methods text.")
    with p.lock:
        st = p.state()
        sv, pl = screening._prep(p, set_name)
        vf = sv["_verify"]
        if not vf:
            raise ProjectError("No verification sample has been drawn for this set.")
        if not sv["_verify_open"]:
            raise ProjectError("This verification sample is already finished.")
        g = progress(p, set_name)
        cur = st.setdefault("sets", {}).setdefault(set_name, {})
        if sv["_verify_logged"]:                  # the state had lost the sample: put back from the lead's log first
            cur["verify"] = copy.deepcopy(screening.verify_logged(p, set_name))
        cur["verify"]["ended"] = {"at": util.now(), "by": user, "reason": reason[:500]}
        p.save_state(st, user)
        p.audit(user, END, {"set": set_name, "screened": g["done"], "of": g["total"], "reason": reason[:500]})
    return progress(p, set_name)


# ================================================================================================ the evaluation download
def verifying(p: Project, me: str) -> dict | None:
    """{"set", "left", "total"} when this person is screening a verification sample that is not finished; the same
    with "lost": True when the project holds votes of theirs on a sample that its state no longer holds (the pages
    stay closed to them until the lead has drawn it again: screening.verify_duty); else None."""
    duty = screening.verify_duty(p, me)
    if not duty:
        return None
    if duty[1] == "lost":
        return {"set": duty[0], "left": 0, "total": 0, "lost": True}
    g = progress(p, duty[0])
    return {"set": duty[0], "left": g["total"] - g["done"], "total": g["total"]}


CLOSED_PAGES = "Review full texts, Extract data, Rate certainty, the audit log and every download"


def blocked_title(p: Project, me: str) -> str:
    """'' or the few words that head a page closed to this person (blocked() gives the reason in full)."""
    v = verifying(p, me)
    return "" if not v else ("Closed until the verification sample is drawn again" if v.get("lost") else
                             "Closed until you finish the verification sample")


def blocked(p: Project, me: str) -> str:
    """'' or why this person is not shown what was decided on records before: they are screening a verification
    sample, which is done without sight of earlier decisions. Until they have finished it (or the lead has ended
    it), the server refuses them every page and file that lists records by what became of them: the full-text
    pages and PDFs (the records mixed into the sample are there, the sampled ones are not), the data extraction and
    certainty pages, the audit log (it names records whose PDFs were added) and every download."""
    v = verifying(p, me)
    if not v:
        return ""
    if v.get("lost"):
        return ("The project holds votes of yours on a verification sample, but the sample itself is no longer in the "
                "project's files (an older copy of them may have been put back). Your votes are kept. Until the project "
                "lead has drawn the sample again (Screen titles and abstracts: the same numbers give the same sample, and "
                f"your votes count again), these are closed to you, because they show what became of records: {CLOSED_PAGES}.")
    return ("You are screening a verification sample, which is done without sight of what was decided on its records "
            f"before. Until you have finished it ({exports._pl(v['left'], 'record')} left), these are closed to you, "
            f"because they show what became of records: {CLOSED_PAGES}. Carry on with the sample on Screen titles and "
            "abstracts; everything opens again when it is finished.")


def open_sample(p: Project) -> str:
    """'' or why a file listing every vote must wait: a verification sample is still being screened, and nobody (the
    lead included) is given its votes or figures before it is finished."""
    for set_name in SETS:
        g = progress(p, set_name)
        if g and not g["finished"]:
            return ("This file lists the votes of a verification sample that is still being screened "
                    f"({g['done']:,} of {g['total']:,} done). It can be downloaded when the sample is finished, so that "
                    "nobody sees how its votes compare before then.")
    return ""


def labels(p: Project, share: bool) -> dict:
    """{person: how the download shows them}: their own initials, or for the copy for sharing Reviewer A, B, C ... in
    the order of the project's list of people, then anyone else found in the project's files (someone who has left
    the project), in order of their initials. Anyone met later still gets a letter, never their initials."""
    ids = [r["id"] for r in p.reviewers()]
    seen = {w for recs in screening.votes(p).values() for voters in recs.values() for w in voters}
    seen |= {str(e.get("who")) for e in p.audit_entries() if isinstance(e, dict) and e.get("who")}
    for set_name in SETS:
        sv, pl = screening._prep(p, set_name)
        seen |= set(screening.named(pl)) | {pl.get("single_reviewer"), pl.get("second_reviewer")}
        seen |= {x for x in ((sv["_verify"] or {}).get(k) for k in ("reviewer", "single_reviewer", "by")) if x}
    ids += sorted(w for w in seen - set(ids) if isinstance(w, str) and w)
    return Labels(ids, share)


class Labels(dict):
    """The names the evaluation download uses for people (see labels)."""
    def __init__(self, ids: list, share: bool):
        super().__init__()
        self.share = share
        for w in ids:
            self[w] = self._name(len(self)) if share else w

    @staticmethod
    def _name(i: int) -> str:
        return "Reviewer " + (chr(65 + i) if i < 26 else str(i + 1))

    def __missing__(self, w):
        if not self.share or not isinstance(w, str) or not w:
            return w
        self[w] = self._name(len(self))
        return self[w]

    def get(self, w, default=None):            # (dict.get does not call __missing__)
        return self[w] if isinstance(w, str) and w else (w if default is None else default)


def _people(p: Project, set_name: str) -> dict:
    """{person: their parts in a set's plan, in words}."""
    sv, pl = screening._prep(p, set_name)
    out: dict = {}
    R = screening.named(pl)
    for i, w in enumerate(R):
        out.setdefault(w, []).append("first reviewer" if i == 0 else "second reviewer")
    mode = sv.get("remainder_mode") or pl["mode"]
    if mode == "single" and pl.get("single_reviewer"):
        out.setdefault(pl["single_reviewer"], []).append("single reviewer")
        if pl.get("second_reviewer") and "second reviewer" not in out.get(pl["second_reviewer"], []):
            out.setdefault(pl["second_reviewer"], []).append("second reviewer")
    vf = sv["_verify"]
    if vf:
        out.setdefault(vf["reviewer"], []).append("verifier")
    for w in screening.ft_people(pl):
        out.setdefault(w, []).append("full-text assessor")
    return out


def _counted_rows(p: Project) -> set:
    """The votes that count for agreement (each reviewer's own vote before the two were compared), by id()."""
    V = screening.votes(p)
    out = set()
    for set_name in SETS:
        sv, pl = screening._prep(p, set_name)
        R = screening.named(pl)
        if len(R) != 2 or not sv.get("order"):
            continue
        a, b = R
        for k, rnd in enumerate(sv.get("calibration_rounds") or []):
            for pair in screening.independent(V, "cal", rnd, a, b, True).values():
                out.update(id(x) for x in pair)
        sample = sv.get("sample") or []
        for pair in screening.independent(V, "ta", sample, a, b, True).values():
            out.update(id(x) for x in pair)
        if (sv.get("remainder_mode") or pl["mode"]) == "dual":
            rest = [r for r in sv["order"] if r not in sv["_sample_set"]]
            for pair in screening.independent(V, "ta", rest, a, b, False).values():
                out.update(id(x) for x in pair)
    sv, pl = screening._prep(p, "main")
    R = screening.named(pl)
    if len(R) == 2:
        for pair in screening.independent(V, "ft", list(screening.ft_outcomes(p)), R[0], R[1], False).values():
            out.update(id(x) for x in pair)
    return out


def task_of(stage: str, rec: str, who: str, sv: dict, pl: dict) -> str:
    """The task a vote was cast in, from its stage, its record and who cast it."""
    if stage != "ta":
        return {"cal": "calibration round", "vs": "verification sample", "ft": "full-text review"}.get(stage, stage)
    if rec in sv.get("_sample_set", ()):
        return "agreement sample"
    alone = (sv.get("remainder_mode") or pl["mode"]) == "single"
    if alone and who == pl.get("second_reviewer") and who != pl.get("single_reviewer"):
        return "second look"
    return "screening"


VOTE_COLUMNS = ["When", "Reviewer", "Part in the plan", "Record", "Record set", "Stage", "Task", "Decision",
                "Full-text exclusion reason", "Vote number", "Latest", "Counts for agreement",
                "Written after its sample was finished", "Time recorded",
                "Shown (ms)", "Active (ms)", "Longest quiet stretch (ms)", "Times shown"]


def votes_rows(p: Project, lab: dict | None = None) -> list:
    lab = lab or labels(p, False)
    by = p.by_id()
    prep = {s: screening._prep(p, s) for s in SETS}
    people = {s: _people(p, s) for s in SETS}
    counted = _counted_rows(p)
    V = screening.votes(p)
    in_sample = {s: set(x[0]["_verify"]["order"]) if x[0]["_verify"] else set() for s, x in prep.items()}
    rows = []
    for stage, recs in V.items():
        for rec, voters in recs.items():
            set_name = (by.get(rec) or {}).get("set", "main")
            if set_name not in prep:
                set_name = "main"
            sv, pl = prep[set_name]
            vf = sv["_verify"]
            for who, slot in voters.items():
                # a verification-sample vote written after its sample was finished (it never counts): every row of the
                # verifier's on that record after the one that counts, once the sample is finished
                late = set()
                if stage == "vs" and vf and who == vf["reviewer"] and not sv["_verify_open"] and rec in in_sample[set_name]:
                    kept = sv["_verify_votes"].get(rec)
                    at = next((n for n, d in enumerate(slot["all"]) if d is kept), -1)
                    late = {id(d) for d in slot["all"][at + 1:]}
                for k, d in enumerate(slot["all"], 1):
                    t = d.get("time") if isinstance(d.get("time"), dict) else {}
                    rows.append([d.get("t", ""), lab.get(who, who), "; ".join(people.get(set_name, {}).get(who, [])), rec, SETS.get(set_name, set_name),
                                 screening.STAGE_NAMES.get(stage, stage), task_of(stage, rec, who, sv, pl), d["d"],
                                 d.get("reason", "") if stage == "ft" else "", k, "yes" if d is slot["latest"] else "no",
                                 "yes" if id(d) in counted else "", "yes" if id(d) in late else "", "yes" if t else "no",
                                 t.get("shown", ""), t.get("active", ""), t.get("quiet", ""), t.get("views", "")])
    rows.sort(key=lambda r: (util.ts(r[0]), r[1], r[3]))      # by the moment, whatever time zone each computer was in
    return rows


def _usage(a: dict, k: str):
    u = a.get("usage") if isinstance(a.get("usage"), dict) else {}
    return u.get(k, "")


def records_table(p: Project, lab: dict | None = None) -> tuple[list, list]:
    """(header, rows): one row per record of every set whose screening has started."""
    lab = lab or labels(p, False)
    by = p.by_id()
    V = screening.votes(p)
    fto = screening.ft_outcomes(p)
    ans, comb = ai.answers(p), ai.combined(p)
    voted = {w for x in V.get("ta", {}).values() for w in x}
    voters = [w for w in lab if w in voted]            # everyone who has voted, people who have left the project too
    counted = _counted_rows(p)
    head = (["Record", "Record set", "Position in the random order", "Title", "Year", "DOI", "PMID", "Text fingerprint",
             "In the agreement sample",
             "Calibration round", "Had to screen it", "Title and abstract outcome", "How decided", "Second look",
             "Verification sample", "Verifier's vote"]
            + [f"Vote of {lab.get(w, w)}" for w in voters] + [f"Vote of {lab.get(w, w)} that counts for agreement" for w in voters]
            + ["Full-text outcome", "Full-text exclusion reason", "Study", "AI (kept unless both exclude)"]
            + [f"AI pass {ps}: {c}" for ps in "AB" for c in ("answer", "criterion", "prompt tokens", "completion tokens",
                                                             "seconds", "asked again", "model as served")])
    rows = []
    for set_name in SETS:
        sv, pl = screening._prep(p, set_name)
        if not sv.get("order"):
            continue
        o = screening.ta_outcomes(p, set_name)
        vf = sv["_verify"]
        in_sample, in_decoy = (set(vf["sample"]), set(vf["decoy"])) if vf else (set(), set())
        cal = {r: k for k, rnd in enumerate(sv.get("calibration_rounds") or [], 1) for r in rnd}
        alone = (sv.get("remainder_mode") or pl["mode"]) == "single"
        for pos, rec in enumerate(sv["order"], 1):
            r = by.get(rec) or {}
            x = o.get(rec) or {}
            req = x.get("required") or []
            f = fto.get(rec) or {}
            vs = sv["_verify_votes"].get(rec) if vf else None
            mine = {w: V.get("ta", {}).get(rec, {}).get(w) for w in voters}
            row = [rec, SETS[set_name], pos, str(r.get("title") or "")[:200], r.get("year", ""), r.get("doi", ""), r.get("pmid", ""),
                   ai.record_hash(r) if r else "",
                   "yes" if rec in sv["_sample_set"] else "no", cal.get(rec, ""), "; ".join(lab.get(w, w) for w in req),
                   x.get("status", "not_open"), x.get("how", ""),
                   "yes" if alone and len(req) == 2 and rec not in sv["_sample_set"] else "no",
                   "sample" if rec in in_sample else "mixed in" if rec in in_decoy else "", vs["d"] if vs else ""]
            row += [mine[w]["latest"]["d"] if mine[w] else "" for w in voters]
            row += [next((d["d"] for d in mine[w]["all"] if id(d) in counted), "") if mine[w] else "" for w in voters]
            row += [f.get("status", ""), f.get("reason", ""), f.get("study", ""), comb.get(rec, "")]
            for ps in "AB":
                a = ans[ps].get(rec) or {}
                row += [a.get("decision", ""), a.get("criterion", ""), _usage(a, "prompt"), _usage(a, "completion"),
                        a.get("latency", ""), ("yes" if a.get("retried") else "no") if a else "", a.get("served", "")]
            rows.append(row)
    return head, rows


def verification_rows(p: Project, lab: dict | None = None) -> list:
    lab = lab or labels(p, False)
    by = p.by_id()
    V = screening.votes(p)
    fto = screening.ft_outcomes(p)
    comb = ai.combined(p)
    rows = []
    for set_name in SETS:
        sv, pl = screening._prep(p, set_name)
        vf = sv["_verify"]
        if not vf:
            continue
        o = screening.ta_outcomes(p, set_name)
        one, who = vf["single_reviewer"] or pl.get("single_reviewer"), vf["reviewer"]
        kind = {**{r: "sample" for r in vf["sample"]}, **{r: "mixed in" for r in vf["decoy"]}}
        for pos, rec in enumerate(vf["order"], 1):
            slot = V.get("vs", {}).get(rec, {}).get(who)
            v = sv["_verify_votes"].get(rec)              # the vote that counts (none written after it was finished)
            n = next((k for k, d in enumerate(slot["all"], 1) if d is v), "") if slot and v else ""
            t = v.get("time") if v and isinstance(v.get("time"), dict) else {}
            mine = latest(V, "ta", rec, one)
            f = fto.get(rec) or {}
            rows.append([SETS[set_name], pos, rec, str((by.get(rec) or {}).get("title") or "")[:200], kind.get(rec, ""),
                         lab.get(one, one), mine["d"] if mine else "", comb.get(rec, ""), lab.get(who, who), v["d"] if v else "",
                         v.get("t", "") if v else "",
                         n, t.get("shown", ""), t.get("active", ""), t.get("quiet", ""),
                         (o.get(rec) or {}).get("status", ""), f.get("status", ""), f.get("reason", "")])
    return rows


VERIFICATION_COLUMNS = ["Record set", "Position shown", "Record", "Title", "Sample or mixed in", "Single reviewer",
                        "Single reviewer's vote", "AI (kept unless both exclude)", "Verifier", "Verifier's vote",
                        "Verifier voted at", "Verifier's votes on it", "Shown (ms)", "Active (ms)",
                        "Longest quiet stretch (ms)", "Title and abstract outcome now", "Full-text outcome",
                        "Full-text exclusion reason"]


def timing_choices(p: Project, lab: dict | None = None) -> list:
    """When the lead switched time recording on or off, and when anyone left their own votes out or put them back."""
    lab = lab or labels(p, False)
    out = []
    for e in p.audit_entries():
        det = e.get("details") if isinstance(e.get("details"), dict) else {}
        who = lab.get(e.get("who", ""), e.get("who", ""))
        if e.get("action") == timing.OWN:
            out.append({"when": e.get("t", ""), "who": who, "what": "own votes timed" if det.get("on") is not False
                        else "own votes not timed"})
        elif e.get("action") == "change settings" and det.get("time recording"):
            out.append({"when": e.get("t", ""), "who": who, "what": "time recording " + str(det["time recording"])})
    return out


# The dated actions an analysis of calendar time and of the AI's cost needs, and the figures of each that may be listed
# (numbers and yes/no only: never words, which could name someone).
EVENTS = {"start screening": ("records", "sample", "calibration"), "finish calibration": (), "another calibration round": (),
          "apply sample decision": ("kappa", "ai_net"), "AI run started": ("records_checked",),
          "AI run ended": ("done", "errors", "stopped"), "change AI settings": (), DRAW: ("sample", "of", "mixed in"),
          END: ("screened", "of"), timing.OWN: ("on",), "process imports": ()}
RESTORED = "restore from this computer's copy"         # (listed when it is the screening state that was put back)
EVENT_COLUMNS = ["When", "Who", "Action", "Figures", "ReviewDesk version"]


def events_rows(p: Project, lab: dict | None = None) -> list:
    lab = lab or labels(p, False)
    rows = []
    for e in p.audit_entries():
        det = e.get("details") if isinstance(e.get("details"), dict) else {}
        action = e.get("action")
        if action == "change settings" and det.get("time recording") in ("switched on", "switched off"):
            action, keys = "time recording " + det["time recording"], ()
        elif action == RESTORED and det.get("file") == "state/state.json":
            action, keys = "screening state put back from an earlier copy", ()
        elif action in EVENTS:
            keys = EVENTS[action]
        else:
            continue
        figs = "; ".join(f"{k}: {('yes' if det[k] else 'no') if isinstance(det[k], bool) else det[k]}" for k in keys
                         if isinstance(det.get(k), (bool, int, float)))
        rows.append([e.get("t", ""), lab.get(e.get("who", ""), e.get("who", "")), action, figs, e.get("version", "")])
    return rows


def summary(p: Project, lab: dict | None = None) -> dict:
    """The plan, what happened and the results, as data (summary.json in the download)."""
    lab = lab or labels(p, False)
    L = lambda w: lab.get(w, w) if isinstance(w, str) else w                     # noqa: E731
    s = p.settings_ro()
    out = {"made_with": "ReviewDesk " + VERSION, "made_at": util.now(), "project": s.get("title") or "",
           "registration": s.get("registration") or {},
           "people": [{"id": L(r["id"]), "role": r.get("role", "reviewer")} for r in p.reviewers()],
           "time_recording": {"on_now": timing.project_on(p), "changes": timing_choices(p, lab)}, "sets": {}}
    for set_name in SETS:
        sv, pl = screening._prep(p, set_name)
        if not sv.get("order"):
            continue
        o = screening.ta_outcomes(p, set_name)
        counts: dict = {}
        for x in o.values():
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        vf = sv["_verify"]
        the_plan = {k: pl.get(k) for k in ("mode", "reviewers", "single_reviewer", "second_reviewer", "sample_fraction",
                                           "kappa_threshold", "ai_second_screener", "ai_keep_rate_stop", "conflicts",
                                           "calibration_size", "ft_mode")}
        the_plan.update(reviewers=[L(w) for w in pl.get("reviewers") or []], single_reviewer=L(pl.get("single_reviewer")),
                        second_reviewer=L(pl.get("second_reviewer")))
        row = {"records": len(sv["order"]), "seed": sv.get("seed"), "started": sv.get("started"), "phase": sv.get("phase"),
               "plan": the_plan,
               "people": {L(w): parts for w, parts in _people(p, set_name).items()}, "remainder_mode": sv.get("remainder_mode"),
               "ai_net_active": bool(sv.get("ai_net_active")), "agreement_sample": len(sv.get("sample") or []),
               "calibration_rounds": [len(x) for x in sv.get("calibration_rounds") or []],
               # (without its one sentence that names people: remainder_mode and ai_net_active say the same)
               "sample_result": {k: ([L(w) for w in v] if k == "reviewers" and isinstance(v, list) else L(v) if k == "by" else v)
                                 for k, v in sv["sample_result"].items() if k != "remainder"}
               if isinstance(sv.get("sample_result"), dict) else None, "title_and_abstract": counts,
               "second_looks": screening.second_look_stats(p, set_name)}
        if vf:
            row["verification"] = {**{k: (L(vf[k]) if k in ("by", "reviewer", "single_reviewer") else vf[k])
                                      for k in ("drawn", "by", "reviewer", "single_reviewer", "seed", "population",
                                                "decoy_pool", "ai_net", "rng")},
                                   "size": len(vf["sample"]), "decoys": len(vf["decoy"]),
                                   "ended": dict(vf["ended"], by=L(vf["ended"]["by"])) if vf["ended"] else None,
                                   # (never before the sample is finished: the download is refused until then)
                                   "results": results(p, set_name) if not sv["_verify_open"] else None}
        out["sets"][set_name] = row
    fc: dict = {}
    for x in screening.ft_outcomes(p).values():
        fc[x["status"]] = fc.get(x["status"], 0) + 1
    out["full_text"] = fc
    if any(v.get("plan", {}).get("ai_second_screener") for v in out["sets"].values()):
        c, keys, ans = ai.config(p), ai.current_keys(p), ai.answers(p)
        tot = {}
        for ps in "AB":
            rows = list(ans[ps].values())
            tot[ps] = {"answers": len(rows), "failed_or_unreadable": sum(1 for a in rows if a.get("decision") not in ai.LABELS),
                       "prompt_tokens": sum(_usage(a, "prompt") or 0 for a in rows),
                       "completion_tokens": sum(_usage(a, "completion") or 0 for a in rows),
                       "seconds": round(sum(a.get("latency") or 0 for a in rows), 2),
                       "asked_again": sum(1 for a in rows if a.get("retried"))}
        out["ai"] = {"engine": ai.ENGINE, "rule": "a record is kept unless both models answer EXCLUDE; a failed or "
                                                  "unreadable answer counts as kept; the AI never excludes a record",
                     "models": {ps: c["passes"][ps]["model"] for ps in "AB"},
                     "settings": {ps: ai.settings_for(c["passes"][ps]) for ps in "AB"},
                     "prompt_fingerprints": {ps: keys[ps][1] for ps in "AB"},
                     "locked": bool(c.get("locked")), "locked_at": c.get("locked_at"), "totals": tot}
    return out


README = """ReviewDesk evaluation data
==========================

Made with ReviewDesk {version} on {made} from the project "{title}".

These files hold what ReviewDesk recorded about how this review was screened, for anyone evaluating that way of
screening: how accurate it was, how long it took and what each part of it added. Nothing here is an analysis; the
figures ReviewDesk itself reports are in summary.json, worked out from the same rows.

{people}
Titles are bibliographic data; abstracts, notes and the AI's written reasons are not in these files.

Times are in ISO 8601 with the time zone of the computer that wrote them. A blank cell means "does not apply" or
"not recorded"; nothing is filled in by estimate.

records.csv: one row per record, for every record set whose screening has started
  Record                          ReviewDesk's ID of the record
  Record set                      Database searches, Update search or Other methods
  Position in the random order    where the record came in the set's random screening order (1 is first)
  Title, Year, DOI, PMID          to trace the record
  Text fingerprint                the first 16 characters of the SHA-256 hash of the text the AI is sent for the record
                                  (title, year, journal, publication type, abstract): the same in any project that
                                  holds the same record, so the AI's answers in a repeat run can be matched to it
  In the agreement sample         yes if both reviewers screened it independently as part of the agreement sample
  Calibration round               the number of the practice round the record was in, if any (practice votes never count)
  Had to screen it                the people whose title-and-abstract votes the plan requires on this record, as things
                                  stand; with the AI second screener, a record the single reviewer excluded that the AI
                                  would keep also requires the second reviewer
  Title and abstract outcome      forward (to full text), excluded, pending (a required vote is missing), conflict (to
                                  be decided together) or not_open
  How decided                     both / one reviewer sent it on / second look sent it on / verification sent it on /
                                  single reviewer / both excluded / agreed together
  Second look                     yes if the record went to the second reviewer because the single reviewer excluded it
                                  and the AI would keep it
  Verification sample             sample (drawn from the records excluded on the single reviewer's vote alone), mixed
                                  in (drawn from the records the single reviewer sent on), or blank
  Verifier's vote                 the verifier's vote on it that counts: their latest up to the moment the sample
                                  was finished
  Vote of XX                      that person's latest title-and-abstract vote (include, maybe, exclude), one column
                                  for each person who has voted; the latest vote is the one that decides the outcome
  Vote of XX that counts for agreement
                                  their own vote before the two reviewers' votes were compared, where the record is one
                                  both screened independently (the agreement sample, or every record under dual screening)
  Full-text outcome               included, excluded, awaiting, ongoing, not_retrieved, pending or conflict, for records
                                  that went to full text
  Full-text exclusion reason, Study
  AI (kept unless both exclude)   keep or exclude: the AI second screener's combined answer; blank if not both models
                                  have answered (in screening, that counts as keep)
  AI pass A / B: answer           INCLUDE, MAYBE or EXCLUDE; ERROR or UNPARSED when the call failed or the reply could
                                  not be read (counted as keep)
  AI pass A / B: criterion        the criterion the model named, if any
  AI pass A / B: prompt tokens, completion tokens
                                  as the service reported them, summed over the calls made for this answer
  AI pass A / B: seconds          how long the service took to answer, summed over those calls
  AI pass A / B: asked again      yes if the first reply could not be read and the model was asked again
  AI pass A / B: model as served  the model name the service reported

votes.csv: one row per vote ever cast (a changed vote is a new row; nothing is overwritten)
  When                            when the vote was saved
  Reviewer                        who cast it
  Part in the plan                first reviewer, second reviewer, single reviewer, verifier, full-text assessor
  Record, Record set
  Stage                           calibration, title and abstract, verification sample, full text
  Task                            calibration round, agreement sample, screening, second look, verification sample,
                                  full-text review
  Decision                        include, maybe, exclude; at full text also awaiting, ongoing, not_retrieved
  Full-text exclusion reason      the reason chosen with an exclusion at full text
  Vote number                     1 for the person's first vote on this record at this stage, 2 for their second, ...
  Latest                          yes for the person's last vote on this record at this stage: the one that stands,
                                  unless the next column says yes
  Counts for agreement            yes for the vote used when agreement between two reviewers is worked out
  Written after its sample was finished
                                  yes for a verification-sample vote written after the sample was finished (its last
                                  record had its first vote, or the project lead ended it). From that moment the
                                  verifier can see what was decided before, so such a vote counts for nothing: not
                                  for the record, not for the figures, and it is not in verification.csv. ReviewDesk
                                  refuses these votes; a row like this can only come from a computer that had not
                                  yet received the news that the sample was finished, or from a file edited by hand
  Time recorded                   yes if times were saved with this vote. They are saved only while the project lead
                                  has time recording switched on and the reviewer has not left their own votes out;
                                  summary.json lists when either changed
  Shown (ms)                      milliseconds from the record appearing on the page to the vote, by the clock
  Active (ms)                     the part of that during which the ReviewDesk page was the visible tab of a window
                                  in front
  Longest quiet stretch (ms)      the longest part of the active time with no key pressed and no pointer moved,
                                  pressed or scrolled (not recorded at full text)
  Times shown                     how many times the record was put on screen before this vote; the times add up
                                  over those viewings, for as long as ReviewDesk stayed open in that browser tab (a
                                  page reloaded, or ReviewDesk opened again, starts from nothing)
  A vote by someone the plan did not require on that record is listed too (its Part in the plan shows that they had
  none there); it decided nothing.
  At full text the report is often read in another program or tab, which the page cannot see: Active is then the
  least the assessment took and Shown the most. Time between records, and work done outside ReviewDesk, is not in any
  of these columns; the gaps between one person's votes (the When column) bound it.

verification.csv: one row per record of each verification sample, in the order the verifier was shown them
  Record set, Position shown, Record, Title
  Sample or mixed in              sample: one of the records excluded on the single reviewer's vote alone;
                                  mixed in: one of the records the single reviewer sent on (the verifier could not
                                  tell which was which; their votes on these change nothing)
  Single reviewer                 whose exclusions the sample checks
  Single reviewer's vote          their latest title-and-abstract vote on the record; never shown to the verifier
  AI (kept unless both exclude)   the AI second screener's combined answer; never shown to the verifier
  Verifier                        who screened the sample
  Verifier's vote                 their vote that counts: the latest up to the moment the sample was finished (until
                                  then they could change a vote, having seen nothing; afterwards its votes are
                                  fixed). Include or maybe sends a sampled record to full text
  Verifier voted at               when that vote was saved
  Verifier's votes on it          which of their votes on the record that was (more than 1: they changed their mind
                                  before the sample was finished)
  Shown (ms), Active (ms), Longest quiet stretch (ms)
                                  the times saved with that vote, as in votes.csv
  Title and abstract outcome now  forward or excluded, as the record stands
  Full-text outcome               what became of a record that went to full text
  Full-text exclusion reason      the reason, if it was excluded there

summary.json: the plan as fixed when screening started, the people and their parts, the agreement sample's result,
  the AI's set-up (models, settings, fingerprints of the prompts) and totals (answers, failures, tokens, seconds), the
  second looks, each verification sample's design and figures, the counts at each stage, and every change to time
  recording.

events.csv: the dated actions an analysis of calendar time and of the AI's cost needs, from the audit log
  When, Who, Action               starting screening, each calibration round's end, applying the agreement rule, each
                                  AI run's start and end, a change to the AI's set-up, drawing the verification sample
                                  (and ending it, if the lead ended it as it stood), time recording switched on or
                                  off, anyone's choice about their own votes, and the screening state put back from
                                  an earlier copy (if that falls after a verification sample was drawn, the sample
                                  stood as the project lead's audit log held it from the draw: the same records, in
                                  the same order, and the pages stayed closed to its verifier)
  Figures                         that action's numbers (for example, an AI run's answers and failures)
  ReviewDesk version              the version that logged it
{audit}"""

README_TEAM = ("This is the team's copy: people appear by the initials they use in ReviewDesk, in the Reviewer and Who columns, "
               "in the\ncolumn headings \"Vote of ...\", in summary.json and in the audit log. Do not pass it on outside the team: "
               "download the\ncopy for sharing instead, which shows people as Reviewer A, B, C and leaves the audit log out.")
README_SHARE = ("This is the copy for sharing: each person appears as Reviewer A, B, C ..., the same letter in every file, and "
                "the audit\nlog is left out (events.csv has its dated actions). A letter does not make a person anonymous in a "
                "small team whose\nmembers are named as authors: share it only as the people in it have agreed.")
README_AUDIT = ("\naudit log.csv: every action ReviewDesk logged, with who and when and its details (the team's copy only).\n")


def _plain(x):
    """Data as JSON can hold it everywhere: a number that is not a number (no interval could be worked out) is null."""
    if isinstance(x, float):
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_plain(v) for v in x]
    return x


def evaluation_zip(p: Project, share: bool = False) -> bytes:
    """Everything an evaluation of the screening needs, as one zip (see the module notes). share: the copy for
    sharing, with people as Reviewer A, B, C and no audit log."""
    made = util.now()
    lab = labels(p, share)
    head, rows = records_table(p, lab)
    readme = README.format(version=VERSION, made=made, title=p.settings_ro().get("title") or "",
                           people=README_SHARE if share else README_TEAM, audit="" if share else README_AUDIT)
    files = [("README.txt", readme.encode("utf-8")),
             ("records.csv", exports._csv(head, rows)),
             ("votes.csv", exports._csv(VOTE_COLUMNS, votes_rows(p, lab))),
             ("verification.csv", exports._csv(VERIFICATION_COLUMNS, verification_rows(p, lab))),
             ("events.csv", exports._csv(EVENT_COLUMNS, events_rows(p, lab))),
             ("summary.json", json.dumps(_plain(summary(p, lab)), indent=1, ensure_ascii=False, default=str,
                                         allow_nan=False).encode("utf-8"))]
    if not share:
        files.append(("audit log.csv", exports.audit_csv(p, True)))
    buf = io.BytesIO()
    import time as _time
    stamp = _time.localtime()[:6]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buf.getvalue()


# ================================================================================================ routes
def h_draw(p: Project, me: str, q, body) -> dict:
    """The lead draws a set's verification sample. Answers with the sample as the lead may see it."""
    if body.get("as") is not None and body.get("as") != me:
        raise ProjectError(f"This page was opened as {body.get('as') or 'nobody'}, but ReviewDesk is now set to "
                           f"{me or 'nobody'} (changed in another tab). Reload the page; nothing was saved.")
    set_name = body.get("set", "main")
    draw(p, me, set_name, body.get("size"), body.get("decoys") or 0, body.get("reviewer") or "")
    return {"verify": verification(p, set_name, me)}


def h_end(p: Project, me: str, q, body) -> dict:
    """The lead ends a verification sample that cannot be finished, as it stands."""
    if body.get("as") is not None and body.get("as") != me:
        raise ProjectError(f"This page was opened as {body.get('as') or 'nobody'}, but ReviewDesk is now set to "
                           f"{me or 'nobody'} (changed in another tab). Reload the page; nothing was saved.")
    set_name = body.get("set", "main")
    end(p, me, set_name, body.get("reason") or "")
    return {"verify": verification(p, set_name, me)}


API_POST = {"/api/screening/verify": h_draw, "/api/screening/verify/end": h_end}
