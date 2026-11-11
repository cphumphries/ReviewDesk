"""The steps of the review as a timeline infographic (SVG), for the timeline page, the reporting pack and the
user guides.

Everything comes from workflow.steps_for(): the phases, every step this project's plan uses, in order, who does
each, and the plan's numbers. The infographic adds the branch at the agreement rule (for plans with an agreement
sample), marks the steps done outside ReviewDesk, shows the planned dates and the registration number when the
project has them, and, for a live project, where each step is (readiness.check). svg_generic() draws the same
picture for the reference plan, with no project, for the guides and the README.

The SVG is made here, on the server, with every piece of text escaped, so the page may insert it with
{ html: svg } as the PRISMA page does. It names the app's fonts with ordinary fallbacks and loads nothing."""
from __future__ import annotations

import bisect
import datetime
import html
import re

from . import VERSION, readiness, workflow
from .project import Project

VARIANTS = ("landscape", "portrait")

# ------------------------------------------------------------------------------------------------ the look
INK, INK2, INK3 = "#17202A", "#56616E", "#5E6A77"
RULE, RULE_SOFT, PAPER = "#C9D1DA", "#E9EDF1", "#F6F7F9"
ACTION, ACTION_SOFT = "#2D5A88", "#E7EEF6"
DONE, DONE_SOFT = "#1E7552", "#E3F1EA"
MISSING, MISSING_SOFT = "#8A5300", "#FBF1DE"
DASH = "#6F7B88"
SANS = "Plex, 'IBM Plex Sans', 'Segoe UI', Arial, Helvetica, sans-serif"
SERIF = "Serif4, 'Source Serif 4', Georgia, 'Times New Roman', serif"
FONTS = {"sans": (SANS, 400), "sans_b": (SANS, 600), "serif": (SERIF, 400), "serif_b": (SERIF, 600)}

# Character widths (hundredths of an em) for characters 32 to 126, then ’ – ≥, measured in Chromium with the app's
# own fonts, so lines wrap where the browser would break them. Arial and Segoe UI are a little narrower.
_EM = {k: [int(x) for x in v.split()] for k, v in {
    "sans": "24 28 42 71 60 93 69 24 34 34 45 60 27 40 27 38 60 60 60 60 60 60 60 60 60 60 29 29 60 60 60 48 89 64 65 62 "
            "67 58 56 70 71 40 51 63 50 81 71 71 61 71 64 58 57 68 61 89 61 59 58 32 38 32 60 56 60 53 58 50 58 55 32 53 57 "
            "25 25 53 27 87 57 56 58 58 37 49 35 57 49 77 51 50 46 34 31 34 60 27 59 55",
    "sans_b": "24 31 47 66 60 96 71 26 34 34 56 60 30 40 30 44 60 60 60 60 60 60 60 60 60 60 32 32 60 60 60 49 90 67 66 "
              "64 69 60 58 71 72 42 54 68 52 82 72 71 64 71 66 61 58 69 64 95 66 63 60 33 44 33 60 56 60 56 60 51 60 56 35 "
              "54 59 28 28 56 29 89 59 56 60 60 39 50 37 59 52 82 54 52 50 36 38 36 60 29 59 55",
    "serif": "24 29 38 52 52 93 73 18 36 36 46 53 30 32 30 34 53 53 53 53 53 53 53 53 53 53 30 30 53 53 53 41 83 71 65 66 "
             "74 64 61 72 81 38 38 71 62 94 77 74 62 74 69 54 63 76 71 100 67 67 59 34 34 34 53 51 40 54 62 52 61 53 37 56 "
             "64 33 30 59 33 96 64 58 62 60 46 46 36 63 58 84 57 59 49 36 29 36 53 22 51 55",
    "serif_b": "23 31 42 54 54 95 74 21 38 38 46 55 31 34 31 36 55 55 55 55 55 55 55 55 55 55 31 31 55 55 55 43 87 71 67 "
               "66 74 63 61 72 81 39 39 73 62 93 76 74 64 74 71 56 65 76 71 102 68 67 59 36 36 36 55 53 40 55 63 53 62 54 "
               "37 57 65 34 31 62 34 96 65 59 63 61 48 48 37 64 58 85 58 59 50 36 30 36 55 24 53 55"}.items()}
_MORE = {"\u2019": 95, "\u2013": 96, "\u2265": 97}

_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


def _clean(s) -> str:
    """Text as it may go into the picture: no characters XML forbids, spaces tidied."""
    return re.sub(r"[ \t\r\f\v]+", " ", _BAD_XML.sub("", str(s if s is not None else ""))).strip()


def _x(s) -> str:
    return html.escape(_clean(s), quote=True)


def _tw(text: str, size: float, font: str = "sans") -> float:
    t = _EM[font]
    total = 0
    for ch in text:
        o = ord(ch)
        total += t[o - 32] if 32 <= o < 127 else t[_MORE[ch]] if ch in _MORE else 62
    return total * size / 100 * 1.02          # a little room for fonts that set wider


def _wrap(text: str, width: float, size: float, font: str = "sans") -> list:
    out = []
    for para in _clean(text).split("\n"):
        line = ""
        for word in para.split():
            cand = word if not line else line + " " + word
            if _tw(cand, size, font) <= width:
                line = cand
                continue
            if line:
                out.append(line)
            pieces = _break(word, width, size, font)                    # a word longer than the line is broken
            out.extend(pieces[:-1])
            line = pieces[-1]
        out.append(line)
    return out or [""]


def _units(ch: str, font: str) -> int:
    o, t = ord(ch), _EM[font]
    return t[o - 32] if 32 <= o < 127 else t[_MORE[ch]] if ch in _MORE else 62


def _break(word: str, width: float, size: float, font: str = "sans") -> list:
    """A word too long for the line, broken into pieces that each fit with a hyphen (the last piece without one), as
    long as each piece has at least one character. Each piece is found from running totals of the characters' widths,
    so a word of any length is broken in one pass (measuring every shorter start of it again, as before, took seconds
    for a title of 1,500 characters with no spaces, and for ever for 200,000)."""
    f = size / 100 * 1.02                     # as _tw: the total in font units, times the size
    run = [0]
    for ch in word:
        run.append(run[-1] + _units(ch, font))
    hyphen = _units("-", font)
    out, a, n = [], 0, len(word)
    while (run[n] - run[a]) * f > width and n - a > 1:
        # the longest piece word[a:a+k], 1 <= k <= n-a-1, that fits with a hyphen; at least one character
        k = bisect.bisect_right(run, width / f - hyphen + run[a], a + 1, n) - 1 - a
        while k > 1 and (run[a + k] - run[a] + hyphen) * f > width:     # (rounding at the edge)
            k -= 1
        k = max(1, min(k, n - a - 1))
        while k < n - a - 1 and (run[a + k + 1] - run[a] + hyphen) * f <= width:
            k += 1
        out.append(word[a:a + k] + "-")
        a += k
    out.append(word[a:])
    return out


