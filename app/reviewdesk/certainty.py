"""Rating the certainty of the evidence for each main outcome with GRADE (PRISMA 2020 items 15 and 22).

Who does what. The project lead defines the outcomes: a name, how important it is, whether it is the effect of an
intervention or the accuracy of a test or tool, the comparison and measure in plain words, and which included studies
contribute. The two reviewers (the same two who judge risk of bias) rate each outcome independently: the starting
certainty, the five reasons to rate down, the three reasons to rate up (with a reason why rating up is justified
wherever GRADE calls it unusual), the effect in words and a plain-language statement. Neither sees the other's ratings
until both have finished the outcome; then both see both, the outcome can no longer be reopened, and the lead records
the agreed rating wherever the two differ. This follows risk of bias exactly (review.py). An outcome counts as finished
only when its plain-language statement's wording matches the final certainty, or the lead has recorded why it differs.

Storage, in the shared folder (each file written by one person on one computer, chained; see integrity.py):
  certainty/outcomes@<computer>.jsonl  the lead's outcome definitions: {"o", "def": {...}, "by"}
  certainty/<ID>@<computer>.jsonl      each reviewer's ratings: {"o", "f": item, "v": value, "x": explanation}, and
                                       {"o", "done": true|false, "brev": the outcome's body revision when finished}
  certainty/agreed@<computer>.jsonl    the lead's agreed ratings: {"o", "f", "v", "x", "by"}, with "why" when the lead
                                       records a rating neither reviewer gave (for example, changes one both gave
                                       identically); and the lead's reason for keeping a statement worded differently
                                       from its certainty: {"o", "f": "statement_kept", "v": the statement, "level", "x",
                                       "by"}
  The lead's outcomes file also holds the figures for each test-accuracy comparison (GRADE guidance 21): {"acc": the
  comparison in lower case, "def": {condition, threshold, prev: [{p, source}], mode, sens, spec, interval, direct}}.

An outcome's "body revision" goes up when the lead changes its kind or its studies. A reviewer's "finished" counts
only for the revision it was given for, so a reviewer who finished before such a change looks at it again. Once both
have finished, the kind and the studies cannot change (both have seen each other's ratings).
"""
from __future__ import annotations

import math
import re

from . import integrity, review, robtools, util
from .project import Project, ProjectError

SUB = "certainty"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

IMPORTANCE = {"critical": "Critical", "important": "Important", "limited": "Of limited importance"}
KINDS = {"effect": "Effect of an intervention", "accuracy": "Accuracy of a test or tool against a reference standard",
         "other": "Other"}
SYNTHESIS = {"": "Not decided yet", "pooled": "Meta-analysis (results pooled)",
             "narrative": "Narrative synthesis (results not pooled)"}
LEVELS = {4: "High", 3: "Moderate", 2: "Low", 1: "Very low"}
SYMBOLS = {4: "⊕⊕⊕⊕", 3: "⊕⊕⊕◯", 2: "⊕⊕◯◯", 1: "⊕◯◯◯"}

# Where the evidence starts. "extreme": risk of bias may be rated down three levels (non-randomised studies that start
# at High, GRADE guidance 18); "upgrade": rating up is possible (randomised trials too, though GRADE calls that unusual
# and ReviewDesk then asks why; not for test accuracy).
START = {
    "rct": {"level": 4, "design": "randomised", "extreme": False, "upgrade": True,
            "label": "Randomised trials: start at High", "design_text": "Randomised trials"},
    "nrs_robins": {"level": 4, "design": "nonrandomised", "extreme": True, "upgrade": True,
                   "label": "Non-randomised studies, risk of bias assessed with ROBINS-I: start at High",
                   "design_text": "Non-randomised studies, risk of bias assessed with ROBINS-I"},
    "nrs": {"level": 2, "design": "nonrandomised", "extreme": False, "upgrade": True,
            "label": "Non-randomised studies: start at Low", "design_text": "Non-randomised studies"},
    "dta": {"level": 4, "design": "accuracy", "extreme": False, "upgrade": False,
            "label": "Test accuracy, cross-sectional or cohort studies: start at High",
            "design_text": "Cross-sectional or cohort test accuracy studies"},
    # ROBINS-E: "users may start at high certainty in the 'study limitations' (risk of bias) domain of GRADE" and use
    # the ROBINS-E evaluation to decide how many levels to rate down (Higgins et al. Environ Int 2024;186:108602).
    "nrs_robins_e": {"level": 4, "design": "nonrandomised", "extreme": True, "upgrade": True,
                     "label": "Non-randomised studies of exposures, risk of bias assessed with ROBINS-E: start at High",
                     "design_text": "Non-randomised studies of exposures, risk of bias assessed with ROBINS-E"},
    # Prognostic factors: evidence from observational (cohort) studies starts at High (GRADE guidelines 28, Foroutan et
    # al. J Clin Epidemiol 2020;121:62-70).
    "prog": {"level": 4, "design": "nonrandomised", "extreme": False, "upgrade": True,
             "label": "Prognostic factor studies (observational): start at High",
             "design_text": "Observational studies of prognostic factors"},
}
# The risk-of-bias tools that tell a study's design (a lead-defined tool does not: its design is read from the plan)
TOOL_START = {"rob2": "rct", "rob2_cluster": "rct", "rob2_crossover": "rct", "robins2": "nrs_robins",
              "robins_ba": "nrs_robins", "robins_e": "nrs_robins_e", "quips": "prog", "quadas2": "dta", "probast_ai": ""}
DOMAINS = [("rob", "Risk of bias"), ("inconsistency", "Inconsistency"), ("indirectness", "Indirectness"),
           ("imprecision", "Imprecision"), ("publication", "Publication bias")]
UPGRADES = [("large_effect", "Large effect"), ("dose_response", "Dose-response gradient"),
            ("confounding", "Plausible residual confounding")]
UP_MAX = {"large_effect": 2, "dose_response": 1, "confounding": 1}
DOWN = {0: "No serious concerns", -1: "Serious", -2: "Very serious", -3: "Extremely serious"}
# GRADE's own words for publication bias (GRADE handbook; GRADEpro evidence profiles)
DOWN_PUBLICATION = {0: "Undetected", -1: "Strongly suspected", -2: "Very strongly suspected"}
EXTREME_ALWAYS = ["imprecision"]     # may be rated down three levels whatever the start (GRADE guidance 34)
UP = {0: "None", 1: "Rated up one level", 2: "Rated up two levels"}
TEXTS = [("effect", "The effect in words"), ("statement", "Plain-language statement")]
DOMAIN_IDS = [d for d, _ in DOMAINS]
UPGRADE_IDS = [u for u, _ in UPGRADES]
UP_WHY = "up_why"                    # why rating up is justified, where GRADE calls rating up unusual
TEXT_IDS = [UP_WHY] + [t for t, _ in TEXTS]
ITEMS = ["start"] + DOMAIN_IDS + UPGRADE_IDS + TEXT_IDS
RATING_IDS = ["start"] + DOMAIN_IDS + UPGRADE_IDS     # the ratings (not the texts): the lead's changes to them are counted
NAMES = dict([("start", "Starting certainty")] + DOMAINS + UPGRADES + [(UP_WHY, "Why rating up is justified")] + TEXTS)
AGREED_TEXT = {"effect": "effect in words", "statement": "plain-language statement",
               UP_WHY: "reason why rating up is justified"}   # "the agreed …"
KEPT = "statement_kept"              # the lead's reason for keeping a statement worded differently from its certainty

INTRO = ("GRADE is the usual way to say how much confidence readers can have in the evidence for each main outcome: "
         "high, moderate, low or very low. The evidence starts high or low depending on the kind of studies, is rated "
         "down for risk of bias, inconsistency, indirectness, imprecision or publication bias, and (mainly for "
         "non-randomised studies) can be rated up. What each level means follows Balshem et al. (2011), and Murad et al. "
         "(2017) where results were not pooled; the suggested plain-language statements follow Santesso et al. (2020).")
# The sources of GRADE's own wording that ReviewDesk prints (the meaning of each level, the suggested statements),
# credited where they are printed
GRADE_REFS = ("Balshem H, Helfand M, Schünemann HJ, et al. GRADE guidelines: 3. Rating the quality of evidence. J Clin "
              "Epidemiol 2011;64:401-6 (what each level of certainty means). Murad MH, Mustafa RA, Schünemann HJ, Sultan S, "
              "Santesso N. Rating the certainty in evidence in the absence of a single estimate of effect. Evid Based Med "
              "2017;22:85-7 (where results were not pooled). Santesso N, Glenton C, Dahm P, et al. GRADE guidelines 26: "
              "informative statements to communicate the findings of systematic reviews of interventions. J Clin Epidemiol "
              "2020;119:126-35 (the plain-language statements).")

# The certainty definitions (Balshem et al. 2011), for a pooled estimate, and for results that were not pooled, where
# there is no single estimate to be confident in (Murad et al. 2017)
MEANING = {4: "We are very confident that the true {what} lies close to the estimate.",
           3: "We are moderately confident in the estimate: the true {what} is likely to be close to it, but it may be "
              "substantially different.",
           2: "Our confidence in the estimate is limited: the true {what} may be substantially different from it.",
           1: "We have very little confidence in the estimate: the true {what} is likely to be substantially different "
              "from it."}
MEANING_NARRATIVE = {
    4: "We are very confident that the true {what} is close to the {found} found in the studies.",
    3: "We are moderately confident: the true {what} is probably close to the {found} found in the studies, but it may "
       "be substantially different.",
    2: "Our confidence is limited: the true {what} may be substantially different from the {found} found in the "
       "studies.",
    1: "We have very little confidence: the true {what} is likely to be substantially different from the {found} found "
       "in the studies."}

HELP = {
    "start": "Randomised trials start at High. Non-randomised studies start at High when their risk of bias was assessed "
             "with ROBINS-I (GRADE guidance 18: ROBINS-I already compares them with a target trial, so the evidence is "
             "then usually rated down at least two levels for risk of bias), and otherwise at Low. Test accuracy "
             "evidence starts at High when it comes from cross-sectional or cohort studies of patients with diagnostic "
             "uncertainty that compare the test directly with an appropriate reference standard (GRADE guidance 21, "
             "Schünemann et al. 2020); other accuracy designs, such as case-control studies, are rated down for risk of "
             "bias or indirectness.",
    "rob": "Limitations in how the studies were designed or run that could make their results wrong. Use the studies' "
           "overall risk-of-bias judgements (shown here): rate down if the studies that give most of the information "
           "have serious limitations. For example, most of the weight comes from studies at high risk of bias because "
           "the people measuring the outcome knew which group each participant was in. For non-randomised studies "
           "assessed with ROBINS-I, which start at High, GRADE guidance 18 links the judgements to levels: moderate risk "
           "of bias, rate down one level; serious, two levels; critical, three levels. Such evidence is generally rated "
           "down at least two levels.",
    "inconsistency": "The studies' results differ more than chance would explain, and you cannot find out why. For "
                     "example, two studies find the intervention shortens the time to treatment and a third finds it lengthens it, "
                     "with confidence intervals that hardly overlap (or a high I²). With one study there is nothing to "
                     "compare: choose no serious concerns and say so.",
    "indirectness": "The studies answer a somewhat different question from yours: other patients or settings, another "
                    "version of the intervention, another comparison, or an outcome that only stands in for the one that "
                    "matters. For example, studies in children when your question is about adults.",
    "imprecision": "The result is uncertain because there are few participants or events: the confidence interval "
                   "includes both an important benefit and no benefit (or harm), or the evidence is smaller than one "
                   "adequately sized study. For example, a mean difference of −4 minutes with a 95% confidence interval "
                   "from −12 to +3 minutes, when a 5-minute difference would matter. Rate down two levels when the "
                   "evidence is far smaller than one adequately sized study would need, or the interval crosses more "
                   "than one threshold that matters; consider three levels (extremely serious) when the interval is so "
                   "wide that its two ends would lead to very different conclusions (GRADE guidance 34, Zeng et al. "
                   "2022).",
    "publication": "Studies are probably missing because their results were not published (often small studies that "
                   "found no benefit). Suspect it when the evidence is a few small studies funded by the intervention's makers, "
                   "when registered studies never reported results, or when a funnel plot (10 or more studies) is "
                   "lopsided.",
    "large_effect": "An effect so large that bias alone is unlikely to explain it: for example a relative risk below 0.5 "
                    "or above 2 (rate up one level), or below 0.2 or above 5 (two levels), from studies without serious "
                    "risk of bias.",
    "dose_response": "The effect grows with the amount of exposure: for example, the more sessions people attended, "
                     "the greater the improvement.",
    "confounding": "The biases that remain would make the effect look smaller than it really is (or would create an "
                   "effect where none was found). For example, the intervention was given to the sickest patients, yet their "
                   "outcomes were still better.",
    "effect": "Give the number of studies and participants, then the estimate and its 95% confidence interval (for "
              "example: 3 studies, 2,410 patients; mean difference −4.2 minutes, 95% CI −7.9 to −0.5). Where the "
              "results were not pooled, summarise them in words: the direction, size and consistency of the effects "
              "across the studies (Murad et al. 2017).",
    "statement": "One sentence for readers, whose wording shows the certainty (GRADE informative statements, Santesso et "
                 "al. 2020): high “reduces”, moderate “probably reduces”, low “may reduce”, very low “the evidence is "
                 "very uncertain”. Add “slightly” or “large” for the size of the effect, and say “little to no "
                 "difference” when it is too small to matter. Choose a suggestion, then edit it. ReviewDesk checks that "
                 "the wording matches the certainty: if it does not, the lead changes it or records why it differs "
                 "before the outcome counts as finished.",
    UP_WHY: "GRADE calls rating up unusual here, so say why it is justified for this evidence. The reason is required "
            "and becomes a footnote in the evidence profile.",
}
HELP_ACCURACY = {
    "rob": "Limitations in how the accuracy studies were designed or run (for example, the reference standard was "
           "applied only to some patients, or its results were known when the tool's output was read). Use the "
           "studies' overall risk-of-bias judgements (PROBAST+AI, shown here). A case-control design is usually a "
           "reason to rate down here.",
    "inconsistency": "The studies' sensitivity or specificity differ more than chance would explain, and you cannot "
                     "find out why (for example, sensitivity 0.95 in one study and 0.60 in another).",
    "indirectness": "Applicability: whether the patients, the setting, the way the tool was used and the reference "
                    "standard match your question. For example, the test was evaluated on stored records rather than in "
                    "routine use. The risk-of-bias tool's applicability judgements (such as PROBAST+AI's) help here.",
    "imprecision": "The confidence intervals for sensitivity or specificity (or for the numbers of true and false "
                   "results per 1,000 patients tested) are wide enough that the decision you would make differs across "
                   "them (for example, sensitivity 0.85, 95% CI 0.60 to 0.96). Rate down more than one level when the "
                   "intervals are very wide.",
    "publication": "Accuracy studies are probably missing because they were not published. Little is known about "
                   "publication bias in accuracy studies, and the funnel plots and tests used for intervention studies "
                   "(such as Egger's test) are misleading here: they suggest bias too often. Suspect it when you know of "
                   "accuracy studies that were done but never reported (for example, registered studies without "
                   "results), or when the evidence is a few small studies run or funded by the tool's makers (GRADE "
                   "guidance 21, Schünemann et al. 2020).",
    "effect": "Give the number of studies and patients, then the accuracy with its 95% confidence interval (for example: "
              "3 studies, 1,400 patients; sensitivity 0.85, 95% CI 0.78 to 0.90). GRADE presents accuracy as the "
              "numbers of true and false results per 1,000 patients tested at a stated prevalence (for example, at 10% "
              "prevalence: 85 true positives and 15 false negatives). Where the results were not pooled, give the range "
              "across the studies.",
    "statement": "One sentence for readers, whose wording shows the certainty. GRADE's informative statements (Santesso "
                 "et al. 2020) are written for the effects of interventions; for accuracy, ReviewDesk suggests the same "
                 "words for each level: high states it plainly, moderate “probably”, low “may”, very low “the evidence "
                 "is very uncertain”. Choose a suggestion, then edit it. ReviewDesk checks that the wording matches the "
                 "certainty: if it does not, the lead changes it or records why it differs before the outcome counts "
                 "as finished.",
}
ACCURACY_NO_UPGRADE = ("Rating up is not offered for test accuracy: GRADE for test accuracy has not yet set out how to do "
                       "it (Schünemann et al. 2020).")
