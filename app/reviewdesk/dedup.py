"""Removing duplicate records across database exports.

The rule is deliberately conservative, and is reported word for word (PRISMA-S item 16): see RULE_TEXT, which must
say exactly what Index.find and merge_into do. De-duplication is incremental: records that already have an ID keep it,
so an update search or a citation search can be added later without renumbering anything already screened. Every
record is compared with every record already kept, whichever search found it, so a record found again by the update
search joins the record already in the project (and counts as a duplicate in the update search's column).

The principle. A record wrongly merged takes a report out of someone's review without a word, which is the worst
thing this program can do; a duplicate left in costs one click. So the rule merges only when it is safe to, and
anything doubtful is listed for a person to decide (possible_duplicates), never merged.

A shared identifier is not proof. One DOI can cover a whole conference supplement (every abstract in it), and a
database can give a DOI or PubMed ID to the wrong record (an erratum given its article's DOI), so records that share
one merge only when their titles are similar, no volume, issue or first page differs (details_differ), their first
authors do not differ unless the titles are the same, and neither is a comment, erratum or conference abstract unless
both are (id_doubt). A pair that fails is listed for a person to check, with the reason.

Titles (title_key). Databases write the same title differently: case, punctuation, spacing, accents, "&" for "and",
and Greek letters spelt out (MEDLINE's "TNF-alpha" is Embase's "TNF-α"); all of those compare equal. What carries
meaning is kept: the letters and digits of every script (a title in Chinese is a title, not an empty one), Greek
letters (as their names, so alpha is never beta), and the signs + − < > = ± (HER2+ is not HER2−).

Short titles. A normalised title under SHORT characters ("Ambulance offload delays") is shared by many unrelated
reports, so the title can never be the main evidence: such a record merges on its title only when the first author,
the year and the journal all agree as well (all four given in both records), no volume, issue or first page differs,
and the title is not one of the HEADINGS a journal prints again and again ("Editorial"). The same article exported
from two databases agrees on all of these; two different reports almost never do.

Projects made by earlier versions. fingerprint() names a record inside its import file in the "not duplicates"
decisions a project has saved, so it must give the same answer for ever: it keeps the plain key (tkey) and the first
author's surname as version 1.3.0 worked them out, whatever the rule now compares.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from .parsers import year_of

SHORT = 25            # normalised titles shorter than this match only with the first author, year and journal as well

# Titles that are only a heading a journal reuses (normalised as title_key does): never matched on their title at all.
HEADINGS = frozenset((
    "editorial", "guesteditorial", "editorialcomment", "editorsnote", "fromtheeditor", "fromtheeditors", "introduction",
    "foreword", "preface", "prologue", "commentary", "invitedcommentary", "comment", "comments", "reply", "authorsreply",
    "response", "lettertotheeditor", "letterstotheeditor", "letter", "letters", "correspondence", "erratum", "errata",
    "correction", "corrections", "corrigendum", "retraction", "noticeofretraction", "expressionofconcern", "abstracts",
    "conferenceabstracts", "meetingabstracts", "posterabstracts", "news", "inbrief", "inthisissue", "highlights",
    "overview", "summary", "conclusion", "conclusions", "discussion", "contents", "tableofcontents", "index",
    "acknowledgement", "acknowledgements", "acknowledgment", "acknowledgments", "reviewers", "thankyoutoourreviewers",
    "obituary", "bookreview", "bookreviews", "announcement", "announcements", "calendar", "perspective", "viewpoint",
    "update", "untitled", "notitle", "noabstract"))

RULE_TEXT = (
    "Each record was compared with every record already kept, whichever search had found it, so a record found again by "
    "the update search or by other methods joined the record already in the project. "
    "Titles were compared after removing punctuation, accents and case, keeping the letters and digits of every script, "
    "Greek letters (spelt out or not) and plus, minus, less-than, greater-than and equals signs. "
    "Two records were treated as the same record when they shared a PubMed ID or a DOI and had similar titles "
    "(at least 85% alike by the sequence-matching ratio of Python's difflib on the first 200 characters; or one title "
    "being the other cut short, both at least 25 characters long; or one record having no title), with no volume, "
    "issue or first page that differed, the same first-author surname unless the titles were the same, and both or "
    "neither being a comment, erratum or conference abstract, because one DOI can cover a whole conference supplement. "
    "Without a shared identifier, two records were treated as the same record when they had "
    "the same normalised title (compared on its first 250 characters) of at least 25 characters and either the same "
    "publication year with the same first-author surname, or the same publication year and journal when they did not "
    "both name a first author (a record with neither author nor journal matched on title and year alone), or the same "
    "first-author surname with publication years no more than one apart (or a year missing) and, when the years "
    "differed, the same journal (written in full or abbreviated). A "
    "normalised title shorter than 25 characters matched only when both records gave the same first-author surname, "
    "publication year and journal; a title that is only a heading "
    "a journal reuses, such as Editorial or Introduction, was never matched on its title. No title-based match was "
    "made between records carrying different DOIs or different PubMed IDs, or giving a different volume, issue or "
    "first page. A record whose publication type marked it as "
    "an erratum, correction, comment, letter, reply or retraction was never merged on title alone with one that was not, "
    "and a conference abstract (including a record of type Abstract) was never merged on title alone with a record "
    "that was not a conference abstract, "
    "because these are separate reports under PRISMA 2020. Files were read in the order they were added to the records "
    "(files added together: database searches, then the update search, then other methods; MEDLINE first, then Embase "
    "and the other databases). When records merged, the first record read gave the merged record its title and year, "
    "the longer abstract was kept, and details missing from it (such as its DOI, PubMed ID, journal or pages) were taken "
    "from the other; a record whose screening had started kept exactly the text it was screened with. Every merge was "
    "listed; pairs marked by the review team as not duplicates were kept apart, and pairs marked as the same report "
    "were merged. Pairs that shared an identifier but were not merged, or had very similar titles (or the same "
    "short title, year and first author), including pairs found by different searches, were listed to be "
    "checked by hand.")

SET_RANK = {"main": 0, "update": 1, "other": 2}       # the order the searches' files are read in


def kept_first(a: dict, b: dict) -> tuple:
    """(the record that stays, the one that joins it) for two records found by different searches: the earlier
    search's record stays, as when the rule merges them, so each PRISMA column still adds up."""
    return (b, a) if SET_RANK.get(b.get("set"), 3) < SET_RANK.get(a.get("set"), 3) else (a, b)


