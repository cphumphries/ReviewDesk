"""What is missing before each step of the review can start, and how to put it right.

check(p, me) is the one place that decides it. The Overview, the guided set-up, each step's page and the server's
own refusals all use it, so the same words appear everywhere. The rule: an item blocks only when going on without it
would make the method wrong or impossible (screening.start refuses, for example); anything else is a warning.

Each step of workflow.steps_for() gets a status:
  done     the step is finished
  now      it is under way, or can be started now
  blocked  it could start, but something it needs is missing
  later    an earlier step comes first
and lists of missing items (they block) and warnings (they do not), each
  {"key", "text", "fix": route, "fix_label", "who": "lead" | "me" | "other", "blocks"}
said in plain words: what is missing, why it matters, and where to put it right. Only what can and must be done now
counts: a step that is "later" lists nothing as missing (its items wait in "later"), and an item is "me" only when the
person looking can act on it. Their own unfinished work is missing (needed now); what they are waiting for someone
else to do is said as "Waiting for ...", marked "waiting": true, listed with the warnings and counted as neither.

Who sees what. The project lead sees everything. Anyone else sees their own tasks and what they are waiting for;
the lead's to-do items appear to them only as "Waiting for the project lead to ...", and the lead's private ones (the
AI's state and the AI key) not at all, so that nothing here can hint at what the AI said.

Also here: the set-up summary (a plain list of every choice, to check against the protocol), as a page and a text
download."""
from __future__ import annotations

import os
import re
import textwrap
from collections import Counter

from . import VERSION, ai, evaluation, fulltext, prisma, review, robtools, screening, userconfig, util, workflow
from .dedup import fingerprint, kept_first
from .project import DamagedFile, Project, PermissionDenied, SETS

HERE = os.path.dirname(os.path.abspath(__file__))

# The page each step's items are shown on (readinessBanner(page, <this>) in the browser).
PAGE = {"setup": "setup", "register": "setup", "searches": "searches", "import": "import", "duplicates": "duplicates",
        "calibration": "screen", "sample": "screen", "ai": "ai", "agreement_rule": "screen", "screen": "screen",
        "get_fulltexts": "fulltext", "fulltext": "fulltext", "citations": "import", "extract": "extract", "rob": "extract",
        "update_search": "import", "synthesis": "", "certainty": "certainty", "report": "report"}

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
          "December")
SCREEN_STEPS = ("calibration", "sample", "screen")      # the first of these in the plan is where screening starts

# The screening plan's own messages (screening.check_plan), with what the item is for: a short phrase for "waiting
# for the project lead to ...", where to put it right, and (for the shortest messages) why it matters.
PLAN_MESSAGES = (
    ("Add the second reviewer to the team", "people_two", "add the second reviewer", "setup/people", "Add a person", ""),
    ("The AI second screener needs a second person", "plan_ai_people",
     "add a second person for the AI second screener, or untick it", "setup/plan", "Open the screening plan", ""),
    ("The first and second reviewers must be two different", "plan_same", "choose two different reviewers",
     "setup/plan", "Choose the reviewers", ""),
    ("Full-text review is set to both reviewers", "plan_ft_two",
     "choose who assesses the full texts", "setup/fulltext", "Open the full-text plan", ""),
    ("Records from the", "plan_other_two", "choose how records from the other sets are screened", "setup/fulltext",
     "Open the full-text plan", ""),
    ("Choose who screens every record", "plan_single_all", "choose who screens every record", "setup/plan",
     "Choose who screens", ""),
    ("Choose the two reviewers who screen in duplicate", "plan_two", "choose the two reviewers who screen in duplicate",
     "setup/plan", "Choose the reviewers", ""),
    ("One of the two screening reviewers is not in", "plan_gone", "choose the two screening reviewers again",
     "setup/plan", "Open the screening plan", " Choose them again from the people in the project."),
    ("Choose who screens on their own", "plan_single", "choose who screens on their own after the sample",
     "setup/plan", "Choose who screens alone", ""),
    ("With the AI second screener, choose a second reviewer", "plan_second",
     "choose the second reviewer for the AI second screener", "setup/plan", "Choose the second reviewer",
     " Records the single reviewer excludes but the AI would keep go to this person (Project set-up, Screening plan)."),
    ("The person who screens alone must be one of", "plan_single_in_sample",
     "make the person who screens alone one of the two who screen the agreement sample", "setup/plan",
     "Open the screening plan", ""),
    ("The second reviewer must be one of", "plan_second_in_sample",
     "make the second reviewer one of the two who screen the agreement sample", "setup/plan", "Open the screening plan",
     " (Project set-up, Screening plan)"),
    ("The agreement sample should be between", "plan_fraction", "correct the size of the agreement sample", "setup/plan",
     "Open the screening plan", " (Project set-up, Screening plan)"),
    ("The kappa needed for single screening", "plan_kappa", "correct the kappa needed for single screening", "setup/plan",
     "Open the screening plan", " (Project set-up, Screening plan)"),
    ("Write the eligibility criteria first", "criteria", "write the eligibility criteria", "setup/criteria",
     "Write the criteria", ""),
    ("Write the review question first", "question", "write the review question", "setup/review", "Write the question",
     " Everyone screens against it: it is shown beside every record, and given to the AI screener if you use it."),
    ("Record the risk-of-bias plan first", "rob_plan", "record the risk-of-bias plan", "setup/robplan", "Record the plan", ""),
    ("The risk-of-bias plan says studies of the design", "rob_plan_reason",
     "give the reason a design in the risk-of-bias plan has no assessment", "setup/robplan", "Open the plan", ""),
)

_GUIDE: dict = {}


def _guidance() -> dict:
    if not _GUIDE:
        _GUIDE.update(util.read_json(os.path.join(HERE, "guidance.json"), {}) or {})
    return _GUIDE


# Words that show a search log entry (typed by hand) is a search of a database in the worksheet's plan.
DB_WORDS = {"medline": ("medline", "pubmed"), "embase": ("embase",), "central": ("central", "cochrane"),
            "cinahl": ("cinahl",), "psycinfo": ("psycinfo", "psych info"), "scopus": ("scopus",),
            "wos": ("web of science", "wos", "science citation"), "epistemonikos": ("epistemonikos",)}