UPGRADE_NOTE = {
    "nonrandomised": "Rating up is usually only for non-randomised studies with no reason to rate down (GRADE guidance "
                     "18). Leave each at None unless it clearly applies, and explain any rating up.",
    "randomised": "Randomised trials are rarely rated up: they already start at High, and GRADE has seen few convincing "
                  "examples (GRADE guidelines 9, Guyatt et al. 2011). Leave each at None unless it clearly applies; if "
                  "you rate up, explain it and say why it is justified.",
}
# GRADE cautions: shown, never blocking, but none passes without a reason. Rating up where GRADE calls it unusual, and
# non-randomised evidence assessed against a target trial (ROBINS-I or ROBINS-E: it starts at High) that ends at High
# after rating up, need a reason why rating up is justified (UP_WHY); such evidence not rated down for risk of bias
# needs the explanation of the risk-of-bias rating. Each reason becomes a footnote and is named in the methods text.
CAUTION = {
    "up_after_down": "Rated up although the evidence is also rated down for {domains}. GRADE advises rating up usually "
                     "only for non-randomised studies with no reason to rate down (GRADE guidance 18, Schünemann et al. "
                     "2019).",
    "up_rct": "Rated up although the evidence comes from randomised trials. This is unusual: they already start at "
              "High, and GRADE has seen few convincing examples (GRADE guidelines 9, Guyatt et al. 2011).",
    "robins_not_down": "Risk of bias is not rated down, but the evidence comes from non-randomised studies assessed with "
                       "{tool}, which start at High. GRADE guidance 18 rates such evidence down one level for "
                       "moderate risk of bias, two for serious and three for critical, and generally at least two "
                       "levels (Schünemann et al. 2019). Check the studies' judgements, and explain the rating if it "
                       "stays.",
    "robins_high": "The evidence comes from non-randomised studies assessed with {tool} and ends at High certainty. "
                   "GRADE guidance 18 generally rates such evidence down at least two levels for risk of bias "
                   "(Schünemann et al. 2019), so ending at High is unusual.",
}
CAUTION_ASK = "Say why rating up is justified here: the reason is required and becomes a footnote."
CAUTION_ASK_PAGE = ("Say why rating up is justified under 3, Reasons to rate up: the reason is required and becomes a "
                    "footnote.")
NEEDS_WHY = ("up_after_down", "up_rct", "robins_high")
ROBINS_ASK = ("Say why risk of bias is not rated down, in the explanation for risk of bias: it is required and becomes a "
              "footnote.")
ROBINS_ASK_PAGE = ("Explain the rating for risk of bias under 2, Reasons to rate down: the explanation is required and "
                   "becomes a footnote.")
ROBINS_FOOT = ("Not rated down, although the evidence comes from non-randomised studies assessed with {tool}, which "
               "GRADE guidance 18 generally rates down by at least two levels for risk of bias (Schünemann et al. "
               "2019). Reason: {why}")

# Does a plain-language statement's wording match the certainty? Words that show each level (Santesso et al. 2020),
# matched generously; "more likely" and "less likely" describe the effect, not the certainty, and are ignored. Very low
# needs "very uncertain" ("the evidence is very uncertain", "we are very uncertain") or "very low certainty": a bare
# "uncertain" is not GRADE's wording for any level (Santesso et al. 2020 found "we are uncertain" was misread).
WORDING = {
    "ignore": r"\b(?:more|less)\s+likely\b",
    "very_uncertain": r"\bvery\s+uncertain\b|\bvery[\s-]+low[\s-]+certainty\b",
    "uncertain": r"\b(?:very\s+)?uncertain\b|\bvery[\s-]+low[\s-]+certainty\b",
    "may": r"\bmay\b|\bmight\b|\b(?:the\s+evidence\s+)?suggests?\b|\blow[\s-]+certainty\b",
    "probably": r"\bprobably\b|\bprobable\b|\blikely\b|\bmoderate[\s-]+certainty\b",
}
WORDING_RULE = {4: "states the finding plainly, without “probably”, “may” or “uncertain”",
                3: "says “probably” or “likely”", 2: "says “may”",
                1: "says that the evidence is very uncertain (“very uncertain”, not just “uncertain”)"}
WORDING_FOUND = "says “{word}”, which is the wording for {said} certainty"
WORDING_BARE = "says “uncertain” without “very”, which is not GRADE's wording for any level of certainty"
WORDING_NONE = "has none of these words, so readers will take it as high certainty"
# the standard word for each level (Santesso et al. 2020), as the outputs quote it when a kept statement is worded more
# strongly than its certainty
STANDARD_WORD = {4: "a plain statement", 3: "“probably”", 2: "“may”", 1: "“the evidence is very uncertain”"}
WORDING_STRONGER = (" If the lead keeps this wording, the summary of findings, the evidence profile and the methods text "
                    "will say that it is worded more strongly than the standard wording for {level} certainty, and give "
                    "the reason recorded.")
WORDING_WHY = ("The plain-language statement does not match the certainty of the evidence. For {level} certainty, "
               "GRADE's wording {rule}; this statement {found}.")


# ================================================================================================ reading
def rows(p: Project) -> dict:
    """{'defs': {o: def}, 'removed': {o: def}, 'vals': {who: {o: {f: row}}}, 'done': {who: {o: row}},
    'agreed': {(o, f): row}, 'kept': {o: row}, 'acc': {comparison key: figures}}. Shared: do not change the result."""
    def work():
        defs, vals, done, agreed, kept, acc = {}, {}, {}, {}, {}, {}
        for who, d in review._rows(p, SUB):                 # every file of the folder, in time order
            if who == "outcomes" and isinstance(d.get("acc"), str) and isinstance(d.get("def"), dict):
                acc[d["acc"]] = {**d["def"], "by": d.get("by", ""), "t": d.get("t", "")}
                continue
            o = d.get("o")
            if not isinstance(o, str) or not o:
                continue
            if who == "outcomes":
                if isinstance(d.get("def"), dict):           # a later row changes or removes it; it keeps its place
                    was = defs.get(o) or {}
                    defs[o] = {**d["def"], "by": was.get("by", d.get("by", "")), "t": was.get("t", d.get("t", "")),
                               "changed": d.get("t", "")}
            elif who == "agreed":
                if d.get("f") in ITEMS:
                    agreed[(o, d["f"])] = d
                elif d.get("f") == KEPT:
                    kept[o] = d
            elif "done" in d:
                done.setdefault(who, {})[o] = d
            elif d.get("f") in ITEMS:
                vals.setdefault(who, {}).setdefault(o, {})[d["f"]] = d
        return {"defs": {k: v for k, v in defs.items() if not v.get("deleted")},
                "removed": {k: v for k, v in defs.items() if v.get("deleted")}, "vals": vals, "done": done,
                "agreed": agreed, "kept": kept, "acc": acc}
    return p.memo("certainty_rows", work)


def people(p: Project) -> list:
    """The two reviewers (as for risk of bias): [a, b], b '' when only one is named."""
    return (review.rob_people(p) + ["", ""])[:2]


def _name(p: Project, rid: str) -> str:
    return ((p.reviewer(rid) or {}).get("name") or rid) if rid else ""


def outcomes(p: Project) -> list:
    """The outcomes defined, in the order they were added: [(id, def)]."""
    return sorted(rows(p)["defs"].items(), key=lambda kv: util.ts(kv[1].get("t", "")))


def _outcome(p: Project, oid: str) -> dict:
    d = rows(p)["defs"].get(oid)
    if not d:
        raise ProjectError("That outcome no longer exists (the lead may have removed it). Reload the page.")
    return d


def studies_of(p: Project, d: dict) -> tuple:
    """(keys, gone): the outcome's studies as they are now, and those no longer among the included studies."""
    so = review.study_of(p)
    keys, gone = [], []
    for k in d.get("studies") or []:
        now = review.resolve_key(p, k, (d.get("rs") or {}).get(k), so)
        if now and now not in keys:
            keys.append(now)
        elif not now:
            gone.append(k)
    return keys, gone


def _done(R: dict, who: str, oid: str, d: dict) -> bool:
    row = R["done"].get(who, {}).get(oid) or {}
    return bool(who and row.get("done") and int(row.get("brev", 0)) == int(d.get("brev", 0)))


def _stale(R: dict, who: str, oid: str, d: dict) -> bool:
    """Finished before the lead changed the outcome's kind or studies."""
    row = R["done"].get(who, {}).get(oid) or {}
    return bool(who and row.get("done") and int(row.get("brev", 0)) != int(d.get("brev", 0)))


def both_done(p: Project, oid: str, d: dict | None = None) -> bool:
    R = rows(p)
    d = d or _outcome(p, oid)
    a, b = people(p)
    return bool(a) and _done(R, a, oid, d) and (not b or _done(R, b, oid, d))


def _mine(R: dict, who: str, oid: str) -> dict:
    return {f: {"v": r.get("v"), "x": r.get("x", "")} for f, r in R["vals"].get(who, {}).get(oid, {}).items()}


# ================================================================================================ the arithmetic
def allowed_down(f: str, start) -> list:
    """The ratings offered for a reason to rate down: three levels for imprecision (GRADE guidance 34) and for risk of
    bias in non-randomised studies that start at High (GRADE guidance 18); never for the others."""
    s = START.get(start or "")
    extreme = f in EXTREME_ALWAYS or (f == "rob" and bool(s and s["extreme"]))
    return [0, -1, -2] + ([-3] if extreme else [])


def _extreme_refused(f: str) -> str:
    if f == "rob":
        return ("Extremely serious (−3) for risk of bias is only for non-randomised studies that start at High, with risk "
                "of bias assessed with ROBINS-I (GRADE guidance 18). Choose serious or very serious.")
    return (f"Extremely serious (−3) is not used for {NAMES[f].lower()}: only imprecision, and risk of bias in "
            "non-randomised studies assessed with ROBINS-I, are rated down three levels. Choose serious or very serious.")


def _int(v):
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def nrs_high(start) -> bool:
    """Non-randomised studies that start at High because their risk of bias was assessed against a target trial
    (ROBINS-I, or ROBINS-E): GRADE guidance 18 generally rates such evidence down at least two levels."""
    s = START.get(start or "")
    return bool(s and s.get("design") == "nonrandomised" and s.get("level") == 4)


def _tool(start) -> str:
    """The risk-of-bias tool a start names ('ROBINS-I')."""
    m = re.search(r"assessed with ([A-Za-z0-9+-]+)", str((START.get(start or "") or {}).get("design_text", "")))
    return m.group(1) if m else "ROBINS-I"


def cautions(start, downs: dict, ups: dict) -> list:
    """GRADE's cautions for these ratings (shown, never blocking): [{'id', 'text'}]. Rating up evidence that is also
    rated down, or that comes from randomised trials, or non-randomised evidence assessed with ROBINS-I that ends at
    High, needs a reason why it is justified (needs_why); such evidence not rated down for risk of bias needs the
    explanation of that rating."""
    s = START.get(start or "")
    if not s:
        return []
    out = []
    up = sum(max(0, _int(ups.get(u)) or 0) for u in UPGRADE_IDS) if s["upgrade"] else 0
    down = [name.lower() for f, name in DOMAINS if (_int(downs.get(f)) or 0) < 0]
    if up and down:
        out.append({"id": "up_after_down", "text": CAUTION["up_after_down"].format(domains=_join(down))})
    if up and s["design"] == "randomised":
        out.append({"id": "up_rct", "text": CAUTION["up_rct"]})
    if nrs_high(start):
        rob, vals = _int(downs.get("rob")), [_int(downs.get(f)) for f in DOMAIN_IDS]
        if rob == 0:
            out.append({"id": "robins_not_down", "text": CAUTION["robins_not_down"].format(tool=_tool(start))})
        elif rob is not None and None not in vals and s["level"] + sum(vals) + up >= 4:
            out.append({"id": "robins_high", "text": CAUTION["robins_high"].format(tool=_tool(start))})
    return out


def robins_unexplained(start, downs: dict, ups: dict, rob_x) -> str:
    """The caution, when risk of bias in non-randomised evidence assessed with ROBINS-I is not rated down and its
    explanation is missing; otherwise ''."""
    c = next((c for c in cautions(start, downs, ups) if c["id"] == "robins_not_down"), None)
    return c["text"] if c and not str(rob_x or "").strip() else ""


def needs_why(start, downs: dict, ups: dict) -> bool:
    """Rating up that GRADE calls unusual (after rating down, or for randomised trials): a reason is required."""
    return any(c["id"] in NEEDS_WHY for c in cautions(start, downs, ups))


def compute(start: str, downs: dict, ups: dict) -> dict:
    """The certainty worked out: {'level': 1-4 or None, 'text', 'problems': [...], 'cautions': [...]}. Start, minus
    the downgrades, plus the upgrades, kept between Very low and High. Upgrades count only where rating up is
    allowed (not for test accuracy)."""
    problems = []
    s = START.get(start or "")
    if not s:
        return {"level": None, "text": "", "problems": ["Choose the starting certainty."], "cautions": []}
    total = s["level"]
    for did, name in DOMAINS:
        v = downs.get(did)
        if v is None or v == "":
            problems.append(f"Choose a rating for {name.lower()}.")
            continue
        if int(v) not in allowed_down(did, start):
            problems.append(_extreme_refused(did))
        total += int(v)
    up = 0
    for uid, name in UPGRADES:
        v = int(ups.get(uid) or 0)
        if v and not s["upgrade"]:
            problems.append(f"{name}: " + ACCURACY_NO_UPGRADE)
            continue
        up += v
    warn = cautions(start, downs, ups)
    if any(p_.startswith("Choose") for p_ in problems):
        return {"level": None, "text": "", "problems": problems, "cautions": warn}
    level = max(1, min(4, total + up))
    return {"level": level, "text": LEVELS[level], "problems": problems, "cautions": warn}


def meaning(level, kind: str = "effect", synthesis: str = "") -> str:
    """What a certainty level means. Where the results were not pooled there is no single estimate, so the narrative
    wording refers to the effects found in the studies (Murad et al. 2017)."""
    if level not in MEANING:
        return ""
    acc = kind == "accuracy"
    if synthesis == "narrative":
        return MEANING_NARRATIVE[level].format(what="accuracy" if acc else "effect", found="accuracy" if acc else "effects")
    return MEANING[level].format(what="accuracy" if acc else "effect")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


# Outcome names in every GRADE output are in sentence case: a capital at the start of a table cell, a heading or a
# sentence, and a small letter inside a sentence. A name the lead wrote with capitals of its own (an acronym such as
# 'ED length of stay' or 'HbA1c', or proper nouns such as 'Glasgow Coma Scale score') keeps them.
def _word_is_plain(w: str) -> bool:
    """A word with no capitals after its first letter and no digits ('Time', 'Under-triage'; not 'ED', 'HbA1c', nor a
    single capital before a hyphen, as in 'X-ray' or 'C-reactive protein')."""
    w = w.strip("()[]{},;:.\"'“”‘’")
    return (bool(w) and w[0].isalpha() and not any(ch.isdigit() for ch in w) and w[1:] == w[1:].lower()
            and w[1:2] != "-")


def _low(s: str) -> str:
    """An outcome's name inside a sentence: 'Time to triage' -> 'time to triage' (but 'ED length of stay', 'HbA1c at
    12 weeks' and 'Glasgow Coma Scale score' stay as written)."""
    s = " ".join(str(s or "").split())
    words = s.split()
    if not words or not words[0][0].isupper() or not _word_is_plain(words[0]) or (len(words[0]) == 1 and len(words) == 1):
        return s
    if any(w.strip("()[]{},;:.\"'“”‘’")[:1].isupper() and _word_is_plain(w) for w in words[1:]):
        return s                                 # other capitalised words: proper nouns, or the lead's own title case
    return s[0].lower() + s[1:]


def name_text(s: str) -> str:
    """An outcome's name inside a sentence (sentence case)."""
    return _low(s)


def name_title(s: str) -> str:
    """An outcome's name at the start of a table cell, heading or sentence: 'time to triage' -> 'Time to triage' (but
    'eGFR decline' stays)."""
    s = " ".join(str(s or "").split())
    w = s.split()[0] if s else ""
    return s[0].upper() + s[1:] if w and w[0].islower() and w == w.lower() else s


def _mid(s: str) -> str:
    """A phrase inside a sentence: 'The AI tool' -> 'the AI tool' (only a leading article changes)."""
    return s[:1].lower() + s[1:] if re.match(r"(The|A|An)\s", s or "") else s


def mid_name(s: str) -> str:
    """A test's or an intervention's name inside a sentence, in sentence case as the outcome names are: 'Clinical
    examination' -> 'clinical examination', 'The AI tool' -> 'the AI tool' (but 'MRI', 'HbA1c testing' and 'Ottawa
    Knee Rules' keep their own capitals)."""
    return _low(_mid(s))


def _parts(d: dict) -> tuple:
    """(intervention or test, comparator or reference) from the comparison in plain words."""
    comp = " ".join(str(d.get("comparison") or "").split())
    bits = re.split(r"\s+(?:compared with|compared to|versus|vs\.?|against)\s+", comp, maxsplit=1, flags=re.I)
    first = bits[0].strip(" .;,") if comp else ""
    second = bits[1].strip(" .;,") if len(bits) > 1 else ""
    return first, second


# ================================================================================================ test accuracy figures
# GRADE guidance 21 part 2 (Schünemann et al. J Clin Epidemiol 2020;122:142-152) presents test accuracy as the numbers
# of true and false results per 1,000 people tested at a stated pre-test probability (prevalence), with the certainty
# of each kind of result. For each accuracy comparison (an index test against a reference standard) the lead enters the
# target condition, one to three pre-test probabilities with their sources, and the pooled sensitivity and specificity
# (or, where results were not pooled, a typical value with the range across the studies), or the numbers per 1,000
# directly. Each accuracy outcome says which results it covers: sensitivity (true positives and false negatives),
# specificity (true negatives and false positives), or one kind of result.
RESULTS = {"sens": "Sensitivity (true positives and false negatives)",
           "spec": "Specificity (true negatives and false positives)",
           "tp": "True positives", "fn": "False negatives", "tn": "True negatives", "fp": "False positives"}
