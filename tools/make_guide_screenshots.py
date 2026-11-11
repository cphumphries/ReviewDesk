"""Make the screenshots for the user guide and the set-up walk-through, from an invented demonstration review.

    python tools/make_guide_screenshots.py [--keep] [--work FOLDER]

It starts ReviewDesk from this repository (app/run_reviewdesk.py) with a private settings folder (REVIEWDESK_HOME)
in a new temporary folder, so your own settings and projects are never touched. It then builds a demonstration
review through ReviewDesk's own HTTP API, step by step, as two people would, and photographs each screen with
Playwright (Chromium) at 1366 x 860, at twice the size, into docs/images/guide/.

The review is invented: "Exercise programmes for adults with knee osteoarthritis", with Dr Sam Patel (the project
lead, SP) and Alex Morgan (the reviewer, AM). Its records, authors, journals and DOIs (10.5555/...) are made up.
The AI second screener talks to a stand-in AI service on this computer (127.0.0.1), so nothing is sent anywhere
and no key is needed. A second, smaller invented project (a test accuracy review of clinical examination for knee
osteoarthritis) gives the pictures of the accuracy figures GRADE needs for a test. Pictures that show a folder's path
show it as it would look on Windows, in the person's own folder under C:\\Users, whatever computer the script
runs on.

Run it again after any change to the screens, then put the new pictures into the user guide and the walk-through
(docs/*.docx): each picture keeps its file name. It needs Python 3.9 or later and Playwright with Chromium
(pip install playwright, then playwright install chromium); with Pillow installed the pictures are stored
with 256 colours, a third of the size. --keep leaves the temporary folder (the project, both
people's settings and ReviewDesk's output) in place for a look afterwards.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "images", "guide")
VIEW = {"width": 1366, "height": 860}

# ================================================================================================ the invented review
TITLE = "Exercise programmes for adults with knee osteoarthritis: a systematic review"
SHORT = "Knee OA exercise review"
FOLDER = "Knee OA exercise review"
QUESTION = ("In adults with knee osteoarthritis, how do structured exercise programmes compare with usual care or no "
            "exercise for pain and physical function?")
LEAD = ("SP", "Dr Sam Patel")
REVIEWER = ("AM", "Alex Morgan")
TEAM_FOLDER = ("Example University", "Knee OA review team - Documents")     # where the Team's files sync to
# The demonstration runs on any computer, but most readers use Windows: pictures that show a folder's path show it as
# Windows would, in each person's own home folder (the path is changed on the screen just before the picture is taken).
WINDOWS_HOME = {"SP": "C:\\Users\\sam.patel", "AM": "C:\\Users\\alex.morgan"}
# A second, smaller demonstration: a test accuracy review, for the accuracy figures that GRADE needs for a test
ACC_TITLE = "Clinical examination for diagnosing knee osteoarthritis: a test accuracy review"
ACC_FOLDER = "Knee OA diagnosis review"
ACC_COMPARISON = "Clinical examination against X-ray"
SEED = 48213907          # the random order: chosen so that the calibration round and the sample hold a few eligible studies

CRITERIA = {
    "include": ["Adults (18 or over) with knee osteoarthritis, diagnosed clinically or on imaging",
                "A structured exercise programme (supervised, group or home-based) lasting at least four weeks",
                "Compared with usual care, no exercise, a waiting list or education alone",
                "Randomised controlled trial",
                "Reports pain or physical function"],
    "exclude": ["Participants recovering from knee replacement or other knee surgery",
                "Exercise given together with another treatment, unless the comparison isolates the exercise",
                "Not randomised (for example cohort studies, case series or reviews)",
                "Animal or laboratory studies"],
    "rules": ["If the abstract does not say whether participants had osteoarthritis, vote Maybe.",
              "Conference abstracts with results are screened like full articles."],
}
FT_REASONS = ["Wrong population", "Wrong intervention", "Wrong comparator", "Wrong study design",
              "No pain or function outcome", "Not in English"]
STRATEGY = """1  exp Osteoarthritis, Knee/
2  ((knee* or tibiofemoral or patellofemoral) adj3 (osteoarthr* or arthros* or gonarthr*)).tw,kf.
3  1 or 2
4  exp Exercise Therapy/ or exp Exercise/
5  (exercis* or strength* or physiotherap* or "physical therapy" or aquatic or "tai chi" or walking).tw,kf.
6  4 or 5
7  randomized controlled trial.pt. or (random* or trial).tw,kf.
8  3 and 6 and 7
9  limit 8 to yr="2000 -Current\""""

FIRST = ["Siobhan", "Ngozi", "Emma", "Omar", "Kenji", "Julie", "Tomas", "Ana", "Marta", "Pieter", "Leila", "Daniel",
         "Grace", "Rafael", "Hannah", "Arjun", "Ingrid", "Mateo", "Chloe", "Yusuf", "Nadia", "Oliver", "Freya", "Kwame"]
LAST = ["Brennan", "Okafor", "Lindqvist", "Haddad", "Takahashi", "Moreau", "Novak", "Fernandes", "Kowalczyk", "Achterberg",
        "Rahman", "Costa", "Whitfield", "Duarte", "Sorensen", "Iyer", "Halvorsen", "Ruiz", "Delaney", "Aydin", "Petrova",
        "Mensah", "Carver", "Nakamura", "Ellison", "Varga", "Quinn", "Abara"]
JOURNALS = ["Journal of Joint Health", "Musculoskeletal Therapy Reports", "Clinical Rehabilitation Letters",
            "Physiotherapy Practice and Research", "Osteoarthritis Studies", "Primary Care Musculoskeletal Journal"]

