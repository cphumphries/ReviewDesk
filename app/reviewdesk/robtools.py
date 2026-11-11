"""The risk-of-bias and critical-appraisal tools: the built-in library, and tools the project lead defines.

ReviewDesk records each domain's judgement (with the reviewer's supporting quotes) on the tool's own scale and checks
the overall judgement against the tool's own rule. It shows domain names and judgement options only, never a tool's
signalling questions: reviewers answer those in the official tool, which each tool links to. (Several tools are
published under licences that do not allow them to be copied into other software, and ReviewDesk is MIT-licensed.)

Every built-in tool below was checked against its official source in November 2026; the source is named beside it.

A tool the project lead defines (for the JBI checklists, CASP, the Newcastle-Ottawa Scale, MMAT, AMSTAR 2, ROBIS, the
EPOC criteria or anything else) holds only what the lead typed: its name, version and citation, a link, the labels of
its items, its answer scale and whether it has an overall judgement. ReviewDesk ships no wording from those tools.
"""
from __future__ import annotations

import re

# ROBINS-I V2 and ROBINS-E label their lowest confounding (and overall) judgement this way: uncontrolled confounding
# can never be ruled out in a non-randomised study.
QUALIFIED_LOW = "Low risk except for concerns about uncontrolled confounding"

# How severe each judgement is: the overall-judgement rules and the colours use it. -1 Not applicable and -0.5 No
# information sit outside the ordered scale.
SEVERITY = {"Low risk": 0, "Low": 0, "Low risk of bias": 0, "Low concern regarding applicability": 0, QUALIFIED_LOW: 0.5,
            "No information": -0.5, "Some concerns": 1, "Moderate risk": 1, "Unclear": 1, "Unclear risk": 1,
            "High risk": 2, "Serious risk": 2, "High": 2, "At risk of bias": 2, "Concerns regarding applicability": 2,
            "Critical risk": 3, "Very high risk": 3, "Not applicable": -1}

# The mark printed in each circle of the traffic-light figure (with the colour, never instead of it). Within any one
# tool's scale every judgement has its own mark; "?" is for Unclear and No information, as is usual.
MARKS = {"Low risk": "+", "Low": "+", "Low risk of bias": "+", "Low concern regarding applicability": "+", QUALIFIED_LOW: "+*",
         "Some concerns": "−", "Moderate risk": "−", "High risk": "×", "Serious risk": "×", "High": "×",
         "At risk of bias": "×", "Concerns regarding applicability": "×", "Critical risk": "!!", "Very high risk": "!!",
         "Unclear": "?", "Unclear risk": "?", "No information": "?", "Not applicable": "NA"}

OVERALL_CODES = ("O", "AO")          # the overall judgement, and (PROBAST+AI, QUADAS-2) the overall applicability

_RANDOMISED_RULE = ("{tool}: the overall risk of bias is low only if every domain is at low risk, and high if any domain is "
                    "at high risk (Sterne et al. BMJ 2019;366:l4898)")