def _f(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _when(v) -> str:
    """A planned date as people write it: 2026-11-23 as 23 November 2026, 2026-11 as November 2026, else as typed
    (also a date that cannot be, such as 2026-00 or 2026-02-30: month 0 must not come out as December)."""
    v = _clean(v)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", v)
    if m:
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))     # refuses month 0 or 13, day 0 or 31 June
            return f"{d.day} {_MONTHS[d.month - 1]} {d.year}" if m.group(3) else f"{_MONTHS[d.month - 1]} {d.year}"
        except ValueError:
            pass
    return v[:60]


# ------------------------------------------------------------------------------------------------ what it shows
# The reference review's plan (docs/dev/DESIGN.md), with roles for names, for the guides and the README.
GENERIC_SETTINGS = {
    "title": "", "short_title": "Example review",
    "reviewers": [{"id": "A", "name": "Reviewer A", "role": "reviewer"}, {"id": "B", "name": "Reviewer B", "role": "lead"}],
    "screening": {"reviewers": ["A", "B"], "calibration_size": 100, "mode": "sample_then_single", "sample_fraction": 0.20,
                  "kappa_threshold": 0.80, "single_reviewer": "A", "second_reviewer": "B", "ai_second_screener": True,
                  "ai_keep_rate_stop": 0.50, "conflicts_at_title_abstract": "forward"},
    "fulltext": {"mode": "dual"}, "registration": {}, "dates": {},
}

# Words a first-time reviewer may not know, each shown when the plan has the step that uses it.
GLOSSARY = [
    ("Calibration round", "A practice round on the same records, so that both reviewers apply the criteria in the same way.",
     "calibration"),
    ("Agreement sample", "A random share of the records that both reviewers screen, to measure how well they agree.", "sample"),
    ("AI second screener", "AI models that can send a record back for a second look, but never exclude one.", "ai"),
    ("Risk of bias", "How far a study's design or conduct could have skewed its results.", "rob"),
    ("GRADE", "A standard way to rate how sure you can be of each result.", "certainty"),
    ("PRISMA 2020", "The reporting guideline for systematic reviews, with its flow diagram of the records at each stage.",
     "report"),
]

STATUS_WORDS = {"done": "Done", "now": "Now", "blocked": "Something is missing", "later": "Later"}


def _dict(v) -> dict:
    """A setting that should be a group of settings; {} if a project file holds something else there."""
    return v if isinstance(v, dict) else {}


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _people(s: dict, generic: bool) -> dict:
    revs = [r for r in _list(s.get("reviewers")) if isinstance(r, dict)]
    by = {r.get("id"): r for r in revs if isinstance(r.get("id"), str)}
    sc = _dict(s.get("screening"))
    lead = next((r for r in revs if r.get("role") == "lead"), None)
    pair = [by[i] for i in _list(sc.get("reviewers")) if isinstance(i, str) and i in by]
    ini = (lambda r: "" if generic or not r else f" ({_clean(r.get('id'))})")
    pair_ini = "" if generic or len(pair) < 2 else f" ({_clean(pair[0].get('id'))} and {_clean(pair[1].get('id'))})"
    one = by.get(str(sc.get("single_reviewer") or ""))
    two = by.get(str(sc.get("second_reviewer") or ""))
    name = (lambda r, dflt: _clean(r.get("name") or r.get("id")) if r else dflt)
    return {"lead": "The project lead" + ini(lead), "pair": "Both reviewers" + pair_ini,
            "single": name(one, "One reviewer"), "single_who": name(one, "One reviewer") + ini(one),
            "second": name(two, "the second reviewer"), "revs": revs,
            "single_short": _clean(one.get("id")) if one and not generic else name(one, "the reviewer"),
            "second_short": _clean(two.get("id")) if two and not generic else name(two, "the second reviewer")}


