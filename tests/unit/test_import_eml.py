"""Job-alert e-mails: offers only, nothing about the sender or the recipient, contact details removed."""

import datetime as dt
import json
from email.message import EmailMessage

from honestcv.importers import import_path, read_path
from honestcv.importers.eml import redact

TODAY = dt.date(2026, 10, 10)
# Built from pieces so that no phone number is written out in this file.
MOBILE = "600 " + "123 " + "456"
INTERNATIONAL = "+1 " + "555 " + "010 " + "0199"
FICTIONAL = "Fictional sample. Any resemblance to real people or companies is accidental."

POSTING = {
    "@context": "https://schema.org", "@type": "JobPosting", "title": "Analityk danych, zespół raportów (k/m)",
    "description": f"Szukamy analityka. Kontakt: rekruter@example.invalid lub {MOBILE}.\n\nWymagania: SQL, Excel.",
    "datePosted": "2026-10-07", "hiringOrganization": {"@type": "Organization", "name": "Example Bank"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Kraków", "addressCountry": "PL"}},
    "baseSalary": {"@type": "MonetaryAmount", "currency": "PLN", "value": {"@type": "QuantitativeValue", "minValue": 12000, "maxValue": 15000, "unitText": "MONTH"}},
    "employmentType": "FULL_TIME", "url": "https://praca.example.invalid/oferta/analityk-danych-raporty?utm_source=alert",
}  # fmt: skip


def build_alert(path, *, html=None, text=None, attach=False):
    msg = EmailMessage()
    msg["From"] = "Job Alerts <alerts@example.invalid>"
    msg["To"] = "Maya Lindqvist <maya@example.invalid>"
    msg["Subject"] = "3 new jobs for you this week"
    msg.set_content(text if text is not None else f"{FICTIONAL}\nSee the HTML version.")
    if html is not None:
        msg.add_alternative(html, subtype="html")
    if attach:
        msg.add_attachment(b"%PDF-1.4 synthetic", maintype="application", subtype="pdf", filename="cv.pdf")
    path.write_bytes(bytes(msg))
    return path


HTML = f"""<html><body>
<!-- {FICTIONAL} -->
<p>{FICTIONAL}</p>
<a href="https://alerts.example.invalid/r/48213?utm_source=mail&amp;token=maya%40example.invalid&amp;id=7"><img src="https://img.example.invalid/l.png"></a>
<a href="https://alerts.example.invalid/r/48213?utm_source=mail&amp;token=maya%40example.invalid&amp;id=7">Data Analyst</a>
<a href="https://jobs.tallgrove.invalid/openings/reporting-analyst#apply">Reporting Analyst · Tallgrove Analytics</a>
<a href="https://click.mailer.invalid/x/9">Support Operations Analyst</a>
<a href="https://alerts.example.invalid/u/unsubscribe">Unsubscribe</a>
<a href="https://alerts.example.invalid/prefs">Manage alerts</a>
<a href="https://alerts.example.invalid/prefs2">Wypisz się z powiadomień</a>
<a href="https://alerts.example.invalid/prefs3">Zrezygnuj</a>
<a href="https://alerts.example.invalid/prefs4">Ustawienia powiadomień</a>
<a href="https://alerts.example.invalid/all">View all jobs</a>
<a href="javascript:alert(1)">Click here for a job</a>
<a href="mailto:rekruter@example.invalid">rekruter@example.invalid</a>
<script type="application/ld+json">{json.dumps(POSTING)}</script>
</body></html>"""  # fmt: skip


def test_import_eml(tmp_path):
    path = build_alert(tmp_path / "alerts.eml", html=HTML, attach=True)
    offers = import_path(path, today=TODAY)
    by_title = {o.title: o for o in offers}
    assert sorted(by_title) == ["Analityk danych, zespół raportów (k/m)", "Data Analyst", "Reporting Analyst · Tallgrove Analytics", "Support Operations Analyst"]
    full = by_title["Analityk danych, zespół raportów (k/m)"]  # the JobPosting data in the e-mail is a full offer
    assert (full.company, full.location, full.countries, full.contract, full.thin) == ("Example Bank", "Kraków, PL", ["PL"], ["employment"], False)
    assert (full.salary.min, full.salary.max, full.salary.currency, full.salary.unit) == (12000, 15000, "PLN", "month")
    assert full.url == "https://praca.example.invalid/oferta/analityk-danych-raporty" and full.posted_at == dt.date(2026, 10, 7)
    assert "[removed]" in full.description and "rekruter@example.invalid" not in full.description and MOBILE not in full.description
    assert "Wymagania: SQL, Excel." in full.description
    # link offers are thin; a link on a mail-redirect host is kept as it is and marked
    data = by_title["Data Analyst"]
    assert data.thin and data.description == "" and data.source == "import:eml"
    assert data.url == "https://alerts.example.invalid/r/48213?id=7"  # utm_source dropped; the parameter that carried an address is gone
    assert data.attribution == "your file alerts.eml (tracking link from your e-mail)"
    direct = by_title["Reporting Analyst · Tallgrove Analytics"]
    assert (direct.url, direct.attribution, direct.thin) == ("https://jobs.tallgrove.invalid/openings/reporting-analyst", "your file alerts.eml", True)
    assert by_title["Support Operations Analyst"].attribution.endswith("(tracking link from your e-mail)")
    # no sender, recipient, subject or unsubscribe link ends up in any stored field
    dump = json.dumps([o.model_dump(mode="json") for o in offers], ensure_ascii=False)
    for forbidden in ("alerts@example.invalid", "maya@example.invalid", "Maya", "3 new jobs", "unsubscribe", "Unsubscribe", "prefs", "mailto", "javascript", "pdf", "rekruter@"):
        assert forbidden not in dump, forbidden
    assert len({o.id for o in offers}) == 4
    # plain-text alerts: a link on its own line, the line above it is the title
    text = "Fictional sample.\nYour alerts\nData Analyst, Tallgrove Analytics\nhttps://jobs.tallgrove.invalid/openings/data-analyst?utm_medium=mail\n\nBye\n"
    (only,) = import_path(build_alert(tmp_path / "plain.eml", text=text), today=TODAY)
    assert (only.title, only.url, only.thin) == ("Data Analyst, Tallgrove Analytics", "https://jobs.tallgrove.invalid/openings/data-analyst", True)
    # an e-mail with no job links says so and gives no offers
    offers, notes = read_path(build_alert(tmp_path / "none.eml", text="Nothing here."), TODAY)
    assert offers == [] and "no job links" in notes[0]


def test_redact_removes_addresses_and_phone_numbers():
    text = f"Mail me at anna.kowalska+jobs@example.invalid or call {MOBILE} / {INTERNATIONAL} / 22 " + "123 " + "45 " + "67 today."
    cleaned = redact(text)
    assert cleaned == "Mail me at [removed] or call [removed] / [removed] / [removed] today."
    safe = "Salary 12 000 - 15 000 PLN, posted 2026-10-07, 3+ years, Excel 365, room 204, 40 000 SEK per month."
    assert redact(safe) == safe  # money, dates and years are not phone numbers