def tkey(t: str) -> str:
    """The plain key of version 1.3.0 and earlier: the letters a to z and the digits, nothing else. The rule no longer
    compares titles with it (IL-1α and IL-1β, HER2+ and HER2−, and every title in a non-Latin script came out the
    same: see title_key). It is kept exactly as it was for fingerprint() and for matching a title inside the text of a
    PDF (fulltext.py), which need a key that never changes and can be built one character at a time."""
    t = unicodedata.normalize("NFKD", t or "").lower()
    t = re.sub(r"^\[|\]\.?$", "", t.strip())
    return re.sub(r"[^a-z0-9]", "", t)


# Accents: the combining marks of the Latin, Greek and Cyrillic scripts, which databases keep, drop or lose. Other
# scripts' marks (the vowel signs of Hindi or Thai, the voicing marks of Japanese) spell different words, and stay.
_ACCENTS = re.compile("[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f]")
# Latin letters that Unicode does not take apart into a letter and an accent, as databases without them write them.
_LATIN = {"ø": "o", "ł": "l", "đ": "d", "ħ": "h", "ŧ": "t", "ı": "i", "æ": "ae", "œ": "oe"}
GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
         "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron", "π": "pi", "ρ": "rho",
         "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega"}
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"       # hyphens, dashes and the minus sign: all written "-" first
MINUS = "\u2212"                                               # in a key: a minus sign that carries meaning
_SIGNS = frozenset("+<>=±" + MINUS)
# "CD4(+)", "HER2(-)", "Ca(2+)": how PubMed writes a superscript sign. The brackets go; the sign stays on its word.
_BRACKETED_SIGN = re.compile(r"\((\d*[+-]+)\)")
# A minus sign, as against a hyphen or a dash: it ends a word ("HER2-", "HER2- and ER-positive", "Rh-") and nothing
# joins it to the next one. "Learning-based", "triage - a trial", "triage--a trial" and "2019-2021" have none.
_MINUS_SIGN = re.compile(r"(?<=[^\W_])-(?![^\W_]|-)")


