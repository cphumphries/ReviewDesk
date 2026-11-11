# Changes

## 1.3.1 (11 November 2026)

For teams who want to measure how well and how fast a review was screened, and the data checks now keep every
fingerprint whole. The two new features are off unless the project lead switches them on: a review that uses neither
is screened exactly as with 1.3.0.

New:
- The verification sample. When one reviewer screens most records alone, the records they exclude leave the review on
  one person's vote (with the AI second screener behind them: on their vote and the AI's failure to object), and
  nothing else in the review looks at those records again. Once the single reviewer has screened every record, the
  project lead can draw a random sample of them, mixed with some records the single reviewer sent on, for a second
  person to screen without seeing any earlier decision or which kind each record is (Screen titles and abstracts,
  Verification sample). When the sample is finished, any sampled record that person would send on goes to full text:
  the check can only add records to the review. The lead chooses the numbers, the sample is drawn once from a
  recorded seed, and when it is finished the lead sees the share its verifier would have sent on with a 95%
  confidence interval, what became of those records at full text, and what that suggests for the records not
  sampled. The methods text and the AI second screener report say what was done.
  What keeps it blind: nothing the sample finds changes any record, count or list until it is finished; until then the
  pages and files that show what became of records (Review full texts, Extract data, Rate certainty, the audit log and
  every download) are closed to its verifier, the project lead included, as are the few other things that say a record
  reached full text (the data checks of the PDFs, a record's link to a free copy, the AI's answers on known studies);
  nobody, the lead included, is given its votes or figures before it is finished; only the lead is given the numbers
  it was drawn with (from them a reviewer could work out how many of their exclusions the AI kept); and its votes are
  fixed the moment it is finished. Until then its verifier, who has seen nothing, can go back and change any vote; the
  vote on the last record finishes the sample, so the page asks first, and a vote written after that moment is refused
  and never counts. The draw is also kept whole in the lead's audit log, so a sample stays drawn, with the same
  records in the same order, if an older copy of the project's state is ever put back. The draw form says so when the
  person chosen assesses full texts or is the lead, and proposes someone else when there is someone. A sample its
  verifier cannot finish can be ended as it stands by the lead, with a reason; every output then says so.
- Time recording. The project lead can switch it on (Screen titles and abstracts, Studying how the review was
  screened). ReviewDesk then saves with each vote how long the record was on screen, how much of that time the page
  was the tab in front, and the longest stretch of that with no key pressed and the pointer still. Nothing else is
  measured. Every screening page says that recording is on, and each person can leave their own votes out, and change
  their mind, from the page itself. At full text, where the report is often read in another program, the two times
  are the least and the most the assessment took.
- Evaluation data (PRISMA and reports, For a study of the screening itself; project lead only): one .zip with a row
  for every record (who had to screen it, its outcome at each stage, both AI models' answers, their tokens and
  seconds), a row for every vote (with its task, whether it counts for agreement, and its times), the verification
  sample, the dated actions of the review, and the plan and its results as data, with a read-me that describes every
  column. It comes as the team's copy, with initials and the audit log, and as a copy for sharing, in which each
  person is Reviewer A, B or C and the audit log is left out. It is not part of the reporting pack.

Changed:
- Whole fingerprints. The data checks keep a fingerprint of every row of decisions, of every screened record and of
  the review question and criteria, and each computer signs the rows it writes. Each is now a whole SHA-256 or
  HMAC-SHA-256 (256 bits), where earlier versions kept only the first 80 or 64 bits. Nothing in a project begun with
  an earlier version is written again: what that version wrote stays as it is and is checked as before, and what is
  added from now on is whole. A fingerprint of the old length is accepted only where that version can have written
  it: before the first whole one in its file, and beside a signature of the old length. In a project begun with
  1.3.1, nothing shorter than the whole is accepted anywhere.
- One team, one version, and one day to move, the project lead first: ReviewDesk 1.3.0 cannot check whole
  fingerprints, so a project needs ReviewDesk 1.3.1 once anyone has opened it with 1.3.1. When the lead opens it,
  the project file says so, and 1.3.0 no longer opens the project. When anyone opens it, a 1.3.0 that still has the
  project open stops saving in it and says which version to download. Each takes effect on a computer once the sync
  service has brought it the newer files, so on the day everyone closes ReviewDesk first (UPGRADING.md has the
  steps). Nothing is then misread, no vote is cast without its time and no record the verification sample sent on is
  counted as excluded. The Overview says in plain words when a teammate is still on the old version.