def model(s: dict, n_records: int = 0, statuses: dict | None = None, generic: bool = False, title: str = "",
          main: dict | None = None) -> dict:
    """Everything the picture and the page show, in order: the phases, each with its steps (numbered), each step
    with who does it, its plan-specific details, and its status (statuses: {step id: status}, or None)."""
    main = main if isinstance(main, dict) else None         # the main set's state: once screening has started, the plan
    steps = workflow.steps_for(s, n_records, main)          # in force is the one fixed then (workflow.plan_numbers)
    nums = workflow.plan_numbers(s, n_records, main)
    one = nums["one"]                                       # one person does every stage
    sc = _dict(s.get("screening"))
    ppl = _people(s, generic)
    dates = _dict(s.get("dates"))
    reg = _dict(s.get("registration"))
    branch = nums["mode"] == "sample_then_single"
    ai = nums["ai"]
    single = ppl["single"]
    kappa = nums["kappa"]
    try:
        stop = round(float(sc.get("ai_keep_rate_stop") or 0.5) * 100)
    except (TypeError, ValueError):
        stop = 50
    about = f" (about {nums['sample_n']})" if n_records else ""
    dual_ft = _dict(s.get("fulltext")).get("mode", "dual") == "dual"
    ns = {st["id"]: i for i, st in enumerate(steps, 1)}
    ai_behind = (f"With the AI second screener behind: records {ppl['single_short']} excludes that the AI would keep "
                 f"go to {ppl['second_short']}.")
    reg_line = ""
    if _clean(reg.get("id")):
        reg_line = " ".join(v for v in (_clean(reg.get("registry")), _clean(reg.get("id"))) if v) + (
            f", {_when(reg.get('date'))}" if _clean(reg.get("date")) else "")
    both = ("Both reviewers, independently", 2)
    who = {"lead": (ppl["lead"], 1), "searcher": ("Whoever searches", 1), "anyone": ("Anyone in the team", 1),
           "both": both, "team": ("One reviewer", 1) if one else ("The review team", 2),
           "extractors": ("One extracts, the other checks", 2),
           "fulltext": both if dual_ft else ("One reviewer", 1),
           "screeners": (both if nums["mode"] == "dual" else (ppl["single_who"], 1) if nums["mode"] == "single"
                         else ("As the agreement rule decided", 2))}
    alongside = f"Alongside step {ns['sample']}, t" if "sample" in ns else "T"
    facts = {
        "setup": ["Write the question and the eligibility criteria, add the team, fix the screening plan and choose the "
                  "risk-of-bias tool for each study design."],
        "register": ["In PROSPERO or on OSF, before the final searches."],
        "searches": ["Search every database within a short time, and log each search."],
        "import": ["Add each database's export file."],
        "duplicates": ["Check the possible duplicates ReviewDesk finds."],
        "calibration": ([f"A calibration round of {nums['calibration']} records was drawn, but it needs two reviewers. The "
                         "project lead ends it: 'End the calibration round' (Project set-up, Screening plan)."] if one else
                        [f"Both screen the same {nums['calibration']} records, then discuss every disagreement."]),
        "sample": [f"Both screen a random {nums['sample_pct']} of the records{about}, without seeing each other's votes."],
        "ai": [f"{alongside}wo AI models screen every record. Only the project lead sees their answers"
               + (", once the sample is finished." if "sample" in ns else "."),
               f"If they would keep over {stop}% of the records, both reviewers screen everything."],
        "screen": (["Both reviewers screen every record."] if nums["mode"] == "dual" else
                   [ai_behind] if ai else [f"{single} screens every record."]),
        "get_fulltexts": ["Find a PDF for every record sent on."],
        "fulltext": ([nums["fulltext_text"].replace(" alone.", " alone, with a reason for each exclusion.")]
                     if " alone." in nums["fulltext_text"] else
                     ["Both assess every full text, with a reason for each exclusion."] if dual_ft else
                     ["One reviewer assesses every full text, with a reason for each exclusion."]),
        "citations": ["Check reference lists and the studies citing included ones; screen what they find."],
        "extract": ([nums["extract_text"]] if " alone" in nums["extract_text"] else
                    ["Outcome data are extracted by both, independently."]),
        "rob": ([nums["rob_text"]] if " alone" in nums["rob_text"] else
                ["With the tool the plan gives for each study design, then agree."]),
        "update_search": ["Run them again, unchanged, before the analysis; screen the new records."],
        "synthesis": ["A meta-analysis, or a narrative synthesis."],
        "certainty": ["With GRADE: how sure you can be of each main result."],
        "report": ["The PRISMA 2020 flow diagram, the tables and the methods summary."],
    }
    notes = {"setup": [("Planned start", _when(dates.get("start")))],
             "searches": [("Planned", _when(dates.get("searches")))],
             "update_search": [("Planned", _when(dates.get("update_search")))],
             "report": [("Planned finish", _when(dates.get("end")))],
             "register": [("Registered", reg_line)] if reg_line else ([] if generic else [("", "No registration number recorded yet.")])}
    phases, by_phase = [], {}
    for pid, ptitle in workflow.PHASES:
        ph = {"id": pid, "title": ptitle, "steps": []}
        phases.append(ph)
        by_phase[pid] = ph
    out = []
    for i, st in enumerate(steps, 1):
        w, wn = who.get(st.get("who"), (_clean(st.get("who")).capitalize() or "The review team", 1))
        x = {"id": st["id"], "n": i, "phase": st["phase"], "phase_title": st.get("phase_title", ""),
             "title": st["title"], "what": st["what"].replace(" (about —)", ""),       # no records yet: no estimate
             "route": st.get("route", ""), "outside": bool(st.get("outside")),
             "who": w, "who_n": wn,
             "facts": facts.get(st["id"]) or [re.split(r"(?<=\.)\s", st["what"])[0]],
             "notes": [(a, b) for a, b in notes.get(st["id"], []) if b],
             "status": (statuses or {}).get(st["id"]) if statuses is not None else None}
        if st["id"] == "agreement_rule":
            x["question"] = f"Is Cohen's kappa on the agreement sample at least {kappa}?"
            x["facts"] = ["Kappa measures agreement beyond chance: 1 is perfect, 0 is no better than chance."]
        if st["id"] == "screen" and branch:
            x["branch"] = {
                "yes": {"label": "Yes", "when": f"kappa {kappa} or more", "who": ppl["single_who"] + ", alone", "who_n": 1,
                        "summary": f"{single} screens the rest alone.", "facts": [ai_behind] if ai else []},
                "no": {"label": "No", "when": f"kappa below {kappa}", "who": "Both reviewers, independently", "who_n": 2,
                       "summary": "Both reviewers screen the rest, independently.", "facts": []}}
            x["facts"] = [f"If kappa is at least {kappa}, {single} screens the rest alone"
                          + (" with the AI second screener behind them" if ai else "")
                          + "; otherwise both reviewers screen everything."]
        out.append(x)
        by_phase.setdefault(st["phase"], {"id": st["phase"], "title": st.get("phase_title", st["phase"]), "steps": []})
        if by_phase[st["phase"]] not in phases:
            phases.append(by_phase[st["phase"]])
        by_phase[st["phase"]]["steps"].append(x)
    phases = [ph for ph in phases if ph["steps"]]
    nxt = {a["id"]: b for a, b in zip(out, out[1:])}
    for x in out:
        if x.get("branch") and x["id"] in nxt:
            x["branch"]["then"] = f"Either way, the records sent on go to step {nxt[x['id']]['n']}."
    here = ""
    if statuses is not None:
        # where the review is: the first step under way or ready to start, leaving out the steps done outside ReviewDesk
        # (registering the protocol, the synthesis) unless nothing else is open, as the Overview's next step and the side
        # bar do (static/app.js, currentStep)
        open_ = [x for x in out if x["status"] in ("now", "blocked")]
        first = next((x for x in open_ if not x["outside"]), open_[0] if open_ else None)
        here = first["phase"] if first else ""
        for ph in phases:
            sts = [x["status"] for x in ph["steps"]]
            ph["status"] = "done" if all(v == "done" for v in sts) else "here" if ph["id"] == here else \
                "started" if any(v == "done" for v in sts) else "later"
    team = ", ".join(_clean(r.get("name") or r.get("id")) + (f" ({_clean(r.get('id'))}" + (", project lead)" if r.get("role") == "lead" else ")"))
                     for r in ppl["revs"]) if not generic else ""
    pct = nums["sample_pct"]
    if generic:
        sub = ("An example plan: both reviewers screen 100 calibration records and a random 20% agreement sample. If "
               "they agree well enough, one reviewer screens the rest, with the AI second screener behind them.")
        if not (branch and ai and nums["calibration"] == 100 and pct == "20%"):
            sub = "An example plan."
    else:
        sub = _clean(title or s.get("title") or s.get("short_title") or "Untitled review")
    ids = {x["id"] for x in out}
    words = [(t, d) for t, d, need in GLOSSARY if need in ids]
    return {"title": "The steps of a systematic review in ReviewDesk" if generic else "The steps of the review, in order",
            "glossary": words,
            "subtitle": sub, "team": team, "registration": reg_line, "phases": phases, "steps": out, "here": here,
            "branch": branch, "ai": ai, "statuses": statuses is not None, "generic": generic,
            "footer": ("ReviewDesk: free software for the record-handling stages of a systematic review." if generic else
                       f"Made with ReviewDesk {VERSION} on {_when(datetime.date.today().isoformat())}.")}