def _fold(s: str) -> str:
    """Case and accents removed, and every form of a character made one (ﬁ as fi, ² as 2, ⁺ as +, Ⅱ as ii, ß as ss)."""
    s = unicodedata.normalize("NFKD", s or "").casefold()
    return s if s.isascii() else _ACCENTS.sub("", unicodedata.normalize("NFKD", s))


@lru_cache(maxsize=65536)
def name_key(s: str) -> str:
    """A name (a surname, a journal) as its letters and digits, in any script: 'Te Whāiti' as tewhaiti, 'Müller' as
    muller, 'Søren' as soren, '张伟' as itself."""
    s = _fold(s)
    if s.isascii():
        return re.sub(r"[^a-z0-9]", "", s)
    return "".join(_LATIN.get(c, c) for c in s if c.isalnum() or unicodedata.category(c)[0] == "M")


@lru_cache(maxsize=65536)
def title_key(t: str) -> str:
    """A title as the rule compares it. Case, punctuation, spacing and accents are removed, and "&" is read as "and",
    because databases differ in them. Everything that carries meaning stays: the letters and digits of every script,
    Greek letters (as their names: MEDLINE spells them out, "TNF-alpha", where Embase keeps "TNF-α"), plus and minus
    signs ("HER2+" is not "HER2−", whether the sign is typed, a superscript, or in brackets as PubMed writes it), and
    the signs <, >, = and ±. Ovid's [brackets] round a translated title are removed."""
    t = _fold(t).strip()
    t = re.sub(r"^\[|\]\.?$", "", t)
    if not t.isascii():
        t = re.sub("[" + _DASHES + "]", "-", t).replace("≥", ">=").replace("⩾", ">=").replace("≧", ">=") \
            .replace("≤", "<=").replace("⩽", "<=").replace("≦", "<=")
    t = _MINUS_SIGN.sub(MINUS, _BRACKETED_SIGN.sub(r"\1", t)).replace("&", " and ")
    return "".join(GREEK.get(c) or _LATIN.get(c, c) for c in t
                   if c.isalnum() or c in _SIGNS or unicodedata.category(c)[0] == "M")


_INITIALS_V1 = re.compile(r"^(?:[A-Z]{1,3}|(?:[A-Z]\.){1,3}|[A-Z]\.?-[A-Z]\.?)\.?$")
_INITIALS = _INITIALS_V1
# After the surname only: the initials of a hyphenated first name as Embase writes them, "Jw." or "Jw" for Ji-woo.
# (Not before it, where "Le Roux J" must keep its "Le".)
_LAST_INITIALS = re.compile(r"^[A-Z][a-z]\.?$")
_SUFFIX = re.compile(r"(?i)^(?:jr|sr|jnr|snr|2nd|3rd|4th)\.?$")          # (II, III and IV already count as initials)


def _not_surname(word: str, after: bool = True) -> bool:
    """A word after the surname that is not part of it: initials, or Jr and the like. (after=False: before a comma,
    where 'Van Le, T' is all surname.)"""
    return bool(_INITIALS.match(word) or (after and _LAST_INITIALS.match(word)) or _SUFFIX.match(word))


def surname(authors: str) -> str:
    """The first author's surname, the same whichever way a database writes it: 'Te Whāiti, Aroha', 'Te Whaiti A.',
    'van der Berg JA', 'J. Smith', 'Smith JA Jr' and 'Smith JA, Jones K' (PubMed's list, with commas between the
    authors) give tewhaiti, vanderberg, smith, smith and smith."""
    a = (authors or "").split(";")[0].strip()
    if "," in a:
        a = a.split(",")[0]
        words = a.split()
        if any(c.islower() for c in a):          # 'Smith JA, Jones K' and 'Smith Jr, John' (but not 'LE VAN, T')
            while len(words) > 1 and _not_surname(words[-1], after=False):
                words.pop()
        return name_key("".join(words))
    words = a.split()
    while len(words) > 1 and _not_surname(words[-1]):          # 'Surname Initials', 'Surname Initials Jr'
        words.pop()
    while len(words) > 1 and _INITIALS.match(words[0]):         # 'Initials Surname'
        words.pop(0)
    return name_key("".join(words))