# ================================================================================================ the built-in tools
# Each: name (as shown), short (in sentences), scale (the judgements every domain allows), domains [(code, name)],
# scales {code: [judgements]} where a domain has its own, overall {overall code: (the domains it summarises) or None for
# all the others}, rule (the tool's rule in words; "" when the tool has no overall judgement), more_severe (an example of
# an overall judgement more severe than every domain, which the tool allows with a reason), cite, version (what the
# methods text asks the authors to give), link (the official tool), for (the studies it is for), help (shown when the
# design is chosen), legacy [(code, name)]: domains the tool no longer has whose judgements are still shown and
# exported where they were recorded.
TOOLS = {
    # RoB 2 for individually randomised, parallel-group trials. Source: Sterne JAC et al. BMJ 2019;366:l4898, and
    # riskofbias.info (RoB 2.0 tool): five domains, judged Low risk / Some concerns / High risk; overall low only if
    # every domain is low, some concerns if any domain has some concerns and none is high, high if any domain is high
    # or several have some concerns that together lower confidence substantially.
    "rob2": {
        "name": "RoB 2 (individually randomised, parallel-group trials)", "short": "RoB 2",
        "scale": ["Low risk", "Some concerns", "High risk"],
        "domains": [("D1", "Randomisation process"), ("D2", "Deviations from intended interventions"),
                    ("D3", "Missing outcome data"), ("D4", "Measurement of the outcome"),
                    ("D5", "Selection of the reported result"), ("O", "Overall risk of bias")],
        # Before 1.3.0 this tool also covered cluster trials, with an optional D1b; judgements recorded there are kept.
        "legacy": [("D1b", "Identifying or recruiting participants (cluster trials only; recorded before ReviewDesk 1.3.0)")],
        "legacy_scale": {"D1b": ["Low risk", "Some concerns", "High risk", "Not applicable"]},
        "overall": {"O": None}, "rule": _RANDOMISED_RULE.format(tool="RoB 2"), "more_severe": "some concerns in several domains",
        "cite": "Sterne JAC, Savović J, Page MJ, et al. RoB 2: a revised tool for assessing risk of bias in randomised trials. "
                "BMJ 2019;366:l4898",
        "version": "the version of RoB 2 used (for individually randomised, parallel-group trials, the version of 22 August "
                   "2019 at riskofbias.info)",
        "link": ["RoB 2 tool and guidance (riskofbias.info)", "https://www.riskofbias.info/welcome/rob-2-0-tool"],
        "for": "randomised trials in which individual participants were randomised to parallel groups",
        "help": "For trials that randomise individual participants to parallel groups. Cluster-randomised and crossover "
                "trials have their own versions of RoB 2."},
    # RoB 2 for cluster-randomised trials. Source: Eldridge S, Campbell MK, Campbell MJ, et al. Revised Cochrane risk of
    # bias tool for randomized trials (RoB 2): additional considerations for cluster-randomized trials (RoB 2 CRT),
    # version of 18 March 2021, and its cribsheet (riskofbias.info): Domain 1a randomisation process; Domain 1b, bias
    # from when the participants in each cluster were identified and recruited (named in the cribsheet); Domains 2 to 5
    # as RoB 2; the same scale and overall rule. It is for parallel-group cluster trials; its guidance says it also
    # mostly fits stepped-wedge cluster trials (where an analysis that ignores time trends is judged under signalling
    # question 2.6). It does not cover cluster crossover trials. (Paraphrased; the tool is CC BY-NC-ND 4.0.)
    "rob2_cluster": {
        "name": "RoB 2 for cluster-randomised trials", "short": "RoB 2 for cluster-randomised trials",
        "scale": ["Low risk", "Some concerns", "High risk"],
        "domains": [("D1a", "Randomisation process"),
                    ("D1b", "Timing of identification or recruitment of participants"),
                    ("D2", "Deviations from intended interventions"), ("D3", "Missing outcome data"),
                    ("D4", "Measurement of the outcome"), ("D5", "Selection of the reported result"),
                    ("O", "Overall risk of bias")],
        "overall": {"O": None}, "rule": _RANDOMISED_RULE.format(tool="RoB 2 for cluster-randomised trials"),
        "more_severe": "some concerns in several domains",
        "cite": "Eldridge S, Campbell MK, Campbell MJ, et al. Revised Cochrane risk of bias tool for randomized trials "
                "(RoB 2): additional considerations for cluster-randomized trials (RoB 2 CRT). Version of 18 March 2021. "
                "riskofbias.info",
        "version": "the version of RoB 2 for cluster-randomised trials used (the version of 18 March 2021 at riskofbias.info)",
        "link": ["RoB 2 for cluster-randomised trials (riskofbias.info)",
                 "https://www.riskofbias.info/welcome/rob-2-0-tool/rob-2-for-cluster-randomized-trials"],
        "for": "cluster-randomised trials",
        "help": "For parallel-group trials that randomise groups of people (clusters), such as hospitals or emergency "
                "departments. D1b, bias arising from the timing of identification or recruitment of participants, is "
                "judged for every cluster trial. Stepped-wedge trials are included, but their analysis must allow for "
                "time trends (secular trends): the guidance asks reviewers to consider this under domain 2 (signalling "
                "question 2.6). Cluster crossover trials are not covered."},
    # RoB 2 for crossover trials. Source: Higgins JPT, Li T, Sterne J (eds). Revised Cochrane risk of bias tool for
    # randomized trials (RoB 2): additional considerations for crossover trials. Preliminary tool version, 18 March
    # 2021, and its cribsheet (riskofbias.info): Domain 1 randomisation process; Domain S bias arising from period and
    # carryover effects; Domains 2 to 5 as RoB 2; the same scale and overall rule.
    "rob2_crossover": {
        "name": "RoB 2 for crossover trials", "short": "RoB 2 for crossover trials",
        "scale": ["Low risk", "Some concerns", "High risk"],
        "domains": [("D1", "Randomisation process"), ("DS", "Period and carryover effects"),
                    ("D2", "Deviations from intended interventions"), ("D3", "Missing outcome data"),
                    ("D4", "Measurement of the outcome"), ("D5", "Selection of the reported result"),
                    ("O", "Overall risk of bias")],
        "overall": {"O": None}, "rule": _RANDOMISED_RULE.format(tool="RoB 2 for crossover trials"),
        "more_severe": "some concerns in several domains",
        "cite": "Higgins JPT, Li T, Sterne J (editors). Revised Cochrane risk of bias tool for randomized trials (RoB 2): "
                "additional considerations for crossover trials. Preliminary tool version, 18 March 2021. riskofbias.info",
        "version": "the version of RoB 2 for crossover trials used (the preliminary version of 18 March 2021 at riskofbias.info)",
        "link": ["RoB 2 for crossover trials (riskofbias.info)",
                 "https://www.riskofbias.info/welcome/rob-2-0-tool/rob-2-for-crossover-trials"],
        "for": "randomised crossover trials",
        "help": "For trials in which each participant receives the interventions in a random order. Domain S covers "
                "bias from period and carryover effects."},
    # ROBINS-I V2. Source: riskofbias.info (ROBINS-I V2), the draft of 20 November 2025 and its template: six domains
    # (the launch version of 22 November 2024 had a seventh, deviations from intended interventions, which that draft
    # dropped: ReviewDesk records the 2025 draft's six, and says so);
    # Domain 1 judged Low (except for concerns about uncontrolled confounding) / Moderate / Serious / Critical; Domains 2
    # to 6 Low / Moderate / Serious / Critical; overall Low risk of bias except for concerns about uncontrolled
    # confounding / Moderate / Serious / Critical, by default the most severe domain, more severe when problems in
    # several domains add up. No plain Low is offered for Domain 1 or overall, and there is no No information judgement.
    "robins2": {
        "name": "ROBINS-I V2 (non-randomised studies of interventions)", "short": "ROBINS-I V2",
        "scale": ["Low risk", "Moderate risk", "Serious risk", "Critical risk"],
        "scales": {"D1": [QUALIFIED_LOW, "Moderate risk", "Serious risk", "Critical risk"],
                   "O": [QUALIFIED_LOW, "Moderate risk", "Serious risk", "Critical risk"]},
        "domains": [("D1", "Confounding"), ("D2", "Classification of interventions"),
                    ("D3", "Selection of participants into the study (or into the analysis)"), ("D4", "Missing data"),
                    ("D5", "Measurement of the outcome"), ("D6", "Selection of the reported result"),
                    ("O", "Overall risk of bias")],
        "overall": {"O": None},
        "rule": "ROBINS-I V2: the overall risk of bias is at least as severe as the most severe domain",
        "more_severe": "moderate risk in several domains",
        "cite": "Sterne JAC, Hernán MA, Higgins JPT, et al. ROBINS-I V2: Risk Of Bias In Non-randomized Studies - of "
                "Interventions, version 2 (draft). riskofbias.info",
        "version": "the version of ROBINS-I V2 used (it is a draft that is still being revised; for example, the revised "
                   "draft posted at riskofbias.info on 20 November 2025)",
        "link": ["ROBINS-I V2 tool and guidance (riskofbias.info)", "https://www.riskofbias.info/welcome/robins-i-v2"],
        "for": "non-randomised studies of interventions with a comparison group",
        "help": "For non-randomised follow-up (cohort-type) studies that compare an intervention group with a comparison "
                "group. ROBINS-I V2 is still a draft: ReviewDesk records the six domains of the draft of 20 November "
                "2025, which has no domain for deviations from intended interventions (the launch version of 22 "
                "November 2024 had seven). Give the version you used.",
        "domains_from": "the six domains of the draft of 20 November 2025, which has no domain for deviations from "
                        "intended interventions; the launch version of 22 November 2024 had seven"},
    # ROBINS-I (2016). Source: Sterne JA et al. BMJ 2016;355:i4919, and the Cochrane Handbook chapter 25 (tables 25.3.a
    # to 25.3.c; section 25.5 for uncontrolled before-after studies and interrupted time series, where concurrent
    # events and time trends are judged under confounding): seven domains, judged Low / Moderate / Serious / Critical /
    # No information; overall low only if every domain is low, moderate if all are low or moderate, serious if any is
    # serious and none critical, critical if any is critical; No information only when information is lacking in a key
    # domain and nothing is serious or critical.
    "robins_ba": {
        "name": "ROBINS-I 2016 (non-randomised studies of interventions, including before-after and interrupted time "
                "series studies)", "short": "ROBINS-I 2016",
        # its earlier name, in plans recorded in the audit log under it (the data checks read them)
        "former_names": ("ROBINS-I (2016) (non-randomised studies of interventions, including before-after and interrupted "
                         "time series studies)",), "former_short": ("ROBINS-I (2016)",),
        "scale": ["Low risk", "Moderate risk", "Serious risk", "Critical risk", "No information"],
        "domains": [("D1", "Confounding (including concurrent changes and secular trends)"),
                    ("D2", "Selection of participants into the study"), ("D3", "Classification of interventions"),
                    ("D4", "Deviations from intended interventions"), ("D5", "Missing data"),
                    ("D6", "Measurement of outcomes"), ("D7", "Selection of the reported result"), ("O", "Overall risk of bias")],
        "overall": {"O": None},
        "rule": "ROBINS-I: the overall risk of bias is at least as severe as the most severe domain",
        "more_severe": "moderate risk in several domains",
        "cite": "Sterne JA, Hernán MA, Reeves BC, et al. ROBINS-I: a tool for assessing risk of bias in non-randomised "
                "studies of interventions. BMJ 2016;355:i4919; for uncontrolled before-after studies and interrupted time "
                "series, Cochrane Handbook for Systematic Reviews of Interventions, chapter 25, section 25.5",
        "version": "the version of the Cochrane Handbook used, where ROBINS-I was applied to before-after or interrupted "
                   "time series studies",
        "link": ["ROBINS-I 2016, and Cochrane Handbook chapter 25",
                 "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-25"],
        "for": "non-randomised studies of interventions, including uncontrolled before-after studies and interrupted time "
               "series",
        "help": "The published 2016 version of ROBINS-I, for non-randomised follow-up (cohort-type) studies of "
                "interventions. Section 25.5 of the Cochrane Handbook describes using it for uncontrolled before-after and "
                "interrupted time series studies: specify a target trial, and judge concurrent changes and secular trends "
                "under confounding."},
    # ROBINS-E. Source: Higgins JPT, Morgan RL, Rooney AA, et al. Environ Int 2024;186:108602, and the ROBINS-E template
    # (riskofbias.info, version of 24 March 2024): seven domains, judged Low / Some concerns / High / Very high risk; the
    # lowest judgement for confounding, and overall, is "Low risk of bias except for concerns about uncontrolled
    # confounding"; the overall judgement is by default that of the domain with the greatest risk of bias, and may be
    # more severe (for example several domains at high risk adding up to very high).
    "robins_e": {
        "name": "ROBINS-E (non-randomised studies of exposures)", "short": "ROBINS-E",
        "scale": ["Low risk", "Some concerns", "High risk", "Very high risk"],
        "scales": {"D1": [QUALIFIED_LOW, "Some concerns", "High risk", "Very high risk"],
                   "O": [QUALIFIED_LOW, "Some concerns", "High risk", "Very high risk"]},
        "domains": [("D1", "Confounding"), ("D2", "Measurement of the exposure"),
                    ("D3", "Selection of participants into the study (or into the analysis)"),
                    ("D4", "Post-exposure interventions"), ("D5", "Missing data"), ("D6", "Measurement of the outcome"),
                    ("D7", "Selection of the reported result"), ("O", "Overall risk of bias")],
        "overall": {"O": None},
        "rule": "ROBINS-E: the overall risk of bias is by default that of the domain with the greatest risk of bias, and "
                "never less severe than it",
        "more_severe": "high risk in several domains",
        "cite": "Higgins JPT, Morgan RL, Rooney AA, et al. A tool to assess risk of bias in non-randomized follow-up "
                "studies of exposure effects (ROBINS-E). Environ Int 2024;186:108602",
        "version": "the version of ROBINS-E used (for example, the version of 24 March 2024 at riskofbias.info)",
        "link": ["ROBINS-E tool and guidance (riskofbias.info)", "https://www.riskofbias.info/welcome/robins-e-tool"],
        "for": "non-randomised studies of the effects of exposures",
        "help": "For non-randomised (follow-up) studies of the effect of an exposure, such as an environmental, "
                "occupational or behavioural exposure, rather than an intervention."},
    # QUADAS-2. Source: Whiting PF, Rutjes AWS, Westwood ME, et al. Ann Intern Med 2011;155:529-36, and the QUADAS-2
    # background document (bristol.ac.uk/population-health-sciences/projects/quadas): four domains (patient selection,
    # index test, reference standard, flow and timing), each judged for risk of bias as low, high or unclear; the first
    # three also for concerns regarding applicability. Its rule for the overall judgement, in ReviewDesk's words: a
    # study low in every domain can be judged low overall (low risk of bias, or low concern about applicability); a study
    # high or unclear in any domain may be judged at risk of bias, or of concern for applicability (Whiting et al. 2011,
    # and the background document). No summary score.
    "quadas2": {
        "name": "QUADAS-2 (diagnostic test accuracy studies)", "short": "QUADAS-2",
        "scale": ["Low", "High", "Unclear"],
        "scales": {"O": ["Low risk of bias", "At risk of bias"],
                   "AO": ["Low concern regarding applicability", "Concerns regarding applicability"]},
        "domains": [("D1", "Patient selection"), ("D2", "Index test(s)"), ("D3", "Reference standard"),
                    ("D4", "Flow and timing"), ("O", "Overall risk of bias"),
                    ("A1", "Applicability: patient selection"), ("A2", "Applicability: index test(s)"),
                    ("A3", "Applicability: reference standard"), ("AO", "Overall applicability")],
        "overall": {"O": ("D1", "D2", "D3", "D4"), "AO": ("A1", "A2", "A3")}, "any_not_low": True,
        "rule": "QUADAS-2: the overall judgement is low only if every domain is low; a study judged high or unclear in any "
                "domain is recorded as at risk of bias (or as having concerns regarding applicability), as the tool's "
                "authors suggest",
        "more_severe": "a problem the domains do not capture",
        "cite": "Whiting PF, Rutjes AWS, Westwood ME, et al. QUADAS-2: a revised tool for the quality assessment of "
                "diagnostic accuracy studies. Ann Intern Med 2011;155:529-36",
        "version": "any review-specific guidance written for QUADAS-2's signalling questions (QUADAS-2 asks reviewers to "
                   "tailor it to the review)",
        "link": ["QUADAS-2 (University of Bristol)", "https://www.bristol.ac.uk/population-health-sciences/projects/quadas/quadas-2/"],
        "for": "diagnostic test accuracy studies",
        "help": "For studies of how accurately a test identifies a target condition against a reference standard. Each "
                "domain is judged for risk of bias, and the first three also for applicability."},
    # PROBAST+AI. Source: Moons KGM, Damen JAA, Kaul T, et al. PROBAST+AI. BMJ 2025;388:e082505 (probast.org): model
    # development (concerns for quality) and model evaluation (risk of bias) each have four domains (participants and
    # data sources, predictors, outcome, analysis), and applicability is rated for the first three; each judged low,
    # high or unclear, and the overall judgement follows the worst domain (low only if every domain is low).
    "probast_ai": {
        "name": "PROBAST+AI (prediction models, including AI models: development or validation)", "short": "PROBAST+AI",
        "scale": ["Low", "High", "Unclear"],
        "domains": [("D1", "Participants and data sources"), ("D2", "Predictors"), ("D3", "Outcome"), ("D4", "Analysis"),
                    ("O", "Overall risk of bias"), ("A1", "Applicability: participants and data sources"),
                    ("A2", "Applicability: predictors"), ("A3", "Applicability: outcome"), ("AO", "Overall applicability")],
        "overall": {"O": ("D1", "D2", "D3", "D4"), "AO": ("A1", "A2", "A3")},
        "rule": "PROBAST+AI: the overall judgement is low only if every domain is low, and high if any domain is high",
        "more_severe": "a model never validated externally",
        "cite": "Moons KGM, Damen JAA, Kaul T, et al. PROBAST+AI: an updated quality, risk of bias, and applicability "
                "assessment tool for prediction models using regression or artificial intelligence methods. BMJ "
                "2025;388:e082505",
        "version": "for studies of how accurately a tool classifies patients, why PROBAST+AI was used rather than a tool "
                   "for diagnostic accuracy studies such as QUADAS-2",
        "link": ["PROBAST+AI", "https://www.probast.org/"],
        "for": "studies that develop or evaluate prediction models, including AI models",
        "help": "For studies that develop or validate a prediction model, including an AI model, for diagnosis or "
                "prognosis. For a model's development, PROBAST+AI calls the first four judgements concerns for quality; "
                "for its evaluation, risk of bias. For a test's accuracy that is not a prediction model, QUADAS-2 is the "
                "usual tool."},
    # QUIPS. Source: Hayden JA, van der Windt DA, Cartwright JL, Côté P, Bombardier C. Ann Intern Med 2013;158:280-6, as
    # summarised by Grooten WJA et al. Diagn Progn Res 2019;3:5: six domains, each judged high, moderate or low risk of
    # bias. Grooten et al. (2019) note that QUIPS gives no rule for an overall risk-of-bias judgement for a study, and
    # summary scores are advised against: so ReviewDesk records no overall judgement for QUIPS.
    "quips": {
        "name": "QUIPS (prognostic factor studies)", "short": "QUIPS",
        "scale": ["Low risk", "Moderate risk", "High risk"],
        "domains": [("D1", "Study participation"), ("D2", "Study attrition"), ("D3", "Prognostic factor measurement"),
                    ("D4", "Outcome measurement"), ("D5", "Study confounding"), ("D6", "Statistical analysis and reporting")],
        "overall": {}, "rule": "", "more_severe": "",
        "cite": "Hayden JA, van der Windt DA, Cartwright JL, Côté P, Bombardier C. Assessing bias in studies of prognostic "
                "factors. Ann Intern Med 2013;158:280-6",
        "version": "how the domain judgements were used in the synthesis (QUIPS has no rule for an overall judgement)",
        "link": ["QUIPS (Hayden et al. 2013)", "https://www.acpjournals.org/doi/10.7326/0003-4819-158-4-201302190-00009"],
        "for": "studies of prognostic factors",
        "help": "For studies of whether a factor predicts an outcome (prognostic factor studies). QUIPS judges six domains "
                "and has no overall judgement: report the domains."},
}

