"""Facts that a pasted or .txt offer states in its own words: pay, work mode, contract, country and city, in English and Polish."""

import datetime as dt
import json
import time
from email.message import EmailMessage

import pytest

from honestcv.importers import import_path, import_text
from honestcv.importers.facts import read_contract, read_mode, read_place, read_salary, with_text_facts
from honestcv.models import Offer

TODAY = dt.date(2026, 10, 10)
MOBILE = "600 " + "123 " + "456"  # built from pieces so that no phone number is written out in this file
POLISH = """Analityk danych
Example Bank S.A.
Kraków, praca hybrydowa, umowa o pracę, 14 000 - 18 000 zł brutto
Szukamy osoby do zespołu raportów.
Wymagania: SQL, Excel, Power BI, angielski B2
"""
ENGLISH = """Senior Data Analyst
Tallgrove Analytics
Stockholm, Sweden. Hybrid, three days in the office.
Permanent contract. Salary: SEK 45,000-55,000 per month.
You will own the weekly reporting and mentor two analysts.
"""


def pay(text: str):
    salary = read_salary(text)
    return None if salary is None else (salary.min, salary.max, salary.currency, salary.unit)


# ---- the whole offer ----


def test_a_polish_paste_gets_its_place_mode_contract_and_pay():
    offer = import_text(POLISH)
    assert (offer.title, offer.company) == ("Analityk danych", None)
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["PL"], "Kraków", "hybrid", ["employment"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit, offer.salary.unit_assumed) == (14000, 18000, "PLN", None, False)
    assert offer.description.startswith("Analityk danych\nExample Bank S.A.") and offer.source == "import:text"