- The AI second screener report says when no verification sample has been drawn: there is then no measure of how
  many of the records the single reviewer excluded, and the AI would not keep either, should have gone on.
- "All decisions.csv" names the new stage (verification sample).
- Whoever screens a verification sample cannot be removed from the project, like the people in the screening plan.

Fixed:
- On Windows, a request that ReviewDesk refuses could reach the page as "ReviewDesk is not running" instead of the
  reason for the refusal: a page left open from an earlier session that tried to save something, or a file over
  300 MB. ReviewDesk answers such a request without reading what came with it, and then closed the connection with
  that still unread; Windows breaks such a connection off, and the answer could be lost on its way. ReviewDesk now
  reads what was sent and throws it away before the connection ends (all of it from one of its own pages; at most
  a megabyte, for two seconds, from anything else). Nothing that was saved was ever affected. 1.3.0 behaved the
  same way.
- On a Mac, ReviewDesk could sit for half a minute after it was started before it said it was running and opened the
  browser. Python's web server asks the system for the computer's full name as it starts, and on some networks a
  Mac leaves that question unanswered for that long (35 seconds on GitHub's macOS 26 computers, the first time the
  tests ran there; a third of a second without the question). ReviewDesk never used the name, and no longer asks.
  1.3.0 behaved the same way.
- A row whose fingerprint was not text at all (a number or a list in its place: damage that no version of ReviewDesk
  causes, but another program could) stopped the data checks, and with a list or an object in its place the project
  no longer opened ("Something went wrong inside ReviewDesk"). Such a row is now listed under Data problems like
  any other changed row, and put back in the same ways. 1.3.0 behaved the same way.
- A project file or screening state that could be read, but held something other than ReviewDesk writes there (a
  list, say), gave "Something went wrong" on every page. It is now treated as the damaged file it is: the page says
  which file, and offers the copy kept on this computer. 1.3.0 behaved the same way.