RESULT_ROWS = {"sens": ["tp", "fn"], "spec": ["tn", "fp"], "tp": ["tp"], "fn": ["fn"], "tn": ["tn"], "fp": ["fp"]}
ROW_IDS = ["tp", "fn", "tn", "fp"]
ROW_NAMES = {"tp": "True positives", "fn": "False negatives", "tn": "True negatives", "fp": "False positives"}
ROW_WHO = {"tp": "people with {c} correctly classified as having it",                 # GRADE guidance 21's own words
           "fn": "people with {c} incorrectly classified as not having it",
           "tn": "people without {c} correctly classified as not having it",
           "fp": "people without {c} incorrectly classified as having it"}
INTERVALS = {"ci": "95% CI", "range": "range across the studies"}
ACC_MODES = {"pooled": "Worked out from the sensitivity and specificity", "direct": "Entered directly"}
ACC_MAX_PREV = 3
ACC_HELP = ("GRADE presents test accuracy as the numbers of true and false results per 1,000 people tested at a stated "
            "pre-test probability (the prevalence of the condition among the people who would be tested), with the "
            "certainty of each (GRADE guidance 21, Schünemann et al. 2020). Give the prevalence and where it comes from "
            "(for example, the median of the included studies, or a national audit), and the pooled sensitivity and "
            "specificity with their 95% confidence intervals. Where the results were not pooled, give a typical value "
            "(such as the median) and the range across the studies. Up to three prevalences can be shown, for example "
            "the lowest and highest seen in practice.")
ACC_MISSING = ("The summary of findings for test accuracy needs the pre-test probability (prevalence) and the numbers of "
               "true and false results per 1,000 people tested (GRADE guidance 21). The project lead enters them under "
               "Accuracy figures on the Rate certainty page.")


def _acc_key(comparison) -> str:
    """The key of an accuracy comparison: its words in lower case."""
    return " ".join(str(comparison or "").split()).lower()


def accuracy_result(d: dict) -> str:
    """Which results an accuracy outcome covers: the lead's choice, or, for an outcome defined before the choice
    existed, what its name or measure says ('sens', 'spec', 'tp', 'fn', 'tn', 'fp'); '' when it cannot tell."""
    r = d.get("result") or ""
    if r in RESULTS:
        return r
    t = " ".join(str(d.get(k) or "") for k in ("name", "measure")).lower()
    if "sensitivity" in t:
        return "sens"
    if "specificity" in t:
        return "spec"
    have = [k for k, words in (("tp", "true positive"), ("fn", "false negative"), ("tn", "true negative"),
                               ("fp", "false positive")) if words in t]
    if set(have) == {"tp", "fn"}:
        return "sens"
    if set(have) == {"tn", "fp"}:
        return "spec"
    return have[0] if len(have) == 1 else ""


def _rhu(x: float) -> int:
    """Rounded half up (0.5 -> 1), as people round."""
    return int(math.floor(x + 0.5))


def _pct(x: float) -> str:
    """A percentage to one decimal place, as in every report: 20.0, 12.5."""
    return f"{float(x):.1f}"


def _prop(x) -> str:
    return f"{x:.2f}" if x is not None else ""


def num_text(v) -> str:
    """'170 (156 to 182)', or '170' without an interval; '' for nothing."""
    if not v:
        return ""
    return f"{v['n']:,}" + (f" ({v['lo']:,} to {v['hi']:,})" if v.get("lo") is not None and v.get("hi") is not None else "")


def per_1000(fig: dict | None) -> list:
    """The numbers per 1,000 people tested at each pre-test probability: [{'p', 'source', 'with', 'without', 'tp',
    'fn', 'tn', 'fp'}], each result {'n', 'lo', 'hi'} or None. Worked out from the sensitivity and specificity
    (true positives = sensitivity × the number with the condition; false negatives the rest of them; the same for
    specificity among those without it), or as the lead entered them."""
    out = []
    fig = fig or {}
    direct = fig.get("direct") or []
    for k, pv in enumerate(fig.get("prev") or []):
        W = float(pv["p"]) * 10
        w = _rhu(W)
        cell = {"p": pv["p"], "source": pv.get("source", ""), "with": w, "without": 1000 - w}
        if fig.get("mode") == "direct":
            given = direct[k] if k < len(direct) and isinstance(direct[k], dict) else {}
            for r in ROW_IDS:
                cell[r] = given.get(r) or None
        else:
            for r_true, r_false, est, n_all, n_exact in (("tp", "fn", fig.get("sens"), w, W),
                                                       ("tn", "fp", fig.get("spec"), 1000 - w, 1000 - W)):
                if not est or est[0] is None:
                    cell[r_true] = cell[r_false] = None
                    continue
                t = _rhu(est[0] * n_exact)
                lo = _rhu(est[1] * n_exact) if est[1] is not None else None
                hi = _rhu(est[2] * n_exact) if est[2] is not None else None
                cell[r_true] = {"n": t, "lo": lo, "hi": hi}
                cell[r_false] = {"n": n_all - t, "lo": n_all - hi if hi is not None else None,
                                 "hi": n_all - lo if lo is not None else None}
        out.append(cell)
    return out


def accuracy_figures(p: Project, d: dict) -> dict:
    """The figures the lead entered for an accuracy outcome's comparison, or {}."""
    return dict(rows(p)["acc"].get(_acc_key(d.get("comparison"))) or {})


def accuracy_view(p: Project, d: dict, fig: dict | None = None) -> dict:
    """What the summary of findings shows for an accuracy outcome: {'result', 'result_name', 'condition',
    'threshold', 'interval', 'sens', 'spec' (in words), 'prevalence': [{'p', 'source', 'with', 'without'}],
    'rows': [{'id', 'name', 'who', 'values': [text per prevalence]}], 'missing': what is still needed, or ''}."""
    fig = accuracy_figures(p, d) if fig is None else fig
    r = accuracy_result(d)
    cond = str(fig.get("condition") or "")
    nums = per_1000(fig)
    iv = INTERVALS.get(fig.get("interval") or "ci", INTERVALS["ci"])

    def est(x):
        if not x or x[0] is None:
            return ""
        return _prop(x[0]) + (f" ({iv} {_prop(x[1])} to {_prop(x[2])})" if x[1] is not None and x[2] is not None else "")
    rws = [{"id": x, "name": ROW_NAMES[x], "who": ROW_WHO[x].format(c=cond or "the condition"),
            "values": [num_text(n.get(x)) for n in nums]} for x in RESULT_ROWS.get(r, [])]
    missing = ""
    if not r:
        missing = ("Say which results this accuracy outcome covers (sensitivity, specificity, or one kind of result such "
                   "as false negatives): the project lead chooses it when changing the outcome.")
    elif not fig or not nums:
        missing = ACC_MISSING
    elif any(not all(rw["values"]) for rw in rws):
        missing = (f"The accuracy figures for “{' '.join(str(d.get('comparison') or '').split())}” give no "
                   f"{'sensitivity' if r in ('sens', 'tp', 'fn') else 'specificity'}, so the "
                   f"{_join([ROW_NAMES[x].lower() for x in RESULT_ROWS[r]])} per 1,000 people tested cannot be shown. "
                   "The project lead adds it under Accuracy figures on the Rate certainty page.")
    return {"result": r, "result_name": RESULTS.get(r, ""), "condition": cond, "threshold": fig.get("threshold", ""),
            "mode": fig.get("mode", ""), "interval": iv if fig.get("mode") != "direct" else "95% CI",
            "sens": est(fig.get("sens")), "spec": est(fig.get("spec")),
            "prevalence": [{k: n[k] for k in ("p", "source", "with", "without")} for n in nums],
            "rows": rws, "missing": missing}


def _number(v, what: str):
    """A proportion entered as 0.85, 0,85, 85 or 85%: as 0.85; None when empty."""
    t = str("" if v is None else v).strip().replace("%", "").strip()
    if not t:
        return None
    if "," in t and "." not in t:
        t = t.replace(",", ".")
    try:
        x = float(t)
    except ValueError:
        raise ProjectError(f"Give the {what} as a number, for example 0.85 or 85%.") from None
    if x > 1:
        x /= 100
    if not 0 <= x <= 1:
        raise ProjectError(f"The {what} must be between 0 and 1 (or 0% and 100%).")
    return round(x, 4)


def _estimate(v, what: str):
    """[estimate, lower, upper] from {'est', 'lo', 'hi'}; None when all are empty."""
    v = v if isinstance(v, dict) else {}
    e, lo, hi = (_number(v.get(k), f"{what}{suffix}") for k, suffix in (("est", ""), ("lo", " (lower limit)"),
                                                                       ("hi", " (upper limit)")))
    if e is None and lo is None and hi is None:
        return None
    if e is None:
        raise ProjectError(f"Give the {what} itself as well as its interval.")
    if (lo is None) != (hi is None):
        raise ProjectError(f"Give both ends of the interval for the {what}, or neither.")
    if lo is not None and not lo <= e <= hi:
        raise ProjectError(f"The {what} ({_prop(e)}) must lie within its interval ({_prop(lo)} to {_prop(hi)}).")
    return [e, lo, hi]


_DIRECT = re.compile(r"\s*(\d{1,4})\s*(?:\(\s*(\d{1,4})\s*(?:to|–|—|-)\s*(\d{1,4})\s*\))?\s*")


def _direct_value(v, what: str):
    t = str("" if v is None else v).strip().replace(",", "")
    if not t:
        return None
    m = _DIRECT.fullmatch(t)
    if not m:
        raise ProjectError(f"Give the {what} as a whole number per 1,000, with its interval if there is one: for "
                           "example 170 (156 to 182).")
    n, lo, hi = (int(x) if x is not None else None for x in m.groups())
    if n > 1000 or (lo is not None and not lo <= n <= hi <= 1000):
        raise ProjectError(f"The {what} must be at most 1,000 and lie within its interval.")
    return {"n": n, "lo": lo, "hi": hi}


def set_accuracy(p: Project, user: str, data: dict) -> dict:
    """The lead records the figures for one accuracy comparison (audited)."""
    p.require_lead(user)
    comp = _clean(data.get("comparison"), 300)
    key = _acc_key(comp)
    if not key or not any(_acc_key(d.get("comparison")) == key and d.get("kind") == "accuracy" for _, d in outcomes(p)):
        raise ProjectError("No accuracy outcome has that comparison any more (the lead may have changed it). Reload the "
                           "page.")
    cond = _clean(data.get("condition"), 200)
    if not cond:
        raise ProjectError("Name the target condition (for example: high acuity, needing to be seen within 10 minutes): "
                           "the summary of findings and the suggested statements use it.")
    prev = []
    for x in list(data.get("prev") or [])[:ACC_MAX_PREV]:
        x = x if isinstance(x, dict) else {}
        t, src = str(x.get("p") or "").replace("%", "").replace(",", ".").strip(), _clean(x.get("source"), 300)
        if not t and not src:
            continue
        try:
            pv = round(float(t), 2)
        except ValueError:
            raise ProjectError("Give each pre-test probability as a percentage, for example 20.") from None
        if not 0 < pv < 100:
            raise ProjectError("Give each pre-test probability as a percentage between 0 and 100, for example 20.")
        if not src:
            raise ProjectError(f"Say where the pre-test probability of {_pct(pv)}% comes from (for example: the median "
                               "prevalence in the included studies, or a national audit).")
        prev.append({"p": pv, "source": src})
    if not prev:
        raise ProjectError("Give the pre-test probability (the prevalence of the condition among the people who would be "
                           "tested) and where it comes from.")
    mode = data.get("mode") or "pooled"
    if mode not in ACC_MODES:
        raise ProjectError("Choose how the numbers per 1,000 are found.")
    fig = {"condition": cond, "threshold": _clean(data.get("threshold"), 200), "prev": prev, "mode": mode,
           "sens": None, "spec": None, "interval": "ci", "direct": []}
    if mode == "pooled":
        fig["sens"] = _estimate(data.get("sens"), "sensitivity")
        fig["spec"] = _estimate(data.get("spec"), "specificity")
        if not fig["sens"] and not fig["spec"]:
            raise ProjectError("Give the sensitivity or the specificity (or both), or choose to enter the numbers per "
                               "1,000 directly.")
        fig["interval"] = data.get("interval") if data.get("interval") in INTERVALS else "ci"
    else:
        given = list(data.get("direct") or [])
        for k, pv in enumerate(prev):
            g = given[k] if k < len(given) and isinstance(given[k], dict) else {}
            vals = {r: _direct_value(g.get(r), f"{ROW_NAMES[r].lower()} at {_pct(pv['p'])}%") for r in ROW_IDS}
            w = _rhu(pv["p"] * 10)
            for a, b_, n, who in (("tp", "fn", w, "with"), ("tn", "fp", 1000 - w, "without")):
                if vals[a] and vals[b_] and abs(vals[a]["n"] + vals[b_]["n"] - n) > 1:
                    raise ProjectError(f"At a pre-test probability of {_pct(pv['p'])}%, {n:,} of every 1,000 people tested "
                                       f"are {who} the condition, so the {ROW_NAMES[a].lower()} and "
                                       f"{ROW_NAMES[b_].lower()} must add up to {n:,} (they add up to "
                                       f"{vals[a]['n'] + vals[b_]['n']:,}).")
            if not any(vals.values()):
                raise ProjectError(f"Give the numbers per 1,000 at the pre-test probability of {_pct(pv['p'])}%.")
            fig["direct"].append({r: v for r, v in vals.items() if v})
    row = _write_outcome(p, user, {"acc": key, "def": fig})
    p.audit(user, "record the accuracy figures for a test", {"comparison": comp, "condition": cond, "mode": mode,
                                                             "prevalence": [f"{_pct(x['p'])}% ({x['source']})" for x in prev]})
    return row


def accuracy_groups(p: Project) -> list:
    """Each accuracy comparison: {'key', 'comparison', 'outcomes': [{'id', 'name', 'result'}], 'figures', 'numbers'}."""
    out = []
    acc = rows(p)["acc"]
    for oid, d in outcomes(p):
        if d.get("kind") != "accuracy":
            continue
        key = _acc_key(d.get("comparison"))
        g = next((g for g in out if g["key"] == key), None)
        if g is None:
            fig = acc.get(key) or {}
            g = {"key": key, "comparison": " ".join(str(d.get("comparison") or "").split()), "outcomes": [],
                 "figures": {k: v for k, v in fig.items() if k not in ("t",)} or None,
                 "numbers": per_1000(fig) if fig else []}
            out.append(g)
        g["outcomes"].append({"id": oid, "name": d.get("name", ""), "result": accuracy_result(d),
                              "synthesis": d.get("synthesis", ""), "studies": len(d.get("studies") or [])})
    return out


# How the false results per 1,000 are worked out (per_1000): as what is left, so that each pair adds up exactly
COMPLEMENT_NOTE = ("The false negatives are the people with the condition less the true positives, and the false "
                   "positives the people without it less the true negatives (their ranges likewise), so that each pair "
                   "adds up exactly; rounded on its own, a false result could differ from this by one.")


def figures_source(outs: list) -> str:
    """What a comparison's sensitivity and specificity are, from the outcomes that use it ({'synthesis', 'studies':
    a count}): 'pooled' only when one of them pools its results; 'single' when every one has one study; otherwise
    'entered' (the figures the review team entered, not a pooled estimate)."""
    if any(o.get("synthesis") == "pooled" for o in outs):
        return "pooled"
    if outs and all(int(o.get("studies") or 0) <= 1 for o in outs):
        return "single"
    return "entered"


# GRADE's informative statements for the effects of interventions, by certainty and size of effect (Santesso et al.
# 2020, table 2): (label, wording); "{name}" is the outcome, and the verbs in braces agree with the intervention
EFFECT_WORDS = {
    4: [("Reduces", "{reduces} {name}"), ("Increases", "{increases} {name}"),
        ("Large reduction", "{results} in a large reduction in {name}"), ("Large increase", "{results} in a large increase in {name}"),
        ("Slight reduction", "{reduces} {name} slightly"), ("Slight increase", "{increases} {name} slightly"),
        ("Little to no difference", "{results} in little to no difference in {name}")],
    3: [("Reduces", "probably {reduces} {name}"), ("Increases", "probably {increases} {name}"),
        ("Large reduction", "probably {results} in a large reduction in {name}"),
        ("Large increase", "probably {results} in a large increase in {name}"),
        ("Slight reduction", "probably {reduces} {name} slightly"), ("Slight increase", "probably {increases} {name} slightly"),
        ("Little to no difference", "probably {results} in little to no difference in {name}")],
    2: [("Reduces", "may reduce {name}"), ("Increases", "may increase {name}"),
        ("Large reduction", "may result in a large reduction in {name}"),
        ("Large increase", "may result in a large increase in {name}"),
        ("Slight reduction", "may reduce {name} slightly"), ("Slight increase", "may increase {name} slightly"),
        ("Little to no difference", "may result in little to no difference in {name}")],
}


# Words ending in -s that are singular (a discipline, a condition, a therapy), and plurals that do not end in -s
SINGULAR_S = {"news", "physics", "mathematics", "economics", "ergonomics", "genetics", "genomics", "informatics",
              "robotics", "aerobics", "athletics", "gymnastics", "pilates", "ethics", "politics", "logistics",
              "linguistics", "statistics", "kinetics", "pharmacokinetics", "dynamics", "therapeutics", "diagnostics",
              "orthotics", "prosthetics", "orthodontics", "paediatrics", "pediatrics", "obstetrics", "geriatrics",
              "series", "species", "measles", "mumps", "diabetes", "rabies", "herpes", "scabies", "lens", "bias",
              "this", "thus"}
