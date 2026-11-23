# Publishing a version of ReviewDesk

The exact steps, in order. The repository is `https://github.com/cphumphries/ReviewDesk`. Run the commands on Linux
or a Mac, or on Windows in Git Bash (it comes with Git for Windows). Windows does not record that the two shell
start files are executable, so step 2 has one more command there.

## 1. Before publishing

1. The version number is the same in `app/reviewdesk/__init__.py`, `CITATION.cff`, `CHANGELOG.md`,
   `launchers/READ ME FIRST.txt` and the title lines of the two guides, and `date-released` in `CITATION.cff` and the
   date in `CHANGELOG.md` are the day of publication.
2. Everything is committed (`git status` shows nothing to commit), the tests pass on the oldest and newest
   Python you have (`python3 -m pytest`), and the static analysis has nothing to report (`ruff check` and
   `npx eslint app/reviewdesk/static`; CONTRIBUTING.md says how to install them).
3. Nothing in the tree names a real person other than the author, or holds a real review's records: tests and guides
   use invented people and reviews only.
4. The Python in the Windows download is the newest release of its series on python.org: `PY_VERSION` and
   `PY_SHA256` in `tools/build_release.py`, and the same two in `NOTICE.txt` (a test fails if they differ).
5. `.bestpractices.json` still says what is true (see "The OpenSSF Best Practices badge" below).
6. Anything that changes how records, decisions or the data checks are kept has had the review that
   CONTRIBUTING.md describes, and its last look found nothing serious.
7. A version number names one build. Once the files of a version have been given to anyone (a release, a teammate,
   a tester), a change to them is a new version with a new number: two builds with one number cannot tell each other
   apart in a shared project, and nothing stops a team from mixing them.

## 2. The first publication: a fresh repository from one commit

The public repository starts from one commit of the final tree, with none of the development history. It is made
private first and opened to the public only when the tests have passed on GitHub's own Windows, Mac and Linux
computers: the test workflow has never run there before the first push, and a first public page with a red cross on
it is avoidable.

1. On github.com, create an empty **private** repository named `ReviewDesk` under `cphumphries`: no README, no
   licence and no .gitignore (choose "None" for each: they are in the tree already, and a repository that starts
   with a commit of its own refuses the push in the next step).
2. Make the one commit from the final tree:

   ```
   git archive --format=tar --prefix=ReviewDesk-public/ HEAD | tar -x -C ..
   cd ../ReviewDesk-public
   git init -b main
   git add -A
   git update-index --chmod=+x launchers/start-reviewdesk.sh "launchers/Start ReviewDesk (Mac).command"   # needed on Windows; harmless elsewhere
   git ls-files -s launchers        # the two shell start files must show 100755
   git commit -m "ReviewDesk 1.3.1"
   git remote add origin https://github.com/cphumphries/ReviewDesk.git
   git push -u origin main
   ```

   Starting from a zip of the source instead of a clone: extract it, open a terminal (on Windows, Git Bash) in the
   folder it makes, and start at `git init`. One of the tests fails on every system if the two shell files are not
   executable in git, so a forgotten `--chmod` shows at step 3. Commit with the address GitHub gives you under
   **Settings > Emails** ("Keep my email address private": it looks like
   `12345678+username@users.noreply.github.com`), so that the commit is linked to your account. Push `main` only,
   never `--all` or `--mirror`.
3. **Wait for both workflows to pass before going on.** Open the **Actions** tab. "tests" runs on Ubuntu, Windows
   and macOS with the oldest and the newest Python ReviewDesk supports and the one in the Windows download, and once
   more on Ubuntu with the browser tests. "static analysis" runs Ruff and ESLint (its CodeQL jobs are skipped while
   the repository is private: GitHub offers CodeQL free to public repositories only). Every job must be green. If
   one fails, fix it, commit, push and wait again. While the repository is private, every run of the tests spends
   about $2 of the Actions allowance that comes with the account ($12 a month with GitHub Free in November 2026),
   and the Mac jobs are two thirds of that: six runs use up the month. So push as few times as the fixes need, and
   do not leave the repository private for long. The Monday runs do nothing while it is private. In a public
   repository every run is free.