# ================================================================================================ small helpers
def _date(iso) -> str:
    """2026-11-30 as 30 November 2026 (anything else as it is)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return str(iso or "")
    return f"{int(m.group(3))} {MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def _join(xs) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _item(key: str, text: str, fix: str = "", label: str = "", who: str = "lead", blocks: bool = False, short: str = "",
          private: bool = False, worded: bool = False, waiting: bool = False) -> dict:
    """short: what the lead has to do, for the words anyone else sees ("Waiting for the project lead to <short>").
    private: the lead's own (the AI's state, the lead's key); never shown to anyone else.
    worded: already written for the person looking (a lead item made only for someone who is not the lead).
    waiting: what the person looking is waiting for someone else to do: information, never needed from them."""
    return {"key": key, "text": text, "fix": fix, "fix_label": label, "who": who, "blocks": bool(blocks) and not waiting,
            "short": short, "private": private, "worded": worded, "waiting": waiting}


def _waiting(key: str, text: str, who: str = "other") -> dict:
    """'Waiting for <someone> to ...': information for the person looking (who is the one waited for)."""
    return _item(key, text, "", "", who=who, worded=True, waiting=True)


def _plan_meta(msg: str) -> tuple:
    for prefix, key, short, fix, label, extra in PLAN_MESSAGES:
        if msg.startswith(prefix):
            return key, short, fix, label, extra
    return "plan:" + msg[:40], "finish the screening plan", "setup/plan", "Open the screening plan", ""


def _search_name(e: dict) -> str:
    return f"the {(e.get('database') or 'unnamed').strip()} search of {_date(e.get('date')) or 'an unknown date'}"


def search_mismatch(e: dict, files: list) -> str:
    """The search log's own message when the files linked to a search do not hold the number of records it found
    (the same words as the Search log page)."""
    got = sum(int((m.get("parse") or {}).get("records") or 0) for m in files)
    want = str(e.get("results") or "").replace(",", "").strip()
    if not files or not want.isdigit() or int(want) == got:
        return ""
    return (f"The search found {util.plural(int(want), 'record')} but the files linked to it hold {got:,}. "
            + ("A part of the export may be missing (databases often export at most a few thousand at a time)."
               if got < int(want) else "Check the number recorded, or whether a file was added twice."))


def registered(s: dict) -> bool:
    reg = s.get("registration") or {}
    return bool(str(reg.get("id") or "").strip() or str(reg.get("url") or "").strip())


def _form_unchanged(f: dict, p: Project) -> bool:
    """The extraction form is still the built-in general form (saved or not), item for item (its Study design item
    offering the risk-of-bias plan's designs, as review.builtin_form gives it)."""
    def shape(sections):
        return [(sec.get("title"), [(x.get("id"), x.get("label"), x.get("type"), x.get("mode") or "check",
                                     list(x.get("options") or []), [c.get("id") for c in x.get("columns") or []])
                                    for x in sec.get("fields") or []]) for sec in sections or []]
    return not f.get("rev") or shape(f.get("sections")) == shape(review.builtin_form(p)["sections"])


# ================================================================================================ the facts
class _Facts:
    """What the checks look at, read once per check (the slow parts are memoised by the project)."""

    def __init__(self, p: Project, me: str):
        self.p, self.me = p, me
        s = self.s = p.settings_ro()
        self.people = [r for r in s.get("reviewers") or [] if isinstance(r, dict)]
        self.names = {r["id"]: (r.get("name") or r["id"]) for r in self.people if r.get("id")}
        self.lead_id = next((r["id"] for r in self.people if r.get("role") == "lead"), "")
        self.lead_name = self.names.get(self.lead_id, "the project lead")
        self.lead = bool(me) and me == self.lead_id
        self.member = me in self.names
        st = self.st = p.state_ro()
        self.sets = {k: (v or {}) for k, v in (st.get("sets") or {}).items()}
        self.main = self.sets.get("main", {})
        self.phase = self.main.get("phase") or "not_started"
        self.started = {k for k, v in self.sets.items() if v.get("phase") not in (None, "not_started")}
        self.main_started = "main" in self.started
        self.recs = p.records()
        self.n_set = Counter(r.get("set") for r in self.recs)
        self.every = p.imports(include_missing=True)
        self.processed = set(st.get("imports_order") or [])
        self.log = p.search_log()
        try:
            self.ws = p.worksheet() or {}
        except DamagedFile:
            self.ws = {}
        self.plan = screening.plan(p, "main")
        self.ai_planned = bool(self.plan.get("ai_second_screener")) and self.plan.get("mode") != "dual"
        self.registered = registered(s)
        self.s_done = False
        self._ta: dict = {}

    def name(self, rid: str) -> str:
        return self.names.get(rid, rid or "someone")

    # --- imports
    def unprocessed(self, set_name: str | None = None) -> list:
        return [m for m in self.every if not m.get("_missing") and m["id"] not in self.processed
                and (set_name is None or m.get("set") == set_name)]

    # --- screening
    def ta_counts(self, set_name: str) -> Counter:
        """A set's outcome counts, as this person may see them (blind to the AI for anyone but the lead)."""
        if set_name not in self.started:
            return Counter()
        if set_name not in self._ta:
            if not self.lead and self.sets[set_name].get("ai_net_active"):
                self._ta[set_name] = Counter(screening.counts_blind_to_ai(self.p, set_name))
            else:
                self._ta[set_name] = Counter(o["status"] for o in screening.ta_outcomes(self.p, set_name).values())
        return self._ta[set_name]

    def ta_open(self) -> int:
        n = 0
        for k in self.started:
            c = self.ta_counts(k)
            n += c.get("pending", 0) + c.get("not_open", 0) + c.get("conflict", 0)
        return n

    def ft(self) -> dict:
        return screening.ft_outcomes(self.p)

    def ft_counts(self) -> Counter:
        return Counter(o["status"] for o in self.ft().values())

    def tasks(self) -> list:
        return screening.tasks_for(self.p, self.me) if self.member else []

    def needs_pdf(self) -> list:
        """Reports at full text with no PDF, except those decided as excluded, awaiting information, ongoing or not
        retrieved: there is nothing to look for there."""
        return [rid for rid, o in sorted(self.ft().items())
                if o["status"] not in ("excluded", "awaiting", "not_retrieved", "ongoing") and not fulltext.has_pdf(self.p, rid)]

    def studies(self) -> list:
        return review.studies(self.p)


# ================================================================================================ the checks, step by step
def _setup(F: _Facts) -> dict:
    s, p = F.s, F.p
    out = []
    if not (s.get("title") or "").strip():
        out.append(_item("title", "Give the review a title (Project set-up, The review). It names the project for the team "
                         "and heads the reports and downloads.", "setup/review", "Write the title",
                         short="give the review a title"))
    pl = F.plan
    if not F.main_started:
        for msg in screening.check_plan(p, pl):
            key, short, fix, label, extra = _plan_meta(msg)
            out.append(_item(key, msg + extra, fix, label, blocks=True, short=short))
    else:
        if not (s.get("question") or "").strip():
            key, short, fix, label, extra = _plan_meta("Write the review question first")
            out.append(_item(key, "Write the review question first (Project set-up)." + extra, fix, label, blocks=True, short=short))
    crit = s.get("criteria") or {}
    inc = [x for x in crit.get("include") or [] if str(x).strip()]
    exc = [x for x in crit.get("exclude") or [] if str(x).strip()]
    if exc and not inc:
        out.append(_item("criteria_include", "Add the inclusion criteria (Project set-up, Eligibility criteria): what a study "
                         "must have to be included, such as its population, the tool or intervention and the study design. "
                         "Only exclusion criteria are written so far.", "setup/criteria", "Write the criteria",
                         short="add the inclusion criteria"))
    if inc and not exc:
        out.append(_item("criteria_exclude", "Add the exclusion criteria (Project set-up, Eligibility criteria): what rules "
                         "a record out on its own. Reviewers vote Exclude only when a criterion is clearly not met, so "
                         "writing them down keeps every vote consistent.", "setup/criteria", "Write the criteria",
                         short="add the exclusion criteria"))
    if not [x for x in s.get("ft_reasons") or [] if str(x).strip()]:
        out.append(_item("ft_reasons", "Add the full-text exclusion reasons, in the order to apply them (Project set-up, "
                         "Full-text exclusion reasons). Every report excluded at full text needs one, and PRISMA reports how "
                         "many were excluded for each reason.", "setup/reasons", "Add the reasons",
                         short="add the full-text exclusion reasons"))
    if not F.main_started and pl.get("mode") == "single" and not pl.get("ai_second_screener"):
        out.append(_item("single_alone", "The plan has one reviewer screening every record with no second screener, so "
                         "nobody checks what they exclude. AMSTAR 2 and Cochrane expect two reviewers to screen "
                         "independently, at least on a sample with good agreement. Consider 'Both screen a random sample "
                         "first' (Project set-up, Screening plan).", "setup/plan", "Open the screening plan"))
    done = F.main_started or not any(x["blocks"] for x in out)
    return {"items": out, "done": done, "ready": True, "started": True}


def _register(F: _Facts) -> dict:
    out = []
    if not F.registered:
        out.append(_item("registration", "Record the protocol's registration (Project set-up, Protocol registration). "
                         "Register the review (for example in PROSPERO or on OSF) before the final searches are run, so "
                         "that readers can check it kept to its plan; PRISMA 2020 asks for the registry and number (item "
                         "24a).", "setup/registration", "Record the registration", short="record the protocol's registration"))
    return {"items": out, "done": F.registered, "ready": True, "started": True}


def _planned_without_search(F: _Facts) -> list:
    """Names of the databases (and registers) in the search worksheet's plan that have no search logged."""
    names = {d.get("id"): d.get("name") for d in _guidance().get("databases", [])}
    logged = [str(e.get("database") or "").lower() for e in F.log]
    out = []
    for d in F.ws.get("databases") or []:
        if d == "registers":
            regs = [str(x).lower() for x in F.ws.get("registers") or [] if str(x).strip()]
            if not any(prisma.is_register(x) or any(r and (r in x or x in r) for r in regs) for x in logged):
                out.append("trial registers")
            continue
        words = DB_WORDS.get(d, (str(names.get(d) or d).lower(),))
        if d == "central":
            hit = any(("central" in x or "cochrane" in x) for x in logged)
        else:
            hit = any(w in x for w in words for x in logged)
        if not hit:
            out.append(names.get(d) or d)
    return out


def _searches(F: _Facts) -> dict:
    out = []
    if not F.log and not F.main_started:
        if not F.ws.get("databases"):
            out.append(_item("databases", "Choose the databases you will search (Plan and log searches, Choose databases). "
                             "ReviewDesk then checks that every one of them has a search logged.", "searches/databases",
                             "Choose the databases", who="me"))
        out.append(_item("no_searches", "No searches are logged yet. Log each search as you run it (Plan and log searches, "
                         "Search log): the database, platform, date, number of records and the full strategy. PRISMA-S, "
                         "the reporting standard for searches, asks for all of these.", "searches/log", "Open the search log",
                         who="me"))
    if not F.registered and not F.log:
        out.append(_item("registration", "The protocol's registration is not recorded yet. Register the review before you "
                         "run the final searches, then record it (Project set-up, Protocol registration): searching first "
                         "and registering afterwards makes it look as if the plan was changed to fit what was found.",
                         "setup/registration", "Record the registration"))
    for e in F.log:
        gaps = []
        if not str(e.get("database") or "").strip():
            gaps.append("the database")
        if not str(e.get("platform") or "").strip():
            gaps.append("the platform (for example Ovid or EBSCO)")
        if not str(e.get("date") or "").strip():
            gaps.append("the date it was run")
        if not str(e.get("results") or "").strip():
            gaps.append("the number of records it found")
        if not str(e.get("string") or "").strip():
            gaps.append("the full search strategy")
        if not gaps:
            continue
        mine = e.get("by") == F.me or F.lead
        who_text = "" if mine else f" {F.name(e.get('by'))} logged it, so they (or the project lead) can add {'it' if len(gaps) == 1 else 'them'}."
        out.append(_item("search_gaps:" + str(e.get("id")), f"{_cap(_search_name(e))} is missing {_join(gaps)}. PRISMA-S asks "
                         "for the database, platform, date, number of records and full strategy of every search, so that "
                         "others can check and repeat it. Add " + ("it" if len(gaps) == 1 else "them") + " in the search log "
                         "(Edit, beside the search)." + who_text, "searches/log", "Open the search log",
                         who="me" if mine else "other"))
    left = _planned_without_search(F)
    if left:
        one = len(left) == 1
        out.append(_item("planned_not_logged", f"{_cap(_join(left))} {'is' if one else 'are'} in the search plan but {'has' if one else 'have'} "
                         "no search logged yet. Log each search as you run it (Plan and log searches, Search log), or take it "
                         "out of the plan (Choose databases) if you decided not to search it.", "searches/log",
                         "Open the search log", who="me"))
    done = bool(F.log) and not left
    return {"items": out, "done": done, "ready": bool(F.log) or bool(F.every) or F.s_done, "started": bool(F.log)}


def _registers_not_imported(F: _Facts) -> list:
    """A warning when trial registers were searched (search log, or Project set-up in older projects) but none of their
    records were imported: they are then in no box of the flow diagram, and the methods must say how they were screened."""
    ni = prisma.registers_not_imported(F.p)
    if not ni:
        return []
    left = [f"{k} ({prisma.found_words(v)})" for k, v in ni.items()]
    one = len(left) == 1
    return [_item("registers_not_imported", f"{_cap(_join(left))} {'was' if one else 'were'} searched, but {'its' if one else 'their'} "
                  "records have not been imported, so they are not de-duplicated, screened or counted in the PRISMA flow "
                  "diagram (a footnote names them). Import each register's export (Import records) so that its records are "
                  "screened and counted with the rest; if you screen them outside ReviewDesk instead, the methods text asks "
                  "you to say how.", "import", "Open Import records", who="me")]


def _import(F: _Facts) -> dict:
    out = _registers_not_imported(F)
    if not F.log and not F.main_started:
        out.append(_item("log_before_import", "Log the searches before you import their results (Plan and log searches, "
                         "Search log). Each file you add is then linked to its search, and ReviewDesk checks that the file "
                         "holds every record the search found.", "searches/log", "Open the search log", who="me"))
    broken = [m["filename"] for m in F.every if m.get("_damaged")]
    if broken:
        out.append(_item("damaged", f"The details file of {_join([b + '.meta.json' for b in broken])} cannot be read (it is "
                         "damaged). Restore it from OneDrive's version history (right-click it in the project's imports "
                         "folder): until then the imports cannot be processed and screening cannot start.", "import",
                         "Open Import records", who="me", blocks=True))
    missing = [m.get("original_name") or m["filename"] for m in F.every if m.get("_missing") and not m.get("_damaged")]
    if missing and F.lead:
        out.append(_item("missing_files", f"{util.plural(len(missing), 'added file has', 'added files have')} not arrived on "
                         f"this computer yet: {_join(missing[:5])}{'…' if len(missing) > 5 else ''}. Wait until the sync "
                         "service has copied them (check the project folder in OneDrive): until then they cannot be "
                         "de-duplicated, and screening cannot start.", "import", "Open Import records", blocks=True))
    by_search = {}
    for m in F.every:
        if m.get("search_id") and not m.get("_missing"):
            by_search.setdefault(m["search_id"], []).append(m)
    for e in F.log:
        msg = search_mismatch(e, by_search.get(e.get("id"), []))
        if msg:
            out.append(_item("mismatch:" + str(e.get("id")), f"{_cap(_search_name(e))}: {msg[:1].lower() + msg[1:]} "
                             "(Plan and log searches, Search log)", "searches/log", "Open the search log", who="me"))
    unlinked = [m for m in F.every if m.get("set") in ("main", "update") and not m.get("search_id")
                and m.get("set") not in F.started]
    if unlinked and F.log:
        names = [f"{m.get('original_name') or m['filename']} ({m.get('database', '?')})" for m in unlinked]
        out.append(_item("unlinked", f"{util.plural(len(unlinked), 'imported file is', 'imported files are')} not linked to a "
                         f"search in the search log: {_join(names[:4])}{'…' if len(names) > 4 else ''}. Linked files let "
                         "ReviewDesk check that each export holds every record its search found. To link one, choose its search "
                         "in the Imported files table on Import records.",
                         "import", "Open Import records"))
    no_file, no_file_other = _searches_without_files(F)
    if any(x["key"] == "registers_not_imported" for x in out):      # those registers are already named above
        no_file_other = [e for e in no_file_other if not prisma.is_register(e.get("database") or "")]
    out += _no_file_items(no_file, no_file_other)
    done = (bool(F.recs) and not missing and not no_file and not (unlinked and F.log)
            and (F.main_started or not F.unprocessed()))
    return {"items": out, "done": done, "ready": bool(F.log) or bool(F.every), "started": bool(F.every)}


# words in a search log entry's database that show it is not a database search (other methods)
OTHER_METHODS = ("citation", "reference list", "references", "hand search", "handsearch", "website", "web search",
                 "google", "grey literature", "gray literature", "expert")


def _words(s) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).split())