PLURAL_NOT_S = {"children", "people", "women", "men", "criteria", "phenomena", "staff", "teams"}
_CUT = (r"\s+(?:for|in|of|with|by|at|on|to|from|among|during|used|given|delivered|plus|alone|added|that|which|who|"
        r"as|after|before|versus|vs|compared)\b.*$")


def plural(phrase: str) -> bool:
    """Is this noun phrase plural, so that its verb is 'reduce' rather than 'reduces'? From its head noun: the last word
    before a preposition or a clause ('Exercise programmes for older adults' → programmes). A word ending in -s is
    plural unless it is a known singular (physics, diabetes), ends in -ss, -us or -is (fitness, stimulus, dialysis) or is
    an acronym in capitals (CBT); 'NSAIDs' and 'SSRIs' (capitals, then a small s) are plural."""
    t = re.sub(r"\([^)]*\)", " ", str(phrase or ""))
    t = re.sub(_CUT, "", " ".join(t.split()), flags=re.I).strip(" .,;:-")
    if not t:
        return False
    head = re.sub(r"[^\w-]", "", t.split()[-1]).split("-")[-1]
    if not head:
        return False
    if re.fullmatch(r"[A-Z0-9]{2,}s", head):
        return True
    if head.isupper():
        return False
    low = head.lower()
    if low in PLURAL_NOT_S:
        return True
    if low in SINGULAR_S or len(low) < 4 or low.endswith(("ss", "us", "is")):
        return False
    return low.endswith("s")


def _verbs(who: str) -> dict:
    """The verbs in the suggested statements, agreeing with the intervention or test."""
    if plural(who):
        return {"reduces": "reduce", "increases": "increase", "results": "result", "has": "have",
                "identifies": "identify", "misses": "miss", "classifies": "classify"}
    return {"reduces": "reduces", "increases": "increases", "results": "results", "has": "has",
            "identifies": "identifies", "misses": "misses", "classifies": "classifies"}


# What an accuracy outcome's results say, as natural frequencies (GRADE guidance 21 presents accuracy per 1,000 people
# tested; Santesso et al. 2020, section 4.6, word accuracy findings the same way as effects: "cytology tests probably
# miss slightly more women with cervical lesions"). {v} is "", "probably " or "may "; verbs agree with the test.
ACC_SAYS = {
    "sens": "correctly {identifies} about {tp} of them and {misses} about {fn}",
    "spec": "correctly {classifies} about {tn} of them as not having it and incorrectly {classifies} about {fp} as having it",
    "tp": "correctly {identifies} about {tp} of them", "fn": "{misses} about {fn} of them",
    "tn": "correctly {classifies} about {tn} of them as not having it",
    "fp": "incorrectly {classifies} about {fp} of them as having it"}
ACC_UNCERTAIN = {"sens": "the sensitivity of {test} for {c}", "spec": "the specificity of {test} for {c}",
                 "tp": "how many people with {c} {test} correctly {identifies}", "fn": "how many people with {c} {test} {misses}",
                 "tn": "how many people without {c} {test} correctly {classifies} as not having it",
                 "fp": "how many people without {c} {test} incorrectly {classifies} as having it"}
ACC_SHOWN = {"sens": "the sensitivity shown", "spec": "the specificity shown",
             "tp": "the number of true positives shown", "fn": "the number of false negatives shown",
             "tn": "the number of true negatives shown", "fp": "the number of false positives shown", "": "the accuracy shown"}


def _acc_statements(d: dict, level: int, fig: dict) -> list:
    """Suggested statements for an accuracy outcome: the numbers per 1,000 people tested at the first pre-test
    probability, and per 1,000 with (or without) the condition, worded for the certainty; without the figures, 'has the
    sensitivity shown'."""
    what, other = _parts(d)
    test = what or "the test"
    r = accuracy_result(d)
    cond = str((fig or {}).get("condition") or "").strip()
    nums = per_1000(fig)[0] if fig and fig.get("prev") else None
    base = {"identifies": "identify", "misses": "miss", "classifies": "classify"}
    out = []
    have = bool(cond and nums and r and all(nums.get(x) for x in RESULT_ROWS[r]))
    if have:
        n = {x: f"{nums[x]['n']:,}" for x in RESULT_ROWS[r]}
        with_ = r in ("sens", "tp", "fn")
        lead = (f"Of 1,000 people tested, of whom {nums['with' if with_ else 'without']:,} "
                f"{'have' if with_ else 'do not have'} {cond}, ")

        def says(lvl, verbs):
            v = {4: "", 3: "probably ", 2: "may "}[lvl]
            return v + ACC_SAYS[r].format(**verbs, **n)
        verbs = _verbs(test) if level in (4, 3) else base
        if level in (4, 3, 2):
            out.append({"label": "Per 1,000 tested", "text": lead + f"{mid_name(test)} {says(level, verbs)}."})
            est = fig.get("sens") if r == "sens" else fig.get("spec") if r == "spec" else None
            if est and est[0] is not None and fig.get("mode") != "direct":
                k = _rhu(est[0] * 1000)
                v = {4: "", 3: "probably ", 2: "may "}[level]
                if r == "sens":
                    t = (f"{test} {v}correctly {verbs['identifies']} about {k:,} per 1,000 people with {cond} and "
                         f"{verbs['misses']} about {1000 - k:,}.")
                else:
                    t = (f"{test} {v}correctly {verbs['classifies']} about {k:,} per 1,000 people without {cond} as not "
                         f"having it, and incorrectly {verbs['classifies']} about {1000 - k:,} as having it.")
                out.append({"label": "Per 1,000 with the condition" if r == "sens" else "Per 1,000 without the condition",
                            "text": _cap(t)})
            return out
    if level == 1:
        v = _verbs(test)
        vs = f" against {other}" if other else ""
        if r and cond:
            what_ = ACC_UNCERTAIN[r].format(test=mid_name(test), c=cond, **v)
        elif r in ("sens", "spec") or not r:
            what_ = f"the {RESULTS[r].split(' (')[0].lower() if r else 'accuracy'} of {mid_name(test)}{vs}"
        else:
            what_ = f"the number of {ROW_NAMES[r].lower()} with {mid_name(test)}{vs}"
        out.append({"label": "Very uncertain", "text": f"The evidence is very uncertain about {what_}."})
        if have:
            out.append({"label": "Direction, very uncertain",
                        "text": lead + f"{mid_name(test)} {says(2, base)}, but the evidence is very uncertain."})
        return out
    has = _verbs(test)["has"]
    verb = {4: has, 3: f"probably {has}", 2: "may have"}[level]
    vs = f" against {other}" if other else ""
    return [{"label": "Accuracy as shown", "text": _cap(f"{test} {verb} {ACC_SHOWN[r]}{vs}.")}]


def statements(d: dict, level) -> list:
    """Suggested plain-language statements for a certainty level: [{'label', 'text'}]. For the effects of
    interventions, GRADE's informative statements (Santesso et al. 2020), with their sizes of effect; for accuracy,
    the numbers per 1,000 people tested (GRADE guidance 21) in the same words for each level of certainty (from the
    figures the lead entered, passed in d['_acc']). Verbs agree with the intervention or test ('Exercise programmes
    probably reduce', 'Physiotherapy probably reduces')."""
    if level not in LEVELS:
        return []
    what, other = _parts(d)
    name = _low(d.get("name") or "the outcome")
    if d.get("kind") == "accuracy":
        return _acc_statements(d, level, d.get("_acc") or {})
    who = what or "the intervention"
    vs = f" compared with {other}" if other else ""
    if level == 1:
        return [{"label": "Very uncertain", "text": f"The evidence is very uncertain about the effect of {mid_name(who)} on {name}{vs}."},
                {"label": "Direction, very uncertain",
                 "text": _cap(f"{who} may reduce {name}{vs}, but the evidence is very uncertain.")}]
    verbs = _verbs(who)
    return [{"label": lab, "text": _cap(f"{who} {w.format(name=name, **verbs)}{vs}.")} for lab, w in EFFECT_WORDS[level]]


def _said(text: str) -> tuple:
    """(the certainty a statement's wording suggests, the word that shows it): (4, '') when it is stated plainly, and
    (None, 'uncertain') for a bare "uncertain", which is GRADE's wording for no level."""
    t = re.sub(WORDING["ignore"], " ", str(text or ""), flags=re.I)
    for lvl, key in ((1, "very_uncertain"), (2, "may"), (3, "probably"), (None, "uncertain")):
        m = re.search(WORDING[key], t, re.I)
        if m:
            return lvl, " ".join(m.group(0).split()).lower()
    return 4, ""


def _closest(d: dict, level, text: str) -> str:
    """The suggested statement nearest to what the statement says (the same direction and size of effect)."""
    sug = statements(d, level)
    if not sug:
        return ""
    t = str(text or "").lower()
    if level == 1 or d.get("kind") == "accuracy":
        return sug[0]["text"]
    if re.search(r"little (?:to|or) no|no (?:important )?difference|no effect|does not|did not", t):
        want = "Little to no difference"
    else:
        up = re.search(r"increas|rais|higher|longer|more\b", t) and not re.search(r"reduc|decreas|lower|shorter|fewer|less\b", t)
        size = "Large " if re.search(r"\blarge|substantial|major", t) else "Slight " if re.search(r"slight|small", t) else ""
        want = (size + ("increase" if up else "reduction")) if size else ("Increases" if up else "Reduces")
    return next((s["text"] for s in sug if s["label"] == want), sug[0]["text"])


def check_statement(text, level, d: dict | None = None) -> dict:
    """Does a plain-language statement's wording match the certainty (Santesso et al. 2020)? Checked generously,
    except at very low: very low needs 'very uncertain' (or 'very low certainty'), not a bare 'uncertain'; low 'may'
    (or 'might', 'suggests'); moderate 'probably' or 'likely'; high none of these. {'ok', 'said': the level the words
    suggest (None for a bare 'uncertain'), 'word', 'why': plain words for the lead, 'suggestion'}."""
    out = {"ok": True, "said": None, "word": "", "why": "", "suggestion": "", "stronger": False}
    text = " ".join(str(text or "").split())
    if not text or level not in LEVELS:
        return out
    t = re.sub(WORDING["ignore"], " ", text, flags=re.I)
    has = {k: bool(re.search(WORDING[k], t, re.I)) for k in ("very_uncertain", "uncertain", "may", "probably")}
    ok = {1: has["very_uncertain"], 2: has["may"] and not has["very_uncertain"],
          3: has["probably"] and not has["very_uncertain"],
          4: not (has["uncertain"] or has["may"] or has["probably"])}[level]
    said, word = _said(text)
    if not ok and has["uncertain"] and not has["very_uncertain"] and level == 1:
        said, word = None, "uncertain"           # the bare "uncertain" is what is wrong here
    out.update({"ok": ok, "said": said, "word": word})
    if not ok:
        found = (WORDING_BARE if word and said is None else
                 WORDING_FOUND.format(word=word, said=LEVELS[said].lower()) if word else WORDING_NONE)
        out["why"] = WORDING_WHY.format(level=LEVELS[level].lower(), rule=WORDING_RULE[level], found=found)
        # worded as a higher certainty than the rating: keeping it is allowed (the check reads free text and can be
        # wrong), but the outputs then say so plainly
        out["stronger"] = said is not None and said > level
        if out["stronger"]:
            out["why"] += WORDING_STRONGER.format(level=LEVELS[level].lower())
        out["suggestion"] = _closest(d or {}, level, text)
    return out


# ================================================================================================ suggesting the start
def _design_word(text: str) -> str:
    t = str(text or "").lower()
    if not t.strip():
        return ""
    if re.search(r"non-?random|quasi|before.?(and.?)?after|time series|cohort|case.?control|cross.?sectional|"
                 r"observational|controlled before|historical", t):
        return "nonrandomised"
    if "random" in t:
        return "randomised"
    return ""


def _design_field(p: Project):
    f = review.form(p)
    flds = review.fields(f)
    return next((x for x in flds if x["id"] == "design"), None) or next(
        (x for x in flds if "design" in x["label"].lower() and x["type"] in ("choice", "text")), None)


def _n_field(p: Project):
    flds = review.fields(review.form(p))
    return next((x for x in flds if x["id"] == "n" and x["type"] == "number"), None) or next(
        (x for x in flds if x["type"] == "number" and "participant" in x["label"].lower()), None)


def study_info(p: Project, keys: list) -> list:
    """For each study: its name, its risk-of-bias assessments with their overall judgement (agreed, or the same from
    both reviewers; nothing until both have finished), its design and its number of participants as extracted."""
    sts = {s["key"]: s for s in review.studies(p)}
    rob = review.rob_table(p)
    fin = review.final_values(p)
    dfl, nfl = _design_field(p), _n_field(p)
    out = []
    for k in keys:
        s = sts.get(k)
        if not s:
            continue
        assess = []
        for r in rob:
            if r["key"] != k:
                continue
            overall = next((j for code, _, j in r["domains"] if code == "O"), None)
            if not any(code == "O" for code, _, _ in r["domains"]) and r["both_done"]:
                js = [j for _, _, j in r["domains"]]          # a tool with no overall judgement (QUIPS): its domains
                worst = [j for j in ("High risk", "Moderate risk") if j in js]
                overall = (f"No overall judgement; {js.count(worst[0])} of {len(js)} domains at {worst[0].lower()}"
                           if worst and all(js) else "No overall judgement" if all(js) else "")
            assess.append({"label": r["label"], "tool": r["tool"], "tool_name": r.get("tool_short") or r["tool_name"].split(" (")[0],
                           "design": r.get("design_name", ""), "overall": overall or "", "both_done": r["both_done"]})
        design = str(fin.get(k, {}).get(dfl["id"], "") or "") if dfl else ""
        n = str(fin.get(k, {}).get(nfl["id"], "") or "") if nfl else ""
        out.append({"key": k, "label": s["label"], "reports": s["reports"], "rob": assess, "design": design, "n": n})
    return out


def suggest_start(d: dict, info: list) -> dict:
    """{'start': START key or '', 'why': text}, from the outcome's kind, the studies' risk-of-bias tools and their
    extracted design."""
    if d.get("kind") == "accuracy":
        return {"start": "dta", "why": "This outcome is the accuracy of a test or tool. GRADE for test accuracy starts at "
                                       "High when the studies are cross-sectional or cohort studies of patients with "
                                       "diagnostic uncertainty that compare the test with an appropriate reference "
                                       "standard (GRADE guidance 21); other designs are rated down for risk of bias or "
                                       "indirectness."}
    if not info:
        return {"start": "", "why": "No studies are chosen for this outcome yet."}
    rand, nonr, robins, unknown = [], [], [], []
    # the start every assessment's tool gives ('' for PROBAST+AI, None for a tool the lead defined): a suggestion that
    # speaks of all the studies is made only when every assessment gives the same one
    every = [TOOL_START.get(a["tool"]) for s in info for a in s["rob"]]
    starts = set(every) if every else set()
    if starts == {"dta"}:
        return {"start": "", "why": "These studies were assessed with QUADAS-2, a tool for test accuracy studies. If this "
                                    "outcome is a test's accuracy, the lead sets its kind to accuracy (GRADE for test "
                                    "accuracy, GRADE guidance 21); otherwise choose the start yourself."}
    if "dta" in starts:
        return {"start": "", "why": "Some of these studies were assessed with QUADAS-2, a tool for test accuracy studies, "
                                    "and others with another tool. If this outcome is a test's accuracy, the lead sets its "
                                    "kind to accuracy (GRADE for test accuracy, GRADE guidance 21); otherwise choose the "
                                    "start yourself."}
    if starts == {"nrs_robins_e"}:
        return {"start": "nrs_robins_e", "why": "The studies are non-randomised studies of exposures assessed with ROBINS-E, "
                                                "whose authors say GRADE may then start at High and rate down for the "
                                                "ROBINS-E judgements (Higgins et al. 2024). This is a suggestion: confirm "
                                                "it, or choose another start and say why."}
    if starts == {"prog"}:
        return {"start": "prog", "why": "The studies are prognostic factor studies assessed with QUIPS. GRADE starts "
                                        "observational evidence about prognostic factors at High (GRADE guidelines 28, "
                                        "Foroutan et al. 2020). This is a suggestion: confirm it, or choose another start "
                                        "and say why."}
    for s in info:
        st = {TOOL_START.get(a["tool"]) for a in s["rob"]}
        if st & {"nrs_robins"}:
            nonr.append(s["label"])
            robins.append(s["label"])
        elif "rct" in st:
            rand.append(s["label"])
        else:
            w = _design_word(" ".join([s["design"]] + [a.get("design", "") for a in s["rob"]]))
            (rand if w == "randomised" else nonr if w == "nonrandomised" else unknown).append(s["label"])
    n = len(info)
    if rand and nonr:
        return {"start": "", "why": "This outcome has both randomised and non-randomised studies. GRADE usually rates "
                                    "them as separate bodies of evidence (the lead can split the outcome in two); "
                                    "otherwise choose the start for the studies that give most of the information."}
    if not rand and not nonr:
        one = len(info) == 1
        if any(s["rob"] for s in info):
            return {"start": "", "why": f"ReviewDesk cannot tell {'this study' + chr(39) + 's' if one else 'these studies' + chr(39)} design: "
                                        f"{'its' if one else 'their'} risk-of-bias "
                                        f"{'tool does' if len(starts) <= 1 else 'tools do'} not show it (for example "
                                        "PROBAST+AI, a tool the project lead defined, or tools for different kinds of "
                                        "study), and no study design has been agreed in the extraction. Choose the start "
                                        "yourself."}
        return {"start": "", "why": f"ReviewDesk cannot tell {'this study' + chr(39) + 's' if one else 'these studies' + chr(39)} design yet: "
                                    f"{'it has' if one else 'they have'} no risk-of-bias assessment and no agreed study "
                                    "design in the extraction. Choose the start yourself."}
    note = (f" ReviewDesk could not tell the design of {_join(unknown)}." if unknown else "")

    def who(xs, what_one, what_many):
        """'The study is …', 'The studies are …', or, when some studies' design is unknown, the studies by name."""
        k = len(xs)
        if unknown:
            return f"{_join(xs)} {'is' if k == 1 else 'are'} {what_one if k == 1 else what_many}", "its" if k == 1 else "their"
        return (f"{'The study is' if n == 1 else 'The studies are'} {what_one if n == 1 else what_many}",
                "its" if n == 1 else "their")
    if rand:
        w, its = who(rand, "a randomised trial", "randomised trials")
        return {"start": "rct", "why": f"{w} (from {its} risk-of-bias tool or extracted design).{note}"}
    if len(robins) == len(nonr):
        w, its = who(nonr, "non-randomised", "non-randomised")
        return {"start": "nrs_robins", "why": f"{w}, and {its} risk of bias was assessed with ROBINS-I, so the evidence "
                                              "starts at High (GRADE guidance 18, Schünemann et al. 2019)." + note}
    w, its = who(nonr, "non-randomised", "non-randomised")
    return {"start": "nrs", "why": f"{w} and {its} risk of bias was not (or not all) assessed with ROBINS-I, so the "
                                   f"evidence starts at Low.{note}"}