def test_the_second_polish_example_with_a_country_a_dash_and_a_period():
    offer = import_text("Analityk danych\nExample Bank S.A.\nKraków, Polska · praca hybrydowa · umowa o pracę\nWynagrodzenie: 11 000 \u2013 14 000 zł brutto / mies.\nWymagania: SQL")
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["PL"], "Kraków", "hybrid", ["employment"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (11000, 14000, "PLN", "month")


def test_an_english_paste_gets_the_same(tmp_path):
    offer = import_text(ENGLISH)
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["SE"], "Sweden", "hybrid", ["employment"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (45000, 55000, "SEK", "month")
    for name in ("offer.txt", "offer.md"):  # the same text from a file
        path = tmp_path / name
        path.write_text(ENGLISH, encoding="utf-8")
        (found,) = import_path(path, today=TODAY)
        assert (found.countries, found.remote, found.contract, found.salary.max) == (["SE"], "hybrid", ["employment"], 55000)


def test_facts_the_text_does_not_state_stay_unknown():
    offer = import_text("Support Agent\nAnswer tickets and chat with customers. We offer a friendly team and a coffee machine.")
    assert (offer.salary, offer.remote, offer.contract, offer.countries, offer.location) == (None, None, [], [], None)


def test_an_offer_that_already_has_facts_keeps_them():
    offer = Offer(id="", source="x", attribution="y", title="Analityk", location="Gdańsk", countries=["PL"], remote="remote", contract=["b2b"], description=POLISH)  # fmt: skip
    filled = with_text_facts(offer)
    assert (filled.location, filled.countries, filled.remote, filled.contract) == ("Gdańsk", ["PL"], "remote", ["b2b"])
    assert filled.salary.max == 18000  # only the empty fields are filled
    link_only = Offer(id="", source="x", attribution="y", title="Link only")
    assert with_text_facts(link_only) is link_only


def test_job_alert_postings_get_what_their_description_states(tmp_path):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Analityk danych", "hiringOrganization": {"name": "Example Bank"},
               "description": "Lokalizacja: Gdańsk\nPraca hybrydowa. Współpraca B2B, 150-180 zł/h.", "url": "https://jobs.example.invalid/offers/1"}  # fmt: skip
    complete = {**posting, "title": "Complete", "url": "https://jobs.example.invalid/offers/2", "jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
                "employmentType": "FULL_TIME", "baseSalary": {"currency": "EUR", "value": {"minValue": 5000, "maxValue": 6000, "unitText": "MONTH"}}}  # fmt: skip
    html = "".join(f'<script type="application/ld+json">{json.dumps(p)}</script>' for p in (posting, complete))
    html += '<a href="https://jobs.example.invalid/openings/data-analyst">Data Analyst</a>'
    msg = EmailMessage()
    msg.set_content("See the HTML version.")
    msg.add_alternative(html, subtype="html")
    path = tmp_path / "alert.eml"
    path.write_bytes(bytes(msg))
    by_title = {o.title: o for o in import_path(path, today=TODAY)}
    filled = by_title["Analityk danych"]
    assert (filled.countries, filled.location, filled.remote, filled.contract) == (["PL"], "Gdańsk", "hybrid", ["b2b"])
    assert (filled.salary.min, filled.salary.max, filled.salary.currency, filled.salary.unit) == (150, 180, "PLN", "hour")
    kept = by_title["Complete"]  # what the posting itself carries is not replaced
    assert (kept.countries, kept.location, kept.remote, kept.contract) == (["DE"], "Berlin, DE", "hybrid", ["employment"])
    assert (kept.salary.min, kept.salary.max, kept.salary.currency) == (5000, 6000, "EUR")
    thin = by_title["Data Analyst"]
    assert thin.thin and (thin.salary, thin.remote, thin.contract, thin.countries, thin.location) == (None, None, [], [], None)


# ---- pay ----


@pytest.mark.parametrize(
    "text, expected",
    [
        ("12 000 - 18 000 PLN", (12000, 18000, "PLN", None)),
        ("12k-18k zł brutto/mies.", (12000, 18000, "PLN", "month")),
        ("12-18k zł brutto", (12000, 18000, "PLN", None)),
        ("Wynagrodzenie 12-18 tys. zł netto", (12000, 18000, "PLN", None)),
        ("od 15 000 zł", (15000, None, "PLN", None)),
        ("Oferujemy do 20 000 zł brutto", (None, 20000, "PLN", None)),
        ("€4,000-5,000 per month", (4000, 5000, "EUR", "month")),
        ("4,000 \u2013 5,000 EUR / month", (4000, 5000, "EUR", "month")),
        ("60,000-70,000 USD a year", (60000, 70000, "USD", "year")),
        ("Salary: $90,000 per year", (90000, 90000, "USD", "year")),
        ("up to £55,000 annually", (None, 55000, "GBP", "year")),
        ("B2B 150-180 zł/h", (150, 180, "PLN", "hour")),
        ("stawka 120 zł netto za godzinę, B2B", (120, 120, "PLN", "hour")),
        ("14 000 - 18 000 zł brutto (UoP)", (14000, 18000, "PLN", None)),
        ("12 000 zł - 18 000 zł", (12000, 18000, "PLN", None)),
        ("from 3,500 CHF per month", (3500, None, "CHF", "month")),
        ("15.000 - 20.000 zł rocznie", (15000, 20000, "PLN", "year")),
        ("The pay range is 12 000 to 18 000 PLN, paid monthly.", (12000, 18000, "PLN", "month")),
        ("Wynagrodzenie: 14 000 - 18 000 zł brutto\n14 000 - 18 000 zł brutto / mies.", (14000, 18000, "PLN", "month")),
        ("Wynagrodzenie: 15 000 zł", (15000, 15000, "PLN", None)),
    ],
)
def test_pay_ranges(text, expected):
    assert pay(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "3-5 years of experience, 40 hours a week",
        f"Call us on {MOBILE} or write",
        "Company turnover 5 000 000 zł in 2025",
        "Karta Multisport, dofinansowanie do 150 zł",
        "Posted 2026-10-11, apply by 2026-11-10 zł",
        "14 000 - 18 000 zł brutto (UoP) or B2B 150 - 180 zł/h",  # two different ranges: neither is chosen
        "18 000 - 14 000 zł",
        "5 PLN",
        "20 EUR",
        "2026",
    ],
)
def test_text_that_is_not_one_clear_pay_range_gives_no_pay(text):
    assert pay(text) is None