def _searches_without_files(F: _Facts) -> tuple:
    """Logged searches no export file has been added for: (database searches, trial registers and other methods).
    A file not linked to its search counts for a search of the same database; a search that found 0 records needs none."""
    files = [m for m in F.every if not m.get("_damaged")]
    linked = {m.get("search_id") for m in files if m.get("search_id")}
    loose = [_words(m.get("database")) for m in files if not m.get("search_id")]
    db, other = [], []
    for e in F.log:
        if e.get("id") in linked or str(e.get("results") or "").replace(",", "").strip() == "0":
            continue
        name = _words(e.get("database"))
        if name and any(x and (x in name or name in x) for x in loose):
            continue
        (other if prisma.is_register(e.get("database") or "") or any(w in name for w in OTHER_METHODS) else db).append(e)
    return db, other


def _no_file_items(db: list, other: list) -> list:
    out = []
    if db:
        one = len(db) == 1
        out.append(_item("no_file", f"{_cap(_join([_search_name(e) for e in db[:5]]))}{'…' if len(db) > 5 else ''} "
                         f"{'has' if one else 'have'} no export file yet. Add {'its export' if one else 'the export of each'} "
                         "(Import records, choosing the search in the search log), so that every record the searches "
                         "found is de-duplicated and screened. If a search was not used, delete it from the search log "
                         "(Plan and log searches, Search log, Edit).", "import", "Open Import records", who="me",
                         blocks=True))
    if other:
        one = len(other) == 1
        out.append(_item("no_file_other", f"{_cap(_join([_search_name(e) for e in other[:5]]))}{'…' if len(other) > 5 else ''} "
                         f"{'has' if one else 'have'} no export file, so what {'it' if one else 'they'} found is not "
                         "screened in ReviewDesk. If those records should be screened here, add the export (Import records); "
                         "a trial register that is only searched and listed is reported from the search log.", "import",
                         "Open Import records", who="me"))
    return out


def _possible_pairs(F: _Facts) -> int:
    dd = F.p.dedup_report() or {}
    if not dd.get("possible"):
        return 0
    by = F.p.by_id()
    nd = {frozenset(x) for x in (F.st.get("overrides") or {}).get("not_dup", [])}
    n = 0
    for x in dd.get("possible", []):
        a, b = by.get(x.get("a")), by.get(x.get("b"))
        if not (a and b) or kept_first(a, b)[1].get("set") in F.started:     # the record that would go is being screened
            continue
        if {frozenset((a["id"], b["id"])), frozenset((a["id"], fingerprint(b))), frozenset((fingerprint(a), b["id"]))} & nd:
            continue
        n += 1
    return n


def _duplicates(F: _Facts) -> dict:
    out = []
    todo = F.unprocessed()
    stopped = [m.get("original_name") or m["filename"] for m in F.every if m["id"] in set(F.p.adding_interrupted())]
    if stopped:                               # (project.process_imports: the files and their records count as not added)
        out.append(_item("interrupted", f"Adding records was interrupted: ReviewDesk was closed, or the computer went to "
                         f"sleep, before it had added {_join(stopped[:4])}{'…' if len(stopped) > 4 else ''} to the "
                         "records. Choose Remove duplicates and add the files again (Import records). Nothing was lost, "
                         "and every record keeps the ID it was given.", "import", "Open Import records", blocks=True,
                         short="add the imported files to the records again (adding them was interrupted)"))
    if todo:
        names = [m.get("original_name") or m["filename"] for m in todo]
        out.append(_item("unprocessed", f"{util.plural(len(todo), 'file has', 'files have')} been added but not yet "
                         f"de-duplicated into the records: {_join(names[:4])}{'…' if len(names) > 4 else ''}. Remove "
                         "duplicates and add them to the records (Import records), so that every record is de-duplicated "
                         "before screening starts.", "import", "Open Import records", blocks=True,
                         short=f"remove duplicates from the {util.plural(len(todo), 'imported file')}"))
    pairs = _possible_pairs(F) if F.processed else 0
    if pairs:
        out.append(_item("pairs", f"{util.plural(pairs, 'pair')} of records may be the same report and {'has' if pairs == 1 else 'have'} "
                         "not been checked (Check duplicates). Once screening starts they can no longer be merged, and a "
                         "report screened twice is counted twice in the PRISMA diagram.", "duplicates", "Check the pairs"))
    # (pairs whose second record is already being screened no longer count: they can no longer be merged)
    done = bool(F.recs) and not todo and not pairs and not stopped
    return {"items": out, "done": done, "ready": bool(F.every), "started": bool(F.every)}


def _start_refusals(F: _Facts, set_name: str) -> list:
    """Everything screening.start refuses for a set, in its own words (one item each, not only the first)."""
    out = []
    broken = [m["filename"] for m in F.every if m.get("_damaged")]
    if broken:
        out.append(_item("damaged", "The details file of " + ", ".join(broken) + " cannot be read (it is damaged). Restore it "
                         "from OneDrive's version history (right-click it in the imports folder), then start screening.",
                         "import", "Open Import records", who="me", blocks=True))
    missing = [m.get("original_name") or m["filename"] for m in F.every if m.get("_missing") and not m.get("_damaged")]
    if missing and F.lead:
        out.append(_item("missing_files", "These added files have not arrived on this computer yet: " + ", ".join(missing) +
                         ". Wait until the sync service has finished copying them, remove duplicates (Import records), then start "
                         "screening, so that every record is in the random order and can fall in the samples.", "import",
                         "Open Import records", blocks=True))
    pending = [m.get("original_name") or m["filename"] for m in F.unprocessed(set_name)]
    if pending:
        out.append(_item("unprocessed", "Duplicates have not been removed from some imported files yet: " + ", ".join(pending) +
                         ". On Import records, choose Remove duplicates and add the files to the records, so that every "
                         "record is de-duplicated before screening starts.", "import",
                         "Open Import records", blocks=True, short=f"remove duplicates from the {util.plural(len(pending), 'imported file')}"))
    if not F.n_set.get(set_name) and not pending:          # one cause, one item: the files above hold the records
        out.append(_item("no_records", "No records have been added yet. Import the exports and remove duplicates first "
                         "(Import records).", "import", "Open Import records", who="me", blocks=True))
    if set_name == "main":                 # a logged search whose records are missing: screening would leave them out
        out += _no_file_items(_searches_without_files(F)[0], [])
    pl = screening.plan(F.p, set_name)
    for msg in screening.check_plan(F.p, pl):
        key, short, fix, label, extra = _plan_meta(msg)
        out.append(_item(key, msg + extra, fix, label, blocks=True, short=short))
    if pl.get("ai_second_screener") and pl.get("mode") == "single" and F.lead:
        cov = ai.coverage(F.p, set_name)
        if cov["missing"]:
            out.append(_item("ai_not_run_single", screening.ai_not_sent(cov["missing"]) + " Run it first (AI second screener page, Screen every record), so that only records "
                             "the AI would keep go to the second reviewer.", "ai", "Open the AI second screener", blocks=True,
                             private=True))
    return out