- A damaged project file could not be put back from the page that reports it: "Restore the chosen copy" was answered
  with the same message again. (1.3.0 began to ask the project file which version the project needs before every
  change, and so before this one.) The lead now puts it back as the page says. A damaged project file cannot say
  who the lead is, so the newest copy of it on the computer does: the page offers the copies only to that person.
  (Someone who became the lead by a hand-over has a copy on their own computer once they have saved the settings
  there; until then, OneDrive's version history is the way back.)
- A ReviewDesk that has been told to stop saving in a project, because a newer version has worked on it, no longer
  repairs its own files in that project either. 1.3.0 still did, when it opened the project, and so could give rows
  the newer version had written fingerprints of its own; once the project lead has opened a project with 1.3.1,
  1.3.0 no longer opens it at all.

For developers:
- Static analysis. Every push and pull request is now read by Ruff (the Python: likely mistakes, and bandit's
  security rules) and ESLint (the pages: its recommended rules, and Mozilla's rules against setting markup from
  text), and on GitHub by CodeQL, which follows data through both. Before this release the code was also read once
  with Bandit and Semgrep. None of them found a fault that a user could meet. What they did find, imports and
  variables that were never used, has been removed, and the few lines where a tool is wrong say so, with the reason.
  CONTRIBUTING.md says how to run the tools, and how changes are sent (pull requests).
- A new test in the browser shows that hostile text in a record's title and abstract, and in the team's highlight
  words, stays text on the screening page.
- `tests/test_fingerprints.py` holds the whole fingerprints to their rules, and `helpers.earlier_version()` makes
  ReviewDesk write as 1.3.0 did, so that a project of that time can be tested: nothing in it reported, nothing
  written again. `versions.NEEDS` is 1.3.1, and the lead's first opening of a project with a version that needs
  more than the project file says now writes it there (it used to wait for the lead's next save). The notes at the
  top of `integrity.py` say what the old lengths still allow in a project begun with an earlier version.
- The tests ran on GitHub's own Windows and Mac computers for the first time before this release. Besides the
  half-minute on a Mac (above), three tests turned out to expect what Linux does: the words Windows uses for a
  refused connection, the way a Mac reads the address 0177.0.0.1 (as the public address 177.0.0.1, where Linux
  reads this computer's own and Windows reads none: ReviewDesk asks the system and is right on each), and five test
  names so long that Windows with Python 3.9 could not keep them in an environment variable.
- Python 3.15 is tested. It is the Python that python.org now offers first, so it is what a new Mac user installs:
  the tests on GitHub run on 3.9, 3.14 and 3.15, and the Linux start file also looks for `python3.15` by name.
- docs/dev/DESIGN.md lists the options ReviewDesk can be started with and the environment variables it reads.
  SECURITY.md says which version gets security fixes and how the code is checked.
- `.bestpractices.json` holds the project's answers to the OpenSSF Best Practices criteria, for the badge's website
  to read (docs/dev/RELEASING.md). Its criterion on key lengths is why the fingerprints are now whole.

Known limits of 1.3.1:
- Time on screen is not all the time a review takes: pauses between records, discussion, and reading done outside
  ReviewDesk are not measured. The read-me in the evaluation data says what each number means.
- The verification sample is blind as far as ReviewDesk can make it, not further. Someone who assesses full texts
  may have seen the records mixed into the sample on the full-text list before it was drawn; and anyone with the
  project folder can open its files. Choose a verifier who has not been through that list where the team allows,
  and report who verified.
- A verification sample should be screened on one computer. If its verifier votes on two, which vote came first is
  decided by the two computers' clocks.
- Times add up over a record's viewings only while ReviewDesk stays open in that browser tab: the time a record was on
  screen before the page was reloaded is not kept.
- The interval for the share of records missed is for the sample. With few eligible records in a review, no sample
  short of screening everything twice can show that no study was missed.

## 1.3.0 (10 November 2026)

New:
- A guided set-up. After Create a new project, choose Guide me through it: one topic per screen (the review; protocol
  registration and planned dates; the team; the eligibility criteria; the screening plan; full texts, extraction and
  risk of bias; the AI second screener, if the plan uses it; the searches), each with what the choices mean and an
  example, then a check of anything still missing and a set-up summary to print or download and check against the
  protocol. Project set-up still has every setting on one page, with a Guided set-up button.
- Checks of what is missing. Before each step, the Overview (Before you go on) and the step's own page say what the
  step still needs (Needed) or what is worth a look (Check), why it matters and where to put it right, with a button
  that goes there. A reviewer who is not the project lead sees their own tasks, and what they are waiting for the
  lead to do.
- The steps, in order: a timeline of every step of the review, drawn for your own project, with who does each step,
  the planned dates and where the review is; as a picture (download it as PNG or SVG, or print it) and as a list. A
  strip on the Overview shows where the review is.
- Protocol registration (registry, number, date and link) and planned dates (work starts, final searches, update
  search, review finished) in Project set-up. The registration goes into the methods text and the timeline, and the
  Overview reminds you once the update search date has passed.
- Rate certainty (GRADE). The project lead adds each main outcome and its studies; the two reviewers rate the
  certainty of the evidence for it independently, with ReviewDesk suggesting where the evidence starts and working
  out the result; the lead agrees any differences. Download the evidence profile (Excel) and a paragraph for the
  methods.
- A new full-text decision, Ongoing study, for registered or ongoing studies with no results yet (such as trial
  register entries): listed but not included, shown in the PRISMA diagram as an outflow of the reports assessed (as
  are reports awaiting classification), and needing no PDF.
- The reporting pack: one .zip with every output that is ready, and a "Read me first" file saying what each is for and
  which PRISMA 2020 or PRISMA-S item it answers. New Word documents: the methods text, the search methods appendix
  (PRISMA-S), screening agreement, studies excluded at full text with reasons, studies awaiting classification,
  ongoing studies, reports not retrieved, included studies and their reports, characteristics of included studies,
  risk of bias tables, the summary of findings (GRADE), the AI second screener report, and the PRISMA 2020 checklist
  filled in where ReviewDesk can. Also new: the PRISMA counts (CSV), the AI prompts and settings (text) and a data
  checks report. The flow diagram also downloads as a picture (PNG).
- The set-up walk-through (ReviewDesk set-up walk-through.docx): a step-by-step guide for a project lead setting up a
  first review, with a checklist to tick.
- The risk-of-bias plan, set before the work begins. In set-up, the project lead records which tool is used for each
  study design the review includes; it is needed before screening starts, and once screening has started a change
  needs a reason and is reported as a protocol amendment (PRISMA 2020 item 24c). Reviewers choose a study's design when
  they add an assessment, and the tool follows. ReviewDesk warns when that design does not fit the one extracted:
  a design of the plan that fits better, a design the plan lacks (a cluster trial in a plan with randomised trials
  only), or a tool that does not cover it (ROBINS-I V2 and ROBINS-E are for follow-up studies, not case-control or
  cross-sectional ones; no version of RoB 2 covers cluster crossover trials). Assessments made with a tool the plan no
  longer gives, after an amendment, are flagged and reported, and the data checks report a plan changed outside
  ReviewDesk (put it back, or record the change as an amendment with its reason).
- A general library of risk-of-bias tools, each with its own domains, judgements and rule for the overall judgement:
  RoB 2 (individually randomised, cluster-randomised and crossover trials), ROBINS-I V2 (the domains of its draft of 20
  November 2025), ROBINS-I 2016 (also for before-after and interrupted time series studies), ROBINS-E, QUADAS-2 (with applicability), PROBAST+AI and QUIPS. A
  checklist not built in (such as a JBI checklist, CASP, the Newcastle-Ottawa Scale, MMAT, AMSTAR 2 or ROBIS) can be
  defined by the project lead (its name, version, citation, items and answers) and is then used in the same way.
- GRADE for test accuracy: pre-test probabilities with their sources, and a summary of findings giving true and false
  positives and negatives per 1,000 people tested, as GRADE guidance 21 lays it out.

Changed:
- The PRISMA and reports page lists every output in three groups (tables and figures, methods text, appendix and
  deposit files), each saying what it is for, which PRISMA item it answers, and why it is not ready yet if it is not.
- The next step on the Overview follows the order of the review.
- Initials cannot change once they are saved, because each person's work is recorded under them: to correct someone
  else's, remove them and add them again before they start. Someone removed before screening starts is also taken out
  of the screening plan.
- An AI run is refused, with the reason, when one of the two models has not been chosen.
- Risk of bias: every domain needs a judgement (cluster-randomised trials have their own version of RoB 2, with its
  cluster domain required). An assessment cannot be finished with an overall judgement less severe than its most
  severe domain (each tool's rule); one more severe than every domain needs its reason (except QUADAS-2's "at risk of
  bias" or "concerns regarding applicability" with an unclear domain, which is its rule). If the agreed judgements leave
  the overall too low, the lead is asked to agree it again, and the risk-of-bias tables say so. PROBAST+AI is
  described as the tool for prediction models, including AI models (QUADAS-2 for other test accuracy studies).
- GRADE: imprecision can be rated down three levels; a plain-language statement must match the certainty (or the lead
  records why it is kept); rating up evidence already rated down, or randomised trials, needs a justification;
  ROBINS-I evidence gets GRADE's mapping and a caution when not rated down; test accuracy outcomes are rated one by one
  (GRADE guidance 21); publication bias uses GRADE's words; "little to no difference", with slight and large wordings;
  the evidence profile has participants, importance and footnotes.
- The PRISMA flow diagram: registers searched but not imported are named in a footnote and are in no box (their
  numbers come from the search log); the source and licence of the template are credited; every box adds up.
- Screening agreement: kappa's large-sample interval says when it is cut at 1, and a bootstrap interval (2,000
  resamples, recorded seed) is given beside it; perfect agreement says why no kappa interval can be given and gives one
  for percentage agreement. Specificity has its counts and interval.
- The AI second screener report: accurate numbers of records and answers, and new sections on funding and interests,
  data protection and licensing, and limitations and possible biases, mostly as fill-ins for the authors.
- The methods text cites each risk-of-bias tool and version, gives search dates for registers and other methods, and
  leaves as a highlighted fill-in anything ReviewDesk did not record. The PRISMA 2020 checklist is quoted word for word.
- "What is missing" lists only what can be done now: later steps' items wait until their step can start; a person's own
  unfinished work is Needed, and what they wait for is said as such. A logged database search with no export file is
  Needed before screening starts.
- Set-up describes the screening standards accurately: kappa is not a percentage (at 0.80 or more, agreement on the
  sample is at least 80%, as AMSTAR 2 asks); Cochrane counts two screeners of titles and abstracts as desirable but not
  mandatory; an AI is not a second reviewer.
- When the lead records an agreed risk-of-bias judgement or GRADE rating that differs from what both reviewers gave,
  a reason is needed, and the tables and methods text report it. GRADE evidence from non-randomised studies left at
  high certainty needs an explanation (GRADE guidance 18).
- The reports state only what ReviewDesk recorded: how disagreements were handled, and what was done with records
  outside ReviewDesk, are highlighted for the authors to fill in. With perfect agreement, kappa gets a goodness-of-fit
  confidence interval (Donner and Eliasziw). The studies excluded at full text are listed with full references.
- Pages for first-time and keyboard users: dialogs work with the keyboard alone (including choosing a folder, or typing
  its path); a skip-to-content link; after a vote the next record is announced; correcting a vote moves on to the next
  record; the side bar, the next-step card and the timeline always agree on the next step; Decide together is easy to
  find; a caution when the agreement sample is small; imported files are matched to their search and can be linked
  later; the same person cannot be both screening reviewers; the kappa box accepts 0,80 as well as 0.80. My settings
  and the AI page lead back to the guided set-up, which reopens where you left it. Pages show another reviewer's work
  when you come back to the window.
- Records found by citation searching or hand-searching show on the Overview and the Reports page until they are
  screened, and a logged search of that kind with no export file is a check.
- Once the risk-of-bias plan is recorded, the extraction form's study design lists the plan's designs, so the design
  is chosen already when an assessment is added.
- De-duplication finds an article with a short title again in a later search (same first author, year and journal,
  nothing differing), never merges a reused heading such as "Editorial", and Check duplicates lists likely pairs across
  searches, which can be merged until the later record is screened.
- The user guide covers all of this, with new screenshots of an invented demonstration review.

Fixed:
- Two database exports added by the same person on the same day could, rarely, be given the same name; one export's
  records were then left out without a word. Names are now always different (and the same for two searches logged on
  the same day).
- Scopus CSV exports: the journal is read from Source title (it was read as "Scopus"), page numbers are kept, sub- and
  superscript markup is removed from titles and abstracts (SpO2, not SpO<inf>2</inf>), and "[No abstract available]"
  is no longer taken as an abstract.
- PubMed IDs are read from Ovid MEDLINE and CINAHL exports, and every author from Cochrane CENTRAL exports.
- Duplicates are found when a first author's surname has two words (Te Whāiti, van der Berg, Ní Fhaoláin) and one
  database writes "Surname, Given" and another "Surname Initials".
- A number written with commas between thousands (12,500) keeps its value in the extraction workbook.
- A project in a folder whose path has square brackets in it (such as "MSc [2026-27]") showed no files: no imports,
  decisions or audit entries were found. Folder names are now always taken literally.
- On Windows: names ReviewDesk makes inside a project are shorter, so a project path of up to 163 characters stays
  within Windows' 260-character limit, and opening a project through a longer path warns once and says what to do;
  starting a new version while an older one is still running says so instead of opening the old one; a file open in
  another program (such as a PDF in Adobe Reader) gives a plain message; moving this computer's copies out of the
  roaming profile is safe when two pages start at once and when the profile is on another drive; download names are
  safe for Windows; the console says when another port is used, and how to stop ReviewDesk.
- A reviewer who is not the lead no longer sees the share of records the AI kept, or how many second looks the lead
  has given, while screening alone with the AI behind them.
- A crash while adding records no longer makes the data checks report records added outside ReviewDesk.
- Pages that need many results (the reporting pack, the Reports page) look at the project's files once a request
  instead of thousands of times, which was slow on some computers and in synced folders.
- The AI run log counts a failed first call, and an interrupted run shows the calls it made.
- Suggested initials leave out titles such as Dr and Prof. The timeline's estimate of the agreement sample counts only
  the records it is drawn from.
- An agreed decision is checked like a vote (a full-text "maybe" could once be saved and break the full-text page).

### Found and fixed in the final audit before publication

Version 1.3.0 was checked from the ground up before it was published: seven independent audits (security, the code
that keeps the data, the review logic, the shared folder, upgrading, a first installation, and the pages in the
browser) and a check of the statistics and of every built-in tool against its published source. These are the changes
that came out of it. Anyone who used the test build of 4 November 2026 should move to this one.

Working alone:
- One person can now run a review alone. "One reviewer screens every record" starts straight into screening, with no
  calibration round (the test build drew one that nobody could finish, with no way back), and the checks say what a
  one-person plan still needs: who screens, and "One reviewer" for full-text review. A project the test build left
  stuck in such a round can be moved on by the project lead ("End the calibration round").
- A report is never included, excluded or anything else until the people its plan names have assessed it.
- In a review one person does alone, the steps, the checks and every document say that one person did the work,
  instead of "both reviewers" and "both extractors".

Records and duplicates:
- A record that has been screened can no longer vanish, or lose its votes, when Remove duplicates is chosen again
  (after an update search is added, or after upgrading from an earlier version whose rule differed).
- Different reports that share an identifier are no longer merged: abstracts of one conference supplement with a
  single DOI, an erratum carrying its article's DOI, old Wiley DOIs with angle brackets, and titles that differ only
  in a Greek letter or a sign, or are written in another script. Two records with one DOI or PubMed ID merge only when
  title, pages, first author and type agree; the rest are listed on Check duplicates with the reason.
- An export with an end-of-record line missing, or a CSV file with a quotation mark never closed, no longer merges or
  loses records without a word: it is read record by record, or refused with the line to put right. A file a project
  has already used is never refused afterwards; a note says which line to check.
- The methods summary and the search appendix give the duplicate rule that was recorded when duplicates were removed,
  with the version that used it, and every earlier rule a review under way has used.
- Two exports can no longer be given the same ID on two computers; if two made earlier share one, Remove duplicates
  says so instead of leaving one out.
- A copy of someone's answers or judgements made by hand in the shared folder ("Copy of ...") is no longer read as
  the work of another person.

The shared folder and the data checks:
- The PRISMA flow diagram, the PRISMA counts and the methods summary follow a change to the search log at once (they
  used to stay as they were until ReviewDesk was restarted, so one reporting pack could contradict itself).
- A person's file that disappears from the folder, a screening state or project file that is missing or older than
  one this computer has used or saved, and rows that ReviewDesk would not have written (with no fingerprint, or
  unsigned after signed ones) are now reported under Data problems, with the way to put them right.
- A vote or answer changed after the computer's clock was put back still counts as the latest: within one file the
  order written decides.
- The project lead on two computers: second versions of the screening state, the records and the de-duplication
  record are settled as one choice, and a choice that would make votes count for other records is refused.
- Restoring a copy is safe to interrupt, and warns whenever a newer version exists that this computer has no copy of.
  If this computer's disk is full and no safety copy of a save could be kept, ReviewDesk says so.
- A project folder that is moved or renamed keeps this computer's safety copies. A Mac keeps its computer ID when its
  network name changes. This computer's settings are no longer lost when two things change them at once.
- The README, SECURITY.md and the guides now say exactly what the data checks do: they notice accidents and sync
  faults; they cannot stop a member of the team who sets out to change the files.

Agreement:
- Kappa no longer depends on the two reviewers' computers showing the same time. Each vote records whether its voter
  could already see the comparison, and only votes made before that count (votes saved by earlier versions are judged
  as before, so an existing project's kappa does not change).
- If votes cast before the agreement rule was applied arrive afterwards and give another kappa, the lead sees a check
  and the methods say so; the rule's outcome stays as it was applied.
- A kappa just below its threshold is printed with the decimals that show it (0.795 against 0.80), never as the
  threshold. Who assesses full texts is fixed with the rest of the plan when screening starts.
- The risk-of-bias plan is no longer reported as "changed outside ReviewDesk" because one computer's clock was ahead,
  or because its entry in the audit log went missing (an older copy of the audit file put back by the sync service):
  Data problems then says that the log has no entry for the plan, and the lead can record it in the log again without
  a protocol amendment being written.
- While an agreement round is open, nothing tells a reviewer that the other reviewer has changed a vote.

One team, one version:
- A project records which version of ReviewDesk it needs, and an older one refuses to open it, saying which to
  download. Every page shows a red banner when someone last worked on the project with another version. UPGRADING.md
  says how to move to a new version: everyone on the same day, and nobody goes back.
- For every version from this one on: once anyone in the team has worked on a project with a later version whose
  records an older one cannot read, the older one stops saving in that project, without waiting for the project lead.
  It still opens the project, to read and to download reports, and says who is ahead and which version to download.
  (Every entry in the audit log says which version the project needs.)
- A project whose agreement rule was applied by version 1.0.0 opens its PRISMA and reports page again. A project
  begun earlier that already has risk-of-bias assessments is offered a draft plan from the tools they use.

The AI second screener and other services:
- Two AI runs can no longer start at once. A run stops sending at once when the service refuses the key or the model,
  and Stop stops a run or a search for free copies even when a service answers a byte at a time.
- The AI key is sent only over https, judged by the real host of the address; a reply or a PDF of any size or shape
  can no longer stall or fill the computer; a busy service is reported as busy.
- Starting ReviewDesk never sends its session token to another program that has taken its port.
- The audit log gives the number of the AI's answers that failed only to the project lead, like the other AI figures.
- A request ReviewDesk cannot use is refused in plain words and saves nothing (some were stored in the shared files,
  after which every page failed for everyone).

Reports and documents:
- A database search logged without its export is named in the flow diagram, the methods and the search appendix,
  instead of dropping out of the counts. Accuracy figures are called "pooled" only when results were pooled. A GRADE
  statement kept although it is worded more strongly than its certainty is reported as such, with the team's reason.
- Counts of one and none read correctly everywhere; workbooks open whatever a tool or a table is called; a tool with
  more items than a page holds is printed in parts.
- PRISMA 2020 is credited as its licence asks; the search worksheet's prompts are ReviewDesk's own, citing PRESS 2015;
  the sources of GRADE's wording are credited; NOTICE.txt lists everything the download contains.

The pages:
- "Compare and agree" (risk of bias, extraction, GRADE): saving one agreed value no longer resets the others you had
  chosen, so the next Save records what you chose.
- An answer whose save was refused (the sync service had the file open) is kept, marked as not saved and sent again;
  a study cannot be marked finished while one is unsaved, and leaving the page asks first.
- Long titles and addresses no longer push pages sideways; an automatic accessibility check (axe-core, WCAG 2.2 AA
  rules) finds nothing on the 161 pages and dialogs tested; a page that cannot open says so, with a way back.
- Leaving a page with something typed and not saved asks first; "ReviewDesk is not running" goes by itself once it is
  running again, and no longer tells you to reload a page that is keeping your answers. Someone working alone is
  offered full-text review by one reviewer.
- In a narrow window, or at 200% zoom on a laptop, the vote buttons keep their names whole and the bars above and
  below the record leave room to read it; the guided set-up's Back button is not broken in two on a phone.
- Saving a vote in a large review is about five times faster (about 0.05 seconds with 5,000 votes from each of two
  reviewers, where it took about 0.25).

Starting and downloading:
- The Windows download carries Python 3.14 (3.13 no longer gets Windows security builds). The start files also work
  from the source code. The release is built only from tracked files, and its notes give each download's checksum.
  A message at start-up never cuts a folder's path in two.
- The tests no longer fail on every day but one, run on Windows' own file encoding, and never name a real person.

### Known limits of 1.3.0

Found by the same audit and not changed, because the cure was riskier than the fault on the eve of publication. None
changes a number in a report without a message on screen.

- The data checks notice accidents and sync faults. They cannot stop a member of the team who sets out to change the
  files, and each computer can compare only with what it has seen (SECURITY.md says exactly what is checked).
- When one person votes on the same record from two computers, the computers' clocks decide which vote is the later
  one. Within one computer the order written decides, whatever its clock says.
- The project lead should add exports and remove duplicates on one computer. If a second computer that has not yet
  received the first one's work does it too, ReviewDesk reports two versions of the screening state and the records,
  settles them as one choice and refuses the choice that would make votes count for other records; Remove duplicates
  then adds the exports the version that was kept does not have yet.
- If ReviewDesk is stopped (or the computer loses power) in the instant between saving a PDF and recording it, Data
  problems reports that PDF as put in, replaced or missing "outside ReviewDesk". Add the PDF again, or keep it, from
  the choices shown there.
- The same Add (a search-log entry, a risk-of-bias assessment, an outcome) sent twice makes two entries, for example
  from two browser tabs. Remove the second.
- The search for free copies decides whether it is already running on another computer by comparing that computer's
  clock with this one's.
- An AI run that stops because the service refuses the key may discard one answer it had already received; that
  record is sent again in the next run.
- Versions 1.2 and earlier cannot be made to check anything, so a team must not mix them with 1.3: ReviewDesk 1.3
  can only warn whoever uses 1.3 (a red banner on every page). Someone who goes on using version 1.2 sees no warning:
  that version shows "Something went wrong" once an ongoing study or a newer risk-of-bias tool is recorded, can
  settle a report both reviewers called ongoing as excluded, shows a file linked to its search as an extra, empty
  search-log entry, and lets a risk-of-bias assessment be added with a tool the plan does not give, which 1.3 then
  shows without a flag. (From 1.3 on this cannot happen between versions: see "One team, one version" above.)
- A ROBINS-I V2 judgement of plain "Low risk" recorded with version 1.2 is shown as it was recorded, although 1.3
  offers the tool's own wording for that judgement.
- Not tested on a real installation before publication: a Mac; a team on OneDrive, Dropbox or Google Drive at the
  same time on two real computers (the tests use a stand-in for the sync service); Microsoft Word and Excel opening
  every document (the documents are checked against the file formats and opened with LibreOffice).

## 1.2.1 (28 October 2026)

Security and privacy:
- Requests started by pages from other sites, or by other programs' pages on this computer, are refused even if
  they carry the session token.
- Stricter browser rules: no data may be sent from the page to anywhere else, no other site may frame ReviewDesk,
  and no address is passed on as a referrer.
- Only ordinary web addresses from imported records and search services become links; links that open a new tab
  pass on nothing.
- The open-access PDF finder never fetches from this computer or the local network, including after a redirect or
  when a web address's name changes to a local address between being checked and being connected to.
- The AI service's key is encrypted for the Windows account on Windows, is removed when the service address is
  changed (so it is never sent to another service), and is shown only as its last four characters.
- Requests to the AI service never follow a redirect. Python passes a request's headers, including the key, on to
  the address a redirect gives, even another site or a plain http address; ReviewDesk now stops and says so.
- PDFs are only ever stored and read under record IDs, so no other file can be named by a request.
- On Windows, this computer's copies of project files and PDFs move from the roaming profile (%APPDATA%) to
  %LOCALAPPDATA%, so they are not copied to an organisation's profile servers. The error log moves with them and
  never holds the key.
- Static files can never be served from outside ReviewDesk's own folder.

## 1.2.0 (28 October 2026)

- The AI second screener works with any OpenAI-compatible service. There is no default service address: choose
  yours under My settings (a button fills in the University of Edinburgh's ELM). With ELM, only its
  University-hosted models are used; other services are checked to offer the chosen models before a run starts.
- Service addresses must use https (except on this computer), so a key is never sent unencrypted.
- Warns when project files are stored online only (OneDrive or SharePoint Files On-Demand, or a Mac's File
  Provider), and says how to keep them on the device. The check is made when a project opens, before any file is
  read.
- Clearer messages when a Mac's Python cannot check a service's security certificate.
- The open-access PDF finder follows only web addresses.
- New projects have no library link prefix; set your library's in Project set-up.
- The general extraction form is the built-in form; adapt it to your protocol with Edit the items.
- The user guide and this repository's screenshots use an invented demonstration review.
- Released under the MIT License.

## 1.1.0 (9 October 2026)

- Data checks: every decision, extraction answer and PDF is fingerprinted when saved, and checked when the project
  is opened.
- Full-text PDFs: matching PDFs to records, and help finding open-access copies.
- Data extraction and risk of bias, each with a second reviewer.

## 1.0.0 (8 October 2026)

- First version: searches, import, de-duplication, title and abstract screening with calibration, an agreement
  sample and an AI second screener, full-text review and PRISMA 2020.
