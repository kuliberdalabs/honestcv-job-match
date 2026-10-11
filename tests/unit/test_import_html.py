"""Saved pages: schema.org JobPosting first, readable text as the fallback."""

import datetime as dt
import json

from honestcv.importers import import_path

TODAY = dt.date(2026, 10, 10)
FICTIONAL = "<!-- Fictional sample. Any resemblance to real people or companies is accidental. -->"

POSTING = {
    "@type": "JobPosting", "title": "Revenue Reporting Analyst", "identifier": {"@type": "PropertyValue", "name": "Harrowmere Hotels", "value": "HH-204"},
    "description": "&lt;p&gt;Own the revenue reports for twelve hotels.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Excel&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;",
    "datePosted": "2026-09-20T08:00:00+02:00", "validThrough": "2026-10-01", "employmentType": ["FULL_TIME", "CONTRACTOR"],
    "hiringOrganization": {"@type": "Organization", "name": "Harrowmere Hotels"}, "jobLocationType": "TELECOMMUTE",
    "jobLocation": [{"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Gothenburg", "addressCountry": {"@type": "Country", "name": "Sweden"}}}],
    "baseSalary": {"@type": "MonetaryAmount", "currency": "SEK", "value": {"@type": "QuantitativeValue", "value": 44000, "unitText": "MONTH"}},
    "skills": ["Excel", "SQL"], "url": "https://harrowmere.invalid/careers/revenue-reporting-analyst?trk=feed#top",
}  # fmt: skip


def page(*blocks: str, head: str = "", body: str = "") -> str:
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in blocks)
    return f"<!doctype html>\n{FICTIONAL}\n<html><head><title>Careers | Harrowmere</title>{head}{scripts}</head><body>{body}</body></html>"


def test_import_html_jsonld(tmp_path):
    def read(name: str, html: str):
        path = tmp_path / name
        path.write_text(html, encoding="utf-8")
        return import_path(path, today=TODAY)

    (offer,) = read("single.html", page(json.dumps(POSTING)))
    assert (offer.title, offer.company, offer.requisition_id, offer.remote) == ("Revenue Reporting Analyst", "Harrowmere Hotels", "HH-204", "remote")
    assert (offer.location, offer.countries, offer.contract) == ("Gothenburg, Sweden", ["SE"], ["contract", "employment"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (44000, 44000, "SEK", "month")
    assert (offer.posted_at, offer.valid_through) == (dt.date(2026, 9, 20), dt.date(2026, 10, 1))
    assert offer.url == "https://harrowmere.invalid/careers/revenue-reporting-analyst"  # trk and the fragment are removed
    assert "Own the revenue reports for twelve hotels." in offer.description and "- Excel" in offer.description and "&lt;" not in offer.description
    assert offer.description.endswith("Skills: Excel, SQL") and offer.source == "import:html" and offer.attribution == "your file single.html"
    # @graph, a list of postings, and damaged blocks next to good ones
    graph = json.dumps({"@context": "https://schema.org", "@graph": [{"@type": "WebSite", "name": "Careers"}, {**POSTING, "title": "In a graph"}]})
    listed = json.dumps([{**POSTING, "title": "First", "identifier": "A-1"}, {**POSTING, "title": "Second", "identifier": "A-2"}])
    assert [o.title for o in read("graph.html", page("{broken", graph))] == ["In a graph"]
    assert [o.title for o in read("list.html", page(listed))] == ["First", "Second"]
    # a page without JSON-LD becomes one offer from its readable text; scripts and styles are not text
    body = "<h1>Specjalista ds. kadr (k/m)</h1><p>Szukamy osoby do działu kadr.</p><script>var tracking = 1;</script><style>p { color: red }</style><ul><li>Excel</li><li>Komunikatywny angielski</li></ul>"
    (plain,) = read("plain.html", page(head='<link rel="canonical" href="https://kadry.example.invalid/oferta/12?utm_source=x">', body=body))
    assert plain.title == "Specjalista ds. kadr (k/m)" and plain.url == "https://kadry.example.invalid/oferta/12" and plain.thin is False
    assert "Szukamy osoby do działu kadr." in plain.description and "- Excel" in plain.description
    assert "tracking" not in plain.description and "color" not in plain.description
    (untitled,) = read("title.html", page(body="<p>Reporting work for the finance team, every month, with Excel.</p>"))
    assert untitled.title == "Careers | Harrowmere" and untitled.url is None
    # text that tries to be markup stays text, and a link that is not http(s) is not stored
    hostile = {**POSTING, "title": "<img src=https://tracker.invalid/p>", "url": "javascript:alert(1)", "description": "<script>alert(1)</script> Build weekly reports."}
    (odd,) = read("hostile.html", page(json.dumps(hostile).replace("</", "<\\/")))
    assert odd.title == "<img src=https://tracker.invalid/p>" and odd.url is None and "alert(1)" not in odd.description
    assert read("empty.html", "<html></html>") == []