# ---- work mode and contract ----


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Fully remote", "remote"), ("100% remote role", "remote"), ("Praca zdalna", "remote"), ("Pracujemy zdalnie", "remote"), ("Work from home", "remote"),
        ("Hybrid", "hybrid"), ("praca hybrydowa", "hybrid"), ("Pracujemy hybrydowo, dwa dni zdalnie", "hybrid"), ("Hybrid: 3 days in the office, 2 remote", "hybrid"),
        ("On-site", "onsite"), ("onsite", "onsite"), ("Praca stacjonarnie", "onsite"), ("praca w biurze w Gdańsku", "onsite"), ("Office-based", "onsite"),
        ("Remote or on-site, your choice", None), ("praca zdalna, owoce w biurze", None),
        ("This is not a remote job. On-site in Berlin", "onsite"), ("bez pracy zdalnej, stacjonarnie", "onsite"), ("No remote work", None),
        ("You will manage remote teams across Europe.", None), ("Microsoft Office and Excel", None), ("Nothing about it.", None),
    ],
)
def test_work_mode(text, expected):
    assert read_mode(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("B2B", ["b2b"]), ("umowa o pracę", ["employment"]), ("Umowa o prace lub B2B", ["b2b", "employment"]), ("UoP", ["employment"]),
        ("Employment contract", ["employment"]), ("Permanent position", ["employment"]), ("umowa zlecenie", ["contract"]), ("Umowa o dzieło", ["contract"]),
        ("Contractor role, B2B", ["b2b", "contract"]), ("contract basis", ["contract"]), ("Fixed-term", ["contract"]),
        ("contract management experience", []), ("We value an international team", []), ("non-permanent", []), ("bez B2B", []),
    ],
)
def test_contract_form(text, expected):
    assert read_contract(text) == expected


# ---- country and city ----


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Data Analyst\nWarsaw, Poland", (["PL"], "Warszawa")),
        ("Analityk\nFirma\nKrakow", (["PL"], "Kraków")),
        ("Analityk\nPraca w Krakowie lub we Wrocławiu", (["PL"], "Kraków, Wrocław")),
        ("Analityk\nLokalizacja: Łódź", (["PL"], "Łódź")),
        ("Analityk\nGdańsk · Gdynia", (["PL"], "Gdańsk, Gdynia")),
        ("Analityk\nPoznań", (["PL"], "Poznań")), ("Analityk\nSzczecin", (["PL"], "Szczecin")), ("Analityk\nBydgoszcz", (["PL"], "Bydgoszcz")),
        ("Analityk\nLublin", (["PL"], "Lublin")), ("Analityk\nw Białymstoku", (["PL"], "Białystok")), ("Analityk\nKatowice", (["PL"], "Katowice")),
        ("Analityk\nCzęstochowa", (["PL"], "Częstochowa")), ("Analityk\nRadom", (["PL"], "Radom")), ("Analityk\nRzeszów", (["PL"], "Rzeszów")),
        ("Analityk\nToruń", (["PL"], "Toruń")), ("Analityk\nSosnowiec", (["PL"], "Sosnowiec")), ("Analityk\nKielce", (["PL"], "Kielce")),
        ("Analityk\nGliwice", (["PL"], "Gliwice")), ("Analityk\nOlsztyn", (["PL"], "Olsztyn")),
        ("Dev\nStockholm, Sverige", (["SE"], "Sweden")),
        ("Dev\nBerlin, Deutschland", (["DE"], "Germany")),
        ("Dev\nNiemcy", (["DE"], "Germany")),
        ("Dev\nAmsterdam, the Netherlands", (["NL"], "Netherlands")),
        ("Dev\nLondon, UK", (["GB"], "United Kingdom")),
        ("Dev\nPraca w Polsce", (["PL"], "Poland")),
        ("Dev\nBerlin, Germany\nKraków", ([], None)),  # two countries
        ("Dev\nRemote", ([], None)),
        ("Dev\nWymagany język polski", ([], None)),
        ("Dev\n" + "\n".join(["filler"] * 12) + "\nWe also have an office in Germany", ([], None)),  # past the header
        ("Dev\n" + "\n".join(["filler"] * 12) + "\nLocation: Germany", (["DE"], "Germany")),  # a labelled line counts
    ],
)
def test_country_and_city(text, expected):
    assert read_place(text) == expected


