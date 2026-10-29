#!/usr/bin/env python3
"""
Weekly permit refresh for Pro Lead Tracker.

Checks Frisco's and Murphy's monthly permit lists, reads any month that hasn't
been processed yet, and adds the permits to data/permits.json, which the app
reads when it opens.

Usage:
  python scraper/fetch_permits.py            # normal weekly run
  python scraper/fetch_permits.py --selftest # re-read September 2026 and compare to known permits
  python scraper/fetch_permits.py --dry-run  # parse but don't write data files

Exit code 1 means something needs attention (GitHub emails you when a run fails).
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DEBUG = os.path.join(ROOT, "debug")
PERMITS_FILE = os.path.join(DATA, "permits.json")
STATE_FILE = os.path.join(DATA, "state.json")
KNOWN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_permits.json")

FRISCO_ARCHIVE = "https://www.friscotexas.gov/Archive.aspx?AMID=86"
MURPHY_PAGE = "https://murphytx.org/951/Permit-Issued-Reports"

# Months before this are already in the tracker (pulled by hand in Oct 2026).
START_MONTH = "2026-10"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ProLeadTracker/1.0; weekly public permit check)",
    "Accept": "text/html,application/pdf,*/*",
}

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}
MONTH_ABBR = {k[:3]: v for k, v in MONTHS.items()}

FRISCO_CODES = {
    "CIFO": ("Commercial interior finish-out", "Tenant finish-out"),
    "CALT": ("Commercial alteration/remodel", "Remodel / alteration"),
    "CNEW": ("Commercial new construction", "Ground-up commercial"),
    "CADD": ("Commercial addition", "Ground-up commercial"),
    "CDMO": ("Commercial demolition", "Other"),
    "COMM": ("Telecom tower", "Other"),
    "ROOF COMMERCIAL": ("Commercial roofing", "Remodel / alteration"),
}
MURPHY_COMMERCIAL = {
    "COMMERCIAL FINISH OUT": "Tenant finish-out",
    "COMMERCIAL ADDITIONS": "Ground-up commercial",
    "COMMERCIAL ADDITION": "Ground-up commercial",
    "COMMERCIAL REMODEL": "Remodel / alteration",
    "COMMERCIAL NEW": "Ground-up commercial",
    "COMMERCIAL BUILDING": "Ground-up commercial",
    "COMMERCIAL FLATWORK": "Other",
    "TEMPORARY KITCHEN/SERVING LINE": "School / public",
}

DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")


# ----------------------------------------------------------------- helpers
def log(*a):
    print(*a, flush=True)


def slug(s, n=60):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:n]


def company_key(name):
    n = (name or "").lower().replace("the ", "")
    n = re.sub(r"\(gc\)|\(gen\)|\(elec\)|- mech\b", "", n)
    return re.sub(r"[^a-z0-9]", "", n)


def iso_date(s):
    m = DATE_RE.search(s or "")
    if not m:
        return ""
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    try:
        return dt.date(y, mo, d).isoformat()
    except ValueError:
        return ""


def money(s):
    m = MONEY_RE.search(s or "")
    if not m:
        m2 = re.search(r"\b([\d,]+\.\d{2})\b", s or "")
        if not m2:
            return 0
        return int(float(m2.group(1).replace(",", "")))
    return int(float(m.group(1).replace(",", "")))


def titlecase(s):
    s = (s or "").strip()
    if not s or not s.isupper():
        return s
    small = {"LLC", "LP", "LLP", "PLLC", "DBA", "GC", "AC", "HVAC", "DFW", "NTX", "USA", "TX", "II", "III", "CO", "A/C", "HEB", "PSI", "JBJ", "CMR", "SES", "DNA", "ISO", "RCSA", "JWC", "MRS", "MTR", "PVB", "CBM", "PSI", "BGA", "CPI", "HUC", "SWX", "PZ", "LMF", "RW", "SF", "AE", "CL", "XS", "K2", "DPR", "MYCON"}
    out = []
    for w in s.split():
        bare = re.sub(r"[^A-Z0-9/&]", "", w)
        out.append(w if bare in small else w.capitalize())
    return " ".join(out)


def looks_like_person(name):
    """'LAST, FIRST' style entries are homeowners pulling their own permit."""
    n = (name or "").strip()
    if re.search(r"\b(LLC|INC|LP|CO|CORP|COMPANY|SERVICES|ROOFING|PLUMBING|CONSTRUCTION)\b", n, re.I):
        return False
    return bool(re.match(r"^[A-Za-z' .-]+,\s*[A-Za-z .]+$", n))


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:  # pragma: no cover
            last = str(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"Couldn't download {url}: {last}")


def get_pdf(url):
    """Download a PDF. Some archive links return an HTML page that links to the file."""
    r = get(url)
    ctype = r.headers.get("content-type", "")
    if r.content[:4] == b"%PDF" or "pdf" in ctype:
        return r.content
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all(["a", "iframe", "embed"]):
        href = a.get("href") or a.get("src") or ""
        if re.search(r"ViewFile|DocumentCenter/View|\.pdf", href, re.I):
            r2 = get(urljoin(url, href))
            if r2.content[:4] == b"%PDF":
                return r2.content
    raise RuntimeError(f"{url} didn't return a PDF")


def month_from_label(label):
    """'September 2026', 'SEPTEMBER-2026', 'Sep 2026 Issued' -> '2026-09'."""
    t = (label or "").lower()
    y = re.search(r"(20\d{2})", t)
    if not y:
        return None
    for name, num in MONTHS.items():
        if re.search(name, t):
            return f"{y.group(1)}-{num:02d}"
    for ab, num in MONTH_ABBR.items():
        if re.search(rf"\b{ab}\b", t):
            return f"{y.group(1)}-{num:02d}"
    return None


# ----------------------------------------------------------------- listings
def frisco_months():
    """Return {'2026-09': url, ...} from Frisco's Commercial Permits archive."""
    soup = BeautifulSoup(get(FRISCO_ARCHIVE).text, "html.parser")
    out = {}
    for a in soup.find_all("a", href=True):
        if "ADID=" not in a["href"]:
            continue
        label = " ".join(a.get_text(" ").split())
        m = month_from_label(label)
        if m and m not in out:
            out[m] = urljoin(FRISCO_ARCHIVE, a["href"])
    return out