# The study designs a review commonly includes, each with the built-in tool recommended for it (the lead can change it).
STANDARD_DESIGNS = [
    ("rct", "Randomised trials (individually randomised, parallel-group)", "rob2"),
    ("cluster_rct", "Cluster-randomised trials", "rob2_cluster"),
    ("crossover_rct", "Randomised crossover trials", "rob2_crossover"),
    ("nrsi", "Non-randomised studies of interventions, with a comparison group", "robins2"),
    ("before_after", "Uncontrolled before-after studies", "robins_ba"),
    ("its", "Interrupted time series", "robins_ba"),
    ("nrse", "Non-randomised studies of exposures", "robins_e"),
    ("dta", "Diagnostic test accuracy studies", "quadas2"),
    ("prediction", "Prediction model studies (developing or validating a model, including AI models)", "probast_ai"),
    ("prognostic_factor", "Prognostic factor studies", "quips"),
]
STANDARD_IDS = [d for d, _, _ in STANDARD_DESIGNS]

# ================================================================================================ lead-defined tools
# Answer scales a lead can pick for a tool's items (or its overall judgement), each in order from least to most concern,
# with how severe each answer is (for the colours, and for the rule that the overall is never less severe than the
# worst item). For the Yes/No scales, Yes means the item is met (a strength).
SCALE_PRESETS = {
    "ynu": {"label": "Yes / No / Unclear / Not applicable", "answers": ["Yes", "No", "Unclear", "Not applicable"],
            "severity": {"Yes": 0, "Unclear": 1, "No": 2, "Not applicable": -1}},
    "ypn": {"label": "Yes / Partial yes / No", "answers": ["Yes", "Partial yes", "No"],
            "severity": {"Yes": 0, "Partial yes": 1, "No": 2}},
    "lmh": {"label": "Low / Moderate / High risk", "answers": ["Low risk", "Moderate risk", "High risk"],
            "severity": {"Low risk": 0, "Moderate risk": 1, "High risk": 2}},
    "luh": {"label": "Low / High / Unclear risk", "answers": ["Low risk", "High risk", "Unclear risk"],
            "severity": {"Low risk": 0, "Unclear risk": 1, "High risk": 2}},
}
NOT_APPLICABLE_WORDS = {"not applicable", "n/a", "na"}
UNCLEAR_WORDS = {"unclear", "unclear risk", "can't tell", "cannot tell", "no information", "not reported", "unknown"}
CUSTOM_PREFIX = "x_"