def _progress_items(F: _Facts, recs: list, stage: str, what: str, task: str) -> list:
    """Who still has records left in a shared piece of work (calibration or the agreement sample)."""
    out = []
    V = screening.votes(F.p)
    for rid in F.plan.get("reviewers") or []:
        done = sum(1 for r in recs if screening.latest(V, stage, r, rid))
        if done >= len(recs):
            continue
        if rid == F.me:
            out.append(_item(f"{task}_me", f"You have {util.plural(len(recs) - done, 'record')} of the {what} left to screen "
                             f"({done:,} of {len(recs):,} done). The two of you are compared only when both have finished.",
                             f"screen/{task}/main", "Continue screening", who="me", blocks=True))
        else:
            out.append(_waiting(f"{task}_wait:{rid}", f"Waiting for {F.name(rid)} to finish the {what} ({done:,} of "
                                f"{len(recs):,} done). Nobody sees the comparison until both reviewers have finished."))
    return out


def _first_screen_step(steps: list) -> str:
    return next((s["id"] for s in steps if s["id"] in SCREEN_STEPS), "screen")


def _calibration(F: _Facts) -> dict:
    out = []
    stuck = screening.calibration_stuck(F.p, "main") if F.phase == "calibration" else ""
    if stuck:                      # a round ReviewDesk 1.3.0 could start with one reviewer: nothing can finish it
        if F.lead:
            out.append(_item("calibration_stuck", stuck + " " + screening.END_STUCK, "setup/plan",
                             "End the calibration round", blocks=True, short="end the calibration round"))
        else:
            out.append(_waiting("calibration_stuck", f"Waiting for {F.lead_name} (the project lead) to end the calibration "
                                "round, which cannot be finished with the screening plan as it is.", who="lead"))
    elif F.phase == "calibration":
        cal = (F.main.get("calibration_rounds") or [[]])[-1]
        out += _progress_items(F, cal, "cal", "calibration round", "calibration")
        if not out:
            if F.lead:
                out.append(_item("calibration_discuss", "Both reviewers have finished the calibration round. Go through "
                                 "every disagreement together, then choose 'Discussed: move on' (Screen titles and abstracts): "
                                 "the agreement sample cannot start before that.", "screening", "Open screening", blocks=True,
                                 short="move on from the calibration round"))
            else:
                out.append(_waiting("calibration_discuss", "Both reviewers have finished the calibration round. Talk the "
                                    f"disagreements through together with {F.lead_name} (the project lead), who then moves "
                                    "the review on to the agreement sample.", who="lead"))
    return {"items": out, "done": F.phase in ("sample", "screening"), "started": F.phase == "calibration"}


def _sample(F: _Facts) -> dict:
    out = []
    if F.phase == "sample":
        out += _progress_items(F, F.main.get("sample") or [], "ta", "agreement sample", "sample")
    return {"items": out, "done": F.phase == "screening", "started": F.phase == "sample"}


def _ai(F: _Facts) -> dict:
    """The AI second screener: all of it the lead's own (the AI's state is never shown to anyone else)."""
    out = []
    p, s = F.p, F.s
    if not F.lead:
        return {"items": [], "done": F.phase == "screening", "ready": bool(F.recs), "started": F.main_started}
    base, key = ai.endpoint()
    c = ai.config(p)
    fixed = bool(c.get("locked")) or F.phase == "screening"
    if not base:
        out.append(_item("ai_address", "No AI service address is set. Add your service's address under My settings. The AI "
                         "second screener runs from your own computer, so its address and key are kept there, never in the "
                         "shared project folder.", "settings", "Open My settings", blocks=True, private=True))
    if not key:
        out.append(_item("ai_key", "No AI key is set. Add your AI service's API key under My settings: without it the AI "
                         "cannot screen any record. The key stays on this computer and is only ever sent to that service.",
                         "settings", "Open My settings", blocks=True, private=True))
    why = ai.models_missing(c)
    if why:
        out.append(_item("ai_models", why, "ai", "Open the AI second screener", blocks=True, private=True))
    elif not fixed:
        a, b = c["passes"]["A"]["model"], c["passes"]["B"]["model"]
        if not os.path.isfile(p.path("ai", "config.json")):
            out.append(_item("ai_models_check", f"Check the two AI models and save the set-up (AI second screener page, Set-up). "
                             f"They are still the built-in choices ({a} and {b}); the method you report should name the two "
                             "you chose, from different developers.", "ai", "Open the AI second screener", private=True))
        elif a.strip() == b.strip():
            out.append(_item("ai_models_same", f"Both passes use the same model ({a}). The method uses two models from "
                             "different developers, so that one model's blind spots are covered by the other. Choose another "
                             "model for one pass (AI second screener page, Set-up).", "ai", "Open the AI second screener",
                             private=True))
    if not [k for k in s.get("known_studies") or [] if any(str(v).strip() for v in (k or {}).values())]:
        out.append(_item("known_studies", "List a few eligible studies you already know (Project set-up, Known studies and "
                         "registers). ReviewDesk checks that the searches find them and, before you rely on it, that the AI "
                         "keeps every one.", "setup/known", "List the known studies", private=True))
    cov = ai.coverage(p, "main") if F.recs else {"records": 0, "missing": 0, "done": 0}
    if F.recs and cov["missing"] and F.phase in ("not_started", "calibration", "sample"):
        left = f"The AI second screener has not screened {util.plural(cov['missing'], 'record')} yet. "
        if F.plan.get("mode") == "single":
            out.append(_item("ai_run", left + "Run it on every record (AI second screener page, Screen every record) before "
                             "screening starts: with one reviewer screening, only records the AI would keep go to the second "
                             "reviewer, so every record must have been sent first.", "ai", "Open the AI second screener",
                             private=True))
        elif not (F.phase == "sample" and screening.agreement(p, "main", "sample")["complete"]):
            out.append(_item("ai_run", left + "Run it on every record (AI second screener page, Screen every record) before the "
                             "agreement sample is finished: its accuracy is checked against the sample, and the agreement "
                             "rule needs it.", "ai", "Open the AI second screener", private=True))
    done = (bool(F.recs) and not cov["missing"]) or F.phase == "screening"
    # (setting it up can be done now, before there are records to send it)
    return {"items": out, "done": done, "ready": True, "started": bool(cov.get("done")) or F.main_started}


def _agreement_rule(F: _Facts) -> dict:
    out = []
    complete = False
    if F.phase == "sample":
        ag = screening.agreement(F.p, "main", "sample")
        complete = ag["complete"]
        if complete and F.lead and F.ai_planned and ag.get("meets_threshold"):
            cov = ai.coverage(F.p, "main")
            if cov["missing"]:
                out.append(_item("ai_not_run_rule", screening.ai_planned_not_sent(cov["missing"]), "ai", "Open the AI second screener", blocks=True, private=True))
        if complete and not F.lead:
            out.append(_waiting("rule_wait", f"Both reviewers have finished the agreement sample. Waiting for {F.lead_name} "
                                "(the project lead) to apply the agreement rule, which decides how the remaining records "
                                "are screened.", who="lead"))
    return {"items": out, "done": F.phase == "screening", "ready": complete, "started": complete}


def _screen(F: _Facts) -> dict:
    out = []
    for t in F.tasks():
        if t["task"] in ("screen", "second") and t["left"]:
            where = "" if t["set"] == "main" else f", {SETS.get(t['set'], t['set']).lower()}"
            what = ("left to screen" if t["task"] == "screen" else
                    "left for a second look (records the single reviewer excluded that the AI would keep)")
            out.append(_item(f"task_{t['task']}_{t['set']}", f"You have {t['left']:,} of {util.plural(t['total'], 'record')} {what}"
                             f"{where}.", f"screen/{t['task']}/{t['set']}", "Continue screening", who="me", blocks=True))
        if t["task"] == "verify" and t["left"]:      # a check that can only add records: it holds no later step back
            where = "" if t["set"] == "main" else f", {SETS.get(t['set'], t['set']).lower()}"
            out.append(_item(f"task_verify_{t['set']}", f"You have {t['left']:,} of {util.plural(t['total'], 'record')} left "
                             f"in the verification sample{where}.", f"screen/verify/{t['set']}", "Continue screening", who="me"))
    out += [_item("kappa_recount", m, "screening", "Open screening") for m in [F.lead and screening.sample_recount_check(F.p)] if m]
    if F.lead:
        conf = sum(F.ta_counts(k).get("conflict", 0) for k in F.started)
        if conf:
            out.append(_item("ta_conflicts", f"{util.plural(conf, 'record needs', 'records need')} a joint decision at title and "
                             "abstract: decide them together (Screen titles and abstracts, Decide the disagreements together).",
                             "screening", "Open screening"))
        for k in ("update",):                 # (records from other methods: the citation-searching step's own work)
            if F.n_set.get(k) and k not in F.started and F.main_started:
                out.append(_item(f"start_{k}", f"Screening of the {util.plural(F.n_set[k], 'record')} from the "
                                 f"{SETS[k].lower()} has not started yet. Start it with the Start screening button in its "
                                 "panel (Screen titles and abstracts).", "screening", "Open screening"))
    else:
        out += _joint_wait(F, "ta")
    done = F.phase == "screening" and not any(_ta_left(F, k) for k in F.started if k != "other")
    return {"items": out, "done": done, "started": F.phase == "screening"}


def _ta_left(F: _Facts, set_name: str) -> int:
    """A started set's records still open at title and abstract, as this person may see them."""
    c = F.ta_counts(set_name)
    return c.get("pending", 0) + c.get("not_open", 0) + c.get("conflict", 0)