# ================================================================================================ outcomes (the lead)
def _clean(s, n: int) -> str:
    return " ".join(str(s or "").split())[:n]


def define(p: Project, user: str, data: dict, oid: str = "", deleted: bool = False) -> dict:
    """Add an outcome, change one, or remove one (lead only; audited). Its kind and studies can change only until both
    reviewers have finished it; a reviewer who finished before such a change finishes it again."""
    p.require_lead(user)
    R = rows(p)
    cur = R["defs"].get(oid) if oid else None
    if (oid or deleted) and not cur:
        raise ProjectError("That outcome no longer exists (reload the page).")
    if deleted:
        row = _write_outcome(p, user, {"o": oid, "def": {**{k: v for k, v in cur.items() if k not in ("by", "t", "changed")},
                                                         "deleted": True}})
        p.audit(user, "remove a certainty outcome", {"outcome": cur.get("name", "")})
        return row
    name = _clean(data.get("name"), 200)
    if not name:
        raise ProjectError("Give the outcome a name (for example: pain at 12 weeks).")
    if data.get("importance") not in IMPORTANCE:
        raise ProjectError("Choose how important the outcome is: critical, important, or of limited importance.")
    if data.get("kind") not in KINDS:
        raise ProjectError("Choose what kind of outcome it is: the effect of an intervention, the accuracy of a test or "
                           "tool, or other.")
    synthesis = data.get("synthesis") or ""
    if synthesis not in SYNTHESIS:
        raise ProjectError("Choose how the results are synthesised.")
    result = (data.get("result") or "") if data["kind"] == "accuracy" else ""
    if result not in RESULTS and result != "":
        raise ProjectError("Choose which results this accuracy outcome covers: sensitivity, specificity, or one kind of "
                           "result.")
    sts = {s["key"]: s for s in review.studies(p)}
    keys = []
    for k in data.get("studies") or []:
        now = review.resolve_any(p, str(k))
        if not now or now not in sts:
            raise ProjectError("One of the chosen studies is no longer among the included studies (its full-text decision "
                               "may have changed). Reload the page and choose again.")
        if now not in keys:
            keys.append(now)
    brev = int((cur or {}).get("brev", 0))
    again = []                                   # reviewers who had finished and now look at it again
    if cur:
        before, _ = studies_of(p, cur)
        if cur.get("kind") != data["kind"] or set(before) != set(keys):
            if both_done(p, oid, cur):
                raise ProjectError("Both reviewers have finished this outcome and have seen each other's ratings, so its "
                                   "kind and its studies can no longer change. Add a new outcome instead (and remove this "
                                   "one if it is no longer needed).")
            again = [x for x in people(p) if x and _done(R, x, oid, cur)]
            brev += 1
    new = {"name": name, "importance": data["importance"], "kind": data["kind"],
           "comparison": _clean(data.get("comparison"), 300), "measure": _clean(data.get("measure"), 300),
           "synthesis": synthesis, "participants": _clean(data.get("participants"), 200), "studies": keys,
           "rs": {k: sts[k]["reports"] for k in keys}, "brev": brev, "deleted": False}
    if result:
        new["result"] = result
    oid = oid or ("o" + util.sha256_text(name + user + util.now())[:6])
    old_key, new_key = _acc_key((cur or {}).get("comparison")), _acc_key(new["comparison"])
    row = _write_outcome(p, user, {"o": oid, "def": new})
    acc = rows(p)["acc"]
    if (cur and new["kind"] == "accuracy" and old_key != new_key and old_key in acc and new_key and new_key not in acc
            and not any(_acc_key(d.get("comparison")) == old_key and d.get("kind") == "accuracy" for _, d in outcomes(p))):
        # the comparison was reworded: its accuracy figures go with it
        _write_outcome(p, user, {"acc": new_key, "def": {k: v for k, v in acc[old_key].items() if k not in ("by", "t")}})
    if cur:
        changed = [lab for k, lab in (("name", "name"), ("importance", "importance"), ("kind", "kind"),
                                      ("comparison", "comparison"), ("measure", "measure"), ("synthesis", "synthesis"),
                                      ("participants", "participants"), ("result", "results covered"))
                   if cur.get(k, "") != new.get(k, "")]
        if set(studies_of(p, cur)[0]) != set(keys):
            changed.append("studies")
        p.audit(user, "change a certainty outcome", {"outcome": name, "was": cur.get("name", ""), "changed": changed,
                                                      "studies": keys, "to finish again": again})
    else:
        p.audit(user, "add a certainty outcome", {"outcome": name, "importance": new["importance"], "kind": new["kind"],
                                                   "studies": keys})
    return {**row, "o": oid}


def _write_outcome(p: Project, user: str, row: dict) -> dict:
    name = f"outcomes@{p.dev()}.jsonl"
    row = {"t": util.now(), **row, "by": user}
    return integrity.append(p.path(SUB, name), row, mirror=p._local("mirror", f"{SUB}__{name}"))


# ================================================================================================ ratings (the reviewers)
def _reviewer(p: Project, user: str) -> None:
    p.require_member(user)
    if user not in [x for x in people(p) if x]:
        raise ProjectError("Only the two reviewers rate certainty (the same two who judge risk of bias: Project set-up, "
                           "Screening plan).")


def _value(f: str, v):
    """A rating as stored: a START key, a whole number, or text; None when cleared."""
    if v is None or v == "":
        return None
    if isinstance(v, (bool, list, dict)):
        raise ProjectError("Choose one of the ratings.")
    if f == "start":
        if not isinstance(v, str) or v not in START:
            raise ProjectError("Choose one of the starting points.")
        return v
    if f in DOMAIN_IDS or f in UPGRADE_IDS:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise ProjectError("Choose one of the ratings.") from None
        if f in DOMAIN_IDS and n not in DOWN:
            raise ProjectError("Choose one of the ratings.")
        if f in UPGRADE_IDS and not 0 <= n <= UP_MAX[f]:
            raise ProjectError("A large effect can rate the evidence up by one or two levels; a dose-response gradient "
                               "or plausible confounding by one." if n > 0 else "Choose one of the ratings.")
        return n
    return str(v)[:4000]


def _check_rule(f: str, v, start) -> None:
    """The GRADE rules that depend on where the evidence starts. (Rating up randomised trials, or evidence that is also
    rated down, is allowed: GRADE calls it unusual, so a reason is asked for when finishing, not refused here.)"""
    s = START.get(start or "")
    if f == "rob" and v == -3 and not s:
        raise ProjectError("Choose the starting certainty first: extremely serious (−3) for risk of bias is only for "
                           "non-randomised studies that start at High.")
    if f in DOMAIN_IDS and v == -3 and -3 not in allowed_down(f, start):
        raise ProjectError(_extreme_refused(f))
    if f in UPGRADE_IDS and v:
        if not s:
            raise ProjectError("Choose the starting certainty first: whether the evidence can be rated up depends on it.")
        if not s["upgrade"]:
            raise ProjectError(ACCURACY_NO_UPGRADE)


def rate(p: Project, user: str, oid: str, f: str, v, x: str = "") -> dict:
    """Save one of a reviewer's ratings (with its explanation)."""
    _reviewer(p, user)
    d = _outcome(p, oid)
    if f not in ITEMS:
        raise ProjectError("Unknown rating.")
    R = rows(p)
    if _done(R, user, oid, d):
        raise ProjectError("You have marked this outcome as finished. Choose 'Reopen' to change a rating.")
    v = _value(f, v)
    mine = _mine(R, user, oid)
    _check_rule(f, v, v if f == "start" else (mine.get("start") or {}).get("v"))
    row = {"o": oid, "f": f, "v": v}
    if f not in TEXT_IDS:
        row["x"] = str(x or "")[:4000]
    return review._write(p, user, SUB, row)


def finish_problems(p: Project, user: str, oid: str) -> list:
    """What a reviewer must still do before finishing an outcome (empty when they can)."""
    d = _outcome(p, oid)
    keys, _ = studies_of(p, d)
    mine = _mine(rows(p), user, oid)
    out = []
    if not keys:
        out.append("The outcome has no studies: the lead chooses which included studies contribute to it.")
    start = (mine.get("start") or {}).get("v")
    sug = suggest_start(d, study_info(p, keys))
    if start and sug["start"] and start != sug["start"] and not str((mine.get("start") or {}).get("x") or "").strip():
        out.append("Say why you chose a different starting certainty from the one suggested.")
    downs = {k: (mine.get(k) or {}).get("v") for k in DOMAIN_IDS}
    ups = {k: (mine.get(k) or {}).get("v") for k in UPGRADE_IDS}
    res = compute(start, downs, ups)
    out += res["problems"]
    for f in DOMAIN_IDS + UPGRADE_IDS:
        r = mine.get(f) or {}
        if r.get("v") and not str(r.get("x") or "").strip():
            out.append(f"Explain the rating for {NAMES[f].lower()} (it becomes a footnote in the evidence profile).")
    robins = robins_unexplained(start, downs, ups, (mine.get("rob") or {}).get("x"))
    if robins:
        out.append(robins + " " + ROBINS_ASK)
    if needs_why(start, downs, ups) and not str((mine.get(UP_WHY) or {}).get("v") or "").strip():
        why = [c["text"] for c in res["cautions"] if c["id"] in NEEDS_WHY]
        out.append(" ".join(why) + " " + CAUTION_ASK)
    if not str((mine.get("effect") or {}).get("v") or "").strip():
        out.append("Say what the evidence shows (the effect in words).")
    if not str((mine.get("statement") or {}).get("v") or "").strip():
        out.append("Write the plain-language statement (you can start from a suggestion).")
    return out


def set_done(p: Project, user: str, oid: str, done: bool) -> dict:
    """Finish an outcome (or reopen it). Once both reviewers have finished, neither can reopen it: they have seen each
    other's ratings, so changes are made by the lead as agreed ratings."""
    _reviewer(p, user)
    d = _outcome(p, oid)
    a, b = people(p)
    if done:
        probs = finish_problems(p, user, oid)
        if probs:
            raise ProjectError("Before you finish: " + " ".join(probs))
    elif both_done(p, oid, d) and b:
        raise ProjectError("You have both finished this outcome and can see each other's ratings, so it cannot be "
                           "reopened. Tell the lead what needs changing: the lead records the agreed rating.")
    row = review._write(p, user, SUB, {"o": oid, "done": bool(done), "brev": int(d.get("brev", 0))})
    p.audit(user, "finish rating certainty for an outcome" if done else "reopen rating certainty for an outcome",
            {"outcome": d.get("name", "")})
    return row


# ================================================================================================ comparing and agreeing
def _norm(v) -> str:
    return " ".join(str("" if v is None else v).split()).lower()


def rating_words(f: str, v) -> str:
    """A rating inside a sentence: 'serious (−1)', 'undetected', 'not rated up', '“Randomised trials: start at High”'."""
    if v is None or v == "":
        return "nothing"
    if f == "start":
        return f"“{START.get(v, {}).get('label', str(v))}”"
    if f in UPGRADE_IDS and not v:
        return "not rated up"
    w = _label(f, v)
    return w[:1].lower() + w[1:]


def gave_words(it: dict) -> str:
    """What the reviewers rated an item: 'both reviewers rated it serious (−1)', or 'the reviewers rated it … and …'."""
    a, b = it["a"]["v"], it["b"]["v"]
    if _norm(a) == _norm(b):
        return f"both reviewers rated it {rating_words(it['f'], a)}"
    return f"the reviewers rated it {rating_words(it['f'], a)} and {rating_words(it['f'], b)}"


def comparison(p: Project, oid: str, d: dict | None = None) -> dict:
    """Both reviewers' ratings side by side, the agreed ones, and the final result: {'items': [...], 'to_agree', 'start',
    'level', 'text', 'problems', 'cautions', 'statement_check', 'complete', 'differences', 'overrides'}. Only for an
    outcome both have finished (callers check). Complete needs every difference agreed, every explanation given, a
    reason wherever rating up is unusual, the explanation of risk of bias where non-randomised evidence assessed with
    ROBINS-I is not rated down for it, the lead's reason for every rating neither reviewer gave, the accuracy figures
    for a test-accuracy outcome, and a statement whose wording matches the certainty (or the lead's reason for keeping
    it). An item's 'override' says when the agreed rating is one neither reviewer gave: {'gave', 'same', 'why', 'by'}."""
    R = rows(p)
    d = d or _outcome(p, oid)
    a, b = people(p)
    A, B = _mine(R, a, oid), (_mine(R, b, oid) if b else None)
    if B is None:
        B = A
    items, final_vals = [], {}
    for f in ITEMS:
        ra, rb = A.get(f) or {}, B.get(f) or {}
        va, vb = ra.get("v"), rb.get("v")
        if f in UPGRADE_IDS:
            va, vb = va or 0, vb or 0
        ag = R["agreed"].get((oid, f))
        same = _norm(va) == _norm(vb)
        final = ag.get("v") if ag else (va if same else None)
        if ag and str(ag.get("x") or "").strip():
            fx = ag["x"]
        else:                                    # the explanation of a reviewer who gave the final rating
            fx = next((str(r.get("x") or "") for r, v in ((ra, va), (rb, vb))
                       if final is not None and _norm(v) == _norm(final) and str(r.get("x") or "").strip()), "")
        it = {"f": f, "name": NAMES[f], "a": {"v": va, "x": ra.get("x", "")}, "b": {"v": vb, "x": rb.get("x", "")},
              "agreed": {"v": ag.get("v"), "x": ag.get("x", ""), "by": ag.get("by", ""), "why": ag.get("why", "")} if ag else None,
              "differs": not same, "final": final, "final_x": fx, "na": False, "override": None}
        if ag and f in RATING_IDS and _norm(ag.get("v")) not in {_norm(va), _norm(vb)}:
            it["override"] = {"gave": gave_words(it), "same": same, "why": str(ag.get("why") or "").strip(),
                              "by": ag.get("by", "")}
        items.append(it)
        final_vals[f] = final
    start = final_vals.get("start")
    s = START.get(start or "")
    if s and not s["upgrade"]:                   # test accuracy is not rated up: nothing to agree for the upgrades
        for it in items:
            if it["f"] in UPGRADE_IDS:
                it.update({"na": True, "differs": False, "final": 0, "final_x": "", "override": None})
                final_vals[it["f"]] = 0
    downs = {k: final_vals.get(k) for k in DOMAIN_IDS}
    ups = {k: final_vals.get(k) for k in UPGRADE_IDS}
    settled = bool(s) and all(v is not None for v in list(downs.values()) + list(ups.values()))
    why_needed = needs_why(start, downs, ups)
    if settled and not why_needed:               # rating up is not unusual here (or nothing is rated up): no reason needed
        it = next(i for i in items if i["f"] == UP_WHY)
        it.update({"na": True, "differs": False, "final": "", "final_x": ""})
        final_vals[UP_WHY] = ""
    res = compute(start, downs, ups)
    to_agree = sum(1 for it in items if it["differs"] and not it["agreed"])
    problems = [] if to_agree else list(res["problems"])
    for it in items:
        if it["f"] in DOMAIN_IDS + UPGRADE_IDS and it["final"] and not str(it["final_x"]).strip() and not it["na"]:
            problems.append(f"Give the explanation for {it['name'].lower()} (it becomes a footnote).")
        if it["f"] in ("effect", "statement") and it["final"] is not None and not str(it["final"]).strip():
            problems.append(f"Record the agreed {AGREED_TEXT[it['f']]}.")
    if not to_agree and why_needed and not str(final_vals.get(UP_WHY) or "").strip():
        problems.append(" ".join(c["text"] for c in res["cautions"] if c["id"] in NEEDS_WHY)
                        + " Record the agreed reason why rating up is justified (it becomes a footnote).")
    rob_it = next(it for it in items if it["f"] == "rob")
    robins = robins_unexplained(start, downs, ups, rob_it["final_x"]) if not to_agree else ""
    if robins:
        problems.append(robins + " Record the agreed explanation for risk of bias (it becomes a footnote).")
    for it in items:
        if it["override"] and not it["override"]["why"]:
            problems.append(f"The agreed rating for {it['name'].lower()} ({rating_words(it['f'], it['final'])}) is one "
                            f"neither reviewer gave ({it['override']['gave']}). Record it again with the reason for "
                            "changing it (it becomes a footnote).")
    acc = accuracy_figures(p, d) if d.get("kind") == "accuracy" else {}
    if d.get("kind") == "accuracy":
        missing = accuracy_view(p, d, acc)["missing"]
        if missing:
            problems.append(missing)
    level = res["level"] if not to_agree else None
    check = {**check_statement(final_vals.get("statement"), level, {**d, "_acc": acc}), "kept": False, "reason": "",
             "by": ""}
    kept = R["kept"].get(oid) or {}
    if (not check["ok"] and str(kept.get("x") or "").strip() and _norm(kept.get("v")) == _norm(final_vals.get("statement"))
            and _int(kept.get("level")) == level):
        check.update({"kept": True, "reason": kept["x"], "by": kept.get("by", "")})
    if not check["ok"] and not check["kept"]:
        problems.append(check["why"] + f" Suggested wording: “{check['suggestion']}”. The lead changes the agreed "
                                       "statement, or keeps this wording and records why.")
    complete = to_agree == 0 and not problems and res["level"] is not None
    return {"items": items, "to_agree": to_agree, "start": start, "level": level,
            "text": res["text"] if not to_agree else "", "problems": problems, "complete": complete,
            "cautions": res["cautions"] if not to_agree else [], "why_needed": bool(why_needed and not to_agree),
            "statement_check": check, "differences": sum(1 for it in items if it["differs"]),
            "overrides": sum(1 for it in items if it["override"])}