def _clean(s, n: int = 200) -> str:
    return " ".join(str(s or "").split())[:n]


def _answers(sc: dict) -> list:
    """The answers of a scale as the lead set it: a preset's, or the lead's own list."""
    pre = SCALE_PRESETS.get(sc.get("preset") or "")
    if pre:
        return list(pre["answers"])
    return [_clean(a, 80) for a in sc.get("answers") or [] if _clean(a, 80)]


def _severity(sc: dict) -> dict:
    """{answer: severity} for a lead's scale: a preset's own; for the lead's own list in order (least concern first),
    0 to 2 (or 3 with four or more answers) spread along it; none at all for a list that is not in order."""
    pre = SCALE_PRESETS.get(sc.get("preset") or "")
    if pre:
        return dict(pre["severity"])
    ans = _answers(sc)
    out = {a: -1 for a in ans if a.lower() in NOT_APPLICABLE_WORDS}
    if not sc.get("ordered"):
        return out
    ranked = [a for a in ans if a not in out]
    n = len(ranked)
    top = 3 if n >= 4 else 2
    for k, a in enumerate(ranked):
        out[a] = round(top * k / (n - 1) * 2) / 2 if n > 1 else 0
    return out


def _ordered(sc: dict) -> bool:
    return bool(sc.get("preset") in SCALE_PRESETS or sc.get("ordered"))


