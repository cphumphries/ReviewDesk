# Security

## Reporting a problem

Please report security problems privately, rather than in a public issue: use **Report a vulnerability** on this
repository's **Security and quality** tab (GitHub used to call it **Security**), or go straight to
[the form](https://github.com/cphumphries/ReviewDesk/security/advisories/new). Include the version, your operating
system and the steps to reproduce the problem.

If there is no **Report a vulnerability** button, open an issue that says only that you have a security problem to
report, with no details, and the maintainer will arrange a private way for you to send them.

## Which version gets fixes

The latest one. A security fix comes out as a new version, and the change log and the release notes say that it is
one; earlier versions get no fixes of their own. [UPGRADING.md](UPGRADING.md) says how to move to a new version.

## How ReviewDesk protects your data

- **It answers only this computer.** The server listens on 127.0.0.1 and refuses any request without the session
  token it gives the browser, any request addressed to another host name (which blocks DNS rebinding), and any
  request that a page from another site or from another program on this computer started (checked from the
  browser's Origin and Sec-Fetch-Site headers).
- **The browser is told to keep pages to themselves.** ReviewDesk's page may load only ReviewDesk's own files and
  run no inline scripts, may not send data anywhere else, and cannot be framed by other sites. No address is ever
  sent to another site as a referrer. Every answer carries `Cache-Control: no-store` and `X-Content-Type-Options:
  nosniff`.
- **Imported text stays text.** Titles, abstracts and other record fields are escaped before they are shown, and
  only ordinary web addresses (http and https) from records or search services ever become links.
- **The PDF finder stays on the public internet.** It follows only web addresses that lead to public internet
  addresses, never this computer or the local network. The address is checked when it is looked up, after every
  redirect, and again once connected (except through a web proxy: see below).
- **The AI key stays with its service.** It is never shown to the browser or written to the shared project folder,
  is encrypted for your Windows account on Windows (in a settings file only your account can read elsewhere), is
  removed if the service address is changed, and is sent only over https (or to an address on this computer
  itself). Requests to the service never follow a redirect, so the key cannot be passed on to another address.
  Errors are logged with any key removed. These promises are about the address and key saved under **My settings**;
  the settings for developers below can replace them.
- **Files are named by ReviewDesk, not by requests.** PDFs are stored and read only under record IDs, and the
  page's own files only from ReviewDesk's folder.
- **The data checks notice accidents.** Every row in the files of decisions (votes, agreed decisions, extraction,
  risk of bias, GRADE ratings, the PDF list and the AI's answers) carries a fingerprint linked to the row before it,
  and each computer keeps its own copy of every such file, and of every PDF, as it last saw it. So when a sync
  service puts back an older file, a file is cut short, or a row that this computer has already seen is edited or
  removed in another program, ReviewDesk reports it under **Data problems** and offers to put it back. When screening
  starts, the project lead's computer also records a fingerprint of every record, every imported file and the
  review question and criteria, and later differences are reported. Each row a computer writes is also signed with
  a secret that never leaves that computer, so it can tell its own rows from rows written by anything else; only
  that computer can check those signatures. Every fingerprint is a SHA-256 and every signature an HMAC-SHA-256,
  kept whole (256 bits). Versions before 1.3.1 kept only the first 80 or 64 bits of each: what they wrote stays as
  it is in the projects they were used in, and is checked as it was, while everything added since is whole. In a
  project begun with 1.3.1 or later, nothing shorter than the whole is accepted.
- **Data sent elsewhere is limited to what the features need**: see "What is sent where" in the README.

### Web proxies

ReviewDesk uses the computer's web proxy settings, as Python reads them: the `HTTPS_PROXY` and `HTTP_PROXY`
environment variables and, on Windows and Mac, the system's proxy settings. A request to an https address stays
encrypted through the proxy, so the proxy sees the AI service's name but not the key or the records sent to it. A
request to an AI service address on this computer itself (http, which only developers use) never goes through the
proxy. The PDF finder still checks each address before it asks for it and after every redirect, but the proxy makes
the connection, so checking the address actually reached is then left to the proxy.

### Settings for developers

These environment variables are for development and testing. Do not set them in normal use.

- `REVIEWDESK_AI_URL`: the AI service address to use instead of the one saved under **My settings**. The saved key
  is then sent to this address. It must still start with `https://` (or be an address on this computer), or nothing
  is sent.
- `ELM_API_KEY`: an AI key to use when none is saved under **My settings**. It is sent to the service address in
  use.
- `REVIEWDESK_OA_BASE`: sends the open-access PDF finder's searches (Unpaywall, Europe PMC, OpenAlex and Semantic
  Scholar) to this one address instead (the tests use a stand-in on the same computer), and switches off the PDF
  finder's checks that an address is on the public internet.

## What it does not protect against

- ReviewDesk does not encrypt the shared project folder itself: anyone who can open that folder can read the
  review. Share it only with the review team, for example through a Microsoft Team with two owners.
- **A team member who sets out to change the files.** The data checks are for accidents and sync faults, not
  against a dishonest member of the team. The name of the person a row belongs to is simply the name of the file it
  is in, and the fingerprints use no secret. So nothing stops someone who can write to the shared folder from
  deliberately adding rows, or a whole file, in another person's name: a computer that has not seen that person's
  real rows has nothing to compare them with, and only the computer that wrote a row can check its signature.
  Choose the team as you would for any shared record of the review, and keep your own backup of the project folder.
- Anyone signed in to your computer account can use ReviewDesk as you, as they could any other program. The
  session key appears in the addresses of PDFs opened in a new tab, so it can be in the browser's history; it works
  only on this computer, and only from ReviewDesk's own pages.

## How the code is checked

- **Tests.** Every change is tested on Windows, Mac and Linux, with the oldest and the newest Python ReviewDesk
  supports. The tests include each protection listed above, and none uses the network, a real AI service or a real
  key.
- **Static analysis.** Every change is also read by tools that look for the ways a program comes to be unsafe: Ruff
  with its security rules for the Python, ESLint with rules against turning text into markup for the pages, and
  GitHub's CodeQL, which follows data through both and reports what it finds to the maintainer.
  [CONTRIBUTING.md](CONTRIBUTING.md) says how to run them.
- **Little to keep up to date.** ReviewDesk uses only Python's standard library and its own JavaScript, and its
  pages load nothing from the internet, so there are no third-party packages whose faults it inherits. The Windows
  download carries a Python of its own ([NOTICE.txt](NOTICE.txt) says which), fetched from python.org when the
  release is built and checked against its published SHA-256. On a Mac or Linux, ReviewDesk runs on the Python
  installed on the computer: keep that up to date as you would any program.
