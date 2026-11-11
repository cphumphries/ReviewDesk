# Contributing

Thank you for helping. ReviewDesk is used by people who are not programmers, so the most useful contributions are
often reports of anything confusing, wrong or missing: open an issue saying what you did, what you expected and what
happened (with the version, shown at the bottom of the menu, and your operating system). Please never attach a real
review's records, decisions or project folder; describe the problem instead, or use invented records.

Report security problems privately: see [SECURITY.md](SECURITY.md). Everyone taking part is asked to follow the
[code of conduct](CODE_OF_CONDUCT.md).

## Code

[docs/dev/DESIGN.md](docs/dev/DESIGN.md) says how ReviewDesk is built, where things are and the rules every change
must keep. In short:

- Python standard library only (Python 3.9 or later) and plain JavaScript modules: nothing to install, no build step,
  nothing loaded from the internet.
- Each person's work goes in files of their own in the shared project folder, and only the project lead writes the
  files the whole team relies on; the search worksheet, which anyone may save, is the one shared exception. Every
  row of decisions is fingerprinted and chained to the row before it, so that accidents and sync faults are
  noticed (what this does not protect against is in [SECURITY.md](SECURITY.md)).
- Nobody sees another reviewer's votes or judgements before both have finished the work they share.
- Words on screen are plain, specific British English that say what to do next.
- Add tests for what you change. They never use the network, a real AI service or a real key.
- The tests and the static analysis (both below) pass before a change is merged.

## Sending a change

Changes come in as pull requests on GitHub. For anything larger than a small fix, open an issue first and say what
you would like to change, so that nobody writes something that cannot be taken.

1. Fork the repository and make a branch from `main`.
2. Make the change, with tests for it, keeping to the rules in [docs/dev/DESIGN.md](docs/dev/DESIGN.md).
3. Run the tests and the static analysis on your own computer (see below).
4. Open a pull request to `main`. Its form asks what the change is, why, and how you checked it. GitHub then runs
   the tests on Windows, Mac and Linux, and the static analysis; both must pass.

The maintainer reads every pull request and merges it, asks for changes, or says why not. There is no promised
reply time.

## Running the tests

The tests need pytest. From the top folder of the repository, on a Mac or Linux:

```
python3 -m pip install pytest
python3 -m pytest
```

On Windows, type `python` (or `py`) instead of `python3`, one line at a time. On Debian and Ubuntu, where pip may
refuse to install into the system's Python, use `sudo apt install python3-pytest` instead, or a virtual environment.
`python3 -m pytest -m slow` adds the runs with about 3,000 records (several minutes).

The tests make project folders inside pytest's temporary folder, and ReviewDesk refuses project folders whose path
is too long for Windows. If many tests fail with "That place is too deep in your folders", run them with a short
temporary folder, for example `python3 -m pytest --basetemp=/tmp/rd-tests` (on Windows `--basetemp=C:\rd-tests`).
The tests say so at the end when this is the cause. pytest empties that folder each time, so give it a folder of its
own.

The tests that open ReviewDesk's pages in a browser are skipped unless Playwright and its Chromium are installed
(`python3 -m pip install playwright`, then `python3 -m playwright install chromium`).

## Coverage

To see how much of ReviewDesk's Python the tests run (lines and branches, the ReviewDesk programs that the tests
start included; `.coveragerc` has the settings), with coverage.py 7.10 or later:

```
python3 -m pip install coverage
python3 -m coverage run -m pytest
python3 -m coverage combine
python3 -m coverage report
```

For version 1.3.1 on Linux, with the browser tests, that was 92% of the lines and 85% of the branches. The code that
only Windows runs counts as not run there.

## Static analysis

Two tools read the code without running it, looking for likely mistakes and for the ways a program comes to be
unsafe. GitHub runs both on every push and pull request, and a change must leave both with nothing to report.
Neither is needed to run ReviewDesk or its tests.

**Ruff** reads the Python (`ruff.toml` says which rules, and why not the others):

```
python3 -m pip install ruff
ruff check
```

**ESLint** reads the pages' JavaScript (`eslint.config.mjs`). It needs [Node.js](https://nodejs.org/):

```
npm install --no-save eslint @eslint/js eslint-plugin-no-unsanitized globals
npx eslint app/reviewdesk/static
```

This makes a `node_modules` folder, which git ignores; delete it when you have finished.

On GitHub, CodeQL also follows data through the Python and the JavaScript and reports what it finds to the
maintainer as code scanning alerts (on a pull request, also as a note on the line it is about).
`.github/workflows/analysis.yml` runs all three and names the versions of the tools the project's own checks use.

When a tool is wrong about a line, say so on that line with the reason, rather than switching the rule off
everywhere: `# noqa: S310 - why` for Ruff, and `// eslint-disable-next-line <rule> -- why` for ESLint.

By contributing you agree that your contribution is released under the project's MIT License.