# ---- typical copied pages: the facts sit on short lines of their own ----

POLISH_PAGE = """Analityk danych
Example Bank S.A.
Kraków
Hybrydowo
Umowa o pracę
18 000 - 24 000 PLN netto (+ VAT) / mies.
Mid
Pełny etat
Opis stanowiska
Szukamy osoby do zespołu raportów. Pracujesz w zespole czterech analityków.
"""
POLISH_B2B_PAGE = """Specjalista ds. raportowania
Example Bank S.A.
Warszawa, Polska · praca zdalna
B2B | 150-180 zł/h
Senior
"""
ENGLISH_PAGE = """Data Analyst
Tallgrove Analytics
Location: Stockholm, Sweden
Remote (EU)
Salary: €4,000-5,000 per month
Mid
Full-time
"""
TRAPS = """Project Coordinator
Example Bank S.A.
Training budget: 1000-2000 EUR per year.
Our product costs 100-200 USD per month. We manage client budgets from 10 000 PLN.
We sell products in Sweden. Our customers are in Poland. You will support clients in Germany.
We work with remote sensing data and build remote access tools for remote teams.
"""


def test_a_polish_page_with_one_fact_per_line():
    offer = import_text(POLISH_PAGE)
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["PL"], "Kraków", "hybrid", ["employment"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (18000, 24000, "PLN", "month")


def test_a_polish_page_with_a_b2b_rate_per_hour():
    offer = import_text(POLISH_B2B_PAGE)
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["PL"], "Warszawa", "remote", ["b2b"])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (150, 180, "PLN", "hour")


def test_an_english_page_with_labelled_lines():
    offer = import_text(ENGLISH_PAGE)
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["SE"], "Sweden", "remote", [])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency, offer.salary.unit) == (4000, 5000, "EUR", "month")


def test_labelled_lines_in_both_languages():
    polish = import_text("Analityk\nMiejsce pracy: Wrocław\nTryb pracy: Hybrydowy\nRodzaj umowy: B2B\nWidełki: 14 000 - 18 000 zł netto / mies.")
    assert (polish.countries, polish.location, polish.remote, polish.contract) == (["PL"], "Wrocław", "hybrid", ["b2b"])
    assert (polish.salary.min, polish.salary.max, polish.salary.currency, polish.salary.unit) == (14000, 18000, "PLN", "month")
    english = import_text("Analyst\nWork mode: On-site\nType of contract: Permanent\nRate: 60-80 EUR per hour\nLocation: Berlin, Germany")
    assert (english.countries, english.remote, english.contract) == (["DE"], "onsite", ["employment"])
    assert (english.salary.min, english.salary.max, english.salary.currency, english.salary.unit) == (60, 80, "EUR", "hour")


def test_the_traps_alone_give_no_fact_at_all():
    offer = import_text(TRAPS)
    assert (offer.salary, offer.remote, offer.contract, offer.countries, offer.location) == (None, None, [], [], None)