def _joint_wait(F: _Facts, stage: str) -> list:
    """For someone who is not the lead: their disagreements waiting for the joint decision the lead records."""
    if not F.member or F.lead:
        return []
    n = sum(1 for c in screening.conflicts(F.p) if c["stage"] == stage and F.me in (c.get("votes") or {}))
    if not n:
        return []
    what = "full-text disagreement" if stage == "ft" else "disagreement at title and abstract"
    return [_waiting(f"{stage}_conflicts_wait", f"Waiting for a joint decision on {util.plural(n, what)}: talk "
                     f"{'it' if n == 1 else 'them'} through together with {F.lead_name} (the project lead), who records "
                     "what you agree.", who="lead")]


def _get_fulltexts(F: _Facts) -> dict:
    out = []
    fto = F.ft()
    need = F.needs_pdf() if fto else []
    if need:
        out.append(_item("no_pdf", f"{util.plural(len(need), 'report')} at full text {'has' if len(need) == 1 else 'have'} no PDF "
                         "yet. Find them (Review full texts, Getting the PDFs): free copies first, then the library, then the "
                         "authors; record any you cannot get as Not retrieved.", "fulltext", "Open full texts", who="me"))
        if not (userconfig.load().get("unpaywall_email") or "").strip():
            out.append(_item("email", "If you will look for open-access copies (Review full texts, Find free copies), add your "
                             "email address under My settings first: Unpaywall and OpenAlex ask for one with each look-up.",
                             "settings", "Open My settings", who="me"))
    return {"items": out, "done": bool(fto) and not need, "ready": bool(fto), "started": bool(fto)}


def _fulltext(F: _Facts, screen_done: bool) -> dict:
    out = []
    fto = F.ft()
    if not [x for x in F.s.get("ft_reasons") or [] if str(x).strip()]:
        out.append(_item("ft_reasons", "Add the full-text exclusion reasons, in the order to apply them (Project set-up, "
                         "Full-text exclusion reasons). Every report excluded at full text needs one, and PRISMA reports how "
                         "many were excluded for each reason.", "setup/reasons", "Add the reasons", blocks=True,
                         short="add the full-text exclusion reasons"))
    for t in F.tasks():
        if t["task"] == "fulltext" and t["left"]:
            out.append(_item("task_fulltext", f"You have {t['left']:,} of {util.plural(t['total'], 'full text')} left to assess.",
                             "fulltext", "Open full texts", who="me", blocks=True))
    c = F.ft_counts()
    if F.lead and c.get("conflict"):
        out.append(_item("ft_conflicts", f"{util.plural(c['conflict'], 'full-text disagreement')} to settle together (Review "
                         "full texts, Decide together). If you still cannot agree, the protocol's rule applies (Project set-up, "
                         "Full texts).", "fulltext", "Open full texts"))
    out += _joint_wait(F, "ft")
    done = bool(fto) and screen_done and not (c.get("pending") or c.get("conflict"))
    return {"items": out, "done": done, "ready": bool(fto), "started": bool(fto)}


def _other_method(e: dict) -> bool:
    """A logged search that is another way of finding studies (citation searching, hand-searching and the like), not
    a database search or a trial register."""
    name = _words(e.get("database"))
    return not prisma.is_register(e.get("database") or "") and any(w in name for w in OTHER_METHODS)


def _other_no_file(F: _Facts, which: list) -> list:
    """Other-methods searches with no export file (a Check: what they found is not screened or counted)."""
    if not which:
        return []
    one = len(which) == 1
    return [_item("no_file_other", f"{_cap(_join([_search_name(e) for e in which[:5]]))}{'…' if len(which) > 5 else ''} "
                  f"{'has' if one else 'have'} no export file, so the records {'it' if one else 'they'} found are not "
                  "screened in ReviewDesk or counted in the PRISMA flow diagram. Add the export (Import records, Which "
                  f"search: Other methods). If {'it' if one else 'they'} found no records, record 0 as the number found "
                  "(Plan and log searches, Search log, Edit).", "import", "Open Import records", who="me")]


def _citations(F: _Facts, screen_items: list) -> dict:
    """Citation searching and other ways of finding studies: their records are this step's own work until they are
    screened at title and abstract (their full texts then join the full-text step)."""
    files = [m for m in F.every if m.get("set") == "other"]
    logged = [e for e in F.log if _other_method(e)]
    n_st = len(F.studies()) if F.ft() else 0
    out, waiting = [], False
    n = F.n_set.get("other", 0)
    if n and "other" not in F.started:
        waiting, one = True, n == 1
        if F.main_started:                  # (before that, the database records' calibration and sample come first)
            out.append(_item("start_other", f"{util.plural(n, 'record')} found by other methods (citation searching, "
                             f"hand-searching) {'has' if one else 'have'} not been screened yet. Start "
                             f"{'its' if one else 'their'} screening with the Start screening button in the Other methods "
                             f"panel (Screen titles and abstracts), so that {'it is' if one else 'they are'} screened like "
                             "the database records and counted in the PRISMA flow diagram.", "screening", "Open screening",
                             blocks=True, short=f"start screening the {util.plural(n, 'record')} found by other methods"))
    elif n:
        waiting = bool(_ta_left(F, "other"))
        out += [x for x in screen_items if x["key"] in ("task_screen_other", "task_second_other")]
        conf = F.ta_counts("other").get("conflict", 0)
        if F.lead and conf:
            out.append(_item("ta_conflicts", f"{util.plural(conf, 'record')} found by other methods "
                             f"{'needs' if conf == 1 else 'need'} a joint decision at title and abstract: decide "
                             f"{'it' if conf == 1 else 'them'} together (Screen titles and abstracts, Decide the "
                             "disagreements together).", "screening", "Open screening"))
    no_file = [e for e in _searches_without_files(F)[1] if _other_method(e)]
    out += _other_no_file(F, no_file)
    started = bool(files or logged)
    done = started and not waiting and not no_file and not F.unprocessed("other")
    return {"items": out, "done": done, "ready": bool(n_st) or started, "started": started,
            # while its records wait to be screened, its work is on the screening page
            "route": "screening" if waiting else "", "page": "screen" if waiting else ""}


def _extract(F: _Facts) -> dict:
    out = []
    p = F.p
    fto = F.ft()
    f = review.form(p)
    ppl = review.people(p, f)
    sts = F.studies() if fto else []
    prog = review.progress(p) if sts else {"studies": 0, "finished": 0}
    finished = bool(sts) and prog["finished"] == prog["studies"]
    if fto or sts:
        if F.lead and _form_unchanged(f, p) and not finished:      # once every study is extracted, it is too late to matter
            out.append(_item("form_builtin", "The extraction form is still the built-in general form: adapt it to your "
                             "protocol (Extract data, The extraction form, Edit the items), so that it asks for the study "
                             "details and outcomes your protocol lists.", "extract", "Open the extraction form"))
        if not ppl.get("first"):
            out.append(_item("extractors_none", "Choose who extracts the data and who checks it (Extract data, The extraction "
                             "form). Nobody can record extraction until the first extractor is chosen.", "extract",
                             "Choose the extractors", blocks=True, short="choose who extracts the data and who checks it"))
        elif ppl.get("gone"):
            out.append(_item("extractors_gone", f"{_join([F.name(x) for x in ppl['gone']])} {'is' if len(ppl['gone']) == 1 else 'are'} "
                             "named as an extractor but no longer in the project. Choose the extractors again (Extract data, "
                             "The extraction form): their part of the extraction waits until then.", "extract",
                             "Choose the extractors", blocks=True, short="choose the extractors again"))
        elif not ppl.get("set") and F.lead and len(F.people) > 1:     # (alone, there is nobody else to choose)
            out.append(_item("extractors_default", f"Choose who extracts the data and who checks it (Extract data, The extraction "
                             f"form). Until you do, {F.name(ppl['first'])} extracts"
                             + (f" and {F.name(ppl['second'])} checks" if ppl.get("second") else "")
                             + ", taken from the screening plan.", "extract", "Choose the extractors"))
    if sts:
        a = review.answers(p)
        first, second = ppl.get("first"), ppl.get("second")
        mine = sum(1 for s in sts if F.me == first and not review._is_done(a, first, s["key"]))
        check = sum(1 for s in sts if F.me == second and review._is_done(a, first, s["key"])
                    and not review._is_done(a, second, s["key"]))
        if mine:
            out.append(_item("task_extract", f"You have {util.plural(mine, 'included study', 'included studies')} to extract "
                             "(Extract data).", "extract", "Open Extract data", who="me", blocks=True))
        if check:
            out.append(_item("task_check", f"{util.plural(check, 'study is', 'studies are')} ready for you to check and to "
                             "extract the outcome data (Extract data).", "extract", "Open Extract data", who="me", blocks=True))
        wait = sum(1 for s in sts if review._is_done(a, first, s["key"]) and not review._is_done(a, second, s["key"])) \
            if F.me == first and second and second != first and not mine else 0
        if wait:
            out.append(_waiting("extract_wait", f"Waiting for {F.name(second)} to check {util.plural(wait, 'study', 'studies')} "
                                "you have extracted and to extract the outcome data; any differences are then agreed (Extract "
                                "data, Compare and agree)."))
    return {"items": out, "done": finished, "ready": bool(sts), "started": bool(sts)}


def _rob_left(F: _Facts, sts: list, who: str) -> list:
    """The risk-of-bias assessments of the included studies that this reviewer has not finished: [(study, assessment)]."""
    R = review.rob_rows(F.p)
    keys = {s["key"] for s in sts}
    return [k for k in sorted(R["defs"]) if k[0] in keys and not R["done"].get(who, {}).get(k)]


def _rob_plan_items(F: _Facts) -> list:
    """The risk-of-bias plan, once screening has started: needed before any assessment is added if screening started
    without one; a design without an assessment needs its reason."""
    from . import robplan
    out = []
    if not F.main_started and not F.started:
        return out                                  # before screening, the set-up's own items cover it
    if not robplan.recorded(F.p):
        out.append(_item("rob_plan_missing", "Record the risk-of-bias plan (Project set-up, Risk-of-bias plan): which tool "
                         "is used for each study design the review includes. Screening started before ReviewDesk asked for "
                         "it, so it is needed now, before any risk-of-bias assessment is added; the methods text says when "
                         "it was recorded, and asks you to confirm it matches the registered protocol.", "setup/robplan",
                         "Record the plan", blocks=True, short="record the risk-of-bias plan"))
    else:
        for key, text in robplan.problems(robplan.plan(F.p)):
            out.append(_item(key, text, "setup/robplan", "Open the plan", short="give the reason a design in the "
                             "risk-of-bias plan has no assessment"))
    return out