# role, title, first author (last, first), year, journal: the relevant records (each role is screened as it says)
RELEVANT = [
    ("inc_brennan", "Supervised strengthening exercise for knee osteoarthritis: a randomised controlled trial in primary care",
     "Brennan, Siobhan; Okafor, Ngozi; Achterberg, Pieter", "2023", "Journal of Joint Health"),
    ("inc_brennan_2", "Supervised strengthening exercise for knee osteoarthritis: twelve-month follow-up of a randomised trial",
     "Brennan, Siobhan; Achterberg, Pieter", "2025", "Physiotherapy Practice and Research"),
    ("inc_lindqvist", "Home-based exercise with telephone coaching for knee osteoarthritis: a pragmatic randomised trial",
     "Lindqvist, Emma; Sorensen, Hannah", "2024", "Primary Care Musculoskeletal Journal"),
    ("inc_haddad", "Aquatic exercise compared with usual care for older adults with knee osteoarthritis: a randomised trial",
     "Haddad, Omar; Rahman, Leila; Costa, Rafael", "2022", "Osteoarthritis Studies"),
    ("inc_takahashi", "Group exercise classes for knee osteoarthritis: a cluster randomised trial in community centres",
     "Takahashi, Kenji; Nakamura, Yusuf", "2025", "Clinical Rehabilitation Letters"),
    ("inc_moreau", "Tai chi compared with an education programme for knee osteoarthritis: a randomised trial",
     "Moreau, Julie; Delaney, Grace", "2021", "Journal of Joint Health"),
    ("second_look", "Physiotherapist-led exercise for knee and hip osteoarthritis: a randomised trial with knee subgroup results",
     "Fernandes, Ana; Ruiz, Mateo", "2024", "Musculoskeletal Therapy Reports"),
    ("exc_pop", "A walking programme for adults with chronic knee pain: a randomised trial",
     "Novak, Tomas; Halvorsen, Ingrid", "2023", "Primary Care Musculoskeletal Journal"),
    ("exc_int", "Exercise with a knee brace compared with usual care for knee osteoarthritis: a randomised trial",
     "Kowalczyk, Marta; Varga, Daniel", "2022", "Clinical Rehabilitation Letters"),
    ("exc_design", "Quadriceps exercise for knee osteoarthritis: a pilot study of feasibility and outcomes",
     "Whitfield, Chloe", "2024", "Physiotherapy Practice and Research"),
    ("exc_outcome", "Strength training for knee osteoarthritis: effects on muscle strength in a randomised trial",
     "Iyer, Arjun; Ellison, Freya", "2023", "Osteoarthritis Studies"),
    ("exc_lang", "Programme d'exercices pour la gonarthrose : essai randomisé en soins primaires",
     "Duarte, Rafael; Moreau, Julie", "2022", "Revue de rééducation articulaire"),
    ("awaiting", "Community exercise for knee osteoarthritis: a randomised trial (conference abstract)",
     "Mensah, Kwame", "2025", "Osteoarthritis Studies (abstracts)"),
    ("not_retrieved", "A stepping exercise programme for knee osteoarthritis: a randomised trial",
     "Aydin, Yusuf", "2016", "Regional Physiotherapy Bulletin"),
]
ONGOING = ("ongoing", "Online exercise programme for adults with knee osteoarthritis (ONLINE-KNEE): a randomised trial",
           "", "2026", "ClinicalTrials.gov")
IRRELEVANT = ["Knee arthroscopy for degenerative meniscal tears", "Glucosamine for hip and knee osteoarthritis",
              "Exercise for chronic low back pain", "Hip fracture rehabilitation at home", "Total knee replacement with a cemented implant",
              "Intra-articular steroid injection for knee osteoarthritis", "Weight loss medicines and knee pain",
              "Falls prevention exercise in care homes", "Cardiac rehabilitation after myocardial infarction",
              "Pulmonary rehabilitation for chronic lung disease", "Hand osteoarthritis splints", "Shoulder pain and physiotherapy",
              "Running injuries in recreational athletes", "Topical anti-inflammatory gel for knee pain",
              "Exercise during pregnancy and back pain", "Balance training after stroke", "Yoga for anxiety in students",
              "Vitamin D and muscle strength in older adults", "Footwear for flat feet in children", "Rheumatoid arthritis drug dosing",
              "Ankle sprain bracing in sport", "Exercise and depression in older adults", "Gout flares and diet",
              "Platelet-rich plasma for tennis elbow", "Physical activity counselling for diabetes", "Neck pain and manual therapy",
              "Bone density and walking in women", "Anterior cruciate ligament reconstruction rehabilitation",
              "Fibromyalgia and aerobic exercise", "Hip osteoarthritis surgery waiting times", "Telehealth for heart failure",
              "Knee osteoarthritis in horses", "Cartilage cells grown in the laboratory", "Opioid prescribing for chronic pain",
              "Sit-to-stand desks in offices", "Hydrotherapy for juvenile arthritis", "Osteoporosis screening in men",
              "Acupuncture for headache", "Massage after marathon running", "Back schools for nurses"]
IRR_KIND = ["a randomised trial", "a cohort study", "a systematic review", "a randomised controlled trial",
            "a cross-sectional survey", "a case series"]


def _today(days: int = 0) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _abstract(title: str, relevant: bool, role: str = "") -> str:
    if role == "exc_pop":
        return (f"Background: {title}. Methods: 214 adults with knee pain lasting more than three months were randomised to "
                "a 12-week walking programme or usual care. Whether participants had osteoarthritis was not recorded. "
                "Results: pain fell more with walking. Conclusion: walking may help knee pain.")
    if relevant:
        return (f"Background: {title}. Methods: adults with knee osteoarthritis were randomised to the exercise programme "
                "or to usual care for 12 weeks. Outcomes were pain (0 to 10 scale) and physical function (WOMAC). "
                "Results: pain improved by 1.2 points more with exercise (95% CI 0.6 to 1.8). Conclusion: exercise helped.")
    return f"Background: {title}. Methods: see the full text. Results: the outcomes are reported. Conclusion: more research is needed."