def keep_statement(p: Project, user: str, oid: str, x: str) -> dict:
    """The lead keeps a plain-language statement whose wording does not match the certainty, and records why. The
    reason holds only for that wording at that certainty: if either changes, the statement is checked again."""
    p.require_lead(user)
    d = _outcome(p, oid)
    if not both_done(p, oid, d):
        raise ProjectError("Both reviewers must finish this outcome before its statement is agreed.")
    c = comparison(p, oid, d)
    if c["level"] is None:
        raise ProjectError("Agree the ratings that differ first: the certainty is not known until then.")
    st = next(it["final"] for it in c["items"] if it["f"] == "statement")
    if not str(st or "").strip():
        raise ProjectError("Record the agreed plain-language statement first.")
    if c["statement_check"]["ok"]:
        raise ProjectError(f"The statement's wording already matches {LEVELS[c['level']].lower()} certainty: there is "
                           "nothing to keep. Reload the page.")
    x = " ".join(str(x or "").split())[:4000]
    if not x:
        raise ProjectError("Say why the statement keeps this wording: the reason becomes a footnote in the evidence "
                           "profile.")
    out = review._write_agreed(p, user, SUB, {"o": oid, "f": KEPT, "v": str(st), "level": c["level"], "x": x})
    p.audit(user, "keep a plain-language statement worded differently from its certainty",
            {"outcome": d.get("name", ""), "certainty": LEVELS[c["level"]], "reason": x})
    return out


def agree(p: Project, user: str, oid: str, f: str, v, x: str = "", why: str = "") -> dict:
    """The lead records the agreed rating (and its explanation) for one item, once both reviewers have finished. A
    rating neither reviewer gave (for example, a change to one both gave identically) needs the lead's reason (why),
    which is reported as a footnote and counted in the methods text."""
    p.require_lead(user)
    d = _outcome(p, oid)
    if not both_done(p, oid, d):
        raise ProjectError("Both reviewers must finish this outcome before ratings are agreed.")
    if f not in ITEMS:
        raise ProjectError("Unknown rating.")
    v = _value(f, v)
    if v is None and f in DOMAIN_IDS + ["start"]:
        raise ProjectError("Choose the agreed rating.")
    if f in UPGRADE_IDS and v is None:
        v = 0
    if f in TEXT_IDS and not str(v or "").strip():
        raise ProjectError(f"Write the agreed {AGREED_TEXT[f]}.")
    cmpd = comparison(p, oid, d)
    _check_rule(f, v, v if f == "start" else cmpd["start"])
    x = str(x or "")[:4000]
    if f in DOMAIN_IDS + UPGRADE_IDS and v and not x.strip():
        raise ProjectError("Give the explanation for this rating: it becomes the footnote in the evidence profile.")
    if f == "rob" and v == 0 and nrs_high(cmpd["start"]) and not x.strip():
        raise ProjectError(f"Say why risk of bias is not rated down: the evidence comes from non-randomised studies "
                           f"assessed with {_tool(cmpd['start'])}, which GRADE guidance 18 generally rates down by at least "
                           "two levels. The explanation becomes a footnote.")
    it = next(i for i in cmpd["items"] if i["f"] == f)
    why = " ".join(str(why or "").split())[:4000]
    row = {"o": oid, "f": f, "v": v}
    if f in RATING_IDS and not it["na"] and _norm(v) not in {_norm(it["a"]["v"]), _norm(it["b"]["v"])}:
        if not why:
            raise ProjectError(f"Neither reviewer gave this rating: {gave_words(it)}. Say why you are changing it: the "
                               "reason is reported in a footnote to the evidence profile and the summary of findings, and "
                               "counted in the methods text.")
        row["why"] = why
    if f not in TEXT_IDS:
        row["x"] = x
    out = review._write_agreed(p, user, SUB, row)
    p.audit(user, "agree a certainty rating", {"outcome": d.get("name", ""), "item": NAMES[f],
                                               "rating": _label(f, v) if f not in TEXT_IDS else "(text)",
                                               **({"changed from": gave_words(it), "reason": why} if "why" in row else {})})
    return out


def down_word(f: str, v) -> str:
    """A rating down in GRADE's words: 'Serious', or for publication bias 'Strongly suspected'."""
    return (DOWN_PUBLICATION if f == "publication" else DOWN).get(v, str(v))


def _label(f: str, v) -> str:
    if v is None:
        return ""
    if f == "start":
        return START.get(v, {}).get("label", str(v))
    if f in DOMAIN_IDS:
        return down_word(f, v) + (f" ({'−' + str(-v)})" if v else "")
    if f in UPGRADE_IDS:
        return UP.get(v, str(v)) + (f" (+{v})" if v else "")
    return str(v)


# ================================================================================================ views
def _state(R: dict, who: str, oid: str, d: dict) -> str:
    if not who:
        return ""
    if _done(R, who, oid, d):
        return "finished"
    if _stale(R, who, oid, d):
        return "check again"
    return "started" if R["vals"].get(who, {}).get(oid) else "not started"


def summary(p: Project, oid: str, d: dict) -> dict:
    """One outcome for the list page: what anyone may see (no ratings until both have finished)."""
    R = rows(p)
    a, b = people(p)
    keys, gone = studies_of(p, d)
    sts = {s["key"]: s for s in review.studies(p)}
    both = both_done(p, oid, d)
    out = {"id": oid, **{k: d.get(k, "") for k in ("name", "importance", "kind", "comparison", "measure", "synthesis",
                                                   "participants", "result")},
           "result_guess": accuracy_result(d) if d.get("kind") == "accuracy" else "",
           "studies": [{"key": k, "label": sts[k]["label"]} for k in keys if k in sts], "gone": gone,
           "state_a": _state(R, a, oid, d), "state_b": _state(R, b, oid, d), "both_done": both,
           "to_agree": 0, "complete": False, "certainty": "", "level": None}
    if both:
        c = comparison(p, oid, d)
        out.update({"to_agree": c["to_agree"], "complete": c["complete"], "certainty": c["text"], "level": c["level"],
                    "problems": c["problems"]})
    return out


def outcome_view(p: Project, oid: str, viewer: str) -> dict:
    """An outcome as one person may see it: their own ratings, and the other's only once both have finished."""
    R = rows(p)
    d = _outcome(p, oid)
    a, b = people(p)
    keys, gone = studies_of(p, d)
    info = study_info(p, keys)
    both = both_done(p, oid, d)
    role = "a" if viewer == a and a else "b" if viewer == b and b else ""
    acc = accuracy_figures(p, d) if d.get("kind") == "accuracy" else {}
    out = {"id": oid, "outcome": {k: v for k, v in d.items() if k not in ("rs",)}, "studies": info, "gone": gone,
           "suggest": suggest_start(d, info), "reviewers": [a, b], "role": role,
           "mine": _mine(R, viewer, oid) if role else None, "done_mine": _done(R, viewer, oid, d) if role else False,
           "stale_mine": _stale(R, viewer, oid, d) if role else False,
           "state_a": _state(R, a, oid, d), "state_b": _state(R, b, oid, d), "both_done": both,
           "statements": {str(k): statements({**d, "_acc": acc}, k) for k in LEVELS}, "comparison": None,
           "accuracy": accuracy_view(p, d, acc) if d.get("kind") == "accuracy" else None}
    if both:
        out["comparison"] = comparison(p, oid, d)
    return out


def grade_terms() -> dict:
    """Everything the pages need to show the choices, with their help."""
    return {"intro": INTRO, "start": START, "levels": {str(k): v for k, v in LEVELS.items()},
            "symbols": {str(k): v for k, v in SYMBOLS.items()},
            "meaning": {str(k): meaning(k) for k in LEVELS}, "meaning_accuracy": {str(k): meaning(k, "accuracy") for k in LEVELS},
            "meaning_narrative": {str(k): meaning(k, "effect", "narrative") for k in LEVELS},
            "meaning_narrative_accuracy": {str(k): meaning(k, "accuracy", "narrative") for k in LEVELS},
            "domains": [{"id": i, "name": n, "help": HELP[i], "help_accuracy": HELP_ACCURACY.get(i, HELP[i])} for i, n in DOMAINS],
            "upgrades": [{"id": i, "name": n, "help": HELP[i], "max": UP_MAX[i]} for i, n in UPGRADES],
            "down": {str(k): v for k, v in DOWN.items()}, "up": {str(k): v for k, v in UP.items()},
            "down_publication": {str(k): v for k, v in DOWN_PUBLICATION.items()}, "extreme_always": EXTREME_ALWAYS,
            "help": HELP, "help_accuracy": HELP_ACCURACY, "no_upgrade_accuracy": ACCURACY_NO_UPGRADE,
            "upgrade_note": UPGRADE_NOTE, "up_why": {"id": UP_WHY, "name": NAMES[UP_WHY], "help": HELP[UP_WHY]},
            "caution": CAUTION, "caution_ask": CAUTION_ASK_PAGE, "needs_why": list(NEEDS_WHY),
            "robins_ask": ROBINS_ASK_PAGE, "start_tool": {k: _tool(k) for k in START},
            "start_nrs_high": [k for k in START if nrs_high(k)],
            "wording": {"patterns": WORDING, "rule": {str(k): v for k, v in WORDING_RULE.items()}, "found": WORDING_FOUND,
                        "bare": WORDING_BARE, "none": WORDING_NONE, "why": WORDING_WHY},
            "importance": IMPORTANCE, "kinds": KINDS, "synthesis": SYNTHESIS,
            "results": RESULTS, "row_names": ROW_NAMES, "intervals": INTERVALS, "acc_modes": ACC_MODES,
            "acc_help": ACC_HELP, "acc_max_prev": ACC_MAX_PREV}


# ================================================================================================ outputs
def progress(p: Project) -> dict:
    """For the Overview and the step list: {"outcomes": n defined, "finished": n with final agreed ratings}."""
    outs = outcomes(p)
    return {"outcomes": len(outs), "finished": sum(1 for oid, d in outs if both_done(p, oid, d) and comparison(p, oid, d)["complete"])}


def participants(p: Project, d: dict, info: list | None = None) -> str:
    """The number of participants: as the lead recorded it for the outcome, or added up from the extracted study sizes."""
    if str(d.get("participants") or "").strip():
        return str(d["participants"]).strip()
    info = info if info is not None else study_info(p, studies_of(p, d)[0])
    nums = []
    for s in info:
        try:
            nums.append(float(str(s["n"]).replace(",", "").strip()))
        except ValueError:
            pass
    if not nums:
        return ""
    total = int(sum(nums)) if all(x.is_integer() for x in nums) else round(sum(nums), 1)
    return f"{total:,}" + ("" if len(nums) == len(info) else f" (in {len(nums)} of {len(info)} studies)")


def evidence_profile(p: Project, me: str) -> dict:
    """The GRADE evidence profile from the final (agreed) ratings. A reviewer who is not the lead gets only outcomes
    both reviewers have finished.

    Each outcome: its definition (name, importance, kind, comparison, measure, synthesis), the number of studies and
    of participants, the study design and start, each reason to rate down or up as {'id', 'name', 'rating' (in GRADE's
    words: publication bias 'Undetected' / 'Strongly suspected'), 'change', 'explanation' (the footnote)}, the
    certainty and what it means, the effect, the statement, and the checks: 'cautions' (GRADE cautions that apply),
    'upgrade_justification', 'statement_check' (does the wording match the certainty, and the lead's reason if kept),
    'statement_mismatch' (True while a mismatch is unresolved), 'footnotes' [{'item', 'rating', 'text'}] and
    'problems' (what is still to do). The outcome's name is given as written ('name'), for the start of a table cell
    ('title') and for inside a sentence ('name_text'). Each rating also has 'override': the footnote when the lead
    recorded a rating neither reviewer gave, else ''; 'start_item' is the starting certainty's own entry. A
    test-accuracy outcome has 'accuracy' (accuracy_view: the numbers per 1,000 people tested, GRADE guidance 21)."""
    lead = p.is_lead(me)
    out = []
    lead_name = _name(p, p.lead_id())
    for oid, d in outcomes(p):
        both = both_done(p, oid, d)
        if not both and not lead:
            continue
        keys, _ = studies_of(p, d)
        info = study_info(p, keys)
        c = comparison(p, oid, d) if both else None
        fin = {it["f"]: it for it in (c["items"] if c else [])}
        start = (c or {}).get("start") or ""
        s = START.get(start)
        robins = any(w["id"] == "robins_not_down" for w in (c["cautions"] if c else []))

        def item(f, word):
            # the footnote: why it was rated down or up, and for a reason to rate down also why it was not, when said;
            # where non-randomised evidence assessed with ROBINS-I is not rated down for risk of bias, GRADE's caution too
            it = fin.get(f)
            v = it["final"] if it else None
            said = v is not None and (f in DOMAIN_IDS or bool(v))
            x = str((it or {}).get("final_x") or "").strip() if said else ""
            if f == "rob" and robins and x:
                x = ROBINS_FOOT.format(tool=_tool(start), why=x)
            ov = (it or {}).get("override")
            return {"id": f, "name": NAMES[f], "rating": word(f, v) if v is not None else "",
                    "change": v if isinstance(v, int) else None, "explanation": x,
                    "override": (f"Changed by the project lead ({lead_name}): {ov['gave']}. Reason: {ov['why']}"
                                 if ov and ov["why"] else "")}
        level = c["level"] if c else None
        start_item = item("start", lambda f_, v: (START.get(v) or {}).get("label", str(v)))
        start_item["explanation"] = ""
        domains = [item(f, down_word) for f in DOMAIN_IDS]
        ups = [item(f, lambda f_, v: UP.get(v, str(v))) for f in UPGRADE_IDS]
        why = str((fin.get(UP_WHY) or {}).get("final") or "").strip() if c and c["why_needed"] else ""
        for u in ups:
            if why and u["change"]:
                u["justification"] = why
        check = dict(c["statement_check"]) if c else {"ok": True, "why": "", "suggestion": "", "kept": False, "reason": ""}
        statement = str((fin.get("statement") or {}).get("final") or "")
        foot = []
        for x in [start_item] + domains + ups:
            if x["explanation"]:
                foot.append({"item": x["name"], "rating": x["rating"], "text": x["explanation"]})
            if x["override"]:
                foot.append({"item": x["name"], "rating": x["rating"], "text": x["override"], "override": True})
        if why:
            foot.append({"item": NAMES[UP_WHY], "rating": "",
                         "text": " ".join(w["text"] for w in c["cautions"] if w["id"] in NEEDS_WHY) + " Reason: " + why})
        if check.get("kept"):
            usual = "the usual" if d.get("kind") == "accuracy" else "GRADE's"      # accuracy wording is not GRADE's own
            if check.get("stronger"):
                text_ = (f"Worded more strongly than {usual} standard wording for {LEVELS[level].lower()} certainty, "
                         f"which is {STANDARD_WORD[level]}; the review team kept this wording because: "
                         + robtools.end_sentence(check["reason"]))
            else:
                text_ = (f"Worded differently from {usual} wording for {LEVELS[level].lower()} certainty, because: "
                         + check["reason"])
            foot.append({"item": "Plain-language statement", "rating": "", "text": text_})
        name = d.get("name", "")
        out.append({"id": oid, "name": name, "title": name_title(name), "name_text": name_text(name),
                    "importance": IMPORTANCE.get(d.get("importance"), ""),
                    "importance_id": d.get("importance", ""),
                    "kind": KINDS.get(d.get("kind"), ""), "kind_id": d.get("kind", ""), "comparison": d.get("comparison", ""),
                    "measure": d.get("measure", ""), "synthesis": d.get("synthesis", ""),
                    "synthesis_text": SYNTHESIS.get(d.get("synthesis") or "", ""), "studies": len(keys),
                    "participants": participants(p, d, info), "design": s["design_text"] if s else "",
                    "start": LEVELS[s["level"]] if s else "", "start_item": start_item,
                    "domains": domains, "upgrades": ups,
                    "certainty": LEVELS.get(level, "") if level else "", "level": level,
                    "symbols": SYMBOLS.get(level, "") if level else "",
                    "meaning": meaning(level, d.get("kind", ""), d.get("synthesis", "")) if level else "",
                    "effect": str((fin.get("effect") or {}).get("final") or ""), "statement": statement,
                    "statement_check": {k: check.get(k) for k in ("ok", "why", "suggestion", "kept", "reason", "stronger")},
                    "statement_mismatch": bool(c and level and not check["ok"] and not check.get("kept")),
                    "cautions": [w["text"] for w in (c["cautions"] if c else [])], "upgrade_justification": why,
                    "footnotes": foot, "problems": list(c["problems"]) if c else [],
                    "overrides": (c or {}).get("overrides", 0),
                    "accuracy": accuracy_view(p, d) if d.get("kind") == "accuracy" else None,
                    "both_done": both, "complete": bool(c and c["complete"])})
    return {"outcomes": out, "method_text": method_text(p)}