def _rob(F: _Facts) -> dict:
    from . import robplan
    out = []
    sts = F.studies() if F.ft() else []
    if not sts:
        return {"items": _rob_plan_items(F), "done": False, "ready": False, "started": False}
    out += _rob_plan_items(F)
    status = {s["key"]: review.rob_status(F.p, s["key"]) for s in sts}
    pl = robplan.plan(F.p)
    by = {d["id"]: d for d in pl.get("designs") or []}
    fit = {s["key"]: robplan.compatible(pl, robplan.extracted_design(F.p, s["key"])) for s in sts}
    exempt = {k for k, f in fit.items() if f and all(by[i].get("none") for i in f)}     # the plan says: no assessment
    none = [s for s in sts if not status[s["key"]]["assessments"] and s["key"] not in exempt]
    known = [s for s in none if fit[s["key"]]]
    unknown = [s for s in none if not fit[s["key"]]]
    if known:
        names = [f"{s['label']}: {robplan._mid(by[fit[s['key']][0]]['name'])}, with "
                 f"{robplan.tool_def(F.p, by[fit[s['key']][0]]['tool'])['short']}" for s in known[:6]]
        out.append(_item("rob_none_known", f"{util.plural(len(known), 'included study has', 'included studies have')} no "
                         f"risk-of-bias assessment yet, although extraction gives the design and the plan its tool. "
                         f"{'; '.join(names)}{'; and others' if len(known) > 6 else ''}. Add one for each result you will report "
                         "(Extract data: open the study, then Risk of bias).", "extract", "Open Extract data", who="me"))
    if unknown:
        out.append(_item("rob_none", f"{util.plural(len(unknown), 'included study has', 'included studies have')} no "
                         "risk-of-bias assessment yet. Add one for each result you will report, choosing the study's design "
                         "from the risk-of-bias plan (Extract data: open the study, then Risk of bias).", "extract",
                         "Open Extract data", who="me"))
    if F.lead:
        views = [(s, v) for s in sts for v in review.rob_view(F.p, s["key"], F.me, settled_only=True)]
        stale = [(s, v) for s, v in views if v.get("superseded")]
        if stale:
            out.append(_item("rob_tool", f"{util.plural(len(stale), 'risk-of-bias assessment was', 'risk-of-bias assessments were')} "
                             "made with a tool the risk-of-bias plan no longer gives for the study's design, because the "
                             "plan was amended after they were added: " + "; ".join(f"{s['label']} ({v['label']})" for s, v in stale[:5])
                             + ("; and others" if len(stale) > 5 else "") + ". Decide what to do with each (Extract data: open "
                             f"the study, then Risk of bias). For {stale[0][0]['label']} ({stale[0][1]['label']}), "
                             + stale[0][1]["superseded"][:1].lower() + stale[0][1]["superseded"][1:],
                             "extract", "Open Extract data"))
        wrong = [(s, v) for s, v in views if v.get("warning")]
        if wrong:
            out.append(_item("rob_design", f"{util.plural(len(wrong), 'risk-of-bias assessment uses', 'risk-of-bias assessments use')} "
                             "a design (and so a tool) that does not fit the design extraction records: "
                             + "; ".join(f"{s['label']} ({v['label']})" for s, v in wrong[:5])
                             + ("; and others" if len(wrong) > 5 else "") + ". Check each (Extract data: "
                             "open the study, then Risk of bias): " + wrong[0][1]["warning"], "extract", "Open Extract data"))
    people = [x for x in review.rob_people(F.p) if x]
    if F.me in people:
        left = _rob_left(F, sts, F.me)
        other = next((x for x in people if x != F.me), "")
        if left:
            n_st = len({k[0] for k in left})
            out.append(_item("rob_task", f"You have {util.plural(len(left), 'risk-of-bias assessment')} to judge and mark as "
                             f"finished{f' in {n_st:,} studies' if n_st > 1 else ''} (Extract data: open the study, then Risk of "
                             "bias). You see the other reviewer's judgements once you have both finished.", "extract",
                             "Open Extract data", who="me", blocks=True))
        elif other and _rob_left(F, sts, other):
            out.append(_waiting("rob_wait", f"Waiting for {F.name(other)} to finish "
                                f"{util.plural(len(_rob_left(F, sts, other)), 'risk-of-bias assessment')}: you see each "
                                "other's judgements once both have finished."))
    done = not none and all(v["both_done"] == v["assessments"] for v in status.values())
    return {"items": out, "done": done, "ready": True, "started": True}


def _update_search(F: _Facts, extract_ready: bool) -> dict:
    out = []
    upd = any(m.get("set") == "update" for m in F.every)
    when = str((F.s.get("dates") or {}).get("update_search") or "")
    if not upd and re.match(r"^\d{4}-\d{2}-\d{2}$", when) and when <= util.today() and F.main_started:
        out.append(_item("update_due", f"The update search was planned for {_date(when)}. Run the searches again, unchanged, "
                         "log them, and import the results as the update search (Import records, Which search: Update "
                         "search).", "import", "Open Import records", who="me"))
    return {"items": out, "done": upd, "ready": extract_ready or upd or bool(out), "started": upd}   # due: it can be run now


def _certainty(F: _Facts, extract_ready: bool) -> dict:
    from . import certainty
    items, done, status = [], False, None
    try:
        prog = certainty.progress(F.p) or {}
        done = bool(prog.get("outcomes")) and prog.get("finished") == prog.get("outcomes")
    except Exception:                         # noqa: BLE001 - another part's progress must never stop these checks
        prog = {}
    fn = getattr(certainty, "readiness", None)
    if fn:
        try:
            got = fn(F.p, F.me)
        except Exception:                     # noqa: BLE001
            got = None
        if isinstance(got, dict):
            raw = list(got.get("missing") or []) + list(got.get("warnings") or []) + list(got.get("items") or [])
            status = got.get("status")
        else:
            raw = list(got or [])
        for k, x in enumerate(raw):
            if isinstance(x, str):
                x = {"text": x}
            if not isinstance(x, dict) or not x.get("text"):
                continue
            text, who = str(x["text"]), x.get("who") or "me"
            mine = who == "me" and text.startswith("You have not finished")       # this person's own ratings: needed
            wait = bool(x.get("waiting")) or (who != "me" and " has not finished " in text)   # someone else's
            items.append(_item(str(x.get("key") or f"certainty:{k}"), text, str(x.get("fix") or "certainty"),
                               str(x.get("fix_label") or "Open Rate certainty"), who, bool(x.get("blocks")) or mine,
                               short=str(x.get("short") or ""), private=bool(x.get("private")), worded=wait, waiting=wait))
    return {"items": items, "done": done, "ready": extract_ready or bool(prog.get("outcomes")), "started": bool(prog.get("outcomes")),
            "status": status if status in ("done", "now", "blocked", "later") else None}


def _report(F: _Facts) -> dict:
    out = _registers_not_imported(F)
    if not F.registered:
        out.append(_item("registration", "The protocol's registration is not recorded (Project set-up, Protocol "
                         "registration). PRISMA 2020 item 24a asks for the registry and registration number, or a statement "
                         "that the review was not registered.", "setup/registration", "Record the registration",
                         short="record the protocol's registration"))
    # What is still open, so that the counts will change. Each says "me" only when the person looking has some of it to
    # do; otherwise it is what they are waiting for.
    left = {t["task"]: t["left"] for t in F.tasks() if t["left"]}
    whose = lambda mine: "me" if mine else "other"                                          # noqa: E731
    open_ta = F.ta_open()
    if open_ta:
        out.append(_item("open_ta", f"{util.plural(open_ta, 'record is', 'records are')} still open at title and abstract, so the "
                         "PRISMA diagram and the counts will still change.", "screening", "Open screening",
                         who=whose(left.keys() & {"calibration", "sample", "screen", "second"})))
    if F.recs and not F.main_started:
        out.append(_item("not_screened", "Title and abstract screening has not started, so the PRISMA diagram shows only the "
                         "searches and duplicates so far.", "screening", "Open screening", who=whose(F.lead)))
    for k, words in (("update", "from the update search"), ("other", "found by other methods")):
        n = F.n_set.get(k, 0)
        if n and k not in F.started and F.main_started:
            out.append(_item(f"not_screened_{k}", f"{util.plural(n, 'record')} {words} {'has' if n == 1 else 'have'} not "
                             "been screened yet, so the PRISMA diagram counts " + ("it" if n == 1 else "them") + " as not "
                             "yet screened and the counts will still change.", "screening", "Open screening",
                             who=whose(F.lead)))
    for k in [x for x in SETS if x in F.started]:      # a verification sample still being screened can send more records on
        v = evaluation.verification(F.p, k, F.me) if F.member else None
        if v and v.get("drawn") and not v["complete"]:
            mine = v["reviewer"] == F.me
            out.append(_item(f"open_verify_{k}", "The verification sample is not finished ("
                             + (f"you have screened {v['done']:,} of its {v['total']:,} records" if mine else
                                f"{F.name(v['reviewer'])} has screened {v['done']:,} of its {v['total']:,} records"
                                if "done" in v else f"{F.name(v['reviewer'])} is still screening it")
                             + "). Any record it sends on goes to full text when it is finished, so the PRISMA diagram and "
                             "the counts may still change.", f"screen/verify/{k}" if mine else "screening",
                             "Continue screening" if mine else "Open screening", who=whose(mine)))
    out += _other_no_file(F, [e for e in _searches_without_files(F)[1] if _other_method(e)])
    c = F.ft_counts() if F.started else Counter()
    if c.get("pending") or c.get("conflict"):
        n = c.get("pending", 0) + c.get("conflict", 0)
        mine, others = left.get("fulltext", 0), n - left.get("fulltext", 0) - c.get("conflict", 0)
        parts = ([f"{util.plural(others, 'report waits', 'reports wait')} for another reviewer's decision"] if others > 0 else []) + \
                ([f"{util.plural(c['conflict'], 'waits', 'wait')} for a joint decision"] if c.get("conflict") else [])
        if mine:                                 # some of it is this person's own work
            text = (f"{util.plural(n, 'report is', 'reports are')} still open at full text (you have {mine:,} of them left to "
                    f"assess{'; ' + _join(parts) if parts else ''}), so the PRISMA diagram and the counts will still change.")
        else:                                    # all of it waits for someone else
            text = (f"The full-text decisions are not finished: {_join(parts)}, so the PRISMA diagram and the counts will "
                    "still change.")
        out.append(_item("open_ft", text, "fulltext", "Open full texts", who=whose(mine)))
    if c.get("awaiting"):
        one = c["awaiting"] == 1
        out.append(_item("awaiting", f"{util.plural(c['awaiting'], 'report is', 'reports are')} awaiting information from the "
                         f"authors. If no answer comes, report {'it as a study' if one else 'them as studies'} awaiting "
                         "classification.", "fulltext", "Open full texts", who=whose(F.lead)))
    sts = F.studies() if c else []
    if sts:
        prog = review.progress(F.p)
        if prog["finished"] < prog["studies"]:
            n = prog["studies"] - prog["finished"]
            ppl = review.people(F.p)
            a = review.answers(F.p)
            mine = any((F.me == ppl.get("first") and not review._is_done(a, F.me, s["key"])) or
                       (F.me == ppl.get("second") and review._is_done(a, ppl.get("first"), s["key"])
                        and not review._is_done(a, F.me, s["key"])) for s in sts)
            out.append(_item("open_extract", f"{util.plural(n, 'included study is', 'included studies are')} not yet extracted by "
                             "both extractors.", "extract", "Open Extract data", who=whose(mine)))
        rob_open = sum(1 for s in sts if (lambda v: not v["assessments"] or v["both_done"] < v["assessments"])(review.rob_status(F.p, s["key"])))
        if rob_open:
            out.append(_item("open_rob", f"{util.plural(rob_open, 'included study has', 'included studies have')} risk-of-bias "
                             "assessments still to finish (or none yet).", "extract", "Open Extract data",
                             who=whose(F.me in review.rob_people(F.p) and _rob_left(F, sts, F.me))))
    done = bool(sts) and not [x for x in out if x["key"] != "registers_not_imported"]     # a warning that may stay
    return {"items": out, "done": done, "ready": bool(F.recs), "started": bool(F.started)}