def check_custom(d: dict) -> dict:
    """A lead-defined tool as it is stored, cleaned, or a ValueError saying what is missing."""
    name = _clean(d.get("name"), 120)
    if not name:
        raise ValueError("Give the tool a name (for example: JBI checklist for qualitative research).")
    version = _clean(d.get("version"), 120)
    cite = _clean(d.get("citation"), 600)
    if not version:
        raise ValueError(f"{name}: give the version used (for example: 2020 edition, or the date on the checklist).")
    if not cite:
        raise ValueError(f"{name}: give its citation, as the methods text will print it.")
    link = _clean(d.get("link"), 400)
    if link and not re.match(r"^https?://", link, re.I):
        raise ValueError(f"{name}: the link should start with https:// (copy it from the tool's web page).")
    items, seen, ids = [], set(), set()
    for it in d.get("items") or []:
        label = _clean(it.get("label") if isinstance(it, dict) else it, 200)
        if not label:
            continue
        if label.lower() in seen:
            raise ValueError(f"{name}: two items are both called “{label}”. Give each item its own label.")
        seen.add(label.lower())
        iid = str(it.get("id") or "") if isinstance(it, dict) else ""
        if not re.fullmatch(r"I\d{1,3}", iid) or iid in ids:
            iid = ""
        items.append({"id": iid, "label": label})
        ids.add(iid)
    if not items:
        raise ValueError(f"{name}: add its items or domains, one label each (only the labels, not the tool's questions).")
    if len(items) > 60:
        raise ValueError(f"{name}: a tool can have at most 60 items.")
    k = max([int(x["id"][1:]) for x in items if x["id"]] + [int(d.get("next") or 0)])
    for x in items:                                    # new items get the next free number; numbers are never reused
        if not x["id"]:
            k += 1
            x["id"] = f"I{k}"
    sc = _scale_in(d.get("scale") or {}, name, "items")
    ov = d.get("overall") or {}
    overall = {"use": bool(ov.get("use"))}
    if overall["use"]:
        overall["same"] = bool(ov.get("same", True))
        if not overall["same"]:
            overall.update(_scale_in(ov, name, "overall judgement"))
        # never less severe than the worst item: on by default for an overall judgement on the items' ordered answers
        overall["rule"] = bool(ov.get("rule", True)) and overall["same"] and _ordered(sc)
    return {"name": name, "version": version, "citation": cite, "link": link, "items": items, "scale": sc,
            "overall": overall, "next": k}