def _join(xs: list) -> str:
    """'a', 'a and b', 'a, b and c'."""
    xs = [x for x in xs if x]
    if len(xs) < 2:
        return xs[0] if xs else ""
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _agreement_text(rated: list, two: bool) -> list:
    """How often the reviewers gave the same rating, how the differences were settled, and every rating the lead
    recorded that neither reviewer gave (counted, and named)."""
    if not rated:
        return []
    ratings = [(d, it) for _, d, c in rated for it in c["items"] if it["f"] in RATING_IDS and not it["na"]]
    out = []
    if two and ratings:
        diff = [it for _, it in ratings if it["differs"]]
        settled = sum(1 for it in diff if it["agreed"])
        same = len(ratings) - len(diff)
        where = ("on the outcome both had finished" if len(rated) == 1
                 else f"on the {util.plural(len(rated), 'outcome')} both had finished")
        if not diff:
            out.append(f"They gave the same rating for all {len(ratings):,} ratings {where}.")
        else:
            txt = (f"They gave the same rating for {same:,} of {util.plural(len(ratings), 'rating')} "
                   f"({100 * same / len(ratings):.1f}%) {where}; ")
            if settled == len(diff):
                txt += ("the project lead recorded the agreed rating for the difference." if len(diff) == 1 else
                        f"the project lead recorded the agreed rating for all {len(diff):,} differences.")
            else:
                txt += (f"the project lead recorded the agreed rating for {settled:,} of the {len(diff):,} differences, "
                        f"and {len(diff) - settled:,} {'is' if len(diff) - settled == 1 else 'are'} still to agree.")
            out.append(txt)
    changed = [(d, it) for d, it in ratings if it["override"]]
    if changed:
        def named(xs):
            return "; ".join(f"{it['name'].lower()} for {name_text(d.get('name', ''))}" for d, it in xs)
        same_ = [x for x in changed if x[1]["override"]["same"]]
        other = [x for x in changed if not x[1]["override"]["same"]]
        if same_:
            out.append(f"The project lead also changed {util.plural(len(same_), 'rating')} that "
                       + ("both reviewers had given identically" if two else "the reviewer had given")
                       + f": {named(same_)}.")
        if other:
            out.append(f"The project lead {'also ' if not same_ else ''}recorded {util.plural(len(other), 'rating')} that "
                       f"neither reviewer had given: {named(other)}.")
        out.append("The reason is given in a footnote." if len(changed) == 1 else "The reasons are given in footnotes.")
    return out


def _robins_text(rated: list) -> list:
    """GRADE guidance 18's caution, with the reviewers' reason, for each outcome from non-randomised studies assessed
    with ROBINS-I that is not rated down for risk of bias or ends at High."""
    out = []
    for _, d, c in rated:
        ids = {w["id"] for w in c["cautions"]}
        fin = {it["f"]: it for it in c["items"]}
        name, tool = name_text(d.get("name", "")), _tool(c["start"])
        if "robins_not_down" in ids:
            why = str(fin["rob"]["final_x"] or "").strip()
            out.append(f"The evidence for {name} came from non-randomised studies assessed with {tool} but was not rated "
                       "down for risk of bias, although GRADE guidance 18 generally rates such evidence down by at least "
                       "two levels (Schünemann et al. 2019); "
                       + (f"the reviewers' reason, also given in a footnote: “{why.rstrip('.')}”." if why
                          else "the reason is still to be recorded."))
        elif "robins_high" in ids:
            why = str(fin[UP_WHY]["final"] or "").strip()
            out.append(f"The evidence for {name} came from non-randomised studies assessed with {tool} and ended at high "
                       "certainty after rating up, although GRADE guidance 18 generally rates such evidence down by at "
                       "least two levels for risk of bias (Schünemann et al. 2019); "
                       + (f"the reviewers' reason, also given in a footnote: “{why.rstrip('.')}”." if why
                          else "the reason is still to be recorded."))
    return out


def _per_1000_text(p: Project, t) -> list:
    """How test accuracy is presented: per 1,000 people tested, at which pre-test probability, from what."""
    parts = []
    for g in accuracy_groups(p):
        f = g["figures"]
        if not f:
            continue
        prev = " and of ".join(f"{_pct(x['p'])}% from {x['source']}" for x in f.get("prev") or [])
        if f.get("mode") == "direct":
            how = "as calculated by the authors"
        else:
            given = _join([w for w in ("sensitivity", "specificity") if f.get(w[:4])])
            src = figures_source(g["outcomes"])
            how = (f"worked out from a typical {given} and the range across the studies" if f.get("interval") == "range"
                   else f"worked out from the pooled {given}" if src == "pooled"
                   else f"worked out from the {given} of the single study" if src == "single"
                   else f"worked out from the {given} the review team entered (not pooled)")
        parts.append(f"for {mid_name(g['comparison'])}, at a pre-test probability of {prev}, {how}")
    if not parts:
        return []
    worked = any(g["figures"] and g["figures"].get("mode") != "direct" for g in accuracy_groups(p))
    return [f"Accuracy {t('was', 'is')} presented as the numbers of true and false results per 1,000 people tested "
            f"(Schünemann et al. 2020): " + "; ".join(parts) + "." + (" " + COMPLEMENT_NOTE if worked else "")]


def method_text(p: Project) -> str:
    """One paragraph for the methods section: how certainty was rated, from what was actually done (or, before any
    outcome is finished, what will be done)."""
    a, b = people(p)
    outs = outcomes(p)
    rated = [(oid, d, comparison(p, oid, d)) for oid, d in outs if both_done(p, oid, d)]
    done = bool(rated)
    t = (lambda past, future: past if done else future)            # noqa: E731
    who = [_name(p, x) for x in (a, b) if x]                        # full names, as the rest of the methods text
    lead = _name(p, p.lead_id())
    bits = []
    if len(who) == 2:
        bits.append(f"Two reviewers ({_join(who)}) {t('rated', 'will rate')} the certainty of the evidence for each main "
                    f"outcome independently, using the GRADE approach, and {t('compared', 'will compare')} their ratings "
                    f"only once both {t('had', 'have')} finished; where they {t('differed', 'differ')}, the agreed rating "
                    f"{t('was', 'is')} recorded by the project lead ({lead}).")
    elif who:
        bits.append(f"One reviewer ({who[0]}) {t('rated', 'will rate')} the certainty of the evidence for each main "
                    "outcome using the GRADE approach.")
    else:
        bits.append(f"The certainty of the evidence for each main outcome {t('was', 'will be')} rated using the GRADE approach.")
    bits += _agreement_text(rated, len(who) == 2)
    if outs:
        groups = []
        for imp in IMPORTANCE:
            names = [name_text(d.get("name", "")) for _, d in outs if d.get("importance") == imp]
            if names:
                noun = "outcome" if len(names) == 1 else "outcomes"
                what = f"{noun} of limited importance" if imp == "limited" else f"{imp} {noun}"
                verb = t("was" if len(names) == 1 else "were", "is" if len(names) == 1 else "are")
                groups.append(f"the {what}{' rated' if not groups else ''} {verb} {_join(names)}")
        bits.append(_cap("; ".join(groups)) + ".")
    effect = [d for _, d in outs if d.get("kind") != "accuracy"]
    accuracy = [d for _, d in outs if d.get("kind") == "accuracy"]
    used = {c["start"] for _, _, c in rated if c["start"]} if done else set()
    if effect or not outs:
        known = {"rct", "nrs_robins", "nrs"}
        starts = used & known if done else known
        if not starts and not used - known:          # only the starts used are described (exposures, prognosis below)
            starts = known
        parts = []
        if "rct" in starts:
            parts.append(f"evidence from randomised trials {t('started', 'starts')} at high certainty")
        if "nrs_robins" in starts:
            parts.append(f"evidence from non-randomised studies whose risk of bias was assessed with ROBINS-I "
                         f"{t('started', 'starts')} at high certainty (Schünemann et al. 2019)")
        if "nrs" in starts:
            parts.append(f"evidence from {'other ' if 'nrs_robins' in starts else ''}non-randomised studies "
                         f"{t('started', 'starts')} at low certainty")
        if parts:
            bits.append(_cap(f"for the effects of interventions, {'; '.join(parts)}."))
        down = (f"Certainty {t('could be', 'can be')} rated down by one or two levels for each of risk of bias, "
                "inconsistency, indirectness, imprecision and publication bias, and by three levels for extremely "
                "serious imprecision (Zeng et al. 2022)")
        if "nrs_robins" in starts or "nrs_robins_e" in used:
            down += (" or for extremely serious risk of bias in non-randomised studies starting at high (Schünemann et "
                     "al. 2019)")
        if starts & {"nrs", "nrs_robins"} or used & {"nrs_robins_e", "prog"}:
            down += (f"; evidence from non-randomised studies {t('could be', 'can be')} rated up for a large effect, "
                     "a dose-response gradient or plausible residual confounding that would reduce the effect")
        bits.append(down + ".")
        if "nrs_robins_e" in used:
            bits.append(f"Evidence from non-randomised studies of exposures whose risk of bias was assessed with ROBINS-E "
                        f"{t('started', 'starts')} at high certainty (Higgins et al. 2024).")
        if "prog" in used:
            bits.append(f"Evidence about prognostic factors from observational studies {t('started', 'starts')} at high "
                        "certainty (Foroutan et al. 2020).")
        unusual = [(d, c) for _, d, c in rated if d.get("kind") != "accuracy" and c["why_needed"]]
        if unusual:
            rct = any(c["start"] == "rct" for _, c in unusual)
            bits.append(f"The evidence for {_join([_low(d.get('name', '')) for d, _ in unusual])} was rated up although "
                        + ("it came from randomised trials or was also rated down" if rct else "it was also rated down")
                        + ", which GRADE advises only in unusual cases (" + ("Guyatt et al. 2011; " if rct else "")
                        + "Schünemann et al. 2019); "
                        + ("the reasons are given in footnotes." if len(unusual) > 1 else "the reason is given in a footnote."))
        bits += _robins_text(rated)
    if accuracy:
        one = len(accuracy) == 1
        bits.append(f"The accuracy outcome{'' if one else 's'}, {_join([name_text(d.get('name', '')) for d in accuracy])}, "
                    f"{t('was' if one else 'were', 'will be')} rated with the GRADE approach for test accuracy "
                    "(Schünemann et al. 2020), in which cross-sectional or cohort studies of patients with diagnostic "
                    "uncertainty, comparing the test with an appropriate reference standard, start at high certainty and "
                    "are rated down for risk of bias, indirectness, inconsistency, imprecision and publication bias"
                    + (f"; each accuracy outcome {t('was', 'is')} rated separately." if not one else "."))
        bits += _per_1000_text(p, t)
    narrative = [d for _, d in outs if d.get("synthesis") == "narrative"]
    if narrative:
        which = "any of the outcomes" if len(narrative) == len(outs) > 1 else _join([name_text(d.get("name", "")) for d in narrative])
        bits.append(f"Results {t('were', 'are')} not pooled for {which}, so certainty {t('was', 'is')} rated for the "
                    "narrative synthesis of the studies' results, as Murad et al. (2017) describe.")
    if outs:
        bits.append(f"Each rating down or up {t('was', 'is')} explained in a footnote.")
        words = []
        if effect:
            words.append(f"the effects {t('were', 'are')} described with GRADE's informative statements (Santesso et "
                         "al. 2020)")
        if accuracy:
            words.append(f"findings about accuracy {t('were', 'are')} described with the same words for each level of "
                         "certainty (high: stated plainly; moderate: probably; low: may; very low: very uncertain)")
        text = _cap("; ".join(words))
        kept = [_low(d.get("name", "")) for _, d, c in rated
                if c["statement_check"].get("kept") and not c["statement_check"].get("stronger")]
        strong = [(d, c) for _, d, c in rated if c["statement_check"].get("kept") and c["statement_check"].get("stronger")]
        open_ = [_low(d.get("name", "")) for _, d, c in rated
                 if c["level"] and not c["statement_check"]["ok"] and not c["statement_check"].get("kept")]
        bits.append(text + ".")
        # a kept statement is a sentence of its own (it may be an effect or an accuracy outcome)
        if kept:
            bits.append(f"The wording for {_join(kept)} differs from this standard wording, for the reason given in "
                        + ("footnotes." if len(kept) > 1 else "a footnote."))
        if strong:
            one = len(strong) == 1
            bits.append(f"The plain-language statement{'' if one else 's'} for "
                        + _join([f"{_low(d.get('name', ''))} ({LEVELS[c['level']].lower()} certainty, whose standard "
                                 f"wording is {STANDARD_WORD[c['level']]})" for d, c in strong])
                        + f" {t('was', 'is') if one else t('were', 'are')} worded more strongly than the standard wording "
                        "for that certainty; the review team's reason is given in "
                        + ("a footnote." if one else "footnotes."))
        if open_:
            bits.append(f"The wording for {_join(open_)} does not yet match "
                        + ("their certainty and is" if len(open_) > 1 else "its certainty and is") + " still to be agreed.")
    return " ".join(bits)