# ================================================================================================ putting it together
def _for_viewer(F: _Facts, items: list) -> list:
    """The items as this person may see them (see the module notes)."""
    out, waiting, seen = [], [], set()
    for x in items:
        if x["key"] in seen:
            continue
        seen.add(x["key"])
        if not F.lead:
            if x["private"]:
                continue
            if x["who"] == "lead" and not x["worded"]:
                if x["blocks"] and x["short"]:
                    waiting.append(x)
                continue                       # the lead's own to-do list: not this person's business
        out.append({**{k: x[k] for k in ("key", "text", "fix", "fix_label", "who", "blocks", "short", "waiting")},
                    "keys": [x["key"]]})
    if waiting:                                # what the lead must do is, for this person, what they are waiting for
        shorts = list(dict.fromkeys(x["short"] for x in waiting))
        out.insert(0, {"key": "waiting_lead:" + ",".join(x["key"] for x in waiting),
                       "text": f"Waiting for {F.lead_name} (the project lead) to {_join(shorts)}.",
                       "fix": "", "fix_label": "", "who": "lead", "blocks": False, "short": "", "waiting": True,
                       "keys": [x["key"] for x in waiting]})
    return out


def check(p: Project, me: str) -> dict:
    """{"steps": [{"id", "title", "phase", "phase_title", "what", "who", "route", "outside", "page", "status",
    "missing": [item], "warnings": [item], "later": [item]}], "missing": n, "warnings": n, "setup_done": bool,
    "is_lead": bool}. See the module notes for the statuses and items. A step that is "later" lists nothing as missing
    or to check, and counts for nothing: its items wait in "later" until an earlier step lets it start."""
    F = _Facts(p, me)
    steps = workflow.steps_for(F.s, sum(1 for r in F.recs if (r.get("set") or "main") == "main"),   # (as timeline.sample_base)
                               main=F.main)
    ids = [s["id"] for s in steps]
    first_screen = _first_screen_step(steps)
    R: dict = {}
    R["setup"] = _setup(F)
    F.s_done = R["setup"]["done"]
    R["register"] = _register(F)
    R["searches"] = _searches(F)
    R["import"] = _import(F)
    R["duplicates"] = _duplicates(F)
    if "calibration" in ids:
        R["calibration"] = _calibration(F)
    if "sample" in ids:
        R["sample"] = _sample(F)
    if "ai" in ids:
        R["ai"] = _ai(F)
    if "agreement_rule" in ids:
        R["agreement_rule"] = _agreement_rule(F)
    R["screen"] = _screen(F)
    # starting screening: everything screening.start would refuse, on the first screening step of this plan
    start_ready = bool(F.n_set.get("main")) and not F.unprocessed("main") and R["duplicates"]["ready"]
    if not F.main_started:
        R[first_screen]["items"] = _start_refusals(F, "main") + R[first_screen]["items"]
        R[first_screen]["ready"] = start_ready
        if start_ready and F.member and not F.lead and not any(x["blocks"] for x in R[first_screen]["items"]):
            # nothing is left but the lead's own start: say what this person is waiting for
            R[first_screen]["items"].append(_waiting("start_wait", f"Waiting for {F.lead_name} (the project lead) to start "
                                                     "title and abstract screening, which puts the records in a random "
                                                     "order and gives you your first records to screen.", who="lead"))
    order = [x for x in SCREEN_STEPS if x in ids]
    for k, x in enumerate(order):                      # each screening step can start once the one before is done
        R[x].setdefault("ready", R[order[k - 1]]["done"] if k else start_ready)
    R["get_fulltexts"] = _get_fulltexts(F)
    R["fulltext"] = _fulltext(F, R["screen"]["done"])
    R["citations"] = _citations(F, R["screen"]["items"])
    R["extract"] = _extract(F)
    R["rob"] = _rob(F)
    R["update_search"] = _update_search(F, R["extract"]["ready"])
    R["synthesis"] = {"items": [], "done": False, "ready": R["extract"]["done"], "started": False}
    R["certainty"] = _certainty(F, R["extract"]["ready"])
    R["report"] = _report(F)
    # a GRADE statement worded for another certainty would reach the Summary of findings: needed before reporting too
    wrong = [x for x in R["certainty"]["items"] if x["key"].startswith("certainty:statement:") and x["blocks"]]
    if wrong:
        R["report"]["items"] += wrong
        R["report"]["done"] = False
    out, n_missing, n_warn, seen = [], 0, 0, set()
    for st in steps:
        r = R.get(st["id"]) or {"items": [], "done": False, "ready": False, "started": False}
        items = _for_viewer(F, r["items"]) if F.member else []
        blocks = any(x["blocks"] for x in (items if F.member else r["items"]))
        if r.get("status"):
            status = r["status"]
        elif r["done"]:
            status = "done"
        elif r.get("started"):
            status = "now"
        elif not r.get("ready"):
            status = "later"
        elif blocks:
            status = "blocked"
        else:
            status = "now"
        later = []
        if status == "later":
            # a step that cannot start yet: what it will need is not needed now (nobody can act on it yet), so it is
            # neither listed as missing nor counted, only kept for a page that shows what comes later
            later, items = items, []
        miss = [x for x in items if x["blocks"]]
        warn = [x for x in items if not x["blocks"]]
        for x in items:                                # counted once, however many steps list the same item
            if status != "done" and not set(x["keys"]) <= seen:
                seen.update(x["keys"])
                n_missing += x["blocks"]
                n_warn += not x["blocks"] and not x.get("waiting")      # waiting for someone else: neither
        out.append(dict(st, route=r.get("route") or st["route"], page=r.get("page") or PAGE.get(st["id"], ""),
                        status=status, missing=miss, warnings=warn, later=later))
    return {"steps": out, "missing": n_missing, "warnings": n_warn, "setup_done": R["setup"]["done"], "is_lead": F.lead,
            "lead_name": F.lead_name, "me": me}


def blocking(p: Project, me: str, step: str) -> list:
    """The texts of everything that blocks a step (for the server's own refusals: the same words as on screen)."""
    for st in check(p, me)["steps"]:
        if st["id"] == step:
            return [x["text"] for x in st["missing"]]
    return []


# ================================================================================================ the set-up summary
TYPE_NAMES = {"intervention": "Effects of an intervention", "diagnostic": "Diagnostic or test accuracy",
              "prognostic": "Prognosis or prediction models", "qualitative": "Qualitative evidence",
              "prevalence": "Prevalence or incidence", "aetiology": "Aetiology or risk factors", "scoping": "Scoping review",
              "other": "Other"}
MODE_NAMES = {"dual": "Both reviewers screen every record independently.",
              "sample_then_single": "Both reviewers screen a random sample independently; if agreement on it is high "
                                    "enough, one reviewer screens the rest.",
              "single": "One reviewer screens every record."}


