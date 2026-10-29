"""
Offline tests for the permit parsers, using small PDFs built to match the
layouts the cities publish. Run: python -m pytest scraper -q   (or python scraper/test_parsers.py)
The real-world check is `fetch_permits.py --selftest`, which re-reads the
September 2026 reports and compares them with permits confirmed by hand.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_permits as fp  # noqa: E402

from reportlab.lib.pagesizes import landscape, letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


def frisco_pdf(colon=True):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    c.setFont("Helvetica", 7)
    y = 580
    c.drawString(30, y, "City of Frisco"); y -= 10
    c.drawString(30, y, "Commercial Permits Issued 9/1/2026 - 9/30/2026"); y -= 14
    cols = [("Permit No", 30), ("Issued", 95), ("Type and Subtype", 140), ("Status", 245), ("Site Address", 285), ("Parcel No", 420), ("Valuation", 470), ("Owner and Contractor", 540)]
    for t, x in cols:
        c.drawString(x, y, t)
    y -= 14
    sep = ": " if colon else " "
    recs = [
        ("B26-00196", "9/17/2026", "BUILDING CIFO", "ISSUED", "10202 COIT RD 100", "3780 SF, AHA AMOGHAM", "ADD354179", "$350,000.00",
         ["OWNER" + sep + "COIT CENTER LP", "APPLICANT" + sep + "DUNCAN KIM", "CONTRACTOR" + sep + "PVB CONTRACTING LLC", "ELECT. CTR." + sep + "SPARKY ELECTRIC"]),
        ("B26-01734", "9/24/2026", "BUILDING CNEW", "ISSUED", "7951 PRESTON RD", "BRAKE MASTERS", "ADD100200", "$1,800,000.00",
         ["OWNER" + sep + "BRAKE MASTERS PROPERTIES", "LLC", "APPLICANT" + sep + "CODY PETRICK", "CONTRACTOR" + sep + "NOVEL BUILDERS (GC)"]),
        ("B26-02050", "8/7/2026", "BUILDING CIFO", "VOID", "4577 OHIO DR 201", "ICODE DANCE", "ADD1", "$20,000.00",
         ["APPLICANT" + sep + "NEERAJ KUMAR", "CONTRACTOR" + sep + "INDIGO STREET, LLC"]),
        ("ROOF26-00554", "9/22/2026", "ROOFING ROOF COMMERCIAL", "ISSUED", "7151 PRESTON RD 451D", "VERIFY ROOF STRUCTURE", "ADD9", "$419,750.00",
         ["APPLICANT" + sep + "ZACH GLOFF", "CONTRACTOR" + sep + "PEACH STATE ROOFING, INC."]),
    ]
    for pno, d, typ, st, addr, name, parcel, val, right in recs:
        lines = max(3, len(right))
        for i in range(lines):
            if i == 0:
                c.drawString(30, y, pno); c.drawString(95, y, d); c.drawString(140, y, typ); c.drawString(245, y, st); c.drawString(285, y, addr)
            if i == 1:
                c.drawString(30, y, "Permit Name: " + name); c.drawString(420, y, parcel); c.drawString(470, y, val)
            if i < len(right):
                c.drawString(540, y, right[i])
            y -= 10
        y -= 6
    c.drawString(30, y, "Total Permits: 4   Total Valuation: $2,589,750.00")
    c.save()
    return buf.getvalue()


def murphy_pdf():
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Courier", 7)
    y = 760
    def line(t):
        nonlocal y
        c.drawString(30, y, t); y -= 9
    line("10/02/2026 09:14 PROJECT MASTER REPORT PAGE 1")
    line("PROJECTS: 260000 THRU 269999 ISSUED DATE: 09/01/2026 THRU 09/30/2026")
    line("=" * 90)
    recs = [
        ("261389", "FINISH OUT", "CFO", "COMMERCIAL FINISH OUT", "623 W FM 544 100", "09/01/2026", "CLEXT LLC", "120,000.00"),
        ("261465", "NEW HOME", "RES", "RESIDENTIAL BUILDING", "1110 OAKLEY DRIVE", "09/09/2026", "DR HORTON", "3,081,000.00"),
        ("260928", "FENCE", "FEN", "FENCE", "537 SEMINOLE TRAIL", "09/14/2026", "RASHEED, ADBUL", "10,000.00"),
    ]
    for num, desc, code, label, prop, iss, who, val in recs:
        line(f"PROJECT: {num} - {desc}        TYPE: {code} {label}")
        line(f"  PROPERTY: {prop}      APPLIED DATE: 08/20/2026  ISSUED DATE: {iss}  EXPIRATION DATE: 03/01/2027")
        line(f"  CONTRACTOR: ISSUED TO: {who}")
        line("     123 MAIN ST DALLAS TX 75201")
        line("  STATUS: ISSUED   BALANCE: 0.00")
        line(f"  SEGMENT: 01 - MAIN   ISSUED DATE: {iss}   STATUS: ISSUED   VALUATION: {val}  BALANCE: 0.00")
        line("  PHASE STATUS")
        line("  =====")
    c.save()
    return buf.getvalue()


def test_frisco_with_colons():
    permits, total = fp.parse_frisco(frisco_pdf(True))
    by = {p["permit"]: p for p in permits}
    assert set(by) == {"B26-00196", "B26-01734", "B26-02050", "ROOF26-00554"}, by.keys()
    a = by["B26-00196"]
    assert a["company"] == "PVB Contracting LLC", a
    assert a["applicant"] == "Duncan Kim", a
    assert a["code"] == "CIFO" and a["ptype"] == "Tenant finish-out"
    assert a["value"] == 350000 and a["date"] == "2026-09-17"
    assert a["tenant"] == "Aha Amogham", a["tenant"]
    assert a["address"].startswith("10202 Coit"), a["address"]
    assert by["B26-01734"]["company"] == "Novel Builders", by["B26-01734"]
    assert by["B26-02050"]["status"] == "VOID"
    assert by["ROOF26-00554"]["code"] == "ROOF COMMERCIAL"
    assert total == 4


def test_frisco_without_colons():
    permits, _ = fp.parse_frisco(frisco_pdf(False))
    by = {p["permit"]: p for p in permits}
    assert by["B26-00196"]["company"] == "PVB Contracting LLC", by["B26-00196"]
    assert by["B26-00196"]["applicant"] == "Duncan Kim"


def test_murphy():
    permits, n = fp.parse_murphy(murphy_pdf())
    by = {p["permit"]: p for p in permits}
    assert n == 3 and set(by) == {"261389", "261465", "260928"}
    a = by["261389"]
    assert a["company"] == "Clext LLC", a
    assert a["code"] == "COMMERCIAL FINISH OUT" and a["ptype"] == "Tenant finish-out", a
    assert a["address"] == "623 W Fm 544 100", a["address"]
    assert a["date"] == "2026-09-01" and a["value"] == 120000
    assert by["261465"]["value"] == 3081000


def test_merge_skips_homeowners_and_dupes():
    feed = {"projects": {"permit-murphy-261389": {"city": "Murphy", "ref": "261389"}}, "contacts": {"pc-clext-llc": {"company": "CLEXT LLC", "notes": "old.", "name": "(main office)", "project": ""}}}
    permits, _ = fp.parse_murphy(murphy_pdf())
    np_, nc, uc = fp.merge(permits, feed)
    assert np_ == 0  # 261389 already there; the others aren't commercial
    assert nc == 1   # DR Horton is new; the homeowner is skipped
    assert "pc-dr-horton" in feed["contacts"]
    assert not any("rasheed" in k for k in feed["contacts"])
    assert uc == 0   # CLEXT's only permit was already known
    permits2, _ = fp.parse_frisco(frisco_pdf())
    np2, nc2, _ = fp.merge(permits2, feed)
    assert np2 == 3  # VOID skipped
    c = feed["contacts"]["pc-pvb-contracting-llc"]
    assert c["name"] == "Duncan Kim" and c["role"] == "Permit applicant" and c["project"] == "permit-frisco-b26-00196"


def test_month_labels():
    assert fp.month_from_label("September 2026") == "2026-09"
    assert fp.month_from_label("MARCH-2026-BUILDING-PROJECTS-REPORT") == "2026-03"
    assert fp.month_from_label("Building Inspections - Commercial Permits - Aug 2026") == "2026-08"
    assert fp.month_from_label("Reports") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