def _scale_in(s: dict, name: str, what: str) -> dict:
    pre = s.get("preset") or ""
    if pre in SCALE_PRESETS:
        return {"preset": pre}
    if pre not in ("own", ""):
        raise ValueError(f"{name}: choose the answer scale for its {what}.")
    ans = [_clean(a, 80) for a in s.get("answers") or [] if _clean(a, 80)]
    if len(ans) < 2:
        raise ValueError(f"{name}: give at least two answers for its {what}, one per line.")
    if len({a.lower() for a in ans}) != len(ans):
        raise ValueError(f"{name}: two answers for its {what} are the same.")
    if len(ans) > 12:
        raise ValueError(f"{name}: a scale can have at most 12 answers.")
    return {"preset": "own", "answers": ans, "ordered": bool(s.get("ordered"))}


def version_words(v, start: bool = False) -> str:
    """A lead-defined tool's version in a sentence: '2020 edition' -> 'version 2020 edition'. A version the lead typed
    with the word already ('Version 2, 2025') does not get it twice. start: at the start of a sentence ('Version ...').
    '' when no version is given."""
    v = re.sub(r"(?i)^version\b[\s:.,-]*", "", " ".join(str(v or "").split())).strip()
    return f"{'Version' if start else 'version'} {v}" if v else ""