def summary(p: Project) -> dict:
    """Every set-up choice in plain words, in the order of the guided set-up: {"title", "made", "sections": [{"title",
    "rows": [[label, value or [values]]]}]}."""
    s = p.settings_ro()
    names = {r["id"]: f"{r.get('name') or r['id']} ({r['id']})" for r in s.get("reviewers") or []}
    nm = lambda rid: names.get(rid, rid or "not chosen")                              # noqa: E731
    lst = lambda xs: [str(x) for x in xs or [] if str(x).strip()] or "none written"   # noqa: E731
    sc = s.get("screening") or {}
    st = p.state_ro()
    fixed = ((st.get("sets") or {}).get("main") or {}).get("plan_at_start")
    secs = []
    secs.append({"title": "The review", "rows": [
        ["Title", s.get("title") or "not written"], ["Short title", s.get("short_title") or "not written"],
        ["Type of question", TYPE_NAMES.get(s.get("review_type"), s.get("review_type") or "")],
        ["Review question", s.get("question") or "not written"]]})
    reg = s.get("registration") or {}
    if registered(s):
        rr = [["Registry", reg.get("registry") or "not given"], ["Registration number", reg.get("id") or "not given"],
              ["Date registered", _date(reg.get("date")) or "not given"], ["Link", reg.get("url") or "not given"]]
    else:
        rr = [["Registration", "Not registered yet" + (f" (planned in {reg['registry']})" if reg.get("registry") else "")]]
    secs.append({"title": "Protocol registration", "rows": rr})
    d = s.get("dates") or {}
    secs.append({"title": "Planned dates", "rows": [[lab, _date(d.get(k)) or "not set"] for k, lab in (
        ("start", "Work starts"), ("searches", "Final searches run"), ("update_search", "Update search"),
        ("end", "Review finished"))]})
    secs.append({"title": "The team", "rows": [[nm(r["id"]), "Project lead" if r.get("role") == "lead" else "Reviewer"]
                                                for r in s.get("reviewers") or []]})
    crit = s.get("criteria") or {}
    secs.append({"title": "Eligibility criteria", "rows": [
        ["Include (every point must be met)", lst(crit.get("include"))],
        ["Exclude (any one point rules a record out)", lst(crit.get("exclude"))],
        ["Rules for borderline cases", lst(crit.get("rules"))],
        ["Full-text exclusion reasons, in order", lst(s.get("ft_reasons"))]]})
    pl = fixed or {"mode": sc.get("mode"), "reviewers": sc.get("reviewers") or [], "single_reviewer": sc.get("single_reviewer"),
                   "second_reviewer": sc.get("second_reviewer"), "ai_second_screener": sc.get("ai_second_screener"),
                   "ai_keep_rate_stop": sc.get("ai_keep_rate_stop"), "kappa_threshold": sc.get("kappa_threshold"),
                   "sample_fraction": sc.get("sample_fraction"), "calibration_size": sc.get("calibration_size"),
                   "conflicts": sc.get("conflicts_at_title_abstract")}
    mode = pl.get("mode") or "sample_then_single"
    rows = [["How records are screened", MODE_NAMES.get(mode, mode)],
            ["The two reviewers", _join([nm(x) for x in pl.get("reviewers") or []]) or "not chosen"],
            ["Calibration round", f"{int(pl.get('calibration_size') or 0):,} records" if pl.get("calibration_size") else "none"]]
    if mode == "sample_then_single":
        rows += [["Agreement sample", f"{round(100 * float(pl.get('sample_fraction') or 0.2))}% of the records"],
                 ["Cohen's kappa needed for single screening", f"{float(pl.get('kappa_threshold') or 0.8):.2f}"]]
    if mode != "dual":
        rows.append(["Who screens on their own" if mode == "sample_then_single" else "Who screens", nm(pl.get("single_reviewer"))])
        ai_on = bool(pl.get("ai_second_screener"))
        rows.append(["AI second screener", "Yes: records the single reviewer excludes but the AI would keep go to the second "
                     "reviewer. The AI never excludes a record." if ai_on else "No"])
        if ai_on:
            rows += [["Second reviewer for those records", nm(pl.get("second_reviewer"))],
                     ["Drop the AI step if it keeps more than", f"{round(100 * float(pl.get('ai_keep_rate_stop') or 0.5))}% of the records"]]
    rows.append(["When the two disagree at title and abstract", "The record goes on to full text" if (pl.get("conflicts") or "forward")
                 == "forward" else "They decide together"])
    if sc.get("seed"):
        rows.append(["Random order seed", f"{sc['seed']} (recorded so that the order can be reproduced)"])
    if fixed:
        rows.append(["Fixed", f"Screening started on {_date(str(((st.get('sets') or {}).get('main') or {}).get('started') or '')[:10])}: "
                     "this is the plan as it was then."])
    secs.append({"title": "Screening plan", "rows": rows})
    ft = s.get("fulltext") or {}
    f = review.form(p)
    ppl = review.people(p, f)
    secs.append({"title": "Full texts, extraction and risk of bias", "rows": [
        ["Full-text review", "Both reviewers assess every full text independently" if ft.get("mode", "dual") == "dual"
         else "One reviewer assesses every full text"],
        ["If reviewers still disagree", ft.get("disagreement_default") or "not written"],
        ["Library link prefix", ft.get("library_prefix") or "none"],
        ["First extractor (extracts every item)", nm(ppl.get("first")) + ("" if ppl.get("set") else " (not chosen yet: taken from the screening plan)")],
        ["Second extractor (checks, and extracts outcome data)", nm(ppl.get("second")) if ppl.get("second") else "none"],
        ["Extraction form", (f.get("name") or "Extraction form") + (": the built-in general form, not yet adapted" if _form_unchanged(f, p) else f", revision {f.get('rev')}")]]})
    secs.append(_plan_summary(p))
    if sc.get("ai_second_screener") and mode != "dual":
        c = ai.config(p)
        known = [k.get("title") or (f"DOI {k['doi']}" if k.get("doi") else "") or (f"PubMed ID {k['pmid']}" if k.get("pmid") else "")
                 for k in s.get("known_studies") or []]
        secs.append({"title": "AI second screener", "rows": [
            ["First AI model", c["passes"]["A"].get("model") or "not chosen"],
            ["Second AI model", c["passes"]["B"].get("model") or "not chosen"],
            ["Known eligible studies", [k for k in known if k] or "none listed"]]})
    try:
        ws = p.worksheet() or {}
    except DamagedFile:
        ws = {}
    dbs = {d.get("id"): d.get("name") for d in _guidance().get("databases", [])}
    secs.append({"title": "Searches", "rows": [
        ["Databases to search", [dbs.get(x, x) for x in ws.get("databases") or [] if x != "registers"] or "none chosen"],
        ["Trial registers", [str(x) for x in ws.get("registers") or [] if str(x).strip()] or ("planned" if "registers" in (ws.get("databases") or []) else "none")],
        ["Other ways of finding studies", [str(x) for x in ws.get("other_sources") or [] if str(x).strip()] or "none"],
        ["Searches logged so far", str(len(p.search_log()))]]})
    other = s.get("other_sets") or {}
    # (the labels and choices are those of Project set-up, Full texts, update searches and other methods)
    upd = {"inherit": "the same way the main screening ended", "dual": "by both reviewers", "single": "by one reviewer"}
    secs.append({"title": "Update searches and other methods", "rows": [
        ["Records from the update search are screened", upd.get((other.get("update") or {}).get("mode", "inherit"), "")],
        ["Records from other methods (citation searching, hand-searching) are screened",
         upd.get((other.get("other") or {}).get("mode", "dual"), "")]]})
    return {"title": s.get("title") or "Untitled review", "made": util.now(), "version": VERSION, "sections": secs}


def _plan_summary(p: Project) -> dict:
    """The risk-of-bias plan, design by design, for the set-up summary."""
    from . import robplan
    pl = robplan.plan(p)
    rows = []
    if pl.get("recorded"):
        rows.append(["Recorded", f"{_date(str(pl['recorded'])[:10])}, "
                     + ("before screening started" if pl.get("before_screening") else "after screening had started")
                     + (f"; changed {util.plural(len(robplan.amendments(p)), 'time')} since, as protocol amendments"
                        if robplan.amendments(p) else "")])
    else:
        rows.append(["Recorded", "Not recorded yet: needed before screening starts"
                     + (" (shown below: a draft made from the tools named before ReviewDesk 1.3.0)" if pl.get("designs") else "")])
    for d in pl.get("designs") or []:
        rows.append([d["name"], f"No risk-of-bias assessment: {d.get('reason', '')}" if d.get("none")
                     else robplan.tool_def(p, d["tool"])["name"]])
    for k, t in (pl.get("tools") or {}).items():
        rows.append([f"Tool defined by the lead: {t.get('name')}",
                     f"{robtools.version_words(t.get('version'), start=True) or 'No version given'}. Citation: "
                     f"{robtools.end_sentence(t.get('citation'))} {util.plural(len(t.get('items') or []), 'item')}."])
    old = pl.get("legacy") or {}
    if old.get("tools") or old.get("other"):
        rows.append(["Named before ReviewDesk 1.3.0", "; ".join(old.get("tools") or []) + (("; " if old.get("tools") else "")
                                                                                           + old["other"] if old.get("other") else "")])
    return {"title": "Risk-of-bias plan", "rows": rows}


def summary_text(p: Project) -> str:
    sm = summary(p)
    out = [f"Set-up summary: {sm['title']}", f"Made by ReviewDesk {sm['version']} on {_date(sm['made'][:10])}. Check every "
           "line against the protocol before screening starts.", ""]
    for sec in sm["sections"]:
        out += [sec["title"].upper(), ""]
        for label, value in sec["rows"]:
            if isinstance(value, list):
                out.append(f"  {label}:")
                for k, v in enumerate(value, 1):
                    out += textwrap.wrap(f"{k}. {v}", 96, initial_indent="    ", subsequent_indent="       ")
            else:
                out += textwrap.wrap(f"{label}: {value}", 96, initial_indent="  ", subsequent_indent="      ")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _summary_why_not(p: Project, me: str) -> str:
    return "" if p.reviewer(me) else "Choose who you are in this project first (top right)."


def h_readiness(p, me, q, body):
    return check(p, me)


def h_summary(p, me, q, body):
    if not p.reviewer(me):
        raise PermissionDenied("Choose who you are in this project first (top right).")
    return summary(p)


API_GET = {"/api/readiness": h_readiness, "/api/setup_summary": h_summary}
API_POST = {}
DOWNLOADS = {"setup_summary": ("set-up summary.txt", "text/plain; charset=utf-8",
                               lambda p, me: summary_text(p).encode("utf-8"), _summary_why_not)}
