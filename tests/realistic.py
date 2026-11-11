"""Realistic database exports for the reference review, with the truth about every study in them.

The review is invented, and so are its people: *AI in emergency department triage: prospective clinical
evaluations* (Nadine Hayes, NH, reviewer; Dr Colin Hawthorne, CH, project lead; see docs/dev/DESIGN.md). Five databases searched within one
week (MEDLINE and Embase via Ovid, CINAHL via EBSCOhost, Scopus, CENTRAL), an update search re-run later, and other
methods (backward and forward citation searching, exported from PubMed; a hand-search of NEJM AI).

``Review(seed, scale)`` makes the *works* (every distinct report: an article, a conference abstract, a preprint, an
erratum, a letter ...), decides which database finds each one, and writes each database's export the way that
database writes it:

=========  ===========================  ===================================================================
MEDLINE    Ovid RIS                     UTF-8 with a byte-order mark, CRLF line ends, DOIs as dx.doi.org links
Embase     Ovid RIS                     Windows-1252 (macrons lost), CRLF, abstracts wrapped over several lines,
                                        conference abstracts, errata, preprints, translated titles in [brackets]
CINAHL     EBSCOhost RIS                UTF-8 without a byte-order mark, LF, &amp; in titles, Title Case
Scopus     CSV (or RIS)                 UTF-8 with a byte-order mark, the full Scopus header, "[No abstract
                                        available]", abstracts with line breaks inside quotes, <inf> markup
CENTRAL    Cochrane Library plain text  "Record #n of N" blocks, trial-registry records
PubMed     .nbib (MEDLINE format)       lines wrapped with six-space indents (citation searching)
NEJM AI    RIS from the journal's site  (hand-search)
=========  ===========================  ===================================================================

The same work found in two databases is a true duplicate; anything else is a different work, however alike
(a conference abstract and the article that followed, an erratum with the article's title, a preprint). Every work
carries the truth a careful reviewer would reach: whether it should go forward at title and abstract, and at full
text whether it is included (with the study it reports), excluded (with the reason), awaiting classification or not
retrieved, and why. ``Review.work_at(export, k)`` names the work behind the k-th record of an export, which is how a
test maps ReviewDesk's records back to the truth (each record keeps the import and position it was first read from).

Two scales: ``"normal"`` (about 230 unique records, about 400 in the five main exports) and ``"full"`` (about 3,340
after de-duplication, as in the real searches). Everything is seeded: the same seed gives the same bytes.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import random
import re
import unicodedata
import zlib
from difflib import SequenceMatcher

DATABASES = ("MEDLINE", "Embase", "CINAHL", "Scopus", "CENTRAL")
PLATFORM = {"MEDLINE": "Ovid", "Embase": "Ovid", "CINAHL": "EBSCOhost", "Scopus": "Elsevier", "CENTRAL": "Cochrane Library",
            "Citation searching": "PubMed", "NEJM AI": "Journal website"}
FT_REASONS = ["Wrong population", "Wrong intervention", "Wrong comparator", "Wrong study design", "Wrong outcomes",
              "Wrong publication type", "Not in English"]
QUESTION = ("In patients presenting to emergency departments, how do artificial intelligence triage tools evaluated "
            "prospectively in clinical practice affect time to triage, under-triage and over-triage, and how accurate are "
            "they against a reference standard?")
CRITERIA = {
    "include": ["Patients of any age presenting to an emergency department and triaged there",
                "An artificial intelligence or machine learning tool used to support or perform triage",
                "Evaluated prospectively in clinical practice (randomised, non-randomised with concurrent controls, "
                "uncontrolled before-after, interrupted time series, or prospective accuracy study)",
                "Reports time to triage, under-triage or over-triage, or accuracy against a reference standard"],
    "exclude": ["Retrospective development or validation only", "Simulated patients, vignettes or case scenarios only",
                "Prehospital, telephone or primary care triage", "Rule-based tools with no learning component",
                "Reviews, editorials, letters and protocols without results"],
    "rules": ["Exclude only when a criterion is clearly unmet; anything either reviewer includes goes to full text.",
              "Conference abstracts and preprints go forward when the study could be eligible."]}
REGISTERS = [{"name": "ClinicalTrials.gov", "records": 11}, {"name": "ANZCTR", "records": 4}]

SCALES = {
    "normal": {"main": 230, "eligible": 10, "ft_exclude": 22, "awaiting": 2, "not_retrieved": 1, "update_new": 24,
               "update_eligible": 1, "update_overlap": 0.2, "citation_new": 10, "citation_eligible": 2, "citation_dups": 9,
               "nejm_new": 4, "nejm_eligible": 1, "part_size": 0},
    "full": {"main": 3340, "eligible": 20, "ft_exclude": 100, "awaiting": 4, "not_retrieved": 3, "update_new": 260,
             "update_eligible": 2, "update_overlap": 1.0, "citation_new": 40, "citation_eligible": 3, "citation_dups": 40,
             "nejm_new": 8, "nejm_eligible": 1, "part_size": 1000},
}
# how likely each database is to find a work (journal articles; other kinds are adjusted in _place)
FIND = {"MEDLINE": 0.36, "Embase": 0.55, "CINAHL": 0.18, "Scopus": 0.42, "CENTRAL": 0.05}
DOI_KEPT = {"MEDLINE": 0.9, "Embase": 0.8, "CINAHL": 0.7, "Scopus": 0.97, "CENTRAL": 0.6, "Citation searching": 0.92,
            "NEJM AI": 1.0}


def tkey(t: str) -> str:
    """The same normalisation ReviewDesk uses to compare titles (dedup.tkey), written out again here."""
    t = unicodedata.normalize("NFKD", t or "").lower()
    t = re.sub(r"^\[|\]\.?$", "", t.strip())
    return re.sub(r"[^a-z0-9]", "", t)


def ascii_fold(s: str) -> str:
    """What Windows-1252 keeps of a text: letters with macrons lose them (é and ü survive)."""
    out = []
    for ch in s:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            try:
                base.encode("cp1252")
            except UnicodeEncodeError:
                base = ""
            out.append(base or "?")
    return "".join(out)


# ================================================================================================ the works
class Work:
    """One distinct report, with the truth about it."""

    def __init__(self, **kw):
        self.wid = ""
        self.kind = "article"          # article | conference | preprint | erratum | comment | protocol | review |
        #                                editorial | registry | secondary
        self.study = ""                # the study it reports (for eligible studies), e.g. "S03"
        self.study_label = ""          # e.g. "Ngata 2025": the name reviewers give the study at full text
        self.design = ""
        self.title = ""
        self.abstract = ""
        self.authors: list = []        # [(surname, given names)]
        self.year = 2024
        self.month = 1
        self.journal = ""
        self.abbr = ""
        self.volume = self.issue = self.pages = ""
        self.doi = ""
        self.pmid = ""
        self.embase_an = self.cinahl_an = self.eid = self.central_id = ""
        self.language = "English"
        self.keywords: list = []
        self.ta = "exclude"            # include | exclude: what a careful reviewer decides at title and abstract
        self.ft = ""                   # include | exclude | awaiting | not_retrieved ('' when excluded at title/abstract)
        self.ft_reason = ""            # one of FT_REASONS when ft == exclude
        self.why = ""                  # the truth in words
        self.hard = False              # eligible, but the abstract does not say enough (a reviewer would say Maybe)
        self.ai_miss = False           # the AI stand-in (with truth_decider) excludes it although it is eligible
        self.set = "main"              # main | update | other
        self.found: dict = {}          # {export label: True} the searches that find it
        self.variants: dict = {}       # {database: {field: value}} how a database writes it differently
        self.__dict__.update(kw)

    @property
    def first_author(self) -> str:
        return self.authors[0][0] if self.authors else ""

    @property
    def eligible(self) -> bool:
        return self.ft == "include"

    def __repr__(self):
        return f"<Work {self.wid} {self.kind} {self.ta}/{self.ft or '-'} {self.title[:50]!r}>"


SURNAMES = ["Fraser", "Smith", "Ngata", "Patel", "Nguyen", "O'Connor", "García-López", "Müller", "Kowalski", "Chen", "Kim",
            "Park", "Wong", "Singh", "Tanaka", "Rāwiri", "Pōtae", "Henare", "Williams", "Brown", "Taylor", "Wilson", "Murray",
            "MacDonald", "Rossi", "Hansen", "Johansson", "Okafor", "Mensah", "Haddad", "Cohen", "Ahmed", "Hussain", "Lee",
            "Zhang", "Wang", "Li", "Liu", "Sato", "Dubois", "Martin", "Lefebvre", "Novak", "Horvat", "Silva", "Santos", "Costa",
            "Ferreira", "Walker", "Robinson", "Clarke", "Hughes", "Evans", "Thomas", "Roberts", "Campbell", "Stewart",
            "Morrison", "Paterson", "Reid", "Gray", "Kerr", "Tūhoe-Smith", "Mahuta", "Kereama", "Ishikawa", "Yamamoto",
            "Andersson", "Nielsen", "Bakker", "Jansen", "Visser", "Moreau", "Laurent", "Bianchi", "Romano", "Kaur", "Gupta",
            "Sharma", "Mohammed", "Ali", "Khan", "Ibrahim", "Adeyemi", "Osei", "Banda", "Kariuki", "Petrov", "Ivanova",
            "Schmidt", "Schneider", "Fischer", "Weber", "Becker", "Hoffmann", "Ó Briain", "Ní Fhaoláin", "Lindqvist"]
MULTIWORD = ["Te Whāiti", "van der Berg", "De Luca", "Mac an tSaoi"]       # never first authors, except where planned
GIVEN = ["Kirsty", "John", "Aroha", "Mere", "Tama", "Priya", "Linh", "Siobhan", "María José", "Jürgen", "Agnieszka", "Wei",
         "Ji-woo", "Hana", "Arjun", "Yuki", "Wiremu", "Hēmi", "Anahera", "Emma", "Oliver", "Sophie", "James", "Isla", "Lucas",
         "Chloé", "Mateus", "Ngozi", "Kwame", "Omar", "Fatima", "David", "Sarah", "Ruth", "Andrew", "Fiona", "Euan", "Morag",
         "Catriona", "Rangi", "Kiri", "Moana", "Søren", "Zoë", "Seán", "Aoife", "Mei", "Haruto", "Leilani", "Tavita"]
JOURNALS = [  # (name, Ovid/PubMed abbreviation, DOI maker)
    ("Emergency Medicine Journal", "Emerg Med J", lambda r, y: f"10.1136/emermed-{y - 1}-{r.randint(210000, 216000)}"),
    ("Annals of Emergency Medicine", "Ann Emerg Med", lambda r, y: f"10.1016/j.annemergmed.{y}.{r.randint(1, 12):02d}.{r.randint(1, 999):03d}"),
    ("Academic Emergency Medicine", "Acad Emerg Med", lambda r, y: f"10.1111/acem.{r.randint(14000, 15999)}"),
    ("Emergency Medicine Australasia", "Emerg Med Australas", lambda r, y: f"10.1111/1742-6723.{r.randint(14000, 14999)}"),
    ("JAMA Network Open", "JAMA Netw Open", lambda r, y: f"10.1001/jamanetworkopen.{y}.{r.randint(10000, 59999)}"),
    ("npj Digital Medicine", "NPJ Digit Med", lambda r, y: f"10.1038/s41746-0{y - 2000}-{r.randint(1000, 1999):05d}-{r.randint(0, 9)}"),
    ("Journal of Medical Internet Research", "J Med Internet Res", lambda r, y: f"10.2196/{r.randint(40000, 69999)}"),
    ("BMJ Open", "BMJ Open", lambda r, y: f"10.1136/bmjopen-{y - 1}-{r.randint(70000, 99999):06d}"),
    ("Scandinavian Journal of Trauma, Resuscitation and Emergency Medicine", "Scand J Trauma Resusc Emerg Med",
     lambda r, y: f"10.1186/s13049-0{y - 2000}-{r.randint(1000, 1299):05d}-{r.randint(0, 9)}"),
    ("American Journal of Emergency Medicine", "Am J Emerg Med", lambda r, y: f"10.1016/j.ajem.{y}.{r.randint(1, 12):02d}.{r.randint(1, 99):03d}"),
    ("International Emergency Nursing", "Int Emerg Nurs", lambda r, y: f"10.1016/j.ienj.{y}.{r.randint(101000, 101999)}"),
    ("Journal of Emergency Nursing", "J Emerg Nurs", lambda r, y: f"10.1016/j.jen.{y}.{r.randint(1, 12):02d}.{r.randint(1, 99):03d}"),
    ("The Lancet Digital Health", "Lancet Digit Health", lambda r, y: f"10.1016/S2589-7500({y - 2000}){r.randint(100, 299):05d}-{r.randint(0, 9)}"),
    ("BMC Emergency Medicine", "BMC Emerg Med", lambda r, y: f"10.1186/s12873-0{y - 2000}-{r.randint(1000, 1199):05d}-{r.randint(0, 9)}"),
    ("Western Journal of Emergency Medicine", "West J Emerg Med", lambda r, y: f"10.5811/westjem.{r.randint(18000, 21999)}"),
    ("CJEM", "CJEM", lambda r, y: f"10.1007/s43678-0{y - 2000}-{r.randint(500, 899):05d}-{r.randint(0, 9)}"),
    ("New Zealand Medical Journal", "N Z Med J", None),
    ("Emergency Medicine International", "Emerg Med Int", lambda r, y: f"10.1155/{y}/{r.randint(1000000, 9999999)}"),
    ("Journal of the American College of Emergency Physicians Open", "J Am Coll Emerg Physicians Open",
     lambda r, y: f"10.1002/emp2.{r.randint(12000, 13999)}"),
    ("Health Informatics Journal", "Health Informatics J", lambda r, y: f"10.1177/146045822{r.randint(1000000, 9999999)}"),
]
TOOLS = ["a machine learning triage algorithm", "an AI-based electronic triage system", "a gradient-boosting triage decision-support tool",
         "a deep learning acuity prediction model", "a large language model (GPT-4o) triage assistant",
         "a natural language processing triage assistant", "a random forest acuity prediction tool",
         "an artificial intelligence triage support system", "a neural network e-triage tool", "an XGBoost-based triage aid"]
SCALES_T = ["the Emergency Severity Index", "the Manchester Triage System", "the Canadian Triage and Acuity Scale",
            "the Australasian Triage Scale", "the Korean Triage and Acuity Scale", "the South African Triage Scale"]
SETTINGS = [("an adult emergency department in Auckland, Aotearoa New Zealand", "New Zealand"),
            ("the emergency department of Whangārei Hospital", "New Zealand"),
            ("three emergency departments in Scotland", "United Kingdom"), ("a paediatric emergency department in Seoul", "South Korea"),
            ("two academic emergency departments in Boston", "United States"), ("a regional emergency department in Queensland", "Australia"),
            ("a tertiary emergency department in Toronto", "Canada"), ("a university hospital emergency department in Rotterdam", "Netherlands"),
            ("an emergency department in Taipei", "Taiwan"), ("a public hospital emergency department in São Paulo", "Brazil"),
            ("a district general hospital in Wales", "United Kingdom"), ("a level 1 trauma centre in Copenhagen", "Denmark"),
            ("four emergency departments in Ontario", "Canada"), ("an urban emergency department in Cape Town", "South Africa"),
            ("the emergency department of a Māori-led hospital network in Waikato", "New Zealand"),
            ("a children's emergency department in Melbourne", "Australia"), ("two emergency departments in Lyon", "France"),
            ("an emergency department in Nairobi", "Kenya"), ("five emergency departments in Stockholm", "Sweden"),
            ("a county hospital emergency department in Michigan", "United States")]
DESIGNS = {  # eligible designs: (design, title pattern, its design in the review's risk-of-bias plan)
    "cluster": ("Cluster randomised trial", "{Tool} versus nurse triage with {scale} in {setting}: a stepped-wedge cluster randomised trial", "cluster_rct"),
    "rct": ("Randomised trial", "Effect of {tool} on time to triage in {setting}: a randomised controlled trial", "rct"),
    "nrsi": ("Non-randomised study with a comparison group", "Prospective controlled evaluation of {tool} at {setting} with concurrent control sites", "nrsi"),
    "ba": ("Before-after study", "Before-and-after evaluation of the introduction of {tool} in {setting}", "before_after"),
    "its": ("Interrupted time series", "Under-triage and over-triage after introducing {tool} in {setting}: an interrupted time series", "its"),
    "acc": ("Cohort study", "Prospective accuracy of {tool} against expert triage in {setting}", "prediction"),
}
TA_EXCLUDE = [  # (share, kind, title patterns, why): titles start in many ways, as real ones do
    (0.25, "article", ["Development and validation of {tool2} to predict {outcome} at emergency department triage{sub_retro}",
                       "{Tool2} for {outcome} using triage data from {setting}{sub_retro}",
                       "Predicting {outcome} from triage notes with {tool2}{sub_retro}",
                       "{Outcome} after emergency department triage: external validation of {tool2} in {region}",
                       "Can {tool2} identify {outcome} at the front door? Retrospective analysis of {n:,} {visits}",
                       "Explainable prediction of {outcome} at triage in {region} using {tool2}{sub_retro}",
                       "Triage-time prediction of {outcome} in {setting} with {tool2} and {extra_data}"],
     "retrospective model development or validation only"),
    (0.08, "article", ["Can {llm} triage? Accuracy on {n2} standardised clinical vignettes of {outcome}",
                       "Simulated patients and {llm}: {scale} assignment for {complaint}{sub_vig}",
                       "{LLM} versus emergency physicians on written case scenarios of {complaint}",
                       "Benchmarking {llm} for acuity assignment with {n2} {vignette_kind}",
                       "{Complaint} vignettes and {llm}{sub_vig}", "Do {llm} and triage nurses agree on {complaint}? {Vignette_kind} from {region}",
                       "Prompting strategies for {llm} on {vignette_kind} of {outcome}"],
     "vignettes or simulated patients only"),
    (0.08, "article", ["{Tool} for ambulance dispatch prioritisation in {region}{sub_pros}",
                       "{Pc_kind} in {region} prioritised by {tool}{sub_pros}", "Paramedic triage of {complaint} with {tool2} in {region}",
                       "After-hours telephone advice for {group} in {region}: does {tool} help?",
                       "AI-supported triage of {pc_kind} in {region}{sub_pros}",
                       "Prehospital recognition of {outcome} by paramedics using {tool2} in {region}"],
     "not emergency department triage"),
    (0.12, "article", ["Deep learning for {other_task} in {region}", "{Other_task}: a prospective multicentre study of {tool2}",
                       "Automated {other_task} with {tool2}{sub_pros}", "Real-world performance of {tool2} for {other_task} in {setting2}"],
     "artificial intelligence, but not triage"),
    (0.33, "article", ["{ed_topic} in {setting}{sub_ed}", "{ed_topic}{sub_ed}", "Association between {ed_topic_l} and {outcome} in {region}",
                       "{ed_topic} and {ed_topic2_l} in {region}", "Changes in {ed_topic_l} over {years} years at {setting}",
                       "What predicts {outcome} among {group}? {Design2} from {region}"],
     "not about artificial intelligence triage"),
    (0.06, "review", ["Artificial intelligence in emergency department triage: a {review_kind} of {topic_r}",
                      "{Tool2} for triage of {group}: a {review_kind}", "Machine learning and the future of emergency triage: a {review_kind}",
                      "{Review_kind} of {topic_r} on {tool2} in emergency care"], "a review"),
    (0.04, "editorial", ["Who triages the triage algorithm in {region}?", "Artificial intelligence triage for {group}: hype, hope or harm?",
                         "{LLM} at triage: a cautionary note on {topic_r}", "{Complaint}, {group} and the machine: an editorial view",
                         "Regulating {tool2} in {region}'s emergency departments", "After the pilot: {topic_r} and {tool}",
                         "Equity first: {group} and automated triage", "Ten questions to ask before buying {tool}"], "an editorial"),
    (0.02, "protocol", ["Protocol for a stepped-wedge cluster randomised trial of {tool} in {setting}",
                        "Evaluating {tool} for {group} in {setting}: protocol for a pragmatic trial"], "a protocol without results"),
    (0.02, "article", ["Inter-rater reliability of {scale} among newly qualified triage nurses in {region}",
                       "A rule-based electronic triage checklist for {outcome} in {setting}"], "a triage tool with no artificial intelligence"),
]
SUBS = {"sub_retro": [": a retrospective cohort study", ": a retrospective multicentre study", " (retrospective analysis)", "",
                      ": development and internal validation", ": a registry-based study", " using electronic health records"],
        "sub_vig": [": a vignette study", ": an evaluation with written cases", " – a simulation study", ": accuracy on standardised cases", ""],
        "sub_pros": [": a prospective study", "", ": a pilot study", ": a multicentre evaluation", " – early experience", ": results of a service evaluation"],
        "sub_ed": ["", ": a retrospective cohort study", ": a cross-sectional survey", ": a qualitative study", ": a quality improvement project",
                   ": a mixed-methods study", ": a multicentre audit", ": a prospective observational study"]}
VISITS = ["visits", "attendances", "presentations", "encounters"]
EXTRA_DATA = ["vital signs", "free-text triage notes", "laboratory results", "prior admissions", "the presenting complaint"]
COMPLAINTS = ["chest pain", "abdominal pain", "shortness of breath", "paediatric fever", "headache", "syncope", "trauma"]
VIGNETTE_KINDS = ["published vignettes", "case scenarios from a nursing textbook", "synthetic patients", "simulated triage encounters"]
PC_KINDS = ["primary care consultations", "out-of-hours GP calls", "urgent care walk-in visits", "virtual emergency consultations",
            "telehealth appointments"]
GROUPS = ["older people", "children", "Māori and Pacific patients", "people with mental illness", "rural patients", "pregnant women",
          "frequent attenders", "people experiencing homelessness"]
SETTINGS2 = ["routine practice", "a national screening programme", "teaching hospitals", "district hospitals", "community clinics"]
OUTCOMES = ["hospital admission", "intensive care admission", "in-hospital mortality", "sepsis", "critical illness", "early deterioration",
            "need for resuscitation", "left without being seen", "acute coronary syndrome", "stroke", "paediatric critical care admission",
            "length of stay over six hours", "return visits within 72 hours", "high triage acuity"]
TOOL2 = ["a gradient-boosting model", "a deep neural network", "a random forest model", "logistic regression and machine learning models",
         "a transformer-based language model", "an XGBoost model", "a recurrent neural network", "an ensemble learning model",
         "explainable machine learning", "natural language processing"]
LLMS = ["ChatGPT", "GPT-4", "a large language model", "Gemini", "Claude", "an open-source large language model", "Llama 3"]
OTHER_TASKS = ["chest radiograph triage in radiology", "diabetic retinopathy screening", "skin lesion classification",
               "electrocardiogram interpretation in cardiology clinics", "pathology slide prioritisation", "intensive care deterioration alerts",
               "sepsis prediction on hospital wards", "mammography reading", "fracture detection on radiographs",
               "stroke imaging prioritisation in radiology", "outpatient referral triage", "mental health crisis line prioritisation"]
ED_TOPICS = ["Nurse-initiated analgesia for long-bone fractures", "Access block and emergency department crowding",
             "Frequent attenders and social deprivation", "Point-of-care ultrasound for abdominal pain",
             "Paediatric asthma presentations after school holidays", "Violence against emergency nurses",
             "Sepsis screening tools at triage without decision support", "Waiting room deterioration and observation frequency",
             "High-sensitivity troponin pathways", "Older people presenting after falls", "Interpreter use at triage",
             "Health equity for Māori and Pacific peoples in emergency care",
             "Accident & emergency attendances by young people", "Drug & alcohol harm in emergency care", "Mental health presentations and boarding",
             "Ambulance offload delays", "Discharge against medical advice", "Rural emergency department staffing",
             "Climate-related heat presentations", "Patient experience of waiting times", "Triage nurse workload and fatigue",
             "Fast-track streams for minor injuries", "Antibiotic timing for febrile neutropenia", "Alcohol-related presentations",
             "Head injury decision rules in children", "Pain scores at triage", "Physician in triage models"]
DESIGN2 = ["retrospective cohort study", "cross-sectional survey", "qualitative study", "quality improvement project", "case series",
           "prospective observational study", "mixed-methods study", "registry analysis", "multicentre audit"]
REGIONS = ["Aotearoa New Zealand", "Scotland", "Australia", "Canada", "the United States", "South Korea", "Japan", "Denmark", "the Netherlands",
           "Brazil", "South Africa", "Kenya", "Taiwan", "Ireland", "Wales", "Sweden", "Germany", "Spain", "Italy", "India"]
REVIEW_KINDS = ["systematic review", "scoping review", "systematic review and meta-analysis", "narrative review", "rapid review"]
TOPICS_R = ["diagnostic accuracy studies", "implementation studies", "large language models", "paediatric populations", "equity"]
FT_EXCLUDE = [  # (reason, title patterns, abstract twist, why)
    ("Wrong study design", ["Implementation of {tool} in {setting}: early experience", "{Tool} at triage for {group}: lessons from {setting}",
                            "Triage performance after introducing {tool} at {setting}"],
     "We report triage performance after implementation.", "the full text shows a retrospective analysis of historical visits"),
    ("Wrong study design", ["{Tool} and triage accuracy in {setting}", "How accurate is {tool} for {complaint} at triage? Evidence from {region}",
                            "Triage of {complaint} by {tool}: performance in {setting}"],
     "Performance was assessed against expert review.", "the full text shows simulated patients played by actors"),
    ("Wrong intervention", ["Evaluation of an electronic triage tool to reduce under-triage in {setting}",
                            "Digital triage support for {group} in {region}{sub_pros}", "An e-triage pathway for {complaint} in {setting}"],
     "An electronic tool supported triage nurses.", "the full text shows a rule-based checklist with no learning component"),
    ("Wrong population", ["{Tool} to support acute care triage in {region}{sub_pros}", "Acuity assessment with {tool} for {group} in {region}",
                          "Front-door triage with {tool} at {setting2} in {region}"],
     "Patients presenting for acute care were triaged with support from the tool.", "the full text shows an urgent care clinic, not an emergency department"),
    ("Wrong outcomes", ["Clinician acceptance of {tool} in {setting}{sub_pros}", "Nurses' trust in {tool} for {complaint} at triage in {region}",
                        "Usability of {tool} among triage staff at {setting}"],
     "We measured how often triage nurses accepted the tool's suggestion.", "the full text reports only acceptance and satisfaction"),
    ("Wrong comparator", ["{Tool} compared with a second algorithm for triage in {setting}", "Two machine learning tools head to head for {complaint} triage in {region}"],
     "Two algorithms were compared prospectively.", "the full text compares two algorithms with no reference standard or usual care"),
    ("Wrong publication type", ["{Tool} in {setting}: a prospective pilot", "Prospective pilot of {tool} for {group} at triage in {region}"],
     "Preliminary results of a prospective pilot.", "a conference abstract later withdrawn; no full report exists"),
]


class Review:
    """The reference review's searches, at one scale, from one seed."""

    def __init__(self, seed: int = 2026, scale: str = "normal", scopus_format: str = "csv"):
        self.seed = seed
        self.scale = scale
        self.cfg = dict(SCALES[scale])
        self.scopus_format = scopus_format
        self.r = random.Random(seed)
        self.works: list = []
        self._titles: set = set()
        self._blocks: dict = {}
        self._ids: set = set()
        self._first_authors = [x for x in SURNAMES if x not in ("Smith", "Lee", "Li", "Wang")]   # one study per first author
        self.r.shuffle(self._first_authors)
        today = dt.date.today()
        d0 = today - dt.timedelta(days=30)          # the main searches, run within one week
        self.dates = {"MEDLINE": d0, "Embase": d0, "CINAHL": d0 + dt.timedelta(days=1), "Scopus": d0 + dt.timedelta(days=2),
                      "CENTRAL": d0 + dt.timedelta(days=4), "Citation searching": today - dt.timedelta(days=12),
                      "NEJM AI": today - dt.timedelta(days=11), "update": today - dt.timedelta(days=2)}
        self._build()
        self.exports = self._write_all()

    # ------------------------------------------------------------------------------------------- helpers
    def _uid(self, lo: int, hi: int, prefix: str = "") -> str:
        while True:
            x = f"{prefix}{self.r.randint(lo, hi)}"
            if x not in self._ids:
                self._ids.add(x)
                return x

    def _authors(self, n: int | None = None, first: str = "") -> list:
        r = self.r
        n = n or r.choice([1, 2, 3, 3, 4, 5, 6, 6, 8, 11])
        out = [(first, r.choice(GIVEN))] if first else []
        while len(out) < n:
            sn = r.choice(MULTIWORD) if (out and r.random() < 0.06) else r.choice(SURNAMES)
            if sn not in [a[0] for a in out]:
                out.append((sn, r.choice(GIVEN)))
        return out

    def _too_like(self, k: str) -> bool:
        """A title very like one already made (as ReviewDesk's possible-duplicate check would flag): real searches have
        a few such pairs, so most are made again and an occasional one is kept."""
        for b in ("40:" + k[:40], "12:" + k[:12]):
            for other in self._blocks.get(b, ())[-25:]:
                if SequenceMatcher(None, k[:200], other[:200]).quick_ratio() >= 0.88 and \
                        SequenceMatcher(None, k[:200], other[:200]).ratio() >= 0.88:
                    return self.r.random() < 0.97
        return False

    def _claim(self, k: str) -> None:
        self._titles.add(k)
        for b in ("40:" + k[:40], "12:" + k[:12]):
            self._blocks.setdefault(b, []).append(k)

    def _unique_title(self, make) -> str:
        for attempt in range(400):
            t = make()
            t = t[0].upper() + t[1:]
            k = tkey(t)
            if k not in self._titles and (attempt > 40 or not self._too_like(k)):
                self._claim(k)
                return t
        raise RuntimeError("could not make a unique title")

    def _journal(self, w: Work, name: str = "") -> None:
        r = self.r
        j = next((x for x in JOURNALS if x[0] == name), None) if name else r.choice(JOURNALS)
        w.journal, w.abbr = j[0], j[1]
        w.volume = str(max(1, w.year - (1990 if "Network" not in j[0] else 2017) + r.randint(-2, 2)))
        w.issue = str(r.randint(1, 12))
        sp = r.randint(1, 900)
        w.pages = f"{sp}-{sp + r.randint(4, 12)}"
        if j[2]:
            for _ in range(20):
                d = j[2](r, w.year).lower()
                if d not in self._ids:
                    self._ids.add(d)
                    w.doi = d
                    break

    def _fill(self, **slots) -> dict:
        r = self.r
        tool = r.choice(TOOLS)
        setting, country = r.choice(SETTINGS)
        base = {"tool": tool, "Tool": tool[0].upper() + tool[1:], "scale": r.choice(SCALES_T), "setting": setting, "country": country,
                "tool2": r.choice(TOOL2), "outcome": r.choice(OUTCOMES), "n": r.randint(120, 480) * 10,
                "llm": r.choice(LLMS), "region": r.choice(REGIONS), "other_task": r.choice(OTHER_TASKS), "ed_topic": r.choice(ED_TOPICS),
                "design2": r.choice(DESIGN2), "review_kind": r.choice(REVIEW_KINDS), "topic_r": r.choice(TOPICS_R)}
        base.update({k: r.choice(v) for k, v in SUBS.items()})
        base.update({"visits": r.choice(VISITS), "extra_data": r.choice(EXTRA_DATA), "complaint": r.choice(COMPLAINTS), "n2": r.randint(40, 400),
                     "vignette_kind": r.choice(VIGNETTE_KINDS), "pc_kind": r.choice(PC_KINDS), "group": r.choice(GROUPS),
                     "setting2": r.choice(SETTINGS2), "years": r.randint(3, 15), "ed_topic2": r.choice(ED_TOPICS)})
        for k in ("tool2", "outcome", "llm", "other_task", "design2", "review_kind", "complaint", "pc_kind", "vignette_kind"):
            base[k[0].upper() + k[1:]] = base[k][0].upper() + base[k][1:]
        base["LLM"] = base["Llm"]
        base["ed_topic_l"] = base["ed_topic"][0].lower() + base["ed_topic"][1:]
        base["ed_topic2_l"] = base["ed_topic2"][0].lower() + base["ed_topic2"][1:]
        base.update(slots)
        return base

    def _new(self, **kw) -> Work:
        w = Work(**kw)
        w.wid = f"W{len(self.works) + 1:05d}"
        if not w.authors:
            w.authors = self._authors()
        if not w.pmid and w.kind not in ("conference", "preprint", "registry"):
            w.pmid = self._uid(37000000, 41999999)
        w.embase_an = self._uid(2020000000, 2039999999)
        w.cinahl_an = self._uid(160000000, 189999999)
        w.eid = "2-s2.0-85" + self._uid(100000000, 999999999)
        w.central_id = "CN-0" + self._uid(2000000, 2999999)
        self.works.append(w)
        return w

    # ------------------------------------------------------------------------------------------- abstracts
    def _abstract_eligible(self, s: dict, design: str, year: int, hard: bool) -> str:
        r = self.r
        n = r.randint(800, 42000)
        t1, t2 = r.randint(6, 19), r.randint(3, 12)
        u1, u2 = round(r.uniform(9, 22), 1), round(r.uniform(4, 12), 1)
        auc = round(r.uniform(0.78, 0.93), 2)
        meth = {"cluster": f"In this stepped-wedge cluster randomised trial, sites crossed over from usual triage with {s['scale']} to "
                           f"triage supported by {s['tool']} in random order",
                "rct": f"Patients were randomly assigned to triage supported by {s['tool']} or usual nurse triage with {s['scale']}",
                "nrsi": f"Intervention sites used {s['tool']} while concurrent control sites continued usual triage with {s['scale']}",
                "ba": f"We compared consecutive patients triaged before and after {s['tool']} was introduced",
                "its": f"Weekly under-triage and over-triage were analysed over 52 weeks before and 52 weeks after {s['tool']} was introduced, "
                       "using segmented regression",
                "acc": f"Consecutive patients were triaged prospectively by {s['tool']} and by expert triage nurses blinded to its output"}[design]
        if hard:
            return (f"Background: Triage decision support may help emergency nurses. Methods: We evaluated {s['tool']} in "
                    f"{s['setting']}. Results: Triage acuity agreed with the reference in {r.randint(70, 90)}% of patients. "
                    "Conclusions: Further evaluation is needed.")
        return (f"Background: Accurate triage is essential for patient safety in emergency departments, but under-triage remains "
                f"common. Methods: {meth} in {s['setting']} between {r.choice(['January', 'March', 'June', 'September'])} "
                f"{year - 2} and {r.choice(['February', 'May', 'August', 'December'])} {year - 1}. The primary outcomes were time to "
                "triage and the proportions of patients under-triaged and over-triaged against an expert reference standard. "
                f"Results: {n:,} patients were included. Median time to triage was {t2} minutes with the tool and {t1} minutes without "
                f"it. Under-triage fell from {u1}% to {u2}%" + (f", and the tool's area under the curve for high acuity was {auc}" if design == "acc" else "")
                + ". Conclusions: In prospective clinical use, the tool was associated with faster and safer triage.")

    def _abstract_plain(self, s: dict, why: str) -> str:
        r = self.r
        if "retrospective" in why:
            return (f"Background: Machine learning may improve triage. Methods: We retrospectively analysed {s['n']:,} emergency department "
                    f"visits from electronic health records to develop and validate {s['tool2']} for {s['outcome']}. Results: The area "
                    f"under the curve was {round(r.uniform(0.71, 0.94), 2)} in the held-out validation set. Conclusions: The model "
                    "should now be evaluated prospectively.")
        if "vignette" in why:
            return (f"Objective: To compare {s['llm']} with emergency physicians. Methods: {s['n'] // 10} written clinical vignettes "
                    f"were assigned a triage level using {s['scale']}. No patients were involved. Results: Exact agreement was "
                    f"{r.randint(45, 80)}%.")
        if "not emergency department" in why:
            return (f"Background: Demand for {r.choice(['ambulance', 'telephone advice', 'primary care'])} services is rising. Methods: "
                    f"We evaluated {s['tool']} outside hospital in {s['region']}. Results: The tool prioritised calls faster than usual care.")
        if "not triage" in why:
            return f"We evaluated artificial intelligence for {s['other_task']}. Sensitivity was {r.randint(80, 97)}% and specificity {r.randint(70, 95)}%."
        if "review" in why:
            return (f"We searched MEDLINE, Embase and Scopus for studies of artificial intelligence in emergency triage and included "
                    f"{r.randint(12, 80)} studies, most of them retrospective.")
        if "editorial" in why or "protocol" in why:
            return "" if "editorial" in why else (f"Methods and analysis: This trial will randomise sites to {s['tool']} or usual triage. "
                                                 "Results will be reported in 2027.")
        if "no artificial" in why:
            return f"Triage nurses used {s['scale']} without any decision-support software. Agreement (weighted kappa) was {round(r.uniform(0.4, 0.8), 2)}."
        return (f"Background: {s['ed_topic']} is a recurring problem. Methods: {s['design2'][0].upper() + s['design2'][1:]} in "
                f"{s['setting']}. Results: We studied {s['n']:,} presentations. Conclusions: Changes to emergency care pathways are needed.")

    # ------------------------------------------------------------------------------------------- building the works
    def _eligible_study(self, k: int, set_name: str, year: int, label_first: str = "") -> list:
        """One eligible study and its reports."""
        r = self.r
        design = list(DESIGNS)[k % len(DESIGNS)]
        dname, pattern, tool = DESIGNS[design]
        for _ in range(500):                    # a title no other work has
            s = self._fill()
            title = pattern.format(**s)
            title = title[0].upper() + title[1:]
            if tkey(title) not in self._titles and not self._too_like(tkey(title)):
                self._claim(tkey(title))
                break
        study = f"S{k + 1:02d}" if set_name == "main" else f"S{set_name[0].upper()}{k + 1:02d}"
        first = label_first or self._first_authors.pop()
        authors = self._authors(r.randint(4, 9), first=first)
        hard = (k == 3 and set_name == "main")
        art = self._new(kind="article", study=study, study_label=f"{authors[0][0]} {year}", design=dname, title=title,
                        abstract=self._abstract_eligible(s, design, year, hard), authors=authors, year=year, month=r.randint(1, 12),
                        ta="include", ft="include", why=f"eligible: {dname.lower()} of {s['tool']} in {s['setting']}", hard=hard,
                        ai_miss=hard, set=set_name, keywords=["Triage", "Artificial intelligence", "Emergency Service, Hospital"],
                        rob_design=tool, country=s["country"])
        self._journal(art)
        out = [art]
        if set_name != "main":
            return out
        if k == 0:        # a conference abstract a year before the article, with the same title
            ca = self._new(kind="conference", study=study, study_label=art.study_label, design=dname, title=title,
                           abstract=art.abstract[:600], authors=authors[:3], year=year - 1, month=10, ta="include", ft="include",
                           why="a conference abstract of an eligible study (the article followed)", set=set_name, pmid="",
                           journal="Academic Emergency Medicine", abbr="Acad Emerg Med", volume=str(year - 1994), issue="S1",
                           pages=f"S{r.randint(10, 300)}", rob_design=tool)
            out.append(ca)
        if k == 1:        # a preprint before the article: another title and DOI
            pp = self._new(kind="preprint", study=study, study_label=art.study_label, design=dname,
                           title=self._unique_title(lambda: title.split(":")[0] + ": preprint of a prospective multicentre evaluation"),
                           abstract=art.abstract, authors=authors, year=year - 1, month=4, ta="include", ft="include",
                           why="a preprint of an eligible study", set=set_name, pmid="", journal="medRxiv", abbr="medRxiv",
                           doi=f"10.1101/{year - 1}.0{r.randint(1, 9)}.{r.randint(10, 28)}.{r.randint(23000000, 23999999)}", rob_design=tool)
            out.append(pp)
        if k == 2:        # an erratum indexed with the article's own title, and a "Correction to" notice
            er = self._new(kind="erratum", title=title, abstract="", authors=authors[:1], year=year, month=min(12, art.month + 2),
                           ta="exclude", why="an erratum (not a report of the study's methods or results)", set=set_name,
                           journal=art.journal, abbr=art.abbr, volume=art.volume, issue=str(int(art.issue) + 1), pages="1")
            out.append(er)
            cr = self._new(kind="erratum", title=self._unique_title(lambda: "Correction to: " + title), abstract="", authors=authors[:1],
                           year=year, month=min(12, art.month + 3), ta="exclude", why="a correction notice", set=set_name,
                           journal=art.journal, abbr=art.abbr)
            self._journal(cr, art.journal)
            out.append(cr)
            lt = self._new(kind="comment", title=self._unique_title(lambda: "Re: " + title.split(":")[0]), abstract="",
                           authors=self._authors(2), year=year, month=min(12, art.month + 2), ta="exclude", why="a letter about an included study",
                           set=set_name)
            self._journal(lt, art.journal)
            out.append(lt)
        if k == 4:        # a second report of the same study (a subgroup analysis)
            sec = self._new(kind="secondary", study=study, study_label=art.study_label, design=dname,
                            title=self._unique_title(lambda: f"Children and young people in a trial of {s['tool']}: a secondary analysis"),
                            abstract="In a prespecified secondary analysis of a prospective evaluation in " + s["setting"]
                                     + ", under-triage in children fell with the tool.",
                            authors=authors, year=year + 1 if year < 2026 else year, ta="include", ft="include",
                            why="a second report of an eligible study", set=set_name, rob_design=tool)
            self._journal(sec)
            out.append(sec)
        if k == 5:        # the trial's registry record in CENTRAL: listed, not included
            rg = self._new(kind="registry", title=self._unique_title(lambda: f"{s['Tool']} for emergency triage (NCT0{r.randint(5000000, 6999999)})"),
                           abstract="Interventional study. Condition: emergency triage. Intervention: " + s["tool"] + ".",
                           authors=[], year=year - 2, ta="include", ft="exclude", ft_reason="Wrong publication type",
                           why="a trial registry record (registry entries are listed, not included)", set=set_name, pmid="",
                           journal="https://clinicaltrials.gov/show/NCT0" + str(r.randint(5000000, 6999999)))
            out.append(rg)
        if k == 6:        # a first author with a two-word surname, epub and print years differ, no DOI in CINAHL
            art.authors[0] = ("Te Whāiti", "Aroha")
            art.study_label = f"Te Whāiti {year}"
            art.variants = {"Embase": {"doi": "", "year": year + 1}, "MEDLINE": {"doi": ""}}
            art.found_force = {"MEDLINE", "Embase"}
        return out

    def _ta_exclude(self, set_name: str, year_lo: int = 2018, year_hi: int = 2026) -> Work:
        r = self.r
        x = r.random()
        acc = 0.0
        for share, kind, patterns, why in TA_EXCLUDE:
            acc += share
            if x <= acc:
                break
        s = self._fill()
        title = self._unique_title(lambda: r.choice(patterns).format(**self._fill()))
        w = self._new(kind=kind, title=title, abstract=self._abstract_plain(s, why), year=r.randint(year_lo, year_hi), month=r.randint(1, 12),
                      ta="exclude", why=why, set=set_name, keywords=r.sample(["Triage", "Emergency Service, Hospital", "Machine Learning",
                                                                          "Nursing", "Quality Improvement", "Humans"], 3))
        self._journal(w)
        return w

    def _ft_exclude(self, k: int, set_name: str) -> Work:
        r = self.r
        reason, patterns, twist, why = FT_EXCLUDE[k % len(FT_EXCLUDE)]
        s = self._fill()
        title = self._unique_title(lambda: r.choice(patterns).format(**self._fill()))
        w = self._new(kind="conference" if reason == "Wrong publication type" else "article", title=title,
                      abstract=f"Background: Triage is difficult in busy emergency departments. Methods: {twist} The setting was {s['setting']}. "
                               f"Results: {r.randint(200, 9000):,} patients were included. Conclusions: Promising.",
                      year=r.randint(2021, 2026), ta="include", ft="exclude", ft_reason=reason, why=why, set=set_name)
        self._journal(w)
        if reason == "Wrong publication type":
            w.pmid, w.doi, w.journal, w.abbr, w.issue = "", "", "Annals of Emergency Medicine", "Ann Emerg Med", "4 Supplement"
        return w

    def _build(self) -> None:
        r, c = self.r, self.cfg
        main: list = []
        for k in range(c["eligible"]):
            main += self._eligible_study(k, "main", r.choice([2023, 2024, 2024, 2025, 2025, 2026]))
        for k in range(c["ft_exclude"]):
            main.append(self._ft_exclude(k, "main"))
        # one study not in English (Ovid gives the English title in [brackets]); reviewers exclude it at full text
        ne = self._new(kind="article", title=self._unique_title(lambda: "Artificial intelligence-assisted triage in a Spanish emergency department: a prospective study"),
                       abstract="Objetivo: evaluar un sistema de triaje con inteligencia artificial. Métodos: estudio prospectivo con "
                                "controles concurrentes. Resultados: el infratriaje disminuyó.", year=2025, language="Spanish", ta="include",
                       ft="exclude", ft_reason="Not in English", why="eligible design, but published in Spanish only", set="main")
        self._journal(ne, "Emergency Medicine International")
        main.append(ne)
        for k in range(c["awaiting"]):
            s = self._fill()
            w = self._new(kind="conference", title=self._unique_title(lambda: f"Prospective use of {s['tool']} at triage in {s['setting']}"),
                          abstract=f"We piloted {s['tool']} prospectively at triage. Data collection is ongoing.", year=2025, pmid="",
                          journal="Emergency Medicine Australasia", abbr="Emerg Med Australas", issue="S1", ta="include", ft="awaiting",
                          why="a conference abstract: the authors were contacted and did not reply; design unclear", set="main")
            main.append(w)
        for k in range(c["not_retrieved"]):
            s = self._fill()
            w = self._new(kind="article", title=self._unique_title(lambda: f"Electronic triage with {s['tool']} in {s['region']}: a prospective evaluation"),
                          abstract="A prospective evaluation of an AI triage tool.", year=2022, ta="include", ft="not_retrieved",
                          why="no copy could be obtained (inter-library loan failed)", set="main")
            self._journal(w, "New Zealand Medical Journal")
            main.append(w)
        # a very long abstract, and a record whose title has the formula characters a spreadsheet would run
        long_w = self._ta_exclude("main")
        long_w.abstract = " ".join(["Background: " + long_w.abstract] + [f"Section {i}: " + ("The emergency department cohort was described in detail. " * 12)
                                                                        for i in range(14)])
        main.append(long_w)
        while len([w for w in main]) < c["main"]:
            main.append(self._ta_exclude("main"))
        # the update search: new works published since
        for k in range(c["update_eligible"]):
            self._eligible_study(k, "update", 2026)
        for _ in range(c["update_new"] - c["update_eligible"]):
            self._ta_exclude("update", 2026, 2026)
        # other methods: citation searching (new works and works the databases found) and the NEJM AI hand-search
        for k in range(c["citation_eligible"]):
            self._eligible_study(k + 50, "other", r.choice([2022, 2023]))
        for _ in range(c["citation_new"] - c["citation_eligible"]):
            self._ta_exclude("other")
        for k in range(c["nejm_eligible"]):
            w = self._eligible_study(k + 80, "other", 2025)[0]
            w.journal, w.abbr, w.doi, w.pmid = "NEJM AI", "NEJM AI", f"10.1056/AIoa{2500000 + k * 137}", ""
            w.nejm = True
        for _ in range(c["nejm_new"] - c["nejm_eligible"]):
            w = self._ta_exclude("other", 2024, 2026)
            w.journal, w.abbr, w.doi, w.pmid = "NEJM AI", "NEJM AI", f"10.1056/AIoa{2400000 + r.randint(0, 99999)}", ""
            w.nejm = True
        self._place()

    def _place(self) -> None:
        """Which searches find which works."""
        r, c = self.r, self.cfg
        main = [w for w in self.works if w.set == "main"]
        for w in main:
            force = getattr(w, "found_force", None)
            if force:
                w.found = {("main", db): True for db in force}
                continue
            p = dict(FIND)
            if w.kind == "conference":
                p.update(MEDLINE=0.0, CINAHL=0.05, Scopus=0.35, Embase=0.95)
            elif w.kind == "preprint":
                p.update(MEDLINE=0.0, CINAHL=0.0, CENTRAL=0.0, Embase=0.9, Scopus=0.6)
            elif w.kind == "registry":
                p = {"CENTRAL": 1.0}
            elif w.kind == "erratum":
                p.update(MEDLINE=0.7, Embase=0.9, CENTRAL=0.0)
            elif w.ta == "include":                       # relevant studies are found by more of the databases
                p.update(MEDLINE=0.8, Embase=0.9, CINAHL=0.55, Scopus=0.85, CENTRAL=0.5 if "andomised" in w.design else 0.08)
            if w.language != "English":
                p.update(CINAHL=0.0, CENTRAL=0.0, Embase=1.0, MEDLINE=1.0)     # Ovid gives the English title in [brackets]
            got = [db for db in DATABASES if r.random() < p.get(db, 0)]
            if not got:
                got = ["Embase" if w.kind in ("conference", "preprint") else ("CENTRAL" if w.kind == "registry" else r.choice(["MEDLINE", "Embase", "Scopus"]))]
            w.found = {("main", db): True for db in got}
        # the update search re-run: every new work, and a share of the earlier ones (dates of entry overlap)
        new = [w for w in self.works if w.set == "update"]
        for w in new:
            got = [db for db in DATABASES if r.random() < (0.85 if w.ta == "include" else FIND[db] + 0.1) and db != "CENTRAL"] or ["Embase"]
            w.found = {("update", db): True for db in got}
        for w in main:
            if w.kind == "registry":
                continue
            for (_, db) in list(w.found):
                if r.random() < c["update_overlap"]:
                    w.found[("update", db)] = True
        # other methods
        for w in self.works:
            if w.set == "other":
                w.found = {("other", "NEJM AI" if getattr(w, "nejm", False) else "Citation searching"): True}
        cites = [w for w in main if w.kind in ("article", "secondary") and w.pmid]
        relevant = [w for w in cites if w.ta == "include"]
        pool = relevant[: c["citation_dups"] // 2] + r.sample([w for w in cites if w.ta != "include"], c["citation_dups"] - c["citation_dups"] // 2)
        for w in pool:
            w.found[("other", "Citation searching")] = True

    # ------------------------------------------------------------------------------------------- the exports
    def _for(self, w: Work, db: str) -> dict:
        """The work as one database records it."""
        r = random.Random(f"{self.seed}|{w.wid}|{db}")
        v = {"title": w.title, "abstract": w.abstract, "authors": list(w.authors), "year": w.year, "journal": w.journal, "abbr": w.abbr,
             "doi": w.doi if r.random() < DOI_KEPT.get(db, 0.9) else "", "pmid": w.pmid, "volume": w.volume, "issue": w.issue,
             "pages": w.pages, "language": w.language}
        if w.kind == "preprint":
            v["doi"] = w.doi                      # a preprint is known by its server's DOI
        if w.wid == self.works[0].wid:            # the first eligible article: found with and without its DOI
            v["doi"] = w.doi if db in ("MEDLINE", "Scopus") else ""
        v.update(w.variants.get(db, {}))
        if db in ("CINAHL", "Scopus") and r.random() < 0.5 and w.kind not in ("registry",):
            v["title"] = title_case(v["title"])
        if db == "MEDLINE" and not v["title"].endswith((".", "?", "!", ")")):
            v["title"] += "."
        if db == "CINAHL" and "&" in v["title"]:
            v["title"] = v["title"].replace("&", "&amp;")       # EBSCO writes an ampersand as an HTML entity
        if db == "Scopus" and w.kind == "article" and "SpO2" in v["title"]:
            v["title"] = v["title"].replace("SpO2", "SpO<inf>2</inf>")
        if db == "Embase":
            v["title"] = v["title"].replace(" - ", " – ").replace("'", "’")
        return v

    def _write_all(self) -> list:
        out = []
        groups: dict = {}
        for w in self.works:
            for key in w.found:
                groups.setdefault(key, []).append(w)
        order = [("main", db) for db in DATABASES] + [("update", db) for db in DATABASES] + [("other", "Citation searching"), ("other", "NEJM AI")]
        for key in order:
            ws = groups.get(key, [])
            if not ws:
                continue
            set_name, db = key
            ws = sorted(ws, key=lambda w: (-w.year, w.wid)) if db != "CENTRAL" else sorted(ws, key=lambda w: w.wid)
            if db in ("Scopus", "CINAHL"):
                self.r.shuffle(ws)
            if db == "CINAHL" and set_name == "main":
                ws = ws + [None]                        # a record with nothing in it but its accession number
            size = self.cfg["part_size"] if db == "Embase" else 0
            chunks = [ws[i:i + size] for i in range(0, len(ws), size)] if size and len(ws) > size else [ws]
            for i, chunk in enumerate(chunks, 1):
                part = f"{i} of {len(chunks)}" if len(chunks) > 1 else ""
                out.append(self._export(set_name, db, chunk, part))
        return out

    def _export(self, set_name: str, db: str, ws: list, part: str) -> "Export":
        when = self.dates["update" if set_name == "update" else db]
        writer = {"MEDLINE": self._ovid, "Embase": self._ovid, "CINAHL": self._ebsco, "Scopus": self._scopus, "CENTRAL": self._cochrane,
                  "Citation searching": self._nbib, "NEJM AI": self._nejm}[db]
        data, name = writer(db, ws, when, set_name)
        if part:
            stem, ext = name.rsplit(".", 1)
            name = f"{stem} part {part.split(' ')[0]}.{ext}"
        return Export(set_name, db, PLATFORM[db], name, data, [w.wid if w else "" for w in ws], when.isoformat(), part, self)

    # Ovid RIS: MEDLINE and Embase
    def _ovid(self, db: str, ws: list, when, set_name: str):
        lines = []
        emb = db == "Embase"
        for w in ws:
            v = self._for(w, db)
            pt = {"article": "Journal Article" if not emb else "Article", "secondary": "Journal Article" if not emb else "Article",
                  "conference": "Conference Abstract", "preprint": "Preprint", "erratum": "Erratum" if emb else "Published Erratum",
                  "comment": "Letter" if emb else "Comment", "review": "Review", "editorial": "Editorial", "protocol": "Article" if emb else "Journal Article"}[w.kind]
            title = v["title"]
            if w.language != "English":
                title = f"[{title}]."
            lines += ["TY  - JOUR", f"DB  - {'Embase' if emb else 'Ovid MEDLINE(R) ALL'}",
                      f"AN  - {w.embase_an if emb else (w.pmid or w.embase_an)}", f"T1  - {title}"]
            for sn, given in v["authors"]:
                lines.append(f"A1  - {sn} {initials(given)}." if emb else f"A1  - {sn}, {given}")
            lines.append(f"Y1  - {v['year']}//")
            if v["abstract"]:
                ab = v["abstract"]
                if emb:                                     # wrapped over several lines, as Ovid's Embase export does
                    chunks = wrap(ab, 78)
                    lines.append("N2  - " + chunks[0])
                    lines += ["      " + x for x in chunks[1:]]
                else:
                    lines.append("N2  - " + ab)
            for kw in w.keywords:
                lines.append(f"KW  - {kw}")
            lines += [f"JF  - {v['journal']}", f"JA  - {v['abbr']}"]
            if v["volume"]:
                lines.append(f"VL  - {v['volume']}")
            if v["issue"]:
                lines.append(f"IS  - {v['issue']}")
            if v["pages"]:
                sp, _, ep = v["pages"].partition("-")
                lines.append(f"SP  - {sp}")
                if ep:
                    lines.append(f"EP  - {ep}")
            lines.append(f"M3  - {pt}")
            lines.append(f"UR  - http://ovidsp.ovid.com/ovidweb.cgi?T=JS&PAGE=reference&D={'emexb' if emb else 'medall'}&NEWS=N&AN="
                         f"{w.embase_an if emb else w.pmid}")
            if v["doi"]:
                lines.append(f"DO  - https://dx.doi.org/{v['doi']}")
            lines += [f"LA  - {v['language']}", "ER  - ", ""]
        text = "\r\n".join(lines)
        if emb:
            data = ascii_fold(text).encode("cp1252")
        else:
            data = b"\xef\xbb\xbf" + text.encode("utf-8")
        name = f"{'embase' if emb else 'medline'}_ovid_{when.isoformat()}{'_update' if set_name == 'update' else ''}.ris"
        return data, name

    # EBSCOhost RIS: CINAHL
    def _ebsco(self, db: str, ws: list, when, set_name: str):
        lines = []
        for w in ws:
            if w is None:
                lines += ["TY  - JOUR", f"ID  - {self._uid(190000000, 199999999)}", "DP  - EBSCOhost", "DB  - ccm", "ER  - ", ""]
                continue
            v = self._for(w, db)
            lines += ["TY  - JOUR", f"ID  - {w.cinahl_an}", f"T1  - {v['title']}"]
            lines += [f"AU  - {sn}, {given}" for sn, given in v["authors"]]
            lines.append(f"Y1  - {v['year']}/{w.month:02d}//")
            pt = {"comment": "Letter", "editorial": "Editorial", "review": "Article; systematic review"}.get(w.kind, "Article; research")
            lines.append(f"N1  - Accession Number: {w.cinahl_an}. Language: {v['language']}. Entry Date: {v['year']}0315. Publication "
                         f"Type: {pt}. Journal Subset: Core Nursing; Peer Reviewed." + (f" PMID: NLM{w.pmid}." if w.pmid else ""))
            lines += [f"KW  - {kw}" for kw in w.keywords]
            if v["abstract"]:
                lines.append(f"N2  - {v['abstract']}")
            if v["pages"]:
                sp, _, ep = v["pages"].partition("-")
                lines += [f"SP  - {sp}"] + ([f"EP  - {ep}"] if ep else [])
            lines += [f"JO  - {v['journal']}", f"JF  - {v['journal']}", f"JA  - {v['abbr'].upper()}", f"VL  - {v['volume']}",
                      f"IS  - {v['issue']}", "PB  - Publisher", "SN  - 0000-0000"]
            if w.pmid:
                lines.append(f"U2  - PMID: NLM{w.pmid}.")
            if v["doi"]:
                lines.append(f"DO  - {v['doi']}")
            lines += [f"UR  - https://search.ebscohost.com/login.aspx?direct=true&db=ccm&AN={w.cinahl_an}&site=ehost-live",
                      "DP  - EBSCOhost", "DB  - ccm", "ER  - ", ""]
        return "\n".join(lines).encode("utf-8"), f"CINAHL EBSCO export {when.isoformat()}{' update' if set_name == 'update' else ''}.ris"

    SCOPUS_HEADER = ["Authors", "Author full names", "Author(s) ID", "Title", "Year", "Source title", "Volume", "Issue", "Art. No.",
                     "Page start", "Page end", "Page count", "Cited by", "DOI", "Link", "Abstract", "Author Keywords", "Index Keywords",
                     "Correspondence Address", "Publisher", "ISSN", "PubMed ID", "Language of Original Document", "Abbreviated Source Title",
                     "Document Type", "Publication Stage", "Open Access", "Source", "EID"]

    def _scopus(self, db: str, ws: list, when, set_name: str):
        if self.scopus_format == "ris":
            return self._scopus_ris(ws, when, set_name)
        buf = io.StringIO()
        wr = csv.writer(buf, lineterminator="\r\n")
        wr.writerow(self.SCOPUS_HEADER)
        for w in ws:
            v = self._for(w, db)
            dt_ = {"conference": "Conference paper", "review": "Review", "editorial": "Editorial", "comment": "Letter", "erratum": "Erratum",
                   "preprint": "Article"}.get(w.kind, "Article")
            sp, _, ep = (v["pages"] or "").partition("-")
            ab = v["abstract"] or "[No abstract available]"
            if len(ab) > 400 and w.ta == "include":
                ab = ab.replace(" Results:", "\nResults:", 1)    # a line break inside the quoted abstract
            wr.writerow(["; ".join(f"{sn}, {initials(g)}." for sn, g in v["authors"]),
                         "; ".join(f"{sn}, {g} ({57200000000 + i})" for i, (sn, g) in enumerate(v["authors"])),
                         "; ".join(str(57200000000 + i) for i in range(len(v["authors"]))), v["title"], v["year"], v["journal"],
                         v["volume"], v["issue"], "", sp, ep, "", self.r.randint(0, 40), v["doi"],
                         f"https://www.scopus.com/inward/record.uri?eid={w.eid}&doi={v['doi'].replace('/', '%2f')}&partnerID=40",
                         ab, "; ".join(w.keywords), "", "", "", "", v["pmid"], v["language"], v["abbr"], dt_, "Final", "", "Scopus", w.eid])
        return ("﻿" + buf.getvalue()).encode("utf-8"), f"scopus{'_update' if set_name == 'update' else ''}.csv"

    def _scopus_ris(self, ws: list, when, set_name: str):
        lines = []
        for w in ws:
            v = self._for(w, "Scopus")
            lines.append("TY  - " + ("CONF" if w.kind == "conference" else "JOUR"))
            lines += [f"AU  - {sn}, {initials(g)}." for sn, g in v["authors"]]
            lines += [f"TI  - {v['title']}", f"PY  - {v['year']}", f"T2  - {v['journal']}", f"J2  - {v['abbr']}"]
            sp, _, ep = (v["pages"] or "").partition("-")
            lines += [f"VL  - {v['volume']}", f"IS  - {v['issue']}"] + ([f"SP  - {sp}", f"EP  - {ep}"] if sp else [])
            if v["doi"]:
                lines.append(f"DO  - {v['doi']}")
            lines.append(f"UR  - https://www.scopus.com/inward/record.uri?eid={w.eid}&partnerID=40")
            if v["abstract"]:
                lines.append(f"AB  - {v['abstract']}")
            lines += [f"KW  - {k}" for k in w.keywords]
            lines += [f"LA  - {v['language']}", f"M3  - {'Conference paper' if w.kind == 'conference' else 'Article'}", "DB  - Scopus",
                      f"N1  - Export Date: {when.strftime('%d %B %Y')}; Cited By: 2", "ER  - ", ""]
        return "\n".join(lines).encode("utf-8"), f"scopus{'_update' if set_name == 'update' else ''}.ris"

    def _cochrane(self, db: str, ws: list, when, set_name: str):
        lines = []
        n = len(ws)
        for i, w in enumerate(ws, 1):
            v = self._for(w, db)
            lines += [f"Record #{i} of {n}", f"ID: {w.central_id}"]
            if v["authors"]:
                lines.append("AU: " + "; ".join(f"{sn} {initials(g)}" for sn, g in v["authors"]))
            lines.append(f"TI: {v['title']}")
            if w.kind == "registry":
                lines += [f"SO: {w.journal}", f"YR: {v['year']}", "PT: Trial registry record"]
            else:
                lines += [f"SO: {v['journal']}", f"YR: {v['year']}", f"VL: {v['volume']}", f"NO: {v['issue']}", f"PG: {v['pages']}"]
                if w.pmid:
                    lines.append(f"PM: PUBMED {w.pmid}")
                pt = "Journal: Conference Abstract" if w.kind == "conference" else "Journal article" + (
                    "; Randomized controlled trial" if "andomised" in w.design else "")
                lines += [f"XR: EMBASE {w.embase_an}", f"PT: {pt}",
                          "KY: " + "; ".join(w.keywords)]
                if v["doi"]:
                    lines.append(f"DOI: {v['doi']}")
            if v["abstract"]:
                parts = v["abstract"].split(" Results: ", 1)     # a long abstract runs on to a second line
                lines.append(f"AB: {parts[0]}")
                if len(parts) > 1:
                    lines.append("Results: " + parts[1])
            lines += [f"US: https://www.cochranelibrary.com/central/doi/10.1002/central/{w.central_id}/full", ""]
        return "\r\n".join(lines).encode("utf-8"), f"citation-export{'-update' if set_name == 'update' else ''}.txt"

    def _nbib(self, db: str, ws: list, when, set_name: str):
        lines = []
        for w in ws:
            v = self._for(w, db)
            lines += [f"PMID- {w.pmid or self._uid(42000000, 42999999)}", "OWN - NLM", "STAT- MEDLINE", f"DCOM- {v['year']}0401",
                      f"VI  - {v['volume']}", f"IP  - {v['issue']}", f"DP  - {v['year']} {MONTHS[w.month - 1]}"]
            lines += nbib_wrap("TI  - ", v["title"] + ("" if v["title"].endswith(".") else "."))
            lines.append(f"PG  - {v['pages']}")
            if v["doi"]:
                lines.append(f"LID - {v['doi']} [doi]")
            if v["abstract"]:
                lines += nbib_wrap("AB  - ", v["abstract"])
            for sn, g in v["authors"]:
                lines += [f"FAU - {sn}, {g}", f"AU  - {sn} {initials(g)}"]
            lines += ["LA  - eng" if v["language"] == "English" else "LA  - spa", "PT  - Journal Article", f"TA  - {v['abbr']}",
                      f"JT  - {v['journal']}", "SB  - IM", "MH  - Humans", "MH  - *Triage/methods"]
            if v["doi"]:
                lines.append(f"AID - {v['doi']} [doi]")
            lines += [f"SO  - {v['abbr']}. {v['year']};{v['volume']}({v['issue']}):{v['pages']}.", ""]
        return "\n".join(lines).encode("utf-8"), "pubmed-citationsearch-set.nbib"

    def _nejm(self, db: str, ws: list, when, set_name: str):
        lines = []
        for w in ws:
            lines += ["TY  - JOUR", f"T1  - {w.title}"] + [f"AU  - {sn}, {g}" for sn, g in w.authors]
            lines += [f"Y1  - {w.year}/{w.month:02d}/26", f"PY  - {w.year}", f"N1  - doi: {w.doi}", f"DO  - {w.doi}", "T2  - NEJM AI",
                      f"AB  - {w.abstract}", f"SP  - {w.doi.split('/')[-1]}", "VL  - 2", "IS  - 7", "PB  - Massachusetts Medical Society",
                      "SN  - 2836-9386", f"UR  - https://doi.org/{w.doi}", f"Y2  - {when.isoformat().replace('-', '/')}", "ER  - ", ""]
        return "\n".join(lines).encode("utf-8"), "NEJM AI hand-search.ris"

    # ------------------------------------------------------------------------------------------- what tests need
    def export(self, database: str, set_name: str = "main") -> "Export":
        return next(e for e in self.exports if e.database == database and e.set == set_name)

    def files(self, set_name: str) -> list:
        return [e for e in self.exports if e.set == set_name]

    def by_wid(self) -> dict:
        return {w.wid: w for w in self.works}

    def work_at(self, export_name: str, k: int):
        e = next(x for x in self.exports if x.name == export_name)
        wid = e.wids[k]
        return self.by_wid().get(wid)

    def searches(self) -> list:
        """The search log, one entry per database search (with the number of records each found)."""
        out = []
        for set_name in ("main", "update", "other"):
            seen = {}
            for e in self.files(set_name):
                seen.setdefault(e.database, []).append(e)
            for db, es in seen.items():
                out.append({"database": db if db != "CENTRAL" else "CENTRAL (Cochrane Library)", "platform": PLATFORM[db],
                            "date": es[0].date, "results": str(sum(e.records for e in es)), "string": STRATEGY.get(db, "Hand-searched"),
                            "limits": "None" if set_name != "update" else "Re-run unchanged",
                            "coverage": "1946 to present" if db == "MEDLINE" else "", "set": set_name, "_db": db,
                            "notes": "Update search" if set_name == "update" else ""})
        return out

    def eligible_studies(self, sets=("main", "update", "other")) -> set:
        """The study labels a perfect review would include (reports found by the searches only)."""
        return {w.study_label for w in self.works if w.ft == "include" and w.set in sets and w.found}

    def ai_decider(self, false_keep: float = 0.15):
        """A decide function for the AI stand-in that answers from the truth: eligible records are kept (Maybe for the
        vague ones), except those planned as missed; a share of the ineligible ones are kept too (the AI is cautious).
        The answer depends only on the record and the model, so a run gives the same answers every time."""
        idx: dict = {}
        for w in self.works:
            idx.setdefault(tkey(w.title)[:120], []).append(w)

        def decide(call):
            ws = idx.get(tkey(call.title)[:120]) or [w for k, v in idx.items() if k[:60] == tkey(call.title)[:60] for w in v]
            if not ws:
                return "MAYBE"
            kinds = [w for w in ws if (w.kind in ("conference",)) == ("onference" in call.pubtype)
                     and (w.kind == "erratum") == ("rratum" in call.pubtype)]
            w = (kinds or ws)[0]
            if w.ai_miss:
                return "EXCLUDE"
            if w.ta == "include":
                return "MAYBE" if w.hard else "INCLUDE"
            h = int(zlib.crc32(f"{w.wid}|{call.model}".encode()))
            return "MAYBE" if (h % 1000) / 1000 < false_keep else "EXCLUDE"
        return decide


class Export:
    """One export file, as downloaded from the database."""

    def __init__(self, set_name, database, platform, name, data, wids, date, part, review):
        self.set, self.database, self.platform, self.name, self.data = set_name, database, platform, name, data
        self.wids, self.date, self.part, self.review = wids, date, part, review

    @property
    def records(self) -> int:
        return len(self.wids)

    def works(self) -> list:
        by = self.review.by_wid()
        return [by.get(w) for w in self.wids]

    def __repr__(self):
        return f"<Export {self.set}/{self.database} {self.name} {self.records} records>"


STRATEGY = {
    "MEDLINE": "1 exp Emergency Service, Hospital/\n2 (emergency adj2 (department* or room* or ward*)).tw,kf.\n3 Triage/\n4 triag*.tw,kf.\n"
               "5 exp Artificial Intelligence/\n6 (artificial intelligence or machine learning or deep learning or neural network* or "
               "large language model* or natural language processing).tw,kf.\n7 (1 or 2) and (3 or 4) and (5 or 6)",
    "Embase": "1 emergency ward/ or emergency health service/\n2 triage/\n3 exp artificial intelligence/ or machine learning/\n"
              "4 (artificial intelligence or machine learning or deep learning).tw,kf.\n5 1 and 2 and (3 or 4)",
    "CINAHL": "S1 (MH \"Emergency Service\") OR TI emergency OR AB emergency\nS2 (MH \"Triage\") OR TI triag* OR AB triag*\n"
              "S3 (MH \"Artificial Intelligence+\") OR TI (\"machine learning\" OR \"artificial intelligence\")\nS4 S1 AND S2 AND S3",
    "Scopus": "TITLE-ABS-KEY ( emergency W/2 ( department* OR room* ) ) AND TITLE-ABS-KEY ( triag* ) AND TITLE-ABS-KEY "
              "( \"artificial intelligence\" OR \"machine learning\" OR \"deep learning\" OR \"large language model*\" )",
    "CENTRAL": "#1 MeSH descriptor: [Triage] explode all trees\n#2 triag*:ti,ab,kw\n#3 (artificial intelligence or machine learning):ti,ab,kw\n"
               "#4 (#1 or #2) and #3",
    "Citation searching": "Backward and forward citation searching of included studies (PubMed 'Cited by' and reference lists)",
    "NEJM AI": "Hand-search of every issue of NEJM AI",
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SMALL = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "versus", "vs"}


def title_case(t: str) -> str:
    words = t.split(" ")
    return " ".join(w if (i and w.lower() in SMALL) or not w[:1].isalpha() or w.isupper() else w[:1].upper() + w[1:]
                    for i, w in enumerate(words))


def initials(given: str) -> str:
    return "".join(p[0] for p in re.split(r"[\s-]+", given) if p)


def wrap(text: str, width: int) -> list:
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return out + ([cur] if cur else [])


def nbib_wrap(tag: str, text: str) -> list:
    chunks = wrap(text, 82)
    return [tag + chunks[0]] + ["      " + c for c in chunks[1:]] if chunks else [tag]


# ================================================================================================ reviewers' errors
class Errors:
    """A reviewer's seeded slips: whether they get a record wrong at a stage, at a configurable rate. The same
    reviewer, seed and record always give the same answer."""

    def __init__(self, who: str, seed: int, rates: dict | None = None):
        self.who, self.seed = who, seed
        self.rates = {"cal": 0.06, "ta": 0.02, "ft": 0.03, **(rates or {})}
        self.made: list = []

    def wrong(self, stage: str, key: str) -> bool:
        h = zlib.crc32(f"{self.seed}|{self.who}|{stage}|{key}".encode()) % 100000 / 100000
        return h < self.rates.get(stage, 0.0)


# ================================================================================================ PDFs
def make_pdf(title: str, doi: str = "", journal: str = "", authors: str = "", pages: int = 9, meta_doi: bool = False,
             body: str = "", banner: str = "", compress: bool = True) -> bytes:
    """A small but real PDF: a page tree with `pages` pages, the first page's text shown in a (compressed) content
    stream as a journal prints it (banner, journal, title, authors, DOI, abstract), and the title (and DOI) in its
    document details. ReviewDesk reads PDFs without a library, as this one is read."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = [x for x in (banner, journal, title, authors, f"https://doi.org/{doi}" if doi else "", body or
                         "Abstract. Background: triage in the emergency department. Methods. Results. Conclusions.") if x]
    content = "BT /F1 11 Tf 72 780 Td 14 TL " + " ".join(f"({esc(ascii_fold(x))}) Tj T*" for x in lines) + " ET"
    stream = zlib.compress(content.encode("latin-1", "replace")) if compress else content.encode("latin-1", "replace")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{4 + i} 0 R' for i in range(pages))}] /Count {pages} >>",
            f"<< /Length {len(stream)}{' /Filter /FlateDecode' if compress else ''} >>"]
    for i in range(pages):
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 3 0 R /Resources << /Font << /F1 << /Type /Font "
                    "/Subtype /Type1 /BaseFont /Helvetica >> >> >> >>")
    info = f"<< /Title ({esc(ascii_fold(title))}) /Producer (ReviewDesk tests)" + (f" /doi ({doi})" if meta_doi and doi else "") + " >>"
    objs.append(info)
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        if i == 3:
            out.write(f"{i} 0 obj\n{o}\nstream\n".encode("latin-1", "replace") + stream + b"\nendstream\nendobj\n")
        else:
            out.write(f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1", "replace"))
    x = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{x}\n%%EOF\n".encode())
    return out.getvalue()


def pdf_for(w: Work, **kw) -> bytes:
    au = ", ".join(f"{sn} {initials(g)}" for sn, g in w.authors[:6])
    return make_pdf(w.title, w.doi, w.journal, au, **kw)