def statuses_for(p: Project, me: str) -> dict:
    """{step id: "done" | "now" | "blocked" | "later"} from the readiness checks, as every page shows them (shown)."""
    return shown({x.get("id"): x.get("status") for x in readiness.check(p, me).get("steps", []) if isinstance(x, dict)})


def shown(statuses: dict) -> dict:
    """The statuses as the pages show them (static/app.js, shownSteps, makes the same two corrections), so that a later
    step is never shown done while one it builds on is still open, and a finished step is not shown as still going:
    the agreement sample is done once both reviewers have finished it (the agreement rule is then under way), and rating
    certainty is not done while data extraction or risk of bias is still open, because GRADE builds on both."""
    st = dict(statuses)
    if st.get("sample") == "now" and st.get("agreement_rule") in ("now", "blocked"):
        st["sample"] = "done"
    if st.get("certainty") == "done" and any(st.get(k) not in (None, "done") for k in ("extract", "rob")):
        st["certainty"] = "now"
    return st


def project_model(p: Project, me: str) -> dict:
    s = p.settings_ro()
    return model(s, sample_base(p), statuses_for(p, me), title=s.get("title", ""),
                 main=p.state_ro().get("sets", {}).get("main"))


def sample_base(p: Project) -> int:
    """How many records the agreement sample is drawn from: the database searches' records (update-search and
    other-methods records are not in it; see screening.start)."""
    return sum(1 for r in p.records() if (r.get("set") or "main") == "main")


# ------------------------------------------------------------------------------------------------ drawing
class _Out:
    def __init__(self, pfx: str):
        self.pfx = pfx
        self.parts: list = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, size, font="sans", fill=INK, anchor="", extra="") -> None:
        fam, weight = FONTS[font]
        a = [f'x="{_f(x)}"', f'y="{_f(y)}"', f'font-size="{_f(size)}"']
        if fam != SANS:
            a.append(f'font-family="{fam}"')
        if weight != 400:
            a.append(f'font-weight="{weight}"')
        if fill != INK:
            a.append(f'fill="{fill}"')
        if anchor:
            a.append(f'text-anchor="{anchor}"')
        self.add(f'<text {" ".join(a)}{extra}>{_x(s)}</text>')

    def lines(self, x, y, lines, size, lh, font="sans", fill=INK) -> float:
        for k, ln in enumerate(lines):
            self.text(x, y + k * lh, ln, size, font, fill)
        return y + len(lines) * lh

    def rect(self, x, y, w, h, fill="#FFFFFF", stroke=RULE, sw=1.25, rx=7, dash="") -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" rx="{rx}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def line(self, pts, color=ACTION, sw=2, arrow=False, dash="") -> None:
        p = " ".join(f"{_f(a)},{_f(b)}" for a, b in pts)
        m = f' marker-end="url(#{self.pfx}-ar)"' if arrow else ""
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{sw}" stroke-linejoin="round"{d}{m}/>')

    def people(self, x, y, n, color=INK2) -> float:
        """One or two small figures, top-left at (x, y), about 12 px high; returns their width."""
        def one(cx, ring):
            r = (' stroke="#FFFFFF" stroke-width="1.6"' if ring else "")
            self.add(f'<circle cx="{_f(cx)}" cy="{_f(y + 3.4)}" r="3" fill="{color}"{r}/>'
                     f'<path d="M{_f(cx - 5)},{_f(y + 12.5)} a5,5.2 0 0 1 10,0 z" fill="{color}"{r}/>')
        if n >= 2:
            one(x + 5.5, False)
            one(x + 12.5, True)
            return 18
        one(x + 5.5, False)
        return 11

    def glyph(self, cx, cy, status, r=7.5) -> None:
        """The status sign: a tick (done), an arrow (now), an exclamation mark (something is missing), an empty ring (later)."""
        if status == "done":
            self.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="{DONE}"/>'
                     f'<path d="M{_f(cx - 3.4)},{_f(cy + 0.2)} l2.3,2.4 l4.5,-4.9" fill="none" stroke="#FFFFFF" '
                     f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>')
        elif status == "now":
            self.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="{ACTION}"/>'
                     f'<path d="M{_f(cx - 3.3)},{_f(cy)} h6.2 M{_f(cx + 0.4)},{_f(cy - 2.9)} l2.6,2.9 l-2.6,2.9" fill="none" '
                     f'stroke="#FFFFFF" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>')
        elif status == "blocked":
            self.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r}" fill="{MISSING}"/>'
                     f'<path d="M{_f(cx)},{_f(cy - 3.8)} v4.2" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>'
                     f'<circle cx="{_f(cx)}" cy="{_f(cy + 3.4)}" r="1.15" fill="#FFFFFF"/>')
        else:
            self.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{r - 0.75}" fill="#FFFFFF" stroke="{INK3}" stroke-width="1.5"/>')


PAD = 13          # inside a card
TITLE = (15.5, 19.5)       # size, line height
BODY = (13.2, 17.6)
SMALL = (12.4, 16.5)