def _surname_v1(authors: str) -> str:
    """The first author's surname as version 1.3.0 keyed it, for fingerprint() only (see the notes at the top)."""
    a = (authors or "").split(";")[0].strip()
    if "," in a:
        return tkey(a.split(",")[0])
    words = a.split()
    while len(words) > 1 and _INITIALS_V1.match(words[-1]):
        words.pop()
    while len(words) > 1 and _INITIALS_V1.match(words[0]):
        words.pop(0)
    return tkey("".join(words))


def journal_key(j: str) -> str:
    k = name_key(re.sub(r"(?i)^the\s+", "", (j or "").split(":")[0].split("=")[0]))
    return k[:60]


def similar_titles(a: str, b: str) -> bool:
    """Whether two records that share a PubMed ID or DOI have titles alike enough to be one report. A record with no
    title at all can be the other's twin; a title with no letter or digit in it ("???", what is left of a title in
    a script the database could not write) is a title that cannot be compared, and is never 'similar'."""
    if not (a or "").strip() or not (b or "").strip():
        return True
    ta, tb = title_key(a), title_key(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if min(len(ta), len(tb)) >= 25 and (ta.startswith(tb) or tb.startswith(ta)):
        return True                     # one database cut the title short, or left out the subtitle
    return SequenceMatcher(None, ta[:200], tb[:200]).ratio() >= 0.85


_JOURNAL_SMALL_WORDS = frozenset(("of", "the", "and", "for", "in", "on", "de", "la", "le", "der", "und", "y", "et"))


def journals_agree(a: str, b: str) -> bool:
    """Whether two journal names can be the same journal, one written in full and the other abbreviated as MEDLINE
    does ('Ann Emerg Med' and 'Annals of Emergency Medicine': word by word, one starts the other). True when either
    is missing."""
    ka, kb = journal_key(a), journal_key(b)
    if not ka or not kb or ka == kb:
        return True
    words = lambda j: [w for w in re.findall(r"[^\W_]+", name_key_words(j)) if w not in _JOURNAL_SMALL_WORDS]
    wa, wb = words(a), words(b)
    return len(wa) == len(wb) and all(x.startswith(y) or y.startswith(x) for x, y in zip(wa, wb))


def name_key_words(j: str) -> str:
    """A journal name folded as name_key folds it, with its words kept apart."""
    j = re.sub(r"(?i)^the\s+", "", (j or "").split(":")[0].split("=")[0]).replace("&", " and ")
    return " ".join(name_key(w) for w in j.split())


def years_close(y1: str, y2: str) -> bool:
    y1, y2 = year_of(y1), year_of(y2)
    return not y1 or not y2 or abs(int(y1) - int(y2)) <= 1


COMMENT_RX = re.compile(r"(?i)erratum|correction|corrigendum|comment|letter|reply|retract")
# "Abstract" alone is RIS's type ABST (a meeting abstract); PubMed's "English Abstract" means only that an article in
# another language has an abstract in English.
CONF_RX = re.compile(r"(?i)conference|congress|meeting abstract|proceeding|(?<!english )\babstracts?\b")


def is_comment(pt: str) -> bool:
    return bool(COMMENT_RX.search(pt or ""))


def is_conference(pt: str) -> bool:
    return bool(CONF_RX.search(pt or ""))


PREPRINT_RX = re.compile(r"(?i)preprint|medrxiv|biorxiv|arxiv|ssrn|research ?square|preprints\.org|authorea")


def is_preprint(r: dict) -> bool:
    return bool(PREPRINT_RX.search(" ".join([r.get("pubtype") or "", r.get("journal") or "", r.get("doi") or ""])))


def safe_to_merge_in_bulk(a: dict, b: dict) -> bool:
    """Only plainly the same report: same title, year and first author, in the same journal, and neither a
    conference abstract, preprint, comment or translation (those are separate reports under PRISMA 2020)."""
    ta = title_key(a.get("title"))
    if not (ta and ta == title_key(b.get("title")) and a.get("year") == b.get("year")):
        return False
    sa = surname(a.get("authors"))
    if not sa or sa != surname(b.get("authors")):
        return False
    ja, jb = journal_key(a.get("journal")), journal_key(b.get("journal"))
    if not ja or ja != jb:
        return False
    for r in (a, b):
        if is_conference(r.get("pubtype")) or is_comment(r.get("pubtype")) or is_preprint(r):
            return False
    la, lb = (a.get("language") or "").lower()[:3], (b.get("language") or "").lower()[:3]
    if la and lb and la != lb:
        return False
    return not details_differ(a, b)


SHORT_RULE = "short title, year, first author and journal"
TITLE_RULES = ("title and year", "title and first author", SHORT_RULE)


def keys_of(r: dict) -> list[tuple[str, str]]:
    tk, sn = title_key(r.get("title")), surname(r.get("authors"))
    keys = []
    if r.get("pmid"):
        keys.append(("PubMed ID", "pmid:" + r["pmid"]))
    if r.get("doi"):
        keys.append(("DOI", "doi:" + r["doi"].lower()))
    if len(tk) >= SHORT:
        if r.get("year"):
            keys.append(("title and year", f"ty:{tk[:250]}|{r['year']}"))
        if sn:
            keys.append(("title and first author", f"ta:{tk[:250]}|{sn}"))
    else:
        jk = journal_key(r.get("journal"))
        if tk and sn and r.get("year") and jk and tk not in HEADINGS:      # all four, in both records (see the notes)
            keys.append((SHORT_RULE, f"ts:{tk}|{r['year']}|{sn}|{jk}"))
    return keys


def _first_page(pages) -> str:
    return tkey(re.split(r"[-–—]", str(pages or ""))[0])


def details_differ(a: dict, b: dict) -> bool:
    """The two records give a different volume, issue or first page (where both give one)."""
    for get in (lambda r: tkey(r.get("volume")), lambda r: tkey(r.get("issue")), lambda r: _first_page(r.get("pages"))):
        x, y = get(a), get(b)
        if x and y and x != y:
            return True
    return False


def kinds(r: dict) -> tuple:
    """What kind of report a record is, as far as merging goes: (conference abstract, comment or erratum, preprint)."""
    return is_conference(r.get("pubtype")), is_comment(r.get("pubtype")), is_preprint(r)


def id_doubt(a: dict, b: dict) -> str:
    """Why two records that share a PubMed ID or DOI may still be different reports ('' when there is no reason to
    doubt it), in words for the list of possible duplicates."""
    if not similar_titles(a.get("title"), b.get("title")):
        return "different titles"
    if details_differ(a, b):
        return "different volume, issue or pages"
    ta = title_key(a.get("title"))
    same_title = bool(ta) and ta == title_key(b.get("title"))
    sa, sb = surname(a.get("authors")), surname(b.get("authors"))
    if sa and sb and sa != sb and not same_title:
        return "different first authors"
    if kinds(a)[:2] != kinds(b)[:2]:
        return "different kinds of report"
    return ""


class Index:
    """The match keys of every record kept so far."""

    def __init__(self, records: list[dict]):
        self.records = records
        self.pos = {r["id"]: i for i, r in enumerate(records)}
        self.map: dict[str, list[int]] = {}
        for i, r in enumerate(records):
            self.add(i, r)

    def add(self, i: int, r: dict) -> None:
        for _, k in keys_of(r):
            lst = self.map.setdefault(k, [])
            if i not in lst:
                lst.append(i)

    def find(self, r: dict, not_dups: set) -> tuple[int | None, str]:
        sn, jn = surname(r.get("authors")), journal_key(r.get("journal"))
        for label, k in keys_of(r):
            for h in self.map.get(k, []):
                old = self.records[h]
                pair = frozenset((old["id"], r.get("_tmp", "")))
                if pair in not_dups or (r.get("_fp") and frozenset((old["id"], r["_fp"])) in not_dups):
                    continue
                matched = label
                if label in ("PubMed ID", "DOI") and id_doubt(r, old):
                    continue
                if label in TITLE_RULES:
                    if r.get("doi") and old.get("doi") and r["doi"].lower() != old["doi"].lower():
                        continue
                    if r.get("pmid") and old.get("pmid") and r["pmid"] != old["pmid"]:
                        continue
                if label in TITLE_RULES and details_differ(r, old):
                    continue
                if label == "title and year":
                    osn, ojn = surname(old.get("authors")), journal_key(old.get("journal"))
                    if sn and osn and sn == osn:
                        matched = "title, year and first author"
                    elif sn and osn:
                        continue                   # different first authors: two reports, or a doubt for a person
                    elif jn and ojn and jn == ojn:
                        matched = "title, year and journal"
                    elif not (sn or jn) or not (osn or ojn):
                        matched = "title and year only (no author or journal to confirm)"
                    else:
                        continue
                if label == "title and first author":
                    if not years_close(r.get("year"), old.get("year")):
                        continue
                    if year_of(r.get("year")) != year_of(old.get("year")) and \
                            not journals_agree(r.get("journal"), old.get("journal")):
                        continue                   # a protocol and its results, or two reports a year apart
                if label in TITLE_RULES:               # a shared PubMed ID or DOI outranks how a database labelled the type
                    if is_comment(r.get("pubtype")) != is_comment(old.get("pubtype")):
                        continue
                    if is_conference(r.get("pubtype")) != is_conference(old.get("pubtype")):
                        continue
                return h, matched
        return None, ""


def merge_into(old: dict, new: dict, source: str) -> None:
    if len(new.get("abstract") or "") > len(old.get("abstract") or ""):
        old["abstract"] = new["abstract"]
    for k in ("doi", "pmid", "volume", "issue", "pages", "pubtype", "journal", "url", "keywords", "language", "authors"):
        if not old.get(k) and new.get(k):
            old[k] = new[k]
    if source not in old["sources"]:
        old["sources"].append(source)


def fingerprint(r: dict) -> str:
    """A stable identity for a record within one import file, used to remember 'not duplicates' decisions
    about records that were merged (and so never got an ID of their own)."""
    return "fp:" + "|".join([tkey(r.get("title"))[:80], r.get("year") or "", (r.get("doi") or "").lower(),
                             _surname_v1(r.get("authors"))])


def possible_duplicates(records: list[dict], limit: int = 500, not_dups: set | frozenset = frozenset()) -> list[dict]:
    """Pairs the rule did not merge but that may be the same report, for a person to check: very similar titles
    (same start of title with the same year, the same first 40 characters of title in any year, or the same short
    title with the same year and first author), or the same DOI or PubMed ID with different titles. Records found by
    different searches are paired too, as the rule merges them: in such a pair "a" is the earlier search's record,
    the one that stays if they are merged (kept_first)."""
    blocks: dict[str, list[dict]] = {}
    for r in records:
        tk = title_key(r.get("title"))
        if len(tk) >= 20:
            blocks.setdefault("ty:" + tk[:12] + "|" + (r.get("year") or ""), []).append(r)
            blocks.setdefault("t40:" + tk[:40], []).append(r)
        elif tk and r.get("year") and surname(r.get("authors")):
            blocks.setdefault(f"ts:{tk}|{r['year']}|{surname(r.get('authors'))}", []).append(r)
        if r.get("doi"):
            blocks.setdefault("doi:" + r["doi"].lower(), []).append(r)
        if r.get("pmid"):
            blocks.setdefault("pmid:" + r["pmid"], []).append(r)
    seen, out = set(), []
    fp = {}
    for group in blocks.values():
        if len(group) < 2 or len(group) > 60:          # a huge block is a common short title, not one study
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = kept_first(group[i], group[j])
                pair = frozenset((a["id"], b["id"]))
                if pair in seen or a["id"] == b["id"]:
                    continue
                seen.add(pair)
                if not_dups:
                    fa = fp.setdefault(a["id"], fingerprint(a))
                    fb = fp.setdefault(b["id"], fingerprint(b))
                    if pair in not_dups or frozenset((a["id"], fb)) in not_dups or frozenset((fa, b["id"])) in not_dups:
                        continue
                ratio = SequenceMatcher(None, title_key(a["title"])[:200], title_key(b["title"])[:200]).ratio()
                # (a pair is looked at once, in the first block that holds it, so whether it shares an identifier is
                # asked of the pair, not of the block)
                shared = ("PubMed ID" if a.get("pmid") and a["pmid"] == b.get("pmid") else
                          "DOI" if a.get("doi") and a["doi"].lower() == (b.get("doi") or "").lower() else "")
                if ratio >= 0.9 or shared:
                    why = "very similar titles"
                    if shared:
                        doubt = id_doubt(a, b) or ("different titles" if ratio < 0.9 else "")
                        if doubt:
                            why = f"same {shared}, {doubt}"
                    out.append({"a": a["id"], "b": b["id"], "similarity": round(ratio, 3),
                                "identical": safe_to_merge_in_bulk(a, b), "kinds_differ": kinds(a) != kinds(b),
                                "why": why})
                if len(out) >= limit:
                    return out
    return out