def test_a_clear_fact_next_to_the_traps_is_the_only_one_read():
    offer = import_text(TRAPS + "Salary: 12 000 - 15 000 PLN gross.\nLocation: Gdańsk\n")
    assert (offer.countries, offer.location, offer.remote, offer.contract) == (["PL"], "Gdańsk", None, [])
    assert (offer.salary.min, offer.salary.max, offer.salary.currency) == (12000, 15000, "PLN")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Stawka: 120 zł netto/h", (120, 120, "PLN", "hour")),
        ("Widełki: 14 000 - 18 000 zł", (14000, 18000, "PLN", None)),
        ("Compensation: £50,000-60,000 a year", (50000, 60000, "GBP", "year")),
        ("Pay: 40-50 USD per hour", (40, 50, "USD", "hour")),
        ("We offer a salary of 12 000 - 15 000 PLN gross per month.", (12000, 15000, "PLN", "month")),
        ("Oferujemy wynagrodzenie 12 000 - 15 000 zł brutto.", (12000, 15000, "PLN", None)),
        ("Wymagania: SQL.\nThe hourly rate is 90-110 PLN net.", (90, 110, "PLN", None)),
        ("Salary: 12 000 - 15 000 PLN gross.\nTraining budget: 1000-2000 EUR per year.\nOur product costs 100-200 USD per month.", (12000, 15000, "PLN", None)),
    ],
)
def test_pay_in_a_clear_context(text, expected):
    assert pay(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Training budget: 1000-2000 EUR per year.",
        "Our product costs 100-200 USD per month.",
        "We manage client budgets from 10 000 PLN.",
        "Our clients pay 100-200 EUR per hour for support.",
        "Salary budget for the team: 100 000 - 200 000 PLN",
        "Budżet szkoleniowy: 3 000 - 5 000 zł brutto rocznie",
        "Cena pakietu: 200 - 400 zł netto / mies.",
        "Pricing starts at 49 EUR per month for the team plan.",
        "Revenue grew from 2 000 000 to 3 000 000 EUR.",
        "Premium support for 100-200 EUR per month",
        "Klient płaci 150-200 zł/h",
        "Multisport: 100-150 zł",
    ],
)
def test_money_that_is_not_the_pay_gives_no_pay(text):
    assert pay(text) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Praca w Krakowie", (["PL"], "Kraków")),
        ("Biuro w Gdańsku", (["PL"], "Gdańsk")),
        ("Siedziba w Warszawie", (["PL"], "Warszawa")),
        ("Lokalizacja Poznań", (["PL"], "Poznań")),
        ("We are based in Berlin, Germany.", (["DE"], "Germany")),
        ("The team is located in Amsterdam, the Netherlands.", (["NL"], "Netherlands")),
        ("Join our office in Wrocław, close to the station.", (["PL"], "Wrocław")),
        ("You can work from our Warsaw office.", (["PL"], "Warszawa")),
        ("We are based in Sweden, selling to customers in Poland.", (["SE"], "Sweden")),
        ("Berlin, Germany · Hybrid", (["DE"], "Germany")),
        ("Location: Stockholm, Sweden", (["SE"], "Sweden")),
        ("Miejsce pracy: Gdynia", (["PL"], "Gdynia")),
    ],
)
def test_place_in_a_clear_context(text, expected):
    assert read_place("Data Analyst\nExample Bank\n" + text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We sell products in Sweden.",
        "Our customers are in Poland.",
        "You will support clients in Germany.",
        "We have customers in Germany and Poland.",
        "Our product is sold in Sweden, Norway and Finland.",
        "You will travel to Warsaw twice a year for a meeting.",
        "Experience with the Polish market is a plus. Our partners in Kraków help us.",
    ],
)
def test_a_place_in_running_text_is_not_the_work_place(text):
    assert read_place("Data Analyst\nExample Bank\n" + text) == ([], None)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("This role is fully remote.", "remote"), ("Remote-first company", "remote"), ("You work remotely from anywhere", "remote"),
        ("Pracujesz zdalnie lub z biura", "remote"), ("100% zdalnie", "remote"), ("The position is remote.", "remote"), ("Data Analyst (Remote)", "remote"),
        ("Remote (EU)", "remote"), ("Zdalnie", "remote"), ("Location: Remote", "remote"), ("Tryb pracy: zdalny", "remote"),
        ("Praca hybrydowa: 2 dni w biurze", "hybrid"), ("You work 3 days in the office.", "hybrid"), ("Hybrid work, 2 days in the office", "hybrid"),
        ("Kraków · Hybrid", "hybrid"), ("Work mode: Hybrid", "hybrid"), ("Hybrydowo", "hybrid"),
        ("Stacjonarnie w Krakowie", "onsite"), ("The role is on-site in Berlin.", "onsite"), ("Tryb pracy: stacjonarny", "onsite"), ("On-site", "onsite"),
    ],
)
def test_work_mode_in_a_clear_context(text, expected):
    assert read_mode(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "We work with remote sensing data.",
        "Remote control of the devices.",
        "You will build remote monitoring for wind farms.",
        "Experience with remote access tools and remote desktops.",
        "Remote Sensing Engineer",
        "Hybrid Cloud Architect",
        "Experience with hybrid cloud and on-site support.",
        "We support remote, hybrid, and on-site teams.",
        "Remote teams across Europe",
        "Hybrydowy układ napędowy w samochodach",
        "Studia stacjonarne",
    ],
)
def test_a_word_in_another_sense_is_not_the_work_mode(text):
    assert read_mode(text) is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("We offer a B2B contract.", ["b2b"]), ("Współpraca w formie kontraktu B2B", ["b2b"]), ("Współpraca B2B", ["b2b"]),
        ("Rodzaj umowy: B2B", ["b2b"]), ("Type of contract: Permanent", ["employment"]), ("Kraków · Umowa o pracę", ["employment"]),
        ("We offer a permanent contract and a laptop.", ["employment"]), ("Oferujemy umowę o pracę lub B2B", ["b2b", "employment"]),
        ("Forma współpracy: UoP / B2B do wyboru", ["b2b", "employment"]), ("Możesz wybrać B2B lub umowę o pracę.", ["b2b", "employment"]),
    ],
)
def test_contract_in_a_clear_context(text, expected):
    assert read_contract(text) == expected