class _Card:
    """One step, laid out for a width: the number, the title, who does it, the plan's details, and the status."""

    def __init__(self, st: dict, w: float, part: dict | None = None, show_status: bool = False):
        self.st, self.w, self.part, self.show_status = st, w, part, show_status
        inner = w - 2 * PAD
        src = part or st
        self.title = _wrap(src.get("title") or st["title"], inner - 32, TITLE[0], "sans_b")
        self.who = _wrap(src["who"], inner - 24, SMALL[0])
        self.question = _wrap(st.get("question", ""), inner, BODY[0], "sans_b") if st.get("question") and not part else []
        self.facts = [_wrap(t, inner, BODY[0]) for t in src.get("facts", [])]
        self.notes = [(a, _wrap((a + ": " if a else "") + b, inner, SMALL[0], "sans")) for a, b in ([] if part else st.get("notes", []))]
        h = PAD + max(24, len(self.title) * TITLE[1] + 3)
        h += 7 + len(self.who) * SMALL[1]
        if self.question:
            h += 9 + len(self.question) * BODY[1]
        for f in self.facts:
            h += 7 + len(f) * BODY[1]
        for _, ln in self.notes:
            h += 6 + len(ln) * SMALL[1]
        if st.get("outside") and not part:
            h += 6 + SMALL[1]
        if show_status and st.get("status"):
            h += 8 + 22
        self.h = h + PAD - 4

    def draw(self, o: _Out, x: float, y: float, h: float | None = None) -> None:
        st, w = self.st, self.w
        h = h or self.h
        status = st.get("status") if self.show_status else None
        if st.get("outside") and not self.part:
            o.rect(x, y, w, h, stroke=DASH, sw=1.4, dash="6 4")
        elif status == "now":
            o.rect(x, y, w, h, stroke=ACTION, sw=2.6)
        else:
            o.rect(x, y, w, h)
        # the number, on the route
        cx, cy = x + PAD + 12, y + PAD + 11
        fill, ink, ring = {"done": (DONE, "#FFFFFF", DONE), "blocked": (MISSING, "#FFFFFF", MISSING),
                           "later": ("#FFFFFF", ACTION, ACTION)}.get(status or "", (ACTION, "#FFFFFF", ACTION))
        o.add(f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="12" fill="{fill}" stroke="{ring}" stroke-width="1.6"/>')
        o.text(cx, cy + 4.6, str(st["n"]), 13, "sans_b", ink, "middle")
        ty = y + PAD + 15.5 if len(self.title) > 1 else y + PAD + 16
        o.lines(x + PAD + 32, ty, self.title, TITLE[0], TITLE[1], "sans_b")
        yy = y + PAD + max(24, len(self.title) * TITLE[1] + 3) + 7
        src = self.part or st
        o.people(x + PAD, yy + 1, src["who_n"])
        o.lines(x + PAD + 24, yy + 11, self.who, SMALL[0], SMALL[1], "sans", INK2)
        yy += len(self.who) * SMALL[1]
        if self.question:
            yy += 9
            o.lines(x + PAD, yy + 12, self.question, BODY[0], BODY[1], "sans_b")
            yy += len(self.question) * BODY[1]
        for f in self.facts:
            yy += 7
            o.lines(x + PAD, yy + 12.5, f, BODY[0], BODY[1], "sans", INK2 if self.question else INK)
            yy += len(f) * BODY[1]
        for label, ln in self.notes:
            yy += 6
            if label:          # the label in bold, then the date
                first = ln[0]
                rest = first[len(label) + 1:] if first.startswith(label + ":") else first
                o.add(f'<text x="{_f(x + PAD)}" y="{_f(yy + 12)}" font-size="{SMALL[0]}" fill="{ACTION}">'
                      f'<tspan font-weight="600">{_x(label)}:</tspan> {_x(rest)}</text>')
                o.lines(x + PAD, yy + 12 + SMALL[1], ln[1:], SMALL[0], SMALL[1], "sans", ACTION)
            else:
                o.lines(x + PAD, yy + 12, ln, SMALL[0], SMALL[1], "sans", ACTION)
            yy += len(ln) * SMALL[1]
        if st.get("outside") and not self.part:
            yy += 6
            o.text(x + PAD, yy + 12, "Done outside ReviewDesk", SMALL[0], "sans_b", INK2)
            yy += SMALL[1]
        if status:
            sy = y + h - PAD - 6
            o.add(f'<line x1="{_f(x + PAD)}" y1="{_f(sy - 14)}" x2="{_f(x + w - PAD)}" y2="{_f(sy - 14)}" stroke="{RULE_SOFT}" stroke-width="1"/>')
            o.glyph(x + PAD + 7.5, sy, status)
            o.text(x + PAD + 21, sy + 4.4, STATUS_WORDS.get(status, status.capitalize()), SMALL[0], "sans_b",
                   {"done": DONE, "now": ACTION, "blocked": MISSING}.get(status, INK2))


def _branch_block(o: _Out | None, st: dict, rule: dict, x: float, y: float, w: float, gap: float, show: bool) -> float:
    """The agreement rule and what follows it: the rule's card, a fork (Yes / No), the two ways of screening the rest
    side by side, and where both lead. Draws when o is given; returns the height."""
    rc = _Card(rule, w, show_status=show) if rule else None
    half = (w - gap) / 2
    yes = _Card(st, half, st["branch"]["yes"], show)
    no = _Card(st, half, st["branch"]["no"], show)
    bh = max(yes.h, no.h)
    then = _wrap(st["branch"].get("then", ""), w - 2 * PAD - 26, BODY[0]) if st["branch"].get("then") else []
    fork = 70
    total = (rc.h if rc else 0) + fork + bh + (24 + len(then) * BODY[1] + 18 if then else 0)
    if o is None:
        return total
    yy = y
    sx = x + PAD + 12                      # the route runs through the numbers
    nx = x + half + gap + PAD + 12
    if rc:
        rc.draw(o, x, yy)
        yy += rc.h
    # the fork: straight down to Yes, across and down to No
    o.line([(sx, yy), (sx, yy + fork - 3)], arrow=True)
    o.line([(sx, yy + 16), (nx - 10, yy + 16), (nx, yy + 26), (nx, yy + fork - 3)], arrow=True)
    for cx, part in ((sx, st["branch"]["yes"]), (nx, st["branch"]["no"])):
        o.text(cx + 11, yy + 40, part["label"], 13.5, "sans_b", ACTION)
        o.text(cx + 11 + _tw(part["label"], 13.5, "sans_b") + 5, yy + 40, part["when"], 13, "sans", ACTION)
    yy += fork + 1
    yes.draw(o, x, yy, bh)
    no.draw(o, x + half + gap, yy, bh)
    yy += bh
    if then:
        jy = yy + 14
        o.line([(sx, yy), (sx, jy + 10)], arrow=False)
        o.line([(nx, yy), (nx, jy), (sx + 10, jy), (sx, jy + 10)], arrow=False)
        o.line([(sx, jy + 10), (sx, jy + 22)], arrow=True)
        by = jy + 24
        bh2 = len(then) * BODY[1] + 16
        o.rect(x, by, w, bh2, fill=ACTION_SOFT, stroke=ACTION_SOFT, rx=7)
        o.lines(x + PAD + 26, by + 21, then, BODY[0], BODY[1], "sans", INK)
        o.add(f'<path d="M{_f(x + PAD + 6)},{_f(by + bh2 / 2 - 5)} l6,5 l-6,5" fill="none" stroke="{ACTION}" stroke-width="2" '
              'stroke-linecap="round" stroke-linejoin="round"/>')
    return total


def _header(o: _Out | None, m: dict, x: float, y: float, w: float, key_w: float) -> float:
    """Title, the project, the team and registration on the left; the key on the right. Returns the height."""
    lw = w - key_w - 40
    title = _wrap(m["title"], lw, 32, "serif_b")
    sub = _wrap(m["subtitle"], lw, 17 if not m["generic"] else 15.5, "serif" if not m["generic"] else "sans")
    if not m["generic"] and len(sub) > 2:
        sub = sub[:2]
        sub[1] = sub[1][: max(0, len(sub[1]) - 2)].rstrip() + "…"
    meta = []
    if m["team"]:
        meta += _wrap("Team: " + m["team"], lw, 13)
    if m["registration"]:
        meta += _wrap("Registered: " + m["registration"], lw, 13)
    hl = len(title) * 38 + 8 + len(sub) * 23 + (8 + len(meta) * 18 if meta else 0)
    keys = [("one", "One person"), ("two", "Both reviewers, independently (neither sees the other's answers)"),
            ("outside", "Done outside ReviewDesk")]
    kh = 0
    key_lines = [(k, _wrap(t, key_w - 40, 12.6)) for k, t in keys]
    kh = sum(len(ln) * 16.5 + 9 for _, ln in key_lines) + (34 if m["statuses"] else 0)
    if o is not None:
        yy = o.lines(x, y + 30, title, 32, 38, "serif_b")
        yy = o.lines(x, yy - 38 + 8 + 23 + 2, sub, 17 if not m["generic"] else 15.5, 23,
                     "serif" if not m["generic"] else "sans", INK2)
        if meta:
            o.lines(x, yy - 23 + 8 + 18 + 4, meta, 13, 18, "sans", INK2)
        kx, ky = x + w - key_w, y + 4
        for k, ln in key_lines:
            if k == "outside":
                o.rect(kx, ky + 1, 26, 14, stroke=DASH, sw=1.3, dash="5 3", rx=3)
            else:
                o.people(kx + (2 if k == "two" else 6), ky + 2, 2 if k == "two" else 1)
            o.lines(kx + 36, ky + 12, ln, 12.6, 16.5, "sans", INK2)
            ky += len(ln) * 16.5 + 9
        if m["statuses"]:
            sx = kx
            ky += 6
            for stt in ("done", "now", "blocked", "later"):
                o.glyph(sx + 7.5, ky + 7, stt)
                o.text(sx + 20, ky + 11.4, STATUS_WORDS[stt], 12.6, "sans", INK2)
                sx += 20 + _tw(STATUS_WORDS[stt], 12.6) + 16
    return max(hl, kh)


def _arrow_defs(o: _Out) -> None:
    o.add(f'<defs><marker id="{o.pfx}-ar" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="6.5" markerHeight="6.5" '
          f'orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M0,0.8 L9.2,5 L0,9.2 z" fill="{ACTION}"/></marker></defs>')


def _svg_open(o: _Out, m: dict, W: float, H: float, variant: str) -> str:
    n = len(m["steps"])
    desc = (f"{n} steps in {len(m['phases'])} phases: "
            + "; ".join(f"{ph['title']} (steps {ph['steps'][0]['n']} to {ph['steps'][-1]['n']})" if len(ph["steps"]) > 1
                        else f"{ph['title']} (step {ph['steps'][0]['n']})" for ph in m["phases"]) + ".")
    if m["branch"]:
        r = next((x for x in m["steps"] if x.get("question")), None)
        if r:
            desc += f" At step {r['n']}, the agreement rule decides whether one reviewer or both screen the rest."
    if m["here"]:
        desc += " You are here: " + next(ph["title"] for ph in m["phases"] if ph["id"] == m["here"]) + "."
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(W)}" height="{_f(H)}" viewBox="0 0 {_f(W)} {_f(H)}" '
            f'font-family="{SANS}" role="img" aria-labelledby="{o.pfx}-t {o.pfx}-d" data-variant="{variant}">'
            f'<title id="{o.pfx}-t">{_x(m["title"] + (": " + m["subtitle"] if not m["generic"] else ""))}</title>'
            f'<desc id="{o.pfx}-d">{_x(desc)}</desc>')