def end_sentence(s) -> str:
    """Text with a full stop at its end, unless it ends with one already (a citation often does)."""
    s = str(s or "").rstrip()
    return s if not s or s[-1] in ".!?" else s + "."


def custom_runtime(key: str, d: dict) -> dict:
    """A lead-defined tool in the same shape as a built-in one."""
    sc = d.get("scale") or {}
    answers = _answers(sc)
    sev = _severity(sc)
    ov = d.get("overall") or {}
    domains = [(x["id"], x["label"]) for x in d.get("items") or []]
    scales, overall, rule = {}, {}, ""
    if ov.get("use"):
        domains.append(("O", "Overall judgement"))
        if ov.get("same", True):
            scales["O"] = answers
        else:
            scales["O"] = _answers(ov)
            for a, v in _severity(ov).items():
                sev.setdefault(a, v)
        if ov.get("rule"):
            overall = {"O": None}
            rule = "The project lead's rule for this tool: the overall judgement is never less severe than the worst item"
    return {"name": d.get("name") or "Tool defined by the project lead", "short": d.get("name") or "the lead's tool",
            "scale": answers, "scales": scales, "domains": domains, "overall": overall, "rule": rule,
            "more_severe": "several items of concern", "cite": d.get("citation", ""), "version": "",
            "version_given": d.get("version", ""),
            "link": [f"{d.get('name')} (the official tool)", d["link"]] if d.get("link") else None,
            "for": "", "help": "A tool defined by the project lead: use the official tool's own questions and guidance.",
            "custom": True, "severity": sev, "ordered": _ordered(sc), "key": key}


# ================================================================================================ using a tool
def scale_for(t: dict, domain: str) -> list:
    """The judgements a domain allows: the domain's own scale, or the tool's, with any extra judgements and Not
    applicable for an optional domain (kept for tools that have them)."""
    if domain in (t.get("scales") or {}):
        out = list(t["scales"][domain])
    elif domain in (t.get("legacy_scale") or {}):
        out = list(t["legacy_scale"][domain])
    else:
        out = t["scale"][:1] + t.get("extra", {}).get(domain, []) + t["scale"][1:]
    if domain in t.get("optional", []) and "Not applicable" not in out:
        out.append("Not applicable")
    return out


def sev(t: dict, j) -> float | None:
    """How severe a judgement is on this tool's scale (None when the scale has no order)."""
    if j is None or j == "":
        return None
    own = t.get("severity")
    if own is not None:
        return own.get(j)
    return SEVERITY.get(j)