def readiness(p: Project, me: str) -> list:
    """What is missing for this step: [{'text', 'fix': route, 'fix_label', 'who': 'lead'|'me'|'other', 'blocks'}], some
    also with a 'key' and a 'short' (what the lead has to do, for others' "Waiting for the project lead to …").
    blocks is True when rating cannot start (or an outcome cannot be finished, or a finished outcome's statement is
    worded for another certainty) until it is put right; False for work that is simply still to do."""
    lead = p.lead_id()
    who_lead = "me" if me == lead else "lead"
    out = []
    a, b = people(p)
    team = [r for r in p.settings_ro().get("reviewers") or [] if isinstance(r, dict) and r.get("id")]
    if a and not b and len(team) <= 1:
        pass                # a review one person does alone: that person rates alone (the methods text says so)
    elif not a or not b:
        out.append({"text": "Two reviewers are not chosen yet. GRADE ratings are made by the same two people who judge "
                            "risk of bias, independently: choose them in Project set-up (Screening plan).",
                    "fix": "setup", "fix_label": "Open Project set-up", "who": who_lead, "blocks": not a})
    if not review.studies(p):
        return out          # nothing can be rated before a study is included (readiness shows the step as Later)
    outs = outcomes(p)
    if not outs:
        out.append({"text": "No outcomes are defined for rating certainty. The project lead adds each main outcome (for "
                            "example pain at 12 weeks) and chooses the studies that report it.",
                    "fix": "certainty", "fix_label": "Add the outcomes", "who": who_lead, "blocks": True})
        return out
    R = rows(p)
    waiting = {a: [], b: []}
    for oid, d in outs:
        keys, gone = studies_of(p, d)
        if not keys:
            out.append({"text": f"“{d.get('name')}” has no studies chosen. The lead chooses which included studies "
                                "contribute to it.", "fix": "certainty", "fix_label": "Choose its studies",
                        "who": who_lead, "blocks": True})
            continue
        if gone:
            out.append({"text": f"{len(gone)} of the studies chosen for “{d.get('name')}” {'is' if len(gone) == 1 else 'are'} "
                                "no longer included (a full-text decision changed). Check the outcome's studies.",
                        "fix": "certainty", "fix_label": "Check the studies", "who": who_lead, "blocks": False})
        for x in (a, b):
            if x and not _done(R, x, oid, d):
                waiting[x].append((oid, d.get("name", "")))
        if both_done(p, oid, d):
            c = comparison(p, oid, d)
            chk = c["statement_check"]
            wrong = bool(c["level"] and not chk["ok"] and not chk["kept"])
            if c["to_agree"]:
                names = [it["name"].lower() for it in c["items"] if it["differs"] and not it["agreed"]]
                out.append({"text": f"“{d.get('name')}”: {c['to_agree']} rating{'s differ' if c['to_agree'] != 1 else ' differs'} "
                                    f"between the two reviewers ({_join(names)}). The lead records the agreed rating.",
                            "fix": f"c/{oid}/compare", "fix_label": "Agree the ratings", "who": who_lead, "blocks": False})
            else:
                acc_missing = accuracy_view(p, d)["missing"] if d.get("kind") == "accuracy" else ""
                rest = [x for x in c["problems"] if not (wrong and x.startswith(chk["why"])) and x != acc_missing]
                if rest:
                    out.append({"text": f"“{d.get('name')}”: " + " ".join(rest), "fix": f"c/{oid}/compare",
                                "fix_label": "Open the outcome", "who": who_lead, "blocks": False})
            if wrong:
                out.append(_mismatch_item(d, oid, c, who_lead))
    for g in accuracy_groups(p):              # the figures for each test's summary of findings (GRADE guidance 21)
        for o in g["outcomes"]:
            if not o["result"]:
                out.append({"text": f"“{o['name']}”: say which results this accuracy outcome covers (sensitivity, "
                                    "specificity, or one kind of result such as false negatives), so that the summary of "
                                    "findings can show them per 1,000 people tested. The project lead chooses it when "
                                    "changing the outcome.", "fix": "certainty", "fix_label": "Change the outcome",
                            "who": who_lead, "blocks": False})
        miss = [o for o in g["outcomes"] if o["result"] and accuracy_view(p, dict(rows(p)["defs"][o["id"]]))["missing"]]
        if miss:
            out.append({"text": f"“{g['comparison'] or 'Test accuracy'}”: the summary of findings needs the pre-test "
                                "probability (prevalence) with its source, and the sensitivity and specificity (or the "
                                "numbers of true and false results per 1,000 people tested), for "
                                f"{_join(['“' + o['name'] + '”' for o in miss])}. The project lead enters them under "
                                "Accuracy figures.",
                        "fix": "certainty", "fix_label": "Enter the accuracy figures", "who": who_lead, "blocks": False})
    for x in (a, b):
        if x and waiting.get(x):
            names = [n for _, n in waiting[x]]
            out.append({"text": f"{'You have' if x == me else _name(p, x) + ' has'} not finished rating "
                                f"{util.plural(len(names), 'outcome')} ({_join(['“' + n + '”' for n in names])}).",
                        "fix": f"c/{waiting[x][0][0]}", "fix_label": "Rate the outcome" if x == me else "Open the outcome",
                        "who": "me" if x == me else ("lead" if x == lead else "other"), "blocks": False})
    return out


def _mismatch_item(d: dict, oid: str, c: dict, who_lead: str) -> dict:
    """Needed: an outcome whose agreed statement is worded for another certainty would reach the Summary of findings
    with the wrong words ("⊕⊕◯◯ Low — … probably reduces"). Named, with the fix, wherever readiness is shown."""
    chk, level = c["statement_check"], LEVELS[c["level"]].lower()
    says = ("says “uncertain” without “very”, which is not GRADE's wording for any level" if chk["word"] and not chk["said"]
            else f"says “{chk['word']}”, the wording for {LEVELS[chk['said']].lower()} certainty" if chk["word"]
            else "states the finding plainly, which readers take as high certainty")
    act = ("Change the agreed statement" if who_lead == "me" else "The project lead changes the agreed statement")
    return {"key": f"certainty:statement:{oid}", "blocks": True, "who": who_lead,
            "text": f"“{d.get('name')}”: the plain-language statement does not match the certainty. The agreed certainty "
                    f"is {level}, but the statement {says}, so the Summary of findings would mislead readers. {act} (for "
                    f"example “{chk['suggestion']}”) or record why it is kept, on the outcome's Compare and agree tab.",
            "short": f"put right the statement for “{d.get('name')}”",
            "fix": f"c/{oid}/compare", "fix_label": "Agree the statement"}


def _why_not(p: Project, me: str) -> str:
    if not p.reviewer(me):
        return "Choose who you are in this project first (top right)."
    if any(o["complete"] for o in evidence_profile(p, me)["outcomes"]):
        return ""
    return ("The evidence profile can be downloaded once both reviewers have finished at least one outcome and the lead "
            "has agreed everything in it: every difference, and a statement worded for its certainty (the outcome's "
            "Compare and agree tab says what is left).")


def _marks(n: int) -> str:
    """Footnote marks: a … z, then aa, ab …"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


# The evidence profile's columns, in the order of a GRADE evidence profile (GRADEpro): certainty assessment, number of
# participants, effect, certainty, importance; then the statement, the footnotes and ReviewDesk's checks
PROFILE_HEAD = (["Outcome", "Number of studies", "Study design", "Starting certainty"] + [n for _, n in DOMAINS]
                + [n for _, n in UPGRADES] + ["Number of participants", "Effect", "Certainty", "Importance",
                                              "Plain-language statement", "Footnotes", "Comparison", "Measure",
                                              "Kind of outcome", "Synthesis", "GRADE cautions", "Finished and agreed",
                                              "Still to resolve"])
PROFILE_WIDE = {"Effect": 44, "Plain-language statement": 44, "Footnotes": 52, "Comparison": 34, "Measure": 30,
                "GRADE cautions": 52, "Still to resolve": 52, "Outcome": 26, "Study design": 26}


def _col_widths(rows_: list, wide: dict, narrow: int = 22) -> list:
    """Column widths that keep every word whole: at least the longest word in the column (headers are bold, so a
    little more), otherwise wide enough for the longest line up to a cap, so longer text wraps at spaces."""
    out = []
    for c in range(max((len(r) for r in rows_), default=0)):
        cells = [str(r[c]) for r in rows_ if c < len(r) and r[c] not in (None, "")]
        head = str(rows_[0][c]) if rows_ and c < len(rows_[0]) else ""
        word = max([len(w) * 1.15 + 3 for w in head.split()] + [len(w) + 2 for x in cells[1:] for w in x.split()] + [10])
        line = max([len(ln) + 2 for x in cells[1:] for ln in x.split("\n")] + [len(head) * 1.15 + 3])
        out.append(math.ceil(max(word, min(line, wide.get(head, narrow))) * 10) / 10)    # rounded up: never narrower
    return out


def _tidy(data: bytes, widths: dict) -> bytes:
    """Finish a workbook made by util.xlsx: put in these column widths ({sheet number: [width, …]}; util.xlsx sizes
    columns by their longest cell), and name the wrapping cell style on every cell, because LibreOffice wraps a cell's
    text (and makes its row taller) only when the cell names its style; Excel does so either way."""
    import io
    import zipfile
    src, buf = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            body = src.read(info.filename)
            m = re.fullmatch(r"xl/worksheets/sheet(\d+)\.xml", info.filename)
            if m:
                xml = body.decode("utf-8")
                xml = re.sub(r'<c (?![^>]*\bs=")([^>/]*)>', r'<c \1 s="0">', xml)
                ws = widths.get(int(m.group(1)))
                if ws:
                    cols = "<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
                                              for i, w in enumerate(ws, 1)) + "</cols>"
                    xml = re.sub(r"<cols>.*?</cols>", lambda _: cols, xml, count=1, flags=re.S) if "<cols>" in xml else \
                        xml.replace("<sheetData>", cols + "<sheetData>", 1)
                body = xml.encode("utf-8")
            z.writestr(info, body)
    return buf.getvalue()


def accuracy_lines(o: dict) -> list:
    """For an accuracy outcome of the evidence profile: a line for each pre-test probability, with its numbers per
    1,000 people tested ('At a pre-test probability of 20%: true positives 170 (156 to 182); false negatives 30 …')."""
    acc = o.get("accuracy") or {}
    ivl = "the range across the studies" if acc.get("interval") == INTERVALS["range"] else "95% confidence intervals"
    out = []
    for k, pv in enumerate(acc.get("prevalence") or []):
        vals = [f"{rw['name'].lower()} {rw['values'][k]}" for rw in acc.get("rows") or [] if k < len(rw["values"])
                and rw["values"][k]]
        if vals:
            out.append(f"At a pre-test probability of {_pct(pv['p'])}% from {pv['source']}, per 1,000 people tested"
                       + (f", with {ivl} in brackets" if "(" in "".join(vals) else "") + ": " + "; ".join(vals) + ".")
    return out


def _effect_text(o: dict) -> str:
    return "\n".join([o.get("effect") or ""] + accuracy_lines(o)).strip()


def profile_xlsx(p: Project, me: str) -> bytes:
    prof = evidence_profile(p, me)
    rows_ = [list(PROFILE_HEAD)]
    notes = [["Outcome", "Mark", "Item", "Rating", "Explanation"]]
    for o in prof["outcomes"]:
        foot, n = [], [0]
        title = o.get("title") or name_title(o.get("name", ""))

        def mark(item: str, rating: str, text: str) -> str:
            m = _marks(n[0])
            n[0] += 1
            foot.append(f"[{m}] {item}: {text}")
            notes.append([title, m, item, rating, text])
            return f"[{m}]"

        def cell(it):
            if not it["rating"]:
                return "" if o["complete"] else "(not yet agreed)" if o["both_done"] else "(not finished by both)"
            out = it["rating"]
            if it["change"]:
                out += f" (−{-it['change']})" if it["change"] < 0 else f" (+{it['change']})"
            ms = []
            if it["explanation"]:
                ms.append(mark(it["name"], it["rating"], it["explanation"]))
            if it.get("override"):
                ms.append(mark(it["name"], it["rating"], it["override"]))
            if it.get("justification"):
                ms.append(mark(NAMES[UP_WHY], "", next(f["text"] for f in o["footnotes"] if f["item"] == NAMES[UP_WHY])))
            return out + (" [" + ", ".join(m.strip("[]") for m in ms) + "]" if ms else "")
        st = o.get("start_item") or {}
        start = o["start"] + (" " + mark(NAMES["start"], st.get("rating", ""), st["override"]) if st.get("override") else "")
        downs = [cell(it) for it in o["domains"]]
        ups, why_done = [], False
        for it in o["upgrades"]:                 # the reason why rating up is justified is given once, at the first
            if why_done:
                it = {**it, "justification": ""}
            why_done = why_done or bool(it.get("justification"))
            ups.append(cell(it))
        statement = o["statement"]
        chk = o["statement_check"]
        if chk.get("kept"):
            statement += " " + mark("Plain-language statement", "", next(f["text"] for f in o["footnotes"]
                                                                       if f["item"] == "Plain-language statement"))
        elif o["statement_mismatch"]:
            statement += f"\n[Does not match {o['certainty'].lower()} certainty: not yet resolved; see Still to resolve]"
        rows_.append([title, o["studies"], o["design"], start] + downs + ups
                     + [o["participants"], _effect_text(o), f"{o['certainty']} {o['symbols']}".strip(), o["importance"],
                        statement, "\n".join(foot), o["comparison"], o["measure"], o["kind"], o["synthesis_text"],
                        "\n".join(o["cautions"]), "yes" if o["complete"] else "no", "\n".join(o["problems"])])
    a, b = people(p)
    an, bn = _name(p, a), _name(p, b)
    both = [["Outcome", "Item", f"{an} rating", f"{an} explanation", f"{bn} rating", f"{bn} explanation", "Agreed",
             "Agreed explanation", "Why the lead changed it"]]
    for oid, d in outcomes(p):
        if not both_done(p, oid, d):
            continue
        c = comparison(p, oid, d)
        for it in c["items"]:
            if it["na"]:
                continue
            both.append([name_title(d.get("name", "")), it["name"], _label(it["f"], it["a"]["v"]), it["a"]["x"],
                         _label(it["f"], it["b"]["v"]), it["b"]["x"],
                         _label(it["f"], it["agreed"]["v"]) if it["agreed"] else "", (it["agreed"] or {}).get("x", ""),
                         (it["override"] or {}).get("why", "")])
        if c["statement_check"].get("kept"):
            both.append([name_title(d.get("name", "")), "Statement kept as worded", "", "", "", "",
                         f"Kept by {_name(p, c['statement_check'].get('by', ''))}", c["statement_check"]["reason"], ""])
    method = [["How certainty was rated"], [prof["method_text"]], [""],
              ["Certainty", "What it means (results pooled)", "What it means (results not pooled)"]] + [
        [f"{LEVELS[k]} {SYMBOLS[k]}", meaning(k), meaning(k, "effect", "narrative")] for k in (4, 3, 2, 1)] + [
        [""], ["Publication bias", "GRADE's words"]] + [[f"{DOWN[k]}" + (f" ({k})".replace("-", "−") if k else ""),
                                                        DOWN_PUBLICATION[k]] for k in (0, -1, -2)] + [
        [""], ["Sources of the wording"], [GRADE_REFS]]
    data = util.xlsx([("Evidence profile", rows_), ("Footnotes", notes), ("Both reviewers", both), ("Method", method)])
    return _tidy(data, {1: _col_widths(rows_, PROFILE_WIDE)})


# ================================================================================================ routes
def _same(me: str, body: dict) -> None:
    """The page must have been opened as the person ReviewDesk now thinks is at this computer (see server.same_person)."""
    if body.get("as") is not None and body.get("as") != me:
        raise ProjectError(f"This page was opened as {body.get('as') or 'nobody'}, but ReviewDesk is now set to "
                           f"{me or 'nobody'} (changed in another tab). Reload the page; nothing was saved.")


def h_list(p, me, q, body):
    a, b = people(p)
    outs = outcomes(p)
    return {"is_lead": p.is_lead(me), "me": me, "reviewers": [x for x in (a, b) if x],
            "outcomes": [summary(p, oid, d) for oid, d in outs],
            "studies": [{"key": s["key"], "label": s["label"], "reports": s["reports"], "title": s.get("title", "")}
                        for s in review.studies(p)],
            "terms": grade_terms(), "download": _why_not(p, me), "method_text": method_text(p),
            "removed": len(rows(p)["removed"]), "accuracy": _accuracy_list(p)}


def _accuracy_list(p: Project) -> list:
    """Each accuracy comparison for the list page: its outcomes, the figures entered and the numbers per 1,000."""
    out = []
    for g in accuracy_groups(p):
        defs = rows(p)["defs"]
        views = [accuracy_view(p, dict(defs[o["id"]])) for o in g["outcomes"]]
        out.append({**g, "numbers": [{**n, **{r: num_text(n.get(r)) for r in ROW_IDS}} for n in g["numbers"]],
                    "missing": [{"name": o["name"], "text": v["missing"]} for o, v in zip(g["outcomes"], views) if v["missing"]],
                    "sens": views[0]["sens"] if views else "", "spec": views[0]["spec"] if views else ""})
    return out


def h_outcome(p, me, q, body):
    oid = (q.get("o") or [""])[0]
    if oid not in rows(p)["defs"]:              # removed, or an old link: the page says so and offers the list
        return {"gone": "That outcome no longer exists: the project lead may have removed it. The list of outcomes "
                        "shows the ones there are now."}
    view = outcome_view(p, oid, me)
    ids = [o for o, _ in outcomes(p)]
    i = ids.index(oid)
    return {"view": view, "terms": grade_terms(), "is_lead": p.is_lead(me), "me": me,
            "prev": ids[i - 1] if i else "", "next": ids[i + 1] if i + 1 < len(ids) else "", "position": i + 1,
            "total": len(ids), "participants": participants(p, view["outcome"], view["studies"])}


def h_define(p, me, q, body):
    r = define(p, me, body.get("outcome") or {}, str(body.get("o") or ""), bool(body.get("deleted")))
    return {"o": r.get("o"), **h_list(p, me, q, {})}


def h_rate(p, me, q, body):
    _same(me, body)
    rate(p, me, str(body.get("o") or ""), str(body.get("f") or ""), body.get("v"), str(body.get("x") or ""))
    return {"ok": True}


def h_done(p, me, q, body):
    _same(me, body)
    set_done(p, me, str(body.get("o") or ""), bool(body.get("done")))
    return h_outcome(p, me, {"o": [str(body.get("o") or "")]}, {})


def h_agree(p, me, q, body):
    _same(me, body)
    agree(p, me, str(body.get("o") or ""), str(body.get("f") or ""), body.get("v"), str(body.get("x") or ""),
          str(body.get("why") or ""))
    return h_outcome(p, me, {"o": [str(body.get("o") or "")]}, {})


def h_accuracy(p, me, q, body):
    _same(me, body)
    set_accuracy(p, me, body.get("figures") or {})
    return h_list(p, me, q, {})


def h_keep_statement(p, me, q, body):
    _same(me, body)
    keep_statement(p, me, str(body.get("o") or ""), str(body.get("x") or ""))
    return h_outcome(p, me, {"o": [str(body.get("o") or "")]}, {})


API_GET = {"/api/certainty": h_list, "/api/certainty/outcome": h_outcome}
API_POST = {"/api/certainty/define": h_define, "/api/certainty/rate": h_rate, "/api/certainty/done": h_done,
            "/api/certainty/agree": h_agree, "/api/certainty/keep_statement": h_keep_statement,
            "/api/certainty/accuracy": h_accuracy}
DOWNLOADS = {"certainty": ("certainty of evidence (GRADE).xlsx", XLSX, profile_xlsx, _why_not)}