def _here_tag(o: _Out, cx: float, y: float) -> None:
    """'You are here', with a small pointer below it, centred on cx with its bottom at y."""
    t = "You are here"
    w = _tw(t, 13, "sans_b") + 20
    o.rect(cx - w / 2, y - 30, w, 23, fill=ACTION, stroke=ACTION, sw=1, rx=11.5)
    o.add(f'<path d="M{_f(cx - 6)},{_f(y - 7.5)} l6,6 l6,-6 z" fill="{ACTION}"/>')
    o.text(cx, y - 14, t, 13, "sans_b", "#FFFFFF", "middle")


def _glossary(o: _Out | None, m: dict, x: float, y: float, w: float, cols: int = 1) -> float:
    """'Words used here': the terms a first-time reviewer may not know. Returns the height."""
    if not m.get("glossary"):
        return 0
    cw = (w - 2 * PAD - (cols - 1) * 24) / cols
    entries = [(_wrap(t, cw, 13, "sans_b"), _wrap(d, cw, 12.6)) for t, d in m["glossary"]]
    per = -(-len(entries) // cols)
    colh = []
    for c in range(cols):
        colh.append(sum(len(a) * 17 + len(b) * 16.5 + 10 for a, b in entries[c * per:(c + 1) * per]))
    h = 44 + max(colh) + PAD - 6
    if o is None:
        return h
    o.rect(x, y, w, h, fill=PAPER, stroke=PAPER, rx=7)
    o.text(x + PAD, y + 28, "Words used here", 16, "serif_b", INK)
    for c in range(cols):
        yy = y + 44
        for a, b in entries[c * per:(c + 1) * per]:
            xx = x + PAD + c * (cw + 24)
            yy = o.lines(xx, yy + 12, a, 13, 17, "sans_b", INK) - 12
            yy = o.lines(xx, yy + 12, b, 12.6, 16.5, "sans", INK2) - 12 + 10
    return h


# ------------------------------------------------------------------------------------------------ landscape
def _landscape(m: dict, pfx: str) -> str:
    W, M, G = 1680, 38, 12
    phases = m["phases"]
    units = [2.3 if (ph["id"] == "screen" and m["branch"]) else 1 for ph in phases]
    u = (W - 2 * M - G * (len(phases) - 1)) / sum(units)
    widths = [k * u + (k - 1) * G for k in units]
    show = m["statuses"]
    o = _Out(pfx)
    hh = _header(None, m, M, M, W - 2 * M, 430)
    band_y = M + hh + 46
    name_lines = [_wrap(ph["title"], w - 46, 17, "serif_b") for ph, w in zip(phases, widths)]
    band_h = max(58, max(len(n) for n in name_lines) * 20 + 26)
    top = band_y + band_h + 26
    # lay out each column
    cols = []
    for ph, w in zip(phases, widths):
        items, y = [], top
        steps = ph["steps"]
        rule = next((s for s in steps if s.get("question")), None)
        scr = next((s for s in steps if s.get("branch")), None)
        plain = [s for s in steps if s is not rule and s is not scr]
        k = 0
        while k < len(plain):
            s = plain[k]
            if w > u * 1.5 and s["id"] == "sample" and k + 1 < len(plain) and plain[k + 1]["id"] == "ai":
                a, b = _Card(s, (w - G) / 2, show_status=show), _Card(plain[k + 1], (w - G) / 2, show_status=show)
                hh2 = max(a.h, b.h)
                items.append(("pair", y, hh2, a, b))
                y += hh2 + 30
                k += 2
                continue
            c = _Card(s, w, show_status=show)
            items.append(("card", y, c.h, c))
            y += c.h + 30
            k += 1
        if scr:
            bh = _branch_block(None, scr, rule, 0, 0, w, G, show)
            items.append(("branch", y, bh, scr, rule))
            y += bh + 30
        cols.append((items, y - 30))
    # the glossary goes under the two neighbouring columns that end highest
    xs = [M + sum(widths[:k]) + G * k for k in range(len(widths))]
    best = None
    for k in range(len(cols) - 1):
        if widths[k] > u * 1.5 or widths[k + 1] > u * 1.5:
            continue
        b = max(cols[k][1], cols[k + 1][1])
        if best is None or b < best[0] - 1 or (abs(b - best[0]) <= 1 and k > best[1]):
            best = (b, k)
    gl = None
    if best and m.get("glossary"):
        gw = widths[best[1]] + widths[best[1] + 1] + G
        gl = (xs[best[1]], best[0] + 40, gw)
        cols.append(([], best[0] + 40 + _glossary(None, m, 0, 0, gw)))
    H = max(bottom for _, bottom in cols) + 40 + 26 + M - 10
    out = [_svg_open(o, m, W, H, "landscape")]
    _arrow_defs(o)
    o.rect(0, 0, W, H, fill="#FFFFFF", stroke="#FFFFFF", sw=0, rx=0)
    _header(o, m, M, M, W - 2 * M, 430)
    # the phases, as one band of arrows
    x = M
    for i, (ph, w, nl) in enumerate(zip(phases, widths, name_lines)):
        here = ph.get("status") == "here"
        done = ph.get("status") == "done"
        tip = 16
        x0, x1 = x - (G / 2 if i else 0), x + w + G / 2 + (0 if i < len(phases) - 1 else -G / 2)
        notch = tip if i else 0
        last = i == len(phases) - 1
        pts = [(x0, band_y), (x1 - (0 if last else 0), band_y), (x1 + (0 if last else tip), band_y + band_h / 2),
               (x1, band_y + band_h), (x0, band_y + band_h), (x0 + notch, band_y + band_h / 2)]
        if last:
            pts = [(x0, band_y), (x1, band_y), (x1, band_y + band_h), (x0, band_y + band_h), (x0 + notch, band_y + band_h / 2)]
        fill = ACTION if here else DONE_SOFT if done else ACTION_SOFT
        o.add(f'<polygon points="{" ".join(f"{_f(a)},{_f(b)}" for a, b in pts)}" fill="{fill}" stroke="#FFFFFF" stroke-width="3" stroke-linejoin="round"/>')
        tx = x + (12 if i else 2) + 8
        ink = "#FFFFFF" if here else DONE if done else ACTION
        if done:
            o.glyph(tx + 7, band_y + band_h / 2, "done", 8)
            tx += 22
        ly = band_y + band_h / 2 - (len(nl) - 1) * 10 + 6
        o.lines(tx, ly, nl, 17, 20, "serif_b", ink)
        if here:
            _here_tag(o, x + w / 2, band_y - 4)
        x += w + G
    # the steps
    x = M
    for (items, _), w in zip(cols, widths):
        sx = x + PAD + 12
        prev_bottom = None
        for it in items:
            kind, y, h = it[0], it[1], it[2]
            if prev_bottom is not None:
                o.line([(sx, prev_bottom), (sx, y - 2)], arrow=True)
            if kind == "card":
                it[3].draw(o, x, y)
            elif kind == "pair":
                it[3].draw(o, x, y, h)
                it[4].draw(o, x + (w + G) / 2, y, h)
            else:
                _branch_block(o, it[3], it[4], x, y, w, G, show)
            prev_bottom = y + h
        x += w + G
    if gl:
        _glossary(o, m, gl[0], gl[1], gl[2])
    o.text(M, H - M + 10, m["footer"], 12.4, "sans", INK3)
    o.text(W - M, H - M + 10, "Follow the numbers: each step builds on the ones before it.", 12.4, "sans", INK3, "end")
    out += o.parts
    out.append("</svg>")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ portrait
def _portrait(m: dict, pfx: str) -> str:
    W, M = 1040, 40
    RAIL = 196
    G = 30
    x0 = M + RAIL
    aw = W - x0 - M
    per_row = 3
    cw = (aw - G * (per_row - 1)) / per_row
    show = m["statuses"]
    o = _Out(pfx)
    hh = _header(None, m, M, M, W - 2 * M, 360)
    y = M + hh + 44
    blocks = []
    for ph in m["phases"]:
        steps = ph["steps"]
        rule = next((s for s in steps if s.get("question")), None)
        scr = next((s for s in steps if s.get("branch")), None)
        plain = [s for s in steps if s is not rule and s is not scr]
        rows, yy = [], y
        name = _wrap(ph["title"], RAIL - 46, 18, "serif_b")
        for k in range(0, len(plain), per_row):
            cards = [_Card(s, cw, show_status=show) for s in plain[k:k + per_row]]
            rh = max(c.h for c in cards)
            rows.append(("cards", yy, rh, cards))
            yy += rh + 26
        if scr:
            bh = _branch_block(None, scr, rule, 0, 0, aw, G, show)
            rows.append(("branch", yy, bh, scr, rule))
            yy += bh + 26
        bottom = max(yy - 26, y + len(name) * 22 + 40)
        blocks.append((ph, y, bottom, name, rows))
        y = bottom + 42
    # the glossary: beside the last row when it leaves two places empty, otherwise below everything
    gl = None
    last = blocks[-1][4][-1] if blocks and blocks[-1][4] else None
    if m.get("glossary") and last and last[0] == "cards" and len(last[3]) <= per_row - 2:
        gx = x0 + len(last[3]) * (cw + G)
        gw = W - M - gx
        gh = _glossary(None, m, 0, 0, gw, 2 if gw > 420 else 1)
        gl = (gx, last[1], gw, 2 if gw > 420 else 1)
        end = max(blocks[-1][2], last[1] + gh)
        H = end + 46 + M
    else:
        gy = y - 42 + 40
        gh = _glossary(None, m, 0, 0, W - x0 - M, 2)
        gl = (x0, gy, W - x0 - M, 2) if gh else None
        H = gy + gh + (46 if gh else 6) + M
    out = [_svg_open(o, m, W, H, "portrait")]
    _arrow_defs(o)
    o.rect(0, 0, W, H, fill="#FFFFFF", stroke="#FFFFFF", sw=0, rx=0)
    _header(o, m, M, M, W - 2 * M, 360)
    spine = M + 15
    o.add(f'<line x1="{_f(spine)}" y1="{_f(blocks[0][1] + 12)}" x2="{_f(spine)}" y2="{_f(blocks[-1][1] + 12)}" stroke="{ACTION_SOFT}" stroke-width="10" stroke-linecap="round"/>')
    for i, (ph, top, bottom, name, rows) in enumerate(blocks):
        here = ph.get("status") == "here"
        done = ph.get("status") == "done"
        if i:
            o.add(f'<line x1="{_f(M + 44)}" y1="{_f(top - 21)}" x2="{_f(W - M)}" y2="{_f(top - 21)}" stroke="{RULE_SOFT}" stroke-width="1.2"/>')
        if done:
            o.glyph(spine, top + 12, "done", 11)
        else:
            o.add(f'<circle cx="{_f(spine)}" cy="{_f(top + 12)}" r="11" fill="{ACTION if here else "#FFFFFF"}" stroke="{ACTION}" stroke-width="2.4"/>')
            if here:
                o.add(f'<circle cx="{_f(spine)}" cy="{_f(top + 12)}" r="4" fill="#FFFFFF"/>')
        o.lines(M + 40, top + 18, name, 18, 22, "serif_b", DONE if done else ACTION)
        first, last = ph["steps"][0]["n"], ph["steps"][-1]["n"]
        o.text(M + 40, top + 18 + len(name) * 22, f"Step {first}" if first == last else f"Steps {first} to {last}",
               12.6, "sans", INK2)
        if here:
            t = "You are here"
            tw = _tw(t, 12.6, "sans_b") + 18
            ty = top + 18 + len(name) * 22 + 14
            o.rect(M + 40, ty, tw, 22, fill=ACTION, stroke=ACTION, sw=1, rx=11)
            o.text(M + 40 + tw / 2, ty + 15.2, t, 12.6, "sans_b", "#FFFFFF", "middle")
        for r in rows:
            kind, ry, rh = r[0], r[1], r[2]
            if kind == "cards":
                xx = x0
                for k, c in enumerate(r[3]):
                    c.draw(o, xx, ry, rh)
                    if k + 1 < len(r[3]):
                        ay = ry + PAD + 11
                        if r[3][k + 1].st["id"] != "ai":          # the AI runs alongside the sample, not after it
                            o.line([(xx + cw + 3, ay), (xx + cw + G - 3, ay)], arrow=True)
                    xx += cw + G
            else:
                _branch_block(o, r[3], r[4], x0, ry, aw, G, show)
    if gl:
        _glossary(o, m, gl[0], gl[1], gl[2], gl[3])
    o.text(M, H - M + 8, m["footer"], 12.4, "sans", INK3)
    out += o.parts
    out.append("</svg>")
    return "\n".join(out)


def render(m: dict, variant: str = "landscape") -> str:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown layout {variant!r}: use landscape or portrait.")
    pfx = "tl" + variant[0]
    return _landscape(m, pfx) if variant == "landscape" else _portrait(m, pfx)


def svg(p: Project, me: str, variant: str = "landscape") -> str:
    """The infographic for this project: its plan, its dates and registration, and where each step is for me."""
    return render(project_model(p, me), variant)


def svg_generic(variant: str = "landscape") -> str:
    """The infographic for the reference plan, with no project and no statuses, for the user guide and the README."""
    return render(model(GENERIC_SETTINGS, 0, None, generic=True), variant)


# ------------------------------------------------------------------------------------------------ routes
def _public_step(x: dict) -> dict:
    out = {k: x[k] for k in ("id", "n", "phase", "phase_title", "title", "what", "route", "outside", "who", "who_n",
                             "facts", "status") if k in x}
    out["notes"] = [{"label": a, "text": b} for a, b in x.get("notes", [])]
    if x.get("question"):
        out["question"] = x["question"]
    if x.get("branch"):
        out["branch"] = x["branch"]
    return out


def h_timeline(p, me, q, body):
    """The steps in order, by phase, with who does each, the plan's numbers and where each step is; and the
    infographic in both layouts (left out with ?svg=0, as the Overview's strip does not need it)."""
    m = project_model(p, me)
    out = {"steps": [_public_step(x) for x in m["steps"]],
           "phases": [{"id": ph["id"], "title": ph["title"], "status": ph.get("status", ""),
                       "steps": [x["id"] for x in ph["steps"]]} for ph in m["phases"]],
           "statuses": {x["id"]: x["status"] for x in m["steps"]}, "here": m["here"], "branch": m["branch"],
           "ai": m["ai"], "registration": m["registration"]}
    if (q.get("svg") or ["1"])[0] != "0":
        out["svg"] = render(m, "landscape")
        out["svg_portrait"] = render(m, "portrait")
    return out


def _download(variant):
    return lambda p, me: svg(p, me, variant).encode("utf-8")


API_GET = {"/api/timeline": h_timeline}
API_POST = {}
DOWNLOADS = {
    "timeline_svg": ("steps of the review.svg", "image/svg+xml", _download("landscape"), lambda p, me: ""),
    "timeline_svg_portrait": ("steps of the review (portrait).svg", "image/svg+xml", _download("portrait"), lambda p, me: ""),
}