@pytest.mark.parametrize(
    "text",
    ["Our B2B customers love us.", "B2B sales experience is a plus.", "We build B2B software.", "Contract management and permanent improvement", "Contractors are welcome to apply."],
)
def test_a_word_in_another_sense_is_not_the_contract(text):
    assert read_contract(text) == []


# ---- speed ----


@pytest.mark.parametrize(
    "text",
    ["000 " * 25000, "1 " * 50000, "1,000-" * 20000, "zł " * 30000, "€ " * 50000, "5-" * 50000, "9" * 100000, "12k " * 25000, "1" + " " * 90000 + "1",
     "12 000 - 18 000 zł " * 5000, "5 zł / " * 14000, "hybrid " * 14000, "no " * 33000, "Kraków\n" * 14000],
)
def test_long_hostile_text_is_read_quickly(text):
    started = time.perf_counter()
    with_text_facts(Offer(id="", source="x", attribution="y", title="t", description=text))
    assert time.perf_counter() - started < 10  # generous for slow CI runners


@pytest.mark.parametrize(
    "text",
    ["salary 12 000 - 18 000 zł " * 4000, "\n".join(f"Kraków {n}" for n in range(14000)), "\n".join(f"Remote - Berlin {n}" for n in range(14000)),
     "based in " * 14000, "remote " * 14000, "\n".join(f"Salary: {n} - {n + 1} zł" for n in range(8000)), "Location: " + "Kraków, " * 8000,
     "Hybrid, " * 12000 + "\n" + "w biurze " * 10000],
    ids=["pay word", "distinct city lines", "distinct mode lines", "based in", "remote", "distinct pay lines", "long label", "hybrid and office"],
)
def test_long_hostile_text_with_many_lines_is_read_quickly(text):
    started = time.perf_counter()
    with_text_facts(Offer(id="", source="x", attribution="y", title="t", description=text))
    assert time.perf_counter() - started < 10  # generous for slow CI runners