def murphy_months():
    """Return {'2026-09': url, ...} from Murphy's Permit Issued Reports page.
    Murphy's link labels are sometimes wrong (a 2026 report once said '2024'),
    so when two links claim the same month the newer document id wins."""
    soup = BeautifulSoup(get(MURPHY_PAGE).text, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "DocumentCenter/View" not in href:
            continue
        label = " ".join(a.get_text(" ").split()) + " " + href.replace("-", " ")
        m = month_from_label(label)
        doc = re.search(r"/View/(\d+)", href)
        if not m or not doc:
            continue
        docid = int(doc.group(1))
        if m not in found or docid > found[m][0]:
            found[m] = (docid, urljoin(MURPHY_PAGE, href))
    return {k: v[1] for k, v in found.items()}


# ----------------------------------------------------------------- parsers
def pdf_pages(content):
    if pdfplumber is None:
        raise RuntimeError("pdfplumber isn't installed")
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            yield page


def save_debug(name, text):
    os.makedirs(DEBUG, exist_ok=True)
    with open(os.path.join(DEBUG, name), "w") as f:
        f.write(text)


FRISCO_PERMIT_RE = re.compile(r"^([A-Z]{1,6}\d{2}-\d{4,6})\b")
FRISCO_ROLES = ["APPLICANT", "CONTRACTOR", "OWNER", "ELECT. CTR.", "MECH. CTR.", "PLUM. CTR.", "ARCHITECT", "ENGINEER"]
ROLE_RE = re.compile(r"\b(" + "|".join(re.escape(r) for r in FRISCO_ROLES) + r")\s*:?\s*", re.I)


def _lines_with_columns(page):
    """Group words into lines and tag where the 'Owner and Contractor' column starts."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5, y_tolerance=2.5)
    owner_x = None
    for w in words:
        if w["text"].lower().startswith("owner"):
            # header row: 'Owner and Contractor'
            neighbors = [x for x in words if abs(x["top"] - w["top"]) < 3 and x["text"].lower().startswith("contractor")]
            if neighbors:
                owner_x = w["x0"] - 2
                break
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1]["top"] - w["top"]) <= 2.5:
            rows[-1]["words"].append(w)
        else:
            rows.append({"top": w["top"], "words": [w]})
    out = []
    for r in rows:
        ws = sorted(r["words"], key=lambda w: w["x0"])
        left = " ".join(w["text"] for w in ws if owner_x is None or w["x0"] < owner_x)
        right = " ".join(w["text"] for w in ws if owner_x is not None and w["x0"] >= owner_x)
        out.append((left.strip(), right.strip(), " ".join(w["text"] for w in ws)))
    return out, owner_x


def parse_frisco(content, debug_name=None):
    """Parse a Frisco 'Commercial Permits Issued' PDF into permit dicts."""
    records, cur = [], None
    raw = []
    for page in pdf_pages(content):
        lines, owner_x = _lines_with_columns(page)
        for left, right, full in lines:
            raw.append(full)
            if re.match(r"^(City of Frisco|Commercial Permits Issued|Permit No\b|Page \d+)", full, re.I):
                continue
            m = FRISCO_PERMIT_RE.match(full)
            if m:
                cur = {"permit": m.group(1), "left": [left], "right": [right] if right else [], "full": [full]}
                records.append(cur)
            elif cur is not None:
                cur["left"].append(left)
                if right:
                    cur["right"].append(right)
                cur["full"].append(full)
    if debug_name:
        save_debug(debug_name, "\n".join(raw))
    total_count = None
    tail = "\n".join(l for l in raw[-8:] if re.search(r"\btotal\b", l, re.I))
    tm = re.search(r"total\s*(?:permits?|records?|count)?\s*:?\s*(\d+)\b(?!,)|(\d+)\s+permits", tail, re.I)
    if tm:
        try:
            total_count = int(tm.group(1) or tm.group(2))
        except ValueError:
            total_count = None

    unglue = lambda t: re.sub(r"(?<=[A-Za-z])(ISSUED|VOID|COMPLETED|FINALED|EXPIRED|CANCELLED)\b", r" \1", t or "")
    permits = []
    for r in records:
        r["full"][0] = unglue(r["full"][0])
        r["left"][0] = unglue(r["left"][0])
        text = " ".join(r["full"])
        first = r["full"][0]
        sm = re.search(r"\b(VOID|CANCELLED|COMPLETED|FINALED|EXPIRED|ISSUED)\b", first) or re.search(r"\b(VOID|CANCELLED|COMPLETED|FINALED|EXPIRED|ISSUED)\b", text)
        status = sm.group(1) if sm else ""
        sub = ""
        for code in sorted(FRISCO_CODES, key=len, reverse=True):
            if re.search(r"\b" + re.escape(code) + r"\b", text):
                sub = code
                break
        # address: after the status word on the first line, before a parcel id / money
        addr = ""
        am = re.search(r"\b(?:ISSUED|VOID|COMPLETED|FINALED|EXPIRED|CANCELLED)\b\s+(.+?)(?=\s+ADD\d|\s+\$|\s{2,}|$)", r["left"][0] or first)
        if am:
            addr = am.group(1).strip()
        tenant = ""
        pm = re.search(r"Permit Name:\s*(.+?)(?=\s+ADD\d|\s+\$|$)", " ".join(r["left"]))
        if pm:
            tenant = pm.group(1).strip()
            tenant = re.sub(r"^[\d,.]+\s*S\.?F\.?,?\s*", "", tenant, flags=re.I)  # '3780 SF, AHA AMOGHAM' -> 'AHA AMOGHAM'
        # roles from the Owner and Contractor column
        roles = {}
        right_text = r["right"] if r["right"] else []
        if not right_text:
            # no column detected: fall back to scanning full lines for role labels
            right_text = [l for l in r["full"]]
        last_role = None
        for line in right_text:
            parts = ROLE_RE.split(line)
            if len(parts) > 1:
                # parts = [before, ROLE, value, ROLE, value...]
                for i in range(1, len(parts) - 1, 2):
                    role = parts[i].upper()
                    val = parts[i + 1].strip()
                    roles.setdefault(role, val)
                    last_role = role
            elif last_role and line.strip() and not FRISCO_PERMIT_RE.match(line):
                # wrapped continuation of the previous value
                if len(roles.get(last_role, "")) < 60:
                    roles[last_role] = (roles.get(last_role, "") + " " + line.strip()).strip()
        gc = roles.get("CONTRACTOR", "")
        gc = re.sub(r"\s*\((GC|GEN)\)\s*", "", gc).strip(" ,;")
        applicant = roles.get("APPLICANT", "").strip(" ,;")
        permits.append({
            "city": "Frisco",
            "permit": r["permit"],
            "date": iso_date(text),
            "code": sub,
            "category": FRISCO_CODES.get(sub, ("Commercial permit", "Other"))[0],
            "ptype": FRISCO_CODES.get(sub, ("", "Other"))[1],
            "status": status,
            "address": titlecase(addr),
            "tenant": titlecase(tenant),
            "value": money(text),
            "company": titlecase(gc),
            "applicant": titlecase(applicant),
        })
    return permits, total_count


MURPHY_START = re.compile(r"PROJECT\s*:?\s*(\d{5,7})\b", re.I)


def parse_murphy(content, debug_name=None):
    """Parse Murphy's 'PROJECT MASTER REPORT' PDF into permit dicts."""
    raw = []
    for page in pdf_pages(content):
        t = page.extract_text(x_tolerance=1.5, y_tolerance=2.5) or ""
        raw.extend(t.splitlines())
    if debug_name:
        save_debug(debug_name, "\n".join(raw))
    blocks, cur = [], None
    for line in raw:
        if re.search(r"PROJECT MASTER REPORT|^={5,}|^PROJECTS?\s*:?\s*\d+\s*(THRU|TO)", line, re.I):
            continue
        m = MURPHY_START.search(line)
        if m and line.strip().upper().startswith("PROJECT"):
            cur = {"permit": m.group(1), "lines": [line]}
            blocks.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    permits = []
    for b in blocks:
        text = "\n".join(b["lines"])
        head = b["lines"][0]
        ptype = ""
        tm = re.search(r"TYPE\s*:?\s*(?:\S+\s+)?([A-Z][A-Z0-9 /&\-().]+?)\s*$", head)
        if tm:
            ptype = tm.group(1).strip()
        if not ptype:
            dm = re.search(r"PROJECT\s*:?\s*\d+\s*-\s*(.+?)(?:\s+TYPE|$)", head, re.I)
            ptype = dm.group(1).strip() if dm else ""
        prop = ""
        pm = re.search(r"PROPERTY\s*:?\s*(.+?)(?=\s{2,}|\s+APPLIED|\s+ISSUED|$)", text, re.I | re.M)
        if pm:
            prop = pm.group(1).strip()
        issued = ""
        im = re.search(r"ISSUED\s+DATE\s*:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", text, re.I)
        if im:
            issued = iso_date(im.group(1))
        contractor = ""
        cm = re.search(r"ISSUED\s+TO\s*:?\s*(.+?)(?=\s{2,}|$)", text, re.I | re.M)
        if cm:
            contractor = cm.group(1).strip()
        val = 0
        vm = re.search(r"VALUATION\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)", text, re.I)
        if vm:
            val = int(float(vm.group(1).replace(",", "")))
        permits.append({
            "city": "Murphy",
            "permit": b["permit"],
            "date": issued,
            "code": ptype,
            "category": ptype.title(),
            "ptype": MURPHY_COMMERCIAL.get(ptype.upper(), ""),
            "status": "",
            "address": titlecase(prop),
            "tenant": "",
            "value": val,
            "company": titlecase(contractor),
            "applicant": "",
        })
    return permits, len(blocks)


# ----------------------------------------------------------------- merging
def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def merge(permits, feed, seen=None):
    """Add permits to the feed. Returns (new_projects, new_contacts, updated_contacts).
    `seen` is a set of "City:permit" strings already handled; it's updated in place."""
    projects, contacts = feed.setdefault("projects", {}), feed.setdefault("contacts", {})
    seen = seen if seen is not None else set()
    known_refs = {(p.get("city"), p.get("ref")) for p in projects.values()}
    known_refs |= {tuple(x.split(":", 1)) for x in seen}
    by_company = {company_key(c.get("company")): cid for cid, c in contacts.items()}
    new_p = new_c = upd_c = 0
    stamp = now_iso()
    grouped = {}
    for p in permits:
        if p["status"] in ("VOID", "CANCELLED"):
            continue
        if not p["company"] or looks_like_person(p["company"]) or "PENDING CONTRACTOR" in p["company"].upper():
            continue
        grouped.setdefault(company_key(p["company"]), []).append(p)

    for key, plist in grouped.items():
        best_pid, best_val = "", -1
        fresh = []
        for p in plist:
            if (p["city"], p["permit"]) in known_refs:
                continue
            fresh.append(p)
            seen.add(f"{p['city']}:{p['permit']}")
            if p["ptype"]:
                pid = slug(f"permit-{p['city']}-{p['permit']}")
                name = f"{p['tenant']} — {p['address']}" if p["tenant"] and p["tenant"].lower() not in ("whitebox", "demo", "shell") else f"{p['category']} — {p['address']}"
                notes = f"{p['category']} permit issued {p['date']}."
                if p["applicant"]:
                    notes += f" Applicant: {p['applicant']}."
                if p["tenant"]:
                    notes += f" Tenant: {p['tenant']}."
                projects[pid] = {
                    "name": name, "address": p["address"], "city": p["city"], "type": p["ptype"],
                    "stage": "Permit applied", "value": p["value"] or "", "materials": "", "gc": p["company"],
                    "sub": "", "source": "City permit portal", "ref": p["permit"], "next": "", "notes": notes,
                    "created": stamp, "updated": stamp, "feed": True,
                }
                known_refs.add((p["city"], p["permit"]))
                new_p += 1
                if (p["value"] or 0) > best_val:
                    best_pid, best_val = pid, p["value"] or 0
        if not fresh:
            continue
        cats = sorted({p["category"] for p in fresh})
        jobs = sorted({p["tenant"] or p["address"] for p in fresh})
        dates = sorted(p["date"] for p in fresh if p["date"])
        span = (dates[0] + (f" to {dates[-1]}" if dates and dates[-1] != dates[0] else "")) if dates else ""
        cities = "/".join(sorted({p["city"] for p in fresh}))
        line = f"{len(fresh)} {cities} permit{'s' if len(fresh) > 1 else ''}{', ' + span if span else ''}: {', '.join(cats)}. Jobs: {'; '.join(jobs[:4])}{' (+%d more)' % (len(jobs) - 4) if len(jobs) > 4 else ''}."
        applicants = sorted({p["applicant"] for p in fresh if p["applicant"]})
        cid = by_company.get(key)
        if cid:
            c = contacts[cid]
            c["notes"] = (c.get("notes", "") + f" New ({stamp[:10]}): {line}").strip()
            if not c.get("project") and best_pid:
                c["project"] = best_pid
            if applicants and c.get("name") in ("", "(main office)"):
                c["name"] = ", ".join(applicants)
            c["updated"] = stamp
            upd_c += 1
        else:
            cid = slug("pc-" + fresh[0]["company"])
            role = "Permit applicant" if applicants else ("Subcontractor" if cities == "Murphy" else "Other")
            contacts[cid] = {
                "name": ", ".join(applicants) or "(main office)", "company": fresh[0]["company"], "role": role,
                "trade": "Permit: " + ", ".join(cats), "phone": "", "email": "", "project": best_pid, "last": "",
                "notes": line + " Phone/email not found yet.", "created": stamp, "updated": stamp, "feed": True,
            }
            by_company[key] = cid
            new_c += 1
    return new_p, new_c, upd_c


# ----------------------------------------------------------------- checks
def selftest():
    """Re-read September 2026 and compare with the permits already confirmed by hand."""
    known = load_json(KNOWN_FILE, {})
    ok = True
    report = []
    for city, parser in (("frisco", parse_frisco), ("murphy", parse_murphy)):
        k = known.get(city)
        if not k:
            continue
        try:
            content = get_pdf(k["url"])
            parsed, count = parser(content, debug_name=f"selftest-{city}.txt")
        except Exception as e:
            report.append(f"{city}: couldn't read the September list ({e})")
            ok = False
            continue
        got = {p["permit"]: p for p in parsed}
        exp = k["permits"]
        found = [n for n in exp if n in got]
        comp_ok = [n for n in found if company_key(got[n]["company"]) and (company_key(got[n]["company"]) in company_key(exp[n]["company"]) or company_key(exp[n]["company"]) in company_key(got[n]["company"]))]
        appl_ok = [n for n in found if not exp[n].get("applicant") or company_key(exp[n]["applicant"]) == company_key(got[n]["applicant"])]
        pct = lambda a: round(100 * len(a) / max(1, len(exp)))
        line = f"{city}: found {pct(found)}% of permits, company right on {pct(comp_ok)}%, applicant right on {pct(appl_ok)}%"
        report.append(line)
        if pct(found) < 90 or pct(comp_ok) < 85 or pct(appl_ok) < 80:
            ok = False
            misses = [n for n in exp if n not in got][:5] + [f"{n}: got '{got[n]['company']}' want '{exp[n]['company']}'" for n in found if n not in comp_ok][:5]
            report.append("  examples: " + "; ".join(misses))
    return ok, report


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = load_json(STATE_FILE, {"processed": {"Frisco": [], "Murphy": []}})
    problems = []

    if args.selftest:
        ok, rep = selftest()
        for line in rep:
            log(line)
        state["last_selftest"] = {"at": now_iso(), "ok": ok, "report": rep}
        if not args.dry_run:
            os.makedirs(DATA, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=1)
        if not ok:
            log("SELF-TEST FAILED: a permit list's layout may have changed. Raw text is in the run's 'debug' artifact.")
            sys.exit(1)
        log("Self-test passed.")
        return

    feed = load_json(PERMITS_FILE, {"version": 1, "projects": {}, "contacts": {}})
    totals = {"projects": 0, "contacts": 0, "updated_contacts": 0, "months": []}
    sources = (("Frisco", frisco_months, lambda c, debug_name=None: parse_frisco(c, debug_name)), ("Murphy", murphy_months, lambda c, debug_name=None: parse_murphy(c, debug_name)))
    today = dt.date.today()
    for city, lister, parser in sources:
        try:
            months = lister()
        except Exception as e:
            problems.append(f"{city}: couldn't open the list of reports ({e})")
            continue
        log(f"{city}: reports found for {', '.join(sorted(months)[-4:]) or 'none'}")
        done = set(state["processed"].get(city, []))
        todo = sorted(m for m in months if m >= START_MONTH and m not in done)
        for m in todo:
            try:
                content = get_pdf(months[m])
                permits, count = parser(content, debug_name=f"{city.lower()}-{m}.txt")
            except Exception as e:
                problems.append(f"{city} {m}: couldn't read the report ({e})")
                continue
            if not permits:
                problems.append(f"{city} {m}: report opened but no permits could be read. The layout may have changed.")
                continue
            missing_co = sum(1 for p in permits if not p["company"])
            if missing_co > 0.25 * len(permits):
                problems.append(f"{city} {m}: {missing_co} of {len(permits)} permits had no company name. The layout may have changed.")
            if count and city == "Frisco" and abs(count - len(permits)) > max(2, 0.1 * count):
                problems.append(f"Frisco {m}: report says {count} permits but {len(permits)} were read.")
            seen = set(state.get("seen_permits", []))
            np_, nc, uc = merge(permits, feed, seen)
            state["seen_permits"] = sorted(seen)
            totals["projects"] += np_
            totals["contacts"] += nc
            totals["updated_contacts"] += uc
            totals["months"].append(f"{city} {m}")
            state["processed"].setdefault(city, []).append(m)
            log(f"{city} {m}: {len(permits)} permits read, {np_} new projects, {nc} new contacts, {uc} contacts updated")
        # Alert if a city has gone quiet for too long (they post monthly).
        latest = max([m for m in months] + state["processed"].get(city, []) or ["0000-00"])
        y, mo = map(int, latest.split("-")) if latest != "0000-00" else (0, 0)
        if y:
            age_months = (today.year - y) * 12 + (today.month - mo)
            if age_months >= 3:
                problems.append(f"{city}: newest report is {latest}. The city may have moved the list.")

    state["last_run"] = {"at": now_iso(), "added": totals, "problems": problems}
    feed["updated"] = now_iso()
    if not args.dry_run:
        os.makedirs(DATA, exist_ok=True)
        if totals["projects"] or totals["contacts"] or totals["updated_contacts"]:
            with open(PERMITS_FILE, "w") as f:
                json.dump(feed, f, indent=1, ensure_ascii=False)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=1)
    log(f"Done: {totals['projects']} new projects, {totals['contacts']} new contacts, {totals['updated_contacts']} updated.")
    if problems:
        log("NEEDS ATTENTION:")
        for p in problems:
            log(" - " + p)
        sys.exit(1)


if __name__ == "__main__":
    main()