4. **Make the repository public**: **Settings > General**, and at the bottom, in the **Danger Zone**, **Change
   visibility** beside "Change repository visibility"; choose public and confirm (the last button is **Make this
   repository public**). Only now can anyone see it. The workflow runs made while it was private become visible
   with it, logs included: if one failed on the way and you would rather it were not on show, delete that run first
   (**Actions**, then the **...** menu beside the run in the list, **Delete workflow run**).
5. **Switch on private vulnerability reporting** (it can be switched on only in a public repository): **Settings**,
   then **Advanced Security** in the "Security and quality" part of the sidebar, then **Enable** beside "Private
   vulnerability reporting". Without it, the **Report a vulnerability** button that SECURITY.md and
   CODE_OF_CONDUCT.md send people to does not exist. On the same page, switch on **Secret Protection** and its
   **Push protection** if they are not on already (both are free for public repositories): GitHub then looks for
   keys in what is pushed, and refuses a push that contains one. (GitHub renames these pages from time to time:
   this one used to be called "Code security".)
6. **Run CodeQL for the first time**: **Actions > static analysis > Run workflow**, on `main`. Making a repository
   public starts nothing by itself. When the two CodeQL jobs are green, open the repository's **Security and
   quality** tab and choose **Code scanning**. The jobs are green whether or not CodeQL found anything, and what it
   found is listed there, where only you can see it. Expect some alerts the first time. CodeQL does not know that
   ReviewDesk answers only its own pages on its own computer, so it reports, for example, that the folder a project
   is in, or the address of the AI service, came from a request: which they did, from the person using it. Go
   through every alert before building the release: fix what is real, and dismiss what is not, choosing the reason
   and writing why. Do not choose GitHub's "default setup" for code scanning as well: this workflow is the set-up,
   and the two together are refused.
7. Optional: under **Settings > General**, add a description and the topics `systematic-review`, `screening`,
   `prisma`; under **About**, tick **Releases**.

For a later version, steps 1, 2 and 4 are replaced by an ordinary commit and push to `main`, and CodeQL runs with
every push; the others are the same.

## 3. Build the download zips

In the clone you pushed from, with nothing uncommitted:

```
python3 tools/build_release.py
```

It refuses to build if a file that goes into the zips has uncommitted changes, downloads the Python embeddable
package from python.org and checks its SHA-256 before writing anything, empties `dist/`, and then writes
`ReviewDesk-<version>.zip`, `ReviewDesk-<version>-windows.zip` and `SHA256SUMS.txt`. Its last line says which Python
and zlib made the zips: copy it into the release notes, so that anyone can rebuild them and compare (the same files,
Python and zlib give the same zips byte for byte; another zlib compresses differently).

The zips can be built on any system and work the same, but they are byte for byte the same only when built from the
same files with the same Python and zlib (a Windows checkout has other line endings in its text files, so build the
zips for a release on one system and say which in the notes).

Check the result: `cd dist && sha256sum -c SHA256SUMS.txt` (on a Mac `shasum -a 256 -c SHA256SUMS.txt`) says OK
for both zips. Unzip each once and start it.

## 4. Make the GitHub Release

1. **Releases > Draft a new release.** Tag: `v1.3.1` (create it on `main`). Title: `ReviewDesk 1.3.1`.
2. Attach the three files from `dist/`: both zips and `SHA256SUMS.txt`.
3. Paste the release notes below, with the two checksums from `SHA256SUMS.txt` and the build line from step 3.
4. Publish. Then download both zips from the release page and check them with the commands in the notes.
5. Check that the README's **Releases** link, its "release notes" link and **Cite this repository** all work.

## The release notes