def records() -> dict:
    """The database exports: {name: [record, ...]}, each record with its role in the review."""
    rnd = random.Random(2026)

    def person(k):
        return f"{LAST[k % len(LAST)]}, {FIRST[(k * 7) % len(FIRST)]}"

    rel = []
    for k, (role, title, authors, year, journal) in enumerate(RELEVANT):
        rel.append({"role": role, "title": title, "authors": authors, "year": year, "journal": journal,
                    "doi": f"10.5555/knee.{year}.{k + 101:03d}", "volume": str(8 + k % 5), "issue": str(1 + k % 4),
                    "sp": str(100 + 7 * k), "ep": str(109 + 7 * k), "abstract": _abstract(title, True, role)})
    irr = []
    for k, topic in enumerate(IRRELEVANT * 3):
        kind = IRR_KIND[(k + k // len(IRRELEVANT)) % len(IRR_KIND)]
        year = str(2005 + (k * 3) % 21)
        title = f"{topic}: {kind}" if k < len(IRRELEVANT) else f"{topic} ({['follow-up', 'secondary analysis'][k // len(IRRELEVANT) - 1]}): {kind}"
        irr.append({"role": f"irr_{k}", "title": title, "authors": f"{person(k)}; {person(k + 11)}", "year": year,
                    "journal": rnd.choice(JOURNALS), "doi": f"10.5555/demo.{year}.{k + 300:03d}", "volume": str(3 + k % 9),
                    "issue": str(1 + k % 6), "sp": str(10 + k), "ep": str(18 + k), "abstract": _abstract(title, False)})
    by = {r["role"]: r for r in rel}
    medline = [by[x] for x in ("inc_brennan", "inc_brennan_2", "inc_lindqvist", "inc_haddad", "inc_moreau", "second_look",
                               "exc_pop", "exc_int", "exc_design", "exc_outcome", "not_retrieved")] + irr[:62]
    embase = [dict(by[x]) for x in ("inc_brennan", "inc_lindqvist", "inc_haddad", "inc_takahashi", "exc_lang", "awaiting",
                                    "exc_int")] + [dict(r) for r in irr[:24]] + irr[62:90]
    central = [dict(by[x]) for x in ("inc_brennan", "inc_takahashi", "inc_moreau", "awaiting", "exc_outcome")] + \
        [dict(r) for r in irr[20:36]] + irr[90:104]
    ctgov = [{"role": ONGOING[0], "title": ONGOING[1], "authors": "", "year": ONGOING[3], "journal": ONGOING[4], "doi": "",
              "accession": "NCT09990001", "url": "https://clinicaltrials.gov/study/NCT09990001", "ty": "DBASE",
              "abstract": "Condition: knee osteoarthritis. Intervention: a 12-week online exercise programme with video "
                          "sessions. Comparator: usual care. Primary outcome: knee pain at 12 weeks. Status: recruiting; "
                          "results expected in 2028."}] + \
        [{"role": f"irr_reg_{k}", "title": t, "authors": "", "year": "2025", "journal": "ClinicalTrials.gov", "doi": "",
          "accession": f"NCT0999{k + 2:04d}", "ty": "DBASE", "abstract": f"Condition: {t.split(' for ')[-1]}. Status: recruiting."}
         for k, t in enumerate(["Exercise for chronic neck pain", "Knee replacement with robotic surgery",
                                "Pain education for fibromyalgia", "Hydrotherapy for low back pain"])]
    return {"MEDLINE": medline, "Embase": embase, "Cochrane CENTRAL": central, "ClinicalTrials.gov": ctgov}


def ris(recs: list) -> bytes:
    out = []
    for r in recs:
        out.append(f"TY  - {r.get('ty', 'JOUR')}")
        out.append(f"TI  - {r['title']}")
        for a in [x.strip() for x in (r.get("authors") or "").split(";") if x.strip()]:
            out.append(f"AU  - {a}")
        for tag, k in (("PY", "year"), ("JO", "journal"), ("VL", "volume"), ("IS", "issue"), ("SP", "sp"), ("EP", "ep"),
                       ("DO", "doi"), ("AN", "accession"), ("UR", "url"), ("AB", "abstract")):
            if r.get(k):
                out.append(f"{tag}  - {r[k]}")
        out += ["ER  - ", ""]
    return "\n".join(out).encode("utf-8")


def pdf(title: str, lines: list) -> bytes:
    """A small one-page PDF: a title and some lines of text (the demonstration's full texts)."""
    import textwrap
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")          # noqa: E731
    head = textwrap.wrap(title, 60)
    body = [y for x in lines for y in (textwrap.wrap(x, 88) or [""])]
    text = ("BT /F1 16 Tf 56 770 Td 21 TL " + " ".join(f"({esc(x)}) '" for x in head) + " ET "
            f"BT /F1 11 Tf 56 {750 - 21 * len(head)} Td 16 TL " + " ".join(f"({esc(x)}) '" for x in body) + " ET")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            f"<< /Length {len(text)} >>\nstream\n{text}\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for k, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{k} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


# ================================================================================================ a stand-in AI service
class MockAI:
    """An OpenAI-compatible service on this computer that screens by a few words, for the demonstration only."""
    MODELS = ["example/model-a-large", "example/model-b-medium"]

    def __init__(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send({"data": [{"id": m} for m in mock.MODELS]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                msg = next((m["content"] for m in body.get("messages", []) if m["role"] == "user"), "").lower()
                title = msg.split("title:", 1)[-1].split("\n", 1)[0]
                if "knee" in title and any(w in title for w in ("exercise", "strength", "tai chi", "walking", "stepping",
                                                                "exercices", "programme")):
                    dec, why = "INCLUDE", "exercise programme for knee osteoarthritis"
                elif "knee" in title or "osteoarthritis" in title:
                    dec, why = "MAYBE", "knee condition; exercise unclear"
                else:
                    dec, why = "EXCLUDE", "not about exercise for knee osteoarthritis"
                self._send({"model": body.get("model"), "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps({"decision": dec, "criterion": "none", "reason": why})}}],
                    "usage": {"prompt_tokens": 700, "completion_tokens": 20}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


# ================================================================================================ ReviewDesk itself
class Desk:
    """ReviewDesk started from the repository, with its own private settings folder, and its HTTP API."""

    def __init__(self, work: str, name: str, home: str):
        self.conf = os.path.join(work, "settings-" + name)
        os.makedirs(self.conf, exist_ok=True)
        url_file = os.path.join(work, f"url-{name}.txt")
        env = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
        env.update({"REVIEWDESK_HOME": self.conf, "REVIEWDESK_URL_FILE": url_file, "REVIEWDESK_NO_REOPEN": "1",
                    "HOME": home, "USERPROFILE": home, "OneDriveCommercial": "", "OneDrive": "", "OneDriveConsumer": "",
                    "NO_PROXY": "127.0.0.1,localhost", "PYTHONDONTWRITEBYTECODE": "1"})
        self.log = open(os.path.join(work, f"reviewdesk-{name}.log"), "w")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "run_reviewdesk.py"), "--port", "0",
                                      "--no-browser"], env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if os.path.isfile(url_file) and open(url_file).read().strip():
                break
            if self.proc.poll() is not None:
                sys.exit(f"ReviewDesk did not start: see {self.log.name}")
            time.sleep(0.1)
        self.url = open(url_file).read().strip()
        self.base = self.url.split("/?")[0]
        self.token = self.url.split("t=", 1)[1]

    def call(self, path: str, body=None, raw: bytes | None = None, query: dict | None = None):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"X-Token": self.token}
        if raw is not None:
            data, headers["Content-Type"] = raw, "application/octet-stream"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        else:
            data = None
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            sys.exit(f"ReviewDesk refused {path}: {e.code} {e.read().decode('utf-8', 'replace')[:400]}")

    def get(self, path: str, **query):
        return self.call(path, query=query or None)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


# ================================================================================================ the browser
QUIET = """#toast { display: none !important; } * { caret-color: transparent !important; }"""
MODAL_ONLY = """#app { visibility: hidden !important; } .modal-back { background: transparent !important; }
.modal-back:not(:last-of-type) { visibility: hidden !important; }"""


def launch(pw):
    """Chromium in its full headless mode, which shows PDFs in the page (the plain one leaves them blank)."""
    opts = {"args": ["--lang=en-GB"], "env": {**os.environ, "LANG": "en_GB.UTF-8", "LANGUAGE": "en_GB"}}   # dates as 23/10/2026
    try:
        return pw.chromium.launch(channel="chromium", **opts)
    except Exception:                                   # noqa: BLE001 - an older Playwright: PDFs show blank
        return pw.chromium.launch(**opts)


def shrink(path: str) -> None:
    """Store a screenshot with 256 colours, which looks the same and is about a third of the size (if Pillow is
    installed; otherwise it is left as it is)."""
    try:
        from PIL import Image
    except ImportError:
        return
    im = Image.open(path).convert("RGB")
    small = path + ".tmp"
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(small, format="PNG", optimize=True)
    if os.path.getsize(small) < os.path.getsize(path):
        os.replace(small, path)
    else:
        os.remove(small)


class Camera:
    def __init__(self, browser, desk: Desk, made: list):
        self.ctx = browser.new_context(viewport=VIEW, device_scale_factor=2, locale="en-GB", timezone_id="Europe/London")
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        # a page left with something not saved (a decision shown but not saved, for a picture) asks first: leave it
        self.page.on("dialog", lambda d: d.accept())
        self.desk = desk
        self.made = made
        self.page.goto(desk.url)
        self.page.wait_for_timeout(800)

    def open(self, route: str, wait: int = 1200):
        """Go to a page of the app, freshly drawn."""
        self.page.evaluate("(r) => { location.hash = '#/' + r; }", route)
        self.page.wait_for_timeout(150)
        leave = self.page.locator(".modal button", has_text="Leave without saving")
        if leave.count():
            leave.first.click()
            self.page.wait_for_timeout(300)
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(wait)

    def _box(self, sel: str):
        return self.page.evaluate("""(s) => { const els = [...document.querySelectorAll(s)].filter((e) => e.getClientRects().length);
            if (!els.length) return null; const r = els.map((e) => e.getBoundingClientRect());
            return { x: Math.min(...r.map((b) => b.left)) + scrollX, y: Math.min(...r.map((b) => b.top)) + scrollY,
                     r: Math.max(...r.map((b) => b.right)) + scrollX, b: Math.max(...r.map((b) => b.bottom)) + scrollY }; }""", sel)

    def windows_paths(self, real_home: str, shown_home: str):
        """Show the folder paths in an open dialog as Windows would: the home folder as shown_home, with backslashes,
        and the root of the folder chooser as the C: drive. Only the screen changes, never what ReviewDesk was given."""
        self.page.evaluate("""([real, shown]) => {
            const fix = (s) => s.startsWith(real) ? shown + s.slice(real.length).replace(/\\//g, '\\\\') : s;
            document.querySelectorAll('.modal .crumbs').forEach((el) => { el.textContent = fix(el.textContent); });
            document.querySelectorAll('.modal input[type=text]').forEach((el) => { el.value = fix(el.value); });
            document.querySelectorAll('.modal .row button').forEach((el) => { if (el.textContent === '/') el.textContent = 'C:'; });
        }""", [real_home, shown_home])

    def shot(self, name: str, sel: str = "main.main", top: str | None = None, bottom: str | None = None,
             height: int | None = None, pad: int = 0, vpad: int | None = None, modal: bool = False, tall: bool = False):
        """Save docs/images/guide/<name>.png: the box around everything `sel` matches (from the top of `top` and to the
        bottom of `bottom`, if given), at most `height` CSS pixels tall, with `pad` pixels round it (`vpad` above and
        below, if given). A picture taller than the window is taken in a window made tall enough to hold it, so that
        bars fixed to the bottom of the window stay at the bottom; `tall` makes the window as tall as the page first."""
        pg = self.page
        tag = pg.add_style_tag(content=QUIET + (MODAL_ONLY if modal else ""))
        pg.evaluate("() => { if (document.activeElement) document.activeElement.blur(); scrollTo(0, 0); }")
        pg.mouse.move(1, 1)
        pg.wait_for_timeout(200)
        resized = False
        vpad = pad if vpad is None else vpad
        if tall:
            full = pg.evaluate("() => document.documentElement.scrollHeight")
            if full > VIEW["height"]:
                pg.set_viewport_size({"width": VIEW["width"], "height": int(full) + 2})
                pg.wait_for_timeout(500)
                resized = True
        for _ in range(2):
            b = self._box(sel)
            if b is None:
                raise RuntimeError(f"{name}: nothing on the page matches {sel}")
            if top:
                b["y"] = self._box(top)["y"]
            if bottom:
                b["b"] = self._box(bottom)["b"]
            y0, y1 = max(0, b["y"] - vpad), b["b"] + vpad
            if height:
                y1 = min(y1, y0 + height)
            if y1 <= VIEW["height"] or resized:
                break
            full = pg.evaluate("() => document.documentElement.scrollHeight")
            pg.set_viewport_size({"width": VIEW["width"], "height": int(max(y1, full)) + 2})
            pg.wait_for_timeout(500)
            resized = True
        x0 = max(0, b["x"] - pad)
        clip = {"x": x0, "y": y0, "width": min(b["r"] + pad, VIEW["width"]) - x0, "height": y1 - y0}
        os.makedirs(OUT, exist_ok=True)
        pg.screenshot(path=os.path.join(OUT, name + ".png"), clip=clip)
        shrink(os.path.join(OUT, name + ".png"))
        if resized:
            pg.set_viewport_size(VIEW)
            pg.wait_for_timeout(300)
        tag.evaluate("(t) => t.remove()")
        self.made.append(name)
        bad = [e for e in self.errors if "favicon" not in e]
        print(f"  {name}.png  {int(clip['width'])} x {int(clip['height'])}" + (f"  (console: {bad[:2]})" if bad else ""))
        self.errors.clear()

    def close(self):
        self.ctx.close()



# ================================================================================================ building the review
class Review:
    def __init__(self, desk: Desk):
        self.d = desk
        self.role = {}           # record ID: role
        self.me = ""

    def be(self, who: str):
        if who != self.me:
            self.d.call("/api/me", {"reviewer": who})
            self.me = who

    def learn_records(self):
        recs = self.d.get("/api/records", limit="5000")["records"]
        by_title = {}
        for lst in records().values():
            for r in lst:
                by_title.setdefault(" ".join(r["title"].split()).lower(), r["role"])
        self.role = {r["id"]: by_title.get(" ".join(r["title"].split()).lower(), "") for r in recs}
        return recs

    def ids(self, role: str) -> list:
        return [k for k, v in self.role.items() if v == role]

    def relevant(self, rid: str) -> bool:
        return not self.role.get(rid, "").startswith("irr")

    def screen(self, who: str, task: str, stop_after: int | None = None, flip: tuple = ()):
        """Vote on every open record of a task, as `who` would: Include for the relevant records, Exclude for the rest
        (a few chosen records the other way, so that the two reviewers disagree now and then)."""
        self.be(who)
        items = self.d.get("/api/queue", task=task, set="main")["items"]
        n = 0
        for it in items:
            if it["done"]:
                continue
            if stop_after is not None and n >= stop_after:
                break
            rid = it["id"]
            role = self.role.get(rid, "")
            vote = "include" if self.relevant(rid) else "exclude"
            if role == "exc_pop" and who == "AM":
                vote = "maybe"
            if role == "second_look" and who == "AM" and task == "screen":
                vote = "exclude"
            if rid in flip:
                vote = "maybe" if vote == "exclude" else "exclude"
            self.d.call("/api/decide", {"record": rid, "stage": "cal" if task == "calibration" else "ta", "decision": vote,
                                        "as": who})
            n += 1
        return n


def build_and_shoot(work: str):
    from playwright.sync_api import sync_playwright

    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith(".png"):
            os.remove(os.path.join(OUT, f))
    sam_home = os.path.join(work, "Sam")
    team = os.path.join(sam_home, *TEAM_FOLDER)
    for d in (team, os.path.join(sam_home, "Documents"), os.path.join(sam_home, "Desktop")):
        os.makedirs(d, exist_ok=True)
    mock = MockAI()
    desk = Desk(work, "sam", sam_home)
    print("ReviewDesk is running at", desk.base, "with its settings in", desk.conf)
    R = Review(desk)
    made: list = []
    with sync_playwright() as pw:
        browser = launch(pw)
        cam = Camera(browser, desk, made)
        try:
            shoot_setup(cam, desk, R, team, sam_home)
            shoot_work(cam, desk, R, mock, work, sam_home, browser)
        finally:
            browser.close()
            desk.stop()
            mock.httpd.shutdown()
    print(f"Saved {len(made)} pictures in {os.path.relpath(OUT, ROOT)}")


def shoot_setup(cam: Camera, desk: Desk, R: Review, team: str, home: str):
    pg = cam.page
    print("The start screen and a new project")
    cam.open("start", 800)
    cam.shot("start", sel="main.page", pad=24)
    pg.get_by_role("button", name="Create a new project").click()
    pg.wait_for_timeout(400)
    pg.locator(".modal input").nth(0).fill(TITLE)
    pg.locator(".modal input").nth(1).fill(LEAD[1])
    pg.locator(".modal input").nth(2).fill(LEAD[0])
    pg.locator(".modal button", has_text="Choose…").click()
    pg.wait_for_timeout(500)
    pg.locator(".modal .row button", has_text="Home").last.click()
    pg.wait_for_timeout(400)
    pg.locator(".modal .folders button", has_text=TEAM_FOLDER[0]).click()
    pg.wait_for_timeout(400)
    pg.locator(".modal .folders button", has_text=TEAM_FOLDER[1]).click()
    pg.wait_for_timeout(400)
    cam.windows_paths(home, WINDOWS_HOME[LEAD[0]])
    cam.shot("create-folder", sel=".modal-back:last-of-type .modal", modal=True)
    pg.locator(".modal button", has_text="Use this folder").click()
    pg.wait_for_timeout(400)
    pg.locator(".modal input").nth(4).fill(FOLDER)
    # the path typed or pasted into "Where to put it", as it would be on Windows (Copy as path in File Explorer)
    where = pg.locator("#cp-parent")
    real = where.input_value()
    where.fill(WINDOWS_HOME[LEAD[0]] + "\\" + "\\".join(TEAM_FOLDER))
    cam.shot("create-project", sel=".modal", modal=True)
    where.fill(real)
    pg.locator(".modal button", has_text="Create project").click()
    pg.wait_for_timeout(900)
    cam.shot("setup-choice", sel=".modal", modal=True)
    pg.locator(".modal button", has_text="Guide me through it").click()
    pg.wait_for_timeout(1200)

    print("The guided set-up")
    # (1) the review, typed in as a lead would; the whole screen, to show how every screen is laid out
    pg.locator(".wz select").select_option("intervention")
    pg.locator(".wz input[type=text]").nth(1).fill(SHORT)
    pg.locator(".wz textarea").fill(QUESTION)
    cam.shot("wizard-1-review", bottom=".wz-actions", tall=True, vpad=16)
    pg.get_by_role("button", name="Save and continue").click()
    pg.wait_for_timeout(1200)
    # the rest are filled in through the API, then photographed: the parts that matter on each screen
    desk.call("/api/settings", {"changes": {
        "registration": {"registry": "PROSPERO", "id": "CRD42026000000", "date": _today(-12),
                         "url": "https://www.crd.york.ac.uk/prospero/"},
        "dates": {"start": _today(-30), "searches": _today(14), "update_search": _today(150), "end": _today(240)}}})
    cam.open("wizard/registration")
    cam.shot("wizard-2-registration", sel=".wz-body", tall=True)
    desk.call("/api/settings", {"changes": {"reviewers": [{"id": LEAD[0], "name": LEAD[1], "role": "lead"},
                                                          {"id": REVIEWER[0], "name": REVIEWER[1], "role": "reviewer"}]}})
    cam.open("wizard/team")
    cam.shot("wizard-3-team", sel=".wz-body > .panel:nth-child(2)", tall=True)
    desk.call("/api/settings", {"changes": {"criteria": CRITERIA, "ft_reasons": FT_REASONS,
                                            "highlight": {"include": ["knee", "osteoarthr*", "exercis*", "random*"],
                                                          "exclude": ["replacement", "arthroplasty", "cohort", "mice"]}}})
    cam.open("wizard/criteria")
    cam.shot("wizard-4-criteria", sel=".wz-body .panel", bottom=".wz-body .panel > .wzf:nth-child(2)", vpad=12, tall=True)
    desk.call("/api/settings", {"changes": {"screening": {
        "mode": "sample_then_single", "reviewers": [LEAD[0], REVIEWER[0]], "single_reviewer": REVIEWER[0],
        "second_reviewer": LEAD[0], "calibration_size": 20, "sample_fraction": 0.2, "kappa_threshold": 0.8,
        "ai_second_screener": True, "ai_keep_rate_stop": 0.5, "conflicts_at_title_abstract": "forward", "seed": SEED}}})
    cam.open("wizard/plan")
    cam.shot("wizard-5-plan-how", sel=".wz-body .panel > fieldset", tall=True)
    cam.shot("wizard-5-plan-numbers", sel=".wz-body .panel", top=".wz-body .panel .grid2",
             bottom=".wz-body fieldset:has(input[type=checkbox])", vpad=18, tall=True)
    desk.call("/api/settings", {"changes": {"fulltext": {"mode": "dual", "library_prefix": "",
                                                         "disagreement_default": "The study is included and the disagreement is reported."}}})
    x = desk.get("/api/extract")
    desk.call("/api/extract/form", {"form": {**x["form_full"], "first": REVIEWER[0], "second": LEAD[0]}})
    cam.open("wizard/fulltext")
    cam.shot("wizard-6-fulltext", sel=".wz-body", bottom=".wz-body > .panel:nth-child(2)", vpad=8, tall=True)
    # the risk-of-bias plan, ticked on the screen as the lead would: randomised trials and cluster-randomised trials,
    # each with its version of RoB 2 (the tool recommended for it is chosen already); Save and continue records it
    for design in ("rct", "cluster_rct"):
        pg.locator(f'#wz-robplan .rp-row[data-row="{design}"] input[type=checkbox]').check()
        pg.wait_for_timeout(300)
    cam.shot("wizard-6-robplan", sel="#wz-robplan", tall=True)
    pg.get_by_role("button", name="Save and continue").click()
    pg.wait_for_timeout(1500)
    if not desk.get("/api/rob/plan").get("recorded"):
        sys.exit("The risk-of-bias plan was not recorded by Save and continue.")
    rel = {r[0]: r for r in RELEVANT}
    desk.call("/api/settings", {"changes": {"known_studies": [{"title": rel["inc_brennan"][1]}, {"title": rel["inc_haddad"][1]}]}})
    cam.open("wizard/ai")
    cam.shot("wizard-7-ai", sel=".wz-body", tall=True)
    ws = desk.get("/api/worksheet")
    desk.call("/api/worksheet", {**ws, "question_type": "intervention", "databases": ["medline", "embase", "central", "registers"],
                                 "registers": ["ClinicalTrials.gov", "WHO ICTRP"],
                                 "other_sources": ["Reference lists of the included studies"]})
    cam.open("wizard/searches")
    cam.shot("wizard-8-searches", sel=".wz-body", bottom=".wz-body .dbcard:nth-child(4)", vpad=10, tall=True)
    cam.shot("wizard-8-registers", sel=".wz-body > .panel:nth-child(2)", tall=True)
    cam.open("wizard/finish", 1500)
    cam.shot("wizard-9-finish", sel=".wz-body", height=820, vpad=8, tall=True)
    cam.open("wizard/summary", 1200)
    cam.shot("wizard-summary", sel="main.main .page", height=760, pad=0, tall=True)

    print("The Overview and the timeline")
    cam.open("overview", 1800)
    cam.shot("overview-before", top=".page-head", bottom=".tl-strip")
    cam.open("timeline", 1800)
    cam.shot("timeline-page", height=1000)


def shoot_work(cam: Camera, desk: Desk, R: Review, mock: MockAI, work: str, home: str, browser):
    pg = cam.page
    print("The second reviewer joins (a second computer, with its own settings)")
    alex = Desk(work, "alex", home)
    try:
        acam = Camera(browser, alex, cam.made)
        apg = acam.page
        apg.get_by_role("button", name="Open a project").click()
        apg.wait_for_timeout(600)
        apg.locator(".modal .row button", has_text="Home").last.click()
        apg.wait_for_timeout(400)
        for folder in (*TEAM_FOLDER, FOLDER):
            apg.locator(".modal .folders button", has_text=folder).click()
            apg.wait_for_timeout(400)
        acam.windows_paths(home, WINDOWS_HOME[REVIEWER[0]])
        acam.shot("join-open", sel=".modal", modal=True)
        apg.locator(".modal button", has_text="Open this project").click()
        apg.wait_for_timeout(1500)
        acam.shot("join-who", sel=".modal", modal=True)
        acam.close()
    finally:
        alex.stop()

    print("Searches and imports")
    R.be(LEAD[0])
    exports = records()
    log = {}
    plan = [("MEDLINE", "Ovid", "MEDLINE", "1946 to the present", "Humans; 2000 to the present"),
            ("Embase", "Ovid", "Embase", "1974 to the present", "Humans; 2000 to the present"),
            ("Cochrane CENTRAL", "Cochrane Library", "Cochrane CENTRAL", "Issue 11 of 12, November 2026", "None"),
            ("ClinicalTrials.gov", "clinicaltrials.gov", "ClinicalTrials.gov", "All years", "None")]
    for name, platform, db, cover, limits in plan:
        e = desk.call("/api/searchlog", {"database": db, "platform": platform, "date": _today(-2), "results": str(len(exports[name])),
                                         "coverage": cover, "limits": limits,
                                         "string": STRATEGY if db != "ClinicalTrials.gov" else
                                         "Condition: knee osteoarthritis; Intervention: exercise OR physiotherapy OR training"})
        log[name] = e["id"]
    R.be(REVIEWER[0])
    for name, platform, db, _, _ in plan:
        fname = {"MEDLINE": "medline.ris", "Embase": "embase.ris", "Cochrane CENTRAL": "central.ris",
                 "ClinicalTrials.gov": "clinicaltrials.ris"}[name]
        desk.call("/api/imports/upload", raw=ris(exports[name]),
                  query={"filename": fname, "set": "main", "database": db, "platform": platform, "search_date": _today(-2),
                         "search_id": log[name], "notes": "", "part": ""})
    R.be(LEAD[0])
    cam.open("duplicates", 1800)                        # a step's page with something needed: duplicates not yet removed
    cam.shot("banner-duplicates", top=".page-head", bottom=".rd-banner")
    cam.open("import", 1500)
    cam.shot("import", top=".page-head", bottom=".panel:last-of-type", vpad=12)
    desk.call("/api/imports/process", {})
    R.learn_records()

    print("The AI second screener (a stand-in service on this computer)")
    desk.call("/api/user", {"elm_url": mock.url, "elm_key": "demo-key-not-a-real-one"})
    desk.call("/api/ai/config", {"passes": {"A": {"model": MockAI.MODELS[0]}, "B": {"model": MockAI.MODELS[1]}}, "concurrency": 4})

    print("Screening: calibration, the agreement sample")
    desk.call("/api/screening/start", {"set": "main"})
    R.learn_records()
    print("The risk-of-bias plan in Project set-up, now that screening has started; a tool the lead defines")
    cam.open("setup/robplan", 1800)
    cam.shot("setup-robplan", sel="#setup-robplan", tall=True)
    # Define a tool: filled in for an invented checklist (the knee review needs none), then cancelled
    pg.locator("#setup-robplan button", has_text="Define a tool").click()
    pg.wait_for_timeout(500)
    pg.locator("#rt-name").fill("Short appraisal checklist for qualitative studies")
    pg.locator("#rt-version").fill("2, 2025")
    pg.locator("#rt-cite").fill("Achterberg P, Iyer A. A short appraisal checklist for qualitative studies. "
                                "Physiotherapy Practice and Research 2025;14:12-19")
    pg.locator("#rt-link").fill("https://example.org/qualitative-checklist")
    labels = ["Aims clearly stated", "Qualitative method suits the aims", "Recruitment described",
              "Data collection described", "Researcher's influence considered", "Findings supported by the data"]
    for k, label in enumerate(labels):
        if k >= pg.locator(".modal ol.rp-items input").count():
            pg.locator(".modal button", has_text="Add an item").click()
            pg.wait_for_timeout(150)
        pg.locator(".modal ol.rp-items input").nth(k).fill(label)
    pg.locator(".modal label", has_text="The tool has an overall judgement").click()
    pg.wait_for_timeout(300)
    rule = pg.locator(".modal label", has_text="never less severe than the worst item").locator("input")
    if not rule.is_checked():                           # ticked at first: the picture shows it as the lead finds it
        sys.exit("Define a tool: the rule for the overall judgement should be ticked at first.")
    cam.shot("rob-define-tool", sel=".modal", modal=True, tall=True)
    pg.locator(".modal button", has_text="Cancel").click()
    pg.wait_for_timeout(300)
    run_ai(desk)
    cal = [x["id"] for x in desk.get("/api/queue", task="calibration", set="main")["items"]]
    odd = [r for r in cal if not R.relevant(r)][:1]
    R.screen(LEAD[0], "calibration")
    R.screen(REVIEWER[0], "calibration", flip=tuple(odd))
    R.be(LEAD[0])
    desk.call("/api/screening/calibration", {"set": "main"})
    sample = [x["id"] for x in desk.get("/api/queue", task="sample", set="main")["items"]]
    odd = [r for r in sample if not R.relevant(r)][:1]
    R.screen(LEAD[0], "sample")
    R.screen(REVIEWER[0], "sample", stop_after=len(sample) // 2, flip=tuple(odd))
    R.be(REVIEWER[0])                                   # the screening page, as the reviewer sees it during the sample
    cam.open("screen/sample/main", 1500)
    cam.shot("screening-record", sel="main.screen")
    R.be(LEAD[0])
    cam.open("screening", 1800)
    cam.shot("screening-sample", height=860)
    R.screen(REVIEWER[0], "sample", flip=tuple(odd))
    R.be(LEAD[0])
    cam.open("screening", 1800)
    cam.shot("screening-sample-done", top=".page-head", bottom=".page > .panel:last-of-type")
    pg.get_by_role("button", name="Apply the agreement rule").click()       # the confirmation, with the caution
    pg.wait_for_timeout(500)
    cam.shot("screening-apply", sel=".modal", modal=True)
    pg.locator(".modal button", has_text="Cancel").click()
    pg.wait_for_timeout(300)
    desk.call("/api/screening/apply", {"set": "main"})

    print("The rest of the screening, with the AI behind one reviewer")
    for _ in range(3):
        R.screen(REVIEWER[0], "screen")
        R.screen(LEAD[0], "second")
    R.be(LEAD[0])

    print("Full texts")
    for row in desk.get("/api/fulltext")["rows"]:
        role = R.role.get(row["id"], "")
        if role in ("not_retrieved", "ongoing", "awaiting", "exc_lang"):
            continue                     # no PDF: the report not found, the register entry, and two still to find
        desk.call("/api/fulltext/upload", query={"record": row["id"]}, raw=pdf(row["title"], [
            "DEMONSTRATION FULL TEXT, invented for the ReviewDesk guides", "", f"{row['authors']} ({row['year']}). {row['journal']}.", "",
            "Abstract", "Background: knee osteoarthritis is a common cause of pain and disability in adults.",
            "Methods: adults with knee osteoarthritis were randomised to the exercise programme or to usual care for 12 "
            "weeks. Pain was measured on a 0 to 10 scale and physical function with the WOMAC questionnaire.",
            "Results: see Table 2 for pain and physical function at 12 weeks.", "",
            "Table 2. Pain (0 to 10) at 12 weeks: exercise 3.1 (SD 1.9); usual care 4.3 (SD 2.0)."]))
    cam.open("fulltext", 1800)
    cam.shot("fulltext-pdfs", sel="section.getpdfs")
    decisions = {"inc_brennan": ("include", "", "Brennan 2023"), "inc_brennan_2": ("include", "", "Brennan 2023"),
                 "inc_lindqvist": ("include", "", "Lindqvist 2024"), "inc_haddad": ("include", "", "Haddad 2022"),
                 "inc_takahashi": ("include", "", "Takahashi 2025"), "inc_moreau": ("include", "", "Moreau 2021"),
                 "second_look": ("include", "", "Fernandes 2024"),
                 "exc_pop": ("exclude", "Wrong population", ""), "exc_int": ("exclude", "Wrong intervention", ""),
                 "exc_design": ("exclude", "Wrong study design", ""), "exc_outcome": ("exclude", "No pain or function outcome", ""),
                 "exc_lang": ("exclude", "Not in English", ""), "awaiting": ("awaiting", "", ""),
                 "not_retrieved": ("not_retrieved", "", ""), "ongoing": ("ongoing", "", "")}
    notes = {"awaiting": "Authors e-mailed for the full results; no reply yet",
             "not_retrieved": "Not held by the library; the inter-library loan request failed",
             "ongoing": "Registered and recruiting; results expected in 2028"}
    R.be(REVIEWER[0])                                   # the decision page, with Ongoing study chosen for the register entry
    cam.open("ft/" + R.ids("ongoing")[0], 1500)
    pg.locator("aside.side button", has_text="Ongoing study").click()
    pg.locator("aside.side textarea").fill(notes["ongoing"])
    pg.wait_for_timeout(300)
    cam.shot("fulltext-ongoing", sel="main.screen")
    for who in (REVIEWER[0], LEAD[0]):
        R.be(who)
        for row in desk.get("/api/fulltext")["rows"]:
            role = R.role.get(row["id"], "")
            d, reason, study = decisions.get(role, ("exclude", "Wrong population", ""))
            if role == "exc_design" and who == LEAD[0]:
                d, reason = "exclude", "Wrong comparator"           # one disagreement on the reason, settled together
            desk.call("/api/decide", {"record": row["id"], "stage": "ft", "decision": d, "reason": reason,
                                      "note": notes.get(role, ""), "study": study, "as": who})
    R.be(LEAD[0])                                       # the disagreement, waiting for the two to decide together
    cam.open("fulltext", 1800)
    pg.select_option("#ft-filter", "conflict")
    pg.wait_for_timeout(300)
    cam.shot("fulltext-decide", top=".page > .row:has(> button.primary)", bottom=".page .tablewrap", vpad=8)
    pg.locator(".page > .row button.primary", has_text="Decide together").click()
    pg.wait_for_timeout(800)
    cam.shot("fulltext-decide-dialog", sel=".modal", modal=True, tall=True)
    pg.locator(".modal button", has_text="Done").click()
    pg.wait_for_timeout(300)
    for rid in R.ids("exc_design"):
        desk.call("/api/resolve", {"record": rid, "stage": "ft", "decision": "exclude", "reason": "Wrong study design",
                                   "note": "Not randomised: a single-group pilot study", "study": "", "as": LEAD[0]})

    print("Data extraction and risk of bias")
    x = desk.get("/api/extract")
    key = {s["label"]: s["key"] for s in x["studies"]}
    design_opts = next(f for sec in x["form_full"]["sections"] for f in sec["fields"] if f["id"] == "design").get("options") or []

    def design(v):
        return next((o for o in design_opts if o.lower().startswith(v.lower()[:9])), design_opts[0] if design_opts else v)

    def outcome(eff, n):
        return [{"outcome": "Pain (0 to 10)", "measure": "Mean difference at 12 weeks", "group": "Exercise",
                 "n": str(int(n) // 2), "result": "3.1 (SD 1.9)", "effect": eff, "where": "Table 2"}]
    values = {"Brennan 2023": ("United Kingdom", "Randomised", "Six general practices", "240", "-1.2 (-1.8 to -0.6)"),
              "Lindqvist 2024": ("Sweden", "Randomised", "Primary care, at home", "186", "-0.9 (-1.5 to -0.3)"),
              "Haddad 2022": ("Lebanon", "Randomised", "Two hospital pools", "120", "-1.4 (-2.2 to -0.6)"),
              "Moreau 2021": ("France", "Randomised", "One university hospital", "96", "-1.0 (-1.9 to -0.1)"),
              "Takahashi 2025": ("Japan", "Cluster", "Twelve community centres", "412", "-0.7 (-1.2 to -0.2)"),
              "Fernandes 2024": ("Portugal", "Randomised", "Physiotherapy clinics", "158", "-0.8 (-1.6 to 0.0)")}
    done_both = ["Brennan 2023", "Lindqvist 2024", "Haddad 2022", "Moreau 2021"]
    for label, (country, des, setting, n, eff) in values.items():
        k = key.get(label)
        if not k:
            continue
        R.be(REVIEWER[0])
        for fid, v in (("author_year", label), ("country", country), ("design", design(des)), ("setting", setting), ("n", n),
                       ("population", "Adults aged 45 or over with knee osteoarthritis (clinical criteria)"),
                       ("intervention", "Exercise programme, two sessions a week for 12 weeks"),
                       ("comparator", "Usual care from the general practitioner"),
                       ("funding", "Public research funding; no conflicts declared")):
            desk.call("/api/extract/value", {"key": k, "f": fid, "v": v, "pg": "p. 3"})
        desk.call("/api/extract/value", {"key": k, "f": "outcomes", "v": outcome(eff, n), "pg": "Table 2"})
        desk.call("/api/extract/done", {"key": k, "done": True})
        if label in done_both:
            R.be(LEAD[0])
            desk.call("/api/extract/value", {"key": k, "f": "outcomes", "v": outcome(eff, n), "pg": "Table 2"})
            st = desk.get("/api/extract/study", key=k)["state"]
            desk.call("/api/extract/done", {"key": k, "done": True, "seen": st.get("seen", "")})
    R.be(LEAD[0])                                       # the second extractor checks a study the first has finished
    cam.open("x/" + key["Takahashi 2025"], 2500)
    cam.shot("extract-study", sel="main.screen")
    for label in done_both:                             # risk of bias: RoB 2 for pain at 12 weeks, both reviewers
        k = key[label]
        desk.call("/api/rob/define", {"key": k, "design": "rct", "label": "Pain at 12 weeks: exercise compared with usual care"})
        st = desk.get("/api/extract/study", key=k)
        aid = st["rob"][0]["a"]
        for who in (LEAD[0], REVIEWER[0]):
            R.be(who)
            for code, _name in st["tools"]["rob2"]["domains"]:
                j = "Not applicable" if code == "D1b" else "Some concerns" if code in ("D2", "O") and label in ("Haddad 2022", "Moreau 2021") else "Low risk"
                if code in ("D4", "O") and label == "Moreau 2021" and who == REVIEWER[0]:
                    j = "High risk"
                desk.call("/api/rob/judge", {"key": k, "a": aid, "d": code, "j": j,
                                             "q": "Participants could not be blinded to exercise; pain was self-reported (p. 4)"
                                             if code in ("D2", "D4") else "Computer-generated sequence, central allocation (p. 3)"})
            desk.call("/api/rob/done", {"key": k, "a": aid, "done": True})
        R.be(LEAD[0])
        if label == "Moreau 2021":
            desk.call("/api/rob/agree", {"key": k, "a": aid, "d": "D4", "j": "Some concerns"})
            desk.call("/api/rob/agree", {"key": k, "a": aid, "d": "O", "j": "Some concerns"})

    R.be(REVIEWER[0])                                   # adding an assessment: the design extraction recorded is chosen
    cam.open("x/" + key["Takahashi 2025"] + "/rob", 2500)
    pg.locator("#rob-label").fill("Pain at 12 weeks")
    cam.shot("rob-add", sel=".xtab .panel:has(#rob-design)", vpad=6)
    R.be(LEAD[0])

    print("Rating certainty (GRADE)")
    R.be(LEAD[0])
    ks = [key[x] for x in done_both]
    o1 = desk.call("/api/certainty/define", {"outcome": {
        "name": "Pain at the end of treatment", "importance": "critical", "kind": "effect",
        "comparison": "Exercise programmes compared with usual care", "measure": "Pain on a 0 to 10 scale; mean difference",
        "synthesis": "pooled", "participants": "", "studies": ks}})["o"]
    desk.call("/api/certainty/define", {"outcome": {
        "name": "Physical function at the end of treatment", "importance": "critical", "kind": "effect",
        "comparison": "Exercise programmes compared with usual care", "measure": "WOMAC function; standardised mean difference",
        "synthesis": "narrative", "participants": "", "studies": ks[:3]}})
    effect = "4 studies, 642 participants; mean difference -1.1 points (95% CI -1.6 to -0.6)"
    statement = "Exercise programmes probably reduce pain slightly at the end of treatment, compared with usual care."
    ratings = [("start", "rct", ""), ("rob", -1, "Participants and therapists knew who exercised, and pain was self-reported."),
               ("inconsistency", 0, ""), ("indirectness", 0, ""), ("imprecision", 0, ""), ("publication", 0, ""),
               ("effect", effect, ""), ("statement", statement, "")]
    for f, v, xx in ratings[:2]:
        desk.call("/api/certainty/rate", {"o": o1, "f": f, "v": v, "x": xx, "as": LEAD[0]})
    cam.open("c/" + o1, 1800)
    cam.shot("certainty-outcome", sel="main.screen")
    for who in (LEAD[0], REVIEWER[0]):
        R.be(who)
        for f, v, xx in ratings[2 if who == LEAD[0] else 0:]:
            desk.call("/api/certainty/rate", {"o": o1, "f": f, "v": v, "x": xx, "as": who})
        desk.call("/api/certainty/done", {"o": o1, "done": True, "as": who})
    R.be(LEAD[0])
    cam.open("certainty", 1800)
    cam.shot("certainty-list", top=".page-head", bottom=".tablewrap")

    print("Reports")
    cam.open("report", 2500)
    cam.shot("reports", top=".page-head", bottom=".pack")
    cam.shot("reports-prisma", sel="section.panel:has(#flow-h)", height=1000)
    cam.shot("reports-list", sel=".rep-group:has(#rep-appendix)", height=900)

    print("The accuracy figures for a test (a second demonstration: a test accuracy review)")
    team = os.path.join(home, *TEAM_FOLDER)
    desk.call("/api/project/create", {"parent": team, "name": ACC_FOLDER, "title": ACC_TITLE, "lead_name": LEAD[1],
                                      "lead_id": LEAD[0]})
    R.me = LEAD[0]
    desk.call("/api/settings", {"changes": {"reviewers": [{"id": LEAD[0], "name": LEAD[1], "role": "lead"},
                                                          {"id": REVIEWER[0], "name": REVIEWER[1], "role": "reviewer"}],
                                            "screening": {"reviewers": [LEAD[0], REVIEWER[0]]}}})
    for name, result, measure in (
            ("Sensitivity: people with knee osteoarthritis whom the examination identifies", "sens",
             "True positives and false negatives per 1,000 people tested"),
            ("Specificity: people without knee osteoarthritis whom the examination rules out", "spec",
             "True negatives and false positives per 1,000 people tested")):
        desk.call("/api/certainty/define", {"outcome": {
            "name": name, "importance": "critical", "kind": "accuracy", "result": result, "comparison": ACC_COMPARISON,
            "measure": measure, "synthesis": "pooled", "participants": "", "studies": []}})
    cam.open("certainty", 1800)
    pg.locator(".c-accpanel button", has_text="Enter the accuracy figures").click()
    pg.wait_for_timeout(500)
    for sel, v in (("#ca-cond", "knee osteoarthritis on X-ray (Kellgren-Lawrence grade 2 or more)"),
                   ("#ca-thr", "three or more of the six clinical criteria"),
                   ("#ca-p0", "30"), ("#ca-s0", "the median of the included studies"),
                   ("#ca-p1", "60"), ("#ca-s1", "adults referred to an orthopaedic clinic (the highest in the studies)"),
                   ("#ca-sens-e", "0.84"), ("#ca-sens-lo", "0.77"), ("#ca-sens-hi", "0.89"),
                   ("#ca-spec-e", "0.70"), ("#ca-spec-lo", "0.61"), ("#ca-spec-hi", "0.78")):
        pg.locator(sel).fill(v)
    cam.shot("certainty-accuracy", sel=".modal", modal=True, tall=True)
    pg.locator(".modal button", has_text="Save the accuracy figures").click()
    pg.wait_for_timeout(1500)
    cam.shot("certainty-accuracy-table", sel=".c-accpanel")


def run_ai(desk: Desk):
    desk.call("/api/ai/run", {"scope": "all"})
    for _ in range(600):
        st = desk.get("/api/ai")["status"]
        if not st.get("running"):
            if st.get("error"):
                sys.exit("The AI run stopped: " + st.get("message", ""))
            return
        time.sleep(0.3)
    sys.exit("The AI run did not finish.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true", help="keep the temporary folder afterwards")
    ap.add_argument("--work", help="the folder to build the demonstration in (default: a new temporary folder)")
    a = ap.parse_args()
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("This needs Playwright with Chromium: pip install playwright, then playwright install chromium.")
        return 1
    work = os.path.abspath(a.work) if a.work else tempfile.mkdtemp(prefix="rd-")
    os.makedirs(work, exist_ok=True)
    try:
        build_and_shoot(work)
    finally:
        if a.keep:
            print("Kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