def every_judgement(t: dict, codes: list | None = None) -> list:
    """Every judgement the tool's scales offer (for the domains given, or all), each once: from least to most severe,
    then those outside the order (No information, Not applicable); a scale with no order as the lead gave it."""
    out = []
    for code in codes if codes is not None else [c for c, _ in t["domains"]]:
        for j in scale_for(t, code):
            if j not in out:
                out.append(j)

    def rank(kj):
        k, j = kj
        s = sev(t, j)
        return (0, s, k) if s is not None and s >= 0 else (1, -(s or 0), k)
    return [j for _, j in sorted(enumerate(out), key=rank)]


def marks(t: dict) -> dict:
    """{judgement: mark} for the traffic-light figure, distinct within the tool's scales. A built-in tool uses the usual
    marks; a lead-defined tool's answers are marked with their initials ("?" for unclear, "NA" for not applicable)."""
    out, taken = {}, set()
    for j in every_judgement(t, [c for c, _ in t["domains"] + list(t.get("legacy") or [])]):
        if not t.get("custom"):
            out[j] = MARKS.get(j, j[:2])
            continue
        if j.lower() in NOT_APPLICABLE_WORDS:
            m = "NA"
        elif j.lower() in UNCLEAR_WORDS:
            m = "?"
        else:
            words = re.findall(r"[^\W_]+", j)
            m = "".join(w[0] for w in words[:3]).upper() if len(words) > 1 else (words[0][0].upper() if words else "#")
        base, k = m, 2
        while m in taken:
            m = f"{base}{k}"
            k += 1
        taken.add(m)
        out[j] = m
    return out


def overall_problems(t: dict, js: dict, support: dict | None = None, domains: list | None = None) -> list:
    """What is wrong with the overall judgement(s) given the domains' judgements js {domain: judgement}: an overall
    judgement less severe than the most severe domain breaks the tool's rule; one more severe than every domain is
    allowed (several moderate problems can add up) but needs its reason written in its support box. support is
    {domain: supporting text}; None checks only the rule (the agreed judgements). domains: the assessment's domains, when
    it has more than the tool's own (a domain kept from an earlier version of the tool). Returns [] when all is well."""
    groups, rule = t.get("overall") or {}, t.get("rule") or ""
    every = list(domains or t["domains"])
    names = dict(every)
    out = []
    for ov, parts in groups.items():
        oj = js.get(ov) or ""
        if not oj:
            continue
        parts = parts or tuple(c for c, _ in every if c not in OVERALL_CODES)
        judged = [(c, js.get(c)) for c in parts if js.get(c) and sev(t, js.get(c)) is not None and sev(t, js.get(c)) != -1]
        # ROBINS-I's No information sits outside its ordered scale; in a tool the lead defines, an answer of that name
        # is an ordinary answer on the lead's own ordered scale (ni is then None, and the ROBINS rule never applies)
        ni = None if t.get("custom") else "No information"
        rated = [(c, j) for c, j in judged if j != ni]
        no_info = [c for c, j in judged if j == ni]
        what = "overall applicability" if ov == "AO" else "overall judgement"
        so = sev(t, oj)
        if so is None:
            continue
        if oj == ni:
            if not no_info or any(sev(t, j) >= 2 for _, j in rated):
                out.append(f"'No information' as the {what} is for a study that lacks information in a key domain and is "
                           "not at serious or critical risk in any domain.")
            continue
        if not rated:
            continue
        wc, wj = max(rated, key=lambda cj: sev(t, cj[1]))
        if so < sev(t, wj) or (no_info and so == 0):
            worst = f"{wc} {names.get(wc, wc)}: {wj}" if so < sev(t, wj) else f"{no_info[0]} {names.get(no_info[0], no_info[0])}: No information"
            out.append(f"The {what} ({oj}) is less severe than {worst}. {rule}.")
        elif t.get("any_not_low") and so > sev(t, wj) and sev(t, wj) > 0:
            continue                         # QUADAS-2: any domain high or unclear allows at risk / concerns
        elif support is not None and so > sev(t, wj) and not str(support.get(ov) or "").strip():
            out.append(f"The {what} ({oj}) is more severe than every domain. The tool allows this (for example, "
                       f"{t.get('more_severe') or 'several problems adding up'}), but say why in the {what}'s support box.")
    return out


def public(t: dict) -> dict:
    """What a page needs to know about a tool."""
    return {"name": t["name"], "short": t.get("short", t["name"]), "domains": [list(x) for x in t["domains"]],
            "scale": t["scale"], "scales": {c: scale_for(t, c) for c, _ in t["domains"]}, "rule": t.get("rule", ""),
            "overall": list((t.get("overall") or {}).keys()), "has_overall": any(c in OVERALL_CODES for c, _ in t["domains"]),
            "link": t.get("link"), "help": t.get("help", ""), "cite": t.get("cite", ""), "for": t.get("for", ""),
            "custom": bool(t.get("custom")), "version": t.get("version_given", ""), "marks": marks(t),
            "severity": {j: sev(t, j) for j in every_judgement(t)}}