```
ReviewDesk 1.3.1 is the first public release.

Download
- Windows: ReviewDesk-1.3.1-windows.zip (Python 3.14 included). Right-click it, choose Extract All, then Extract;
  open the ReviewDesk-1.3.1 folder inside the folder it makes, and double-click Start ReviewDesk.bat.
- Mac and Linux: ReviewDesk-1.3.1.zip (needs Python 3.9 or later). The README says how to start it.
"Source code" below is for developers: it has no Python of its own.

Upgrading from an earlier version: extract to a new folder. Everyone in the team upgrades on the same day: close
ReviewDesk on every computer first, and the project lead upgrades and opens the project first. A project that has
been opened with 1.3.1 needs 1.3.1 (ReviewDesk 1.3.0 refuses to open it, or stops saving in it), and no earlier
version should be used on it again. See UPGRADING.md.

Checking your download (optional)
SHA-256:
  <checksum>  ReviewDesk-1.3.1-windows.zip
  <checksum>  ReviewDesk-1.3.1.zip
Windows (PowerShell, in the Downloads folder):  Get-FileHash ReviewDesk-1.3.1-windows.zip
Mac (Terminal, in the Downloads folder):        shasum -a 256 ReviewDesk-1.3.1.zip
Linux (with SHA256SUMS.txt beside the zips):     sha256sum -c --ignore-missing SHA256SUMS.txt
The letters and numbers shown should be exactly those above (PowerShell shows them in capitals).
<the build line: "Built with Python ..., zlib ..., on ...">, by tools/build_release.py.

What it does
ReviewDesk runs the record-handling stages of a systematic review for a small team on their own computers, with the
review in a shared folder: logging the searches, importing and removing duplicates, screening titles and abstracts
(with an optional AI second screener that never excludes a record), reviewing full texts, extracting data, assessing
risk of bias, rating certainty with GRADE, and producing the PRISMA 2020 flow diagram and the reporting files.
README.md says more, and CHANGELOG.md has the history.

For a study of the screening itself (all off unless the project lead switches them on): a verification sample, in
which a second person screens, without sight of any earlier decision, a random sample of the records that left the
review on one reviewer's vote; time recording for screening votes; and the evaluation data, one zip with a row for
every record, vote and sampled record.
```

The two `<checksum>` lines and the build line cannot be written here: they come from the build in step 3.

## 5. The OpenSSF Best Practices badge

The [OpenSSF Best Practices badge](https://www.bestpractices.dev/) is a public self-assessment against a list of
criteria for how an open-source project is run: 67 for the first level, "passing". `.bestpractices.json`, at the top
of the repository, holds ReviewDesk's answer to each of them, with its reason, and the badge's website reads that
file and proposes those answers, so that the form does not have to be filled in by hand.

The first time, once 1.3.1 is public and released (sections 2 to 4):

1. At <https://www.bestpractices.dev/> choose **Get Your Badge Now!**, sign in with GitHub, and pick
   `cphumphries/ReviewDesk`. The site gives the project a number and opens the form for "passing", with the answers
   from `.bestpractices.json` proposed (marked with a robot).
2. Add the badge to the top of README.md, under the title, with the project's number in place of `NUMBER`, and
   commit and push it. Until the badge is earned it shows how far the form has got.

   ```
   [![OpenSSF Best Practices](https://www.bestpractices.dev/projects/NUMBER/badge)](https://www.bestpractices.dev/projects/NUMBER)
   ```

   Do this before saving the last answers: it is the first public change after the release, and the answer to
   "repo_interim" (that work between releases is public) rests on there being one.
3. Read each answer before saving: it is your statement, published under your name. Two of them, under
   **Security > Secure development knowledge**, are about what the project's main developer knows: they say that
   you know the principles of secure design and the common errors, and where ReviewDesk applies each. They are true
   of the person who wrote them, so whoever maintains the project next must answer them again.
4. Save. The page shows the badge as passing when every criterion has an answer and every MUST is met (a SHOULD
   may be unmet with its reason given, and a SUGGESTED may be unmet).

For a later version: when a change makes an answer untrue or out of date (a tool replaced, a document moved), change
`.bestpractices.json` in the same commit; then, on the badge's edit page, choose **Save (and continue)**, which
reads the file again. The site keeps the answers already saved where the two differ, and shows the difference.
