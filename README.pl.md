# honestcv po polsku: od czego zacząć

Cała dokumentacja jest po angielsku. Ta strona mówi tylko, od czego zacząć.

honestcv dopasowuje oferty pracy do Twojego CV i pilnuje, żeby CV pod konkretną ofertę nie obiecywało więcej, niż zostało zrobione. Sprawdza każde zdanie, które jego reguły rozpoznają jako twierdzenie o Twojej pracy, i porównuje je z rejestrem tego, co naprawdę zrobiono i czym to potwierdzisz. Zdania, w których twierdzenie jest ujęte słowami nieznanymi regułom, nie są sprawdzane, więc PASS nie znaczy, że cały dokument jest prawdziwy. Działa na Twoim komputerze i nie korzysta z żadnego modelu AI. Komunikaty programu są po angielsku, ale polskie CV i polskie oferty czyta normalnie. Na dole jest tabela z tłumaczeniem najczęstszych komunikatów.

Przykłady ekranów poniżej pochodzą z prawdziwych uruchomień honestcv 0.1.0 na fikcyjnym CV (Maya Lindqvist). U Ciebie nazwy, liczby i daty będą inne.

## 1. Instalacja uv (raz na komputer)

uv to małe narzędzie, które instaluje programy w Pythonie. Pythona nie musisz mieć, uv pobierze go sam.

- macOS: naciśnij Cmd+Spacja, wpisz Terminal, naciśnij Enter i wklej:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows: otwórz Start, wpisz PowerShell, naciśnij Enter i wklej:
  `winget install --id=astral-sh.uv -e`
- Linux: otwórz terminal i wklej:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

Zamknij okno i otwórz nowe.

## 2. Instalacja honestcv

```
$ uv tool install https://github.com/kuliberdalabs/honestcv-job-match/archive/refs/tags/v0.1.0.zip
$ honestcv --version
honestcv 0.1.0
```

Git nie jest potrzebny. Jeśli terminal odpowie, że nie zna polecenia `honestcv`, uruchom `uv tool update-shell`, zamknij okno i otwórz nowe. Na Macu nie używaj Pythona wbudowanego w system, jest za stary.

## 3. Demo (bez Twoich plików)

```
$ honestcv demo --out ~/honestcv-demo
honestcv 0.1.0 · demo · offline · no API keys · sample data only · sample date 2026-10-10
Workspace: ~/honestcv-demo/   (English sample: Maya Lindqvist, fictional)

Read 8 sample offers; scored 4 against 2 CV variants.

  #  Match  Layer      Offer                                                  Best CV
  1   83%   CORE       Data Analyst · Tallgrove Analytics                     maya-analytics.md
  2   68%   STRETCH    Analytics Engineer · Quarry Lane Software              maya-analytics.md
  3   64%   CORE       Support Operations Analyst · Mossby Support Services   maya-support-ops.md
  4   62%   DOWNSHIFT  Junior Reporting Analyst · Example Bank                maya-analytics.md
  Filtered out: 4 (deny-list 1, commission only 1, expired 1, ledger ceiling 1)

Match % = how much of the offer your CV covers, by your own criteria. Not a hire probability.

Claims check of a planted cover letter (letters/cover-letter-planted.md):
  BLOCK · 4 lines blocked · 0 lines to review · 1 line passed
  ...
Report: ~/honestcv-demo/out/report-latest.html
Next: honestcv init --cv path/to/your-cv.pdf
```

Demo działa bez internetu. `--out ~/honestcv-demo` zostawia je w folderze `honestcv-demo` w Twoim folderze domowym; bez tej opcji demo trafia do folderu tymczasowego. Kroki 4 i 6 biorą z niego pliki. Procent dopasowania mówi, jaką część wymagań oferty pokrywa CV według Twoich kryteriów. To nie jest szansa na zatrudnienie. Raport otworzy się w przeglądarce.

## 4. Twój folder roboczy i Twoje CV

CV może być w PDF, DOCX, Markdown albo zwykłym tekście. Jeśli nazwa pliku ma spacje, weź ją w cudzysłów albo przeciągnij plik do okna terminala. Ekrany na tej stronie używają CV Mai z folderu demo, skopiowanego do folderu domowego jako `maya-cv.md`: `~/honestcv-demo/cvs/maya-analytics.md`.

```
$ honestcv init --cv maya-cv.md
Created ~/honestcv/
  honestcv.yml     settings; every line optional, defaults work
  cvs/             your CVs (.pdf .docx .md .txt); maya-cv.md copied here
  ledger.yml       your claims ledger, empty (start with: honestcv ledger draft)
  offers/inbox/    drop job-alert .eml, saved .html, .csv or .json files here
  evidence/        optional files your ledger points to
  out/             reports land here
  .gitignore       contains "*": nothing in this folder is meant for git
Read cvs/maya-cv.md: 10 skills, languages en (C1), sv.
Job titles and dates found under experience headings:
  Support Operations Analyst     2020-2026
  Customer Support Agent         2016-2020
  about 10 years in total (used only for "N+ years" requirements)
Set profile.level in honestcv.yml to get level matching. A guess from CV dates is wrong if you are changing careers.
Next: honestcv scan
```

Otwórz `~/honestcv/honestcv.yml` w edytorze tekstu i wpisz, czego szukasz. Każda linia jest opcjonalna. To, czego nie wpiszesz, nie jest oceniane. Stanowiska podaj w obu językach:

```yaml
profile:
  level: mid
  target_titles: ["analityk danych", "data analyst"]
preferences:
  remote: [remote, hybrid]
  countries: [PL]
  min_salary: [{amount: 12000, currency: PLN, unit: month}]
```

Stanowiska z `target_titles` służą też jako hasła wyszukiwania wysyłane do Himalayas. Opisuje to sekcja „Co wychodzi z Twojego komputera” niżej.

## 5. Oferty z polskich portali: wklej tekst

honestcv nie pobiera ofert z polskich portali ani z żadnej strony spoza swojej listy. Oferty dodajesz samodzielnie. Najprościej tak:

1. Otwórz ofertę w przeglądarce.
2. Zaznacz cały tekst (Ctrl+A, na Macu Cmd+A) i skopiuj (Ctrl+C, na Macu Cmd+C).
3. Uruchom:

```
$ honestcv add-offer --paste
Imported 1 offer from the clipboard: "Analityk danych" (title from the first line). It joins the next scan.
Saved as offers/inbox/added-2026-10-11-analityk-danych.json
```

Pierwsza linia tekstu staje się tytułem. Inny tytuł podasz tak: `--title "Analityk danych"`, a firmę: `--company "Example Bank"`.

Z wklejonego tekstu honestcv odczytuje też miasto i kraj, tryb pracy (zdalnie, hybrydowo, stacjonarnie), rodzaj umowy i widełki płacowe: z krótkich linii, na przykład "Kraków, praca hybrydowa, umowa o pracę", "Lokalizacja: Kraków" i "14 000 - 18 000 zł brutto miesięcznie", oraz ze zdań, które mówią o wynagrodzeniu, miejscu albo trybie pracy ("praca w Krakowie", "praca w pełni zdalna"). Miasto i kraj bierze z pierwszych linii tekstu albo z linii "Lokalizacja". Budżet, cena produktu, kraj klientów czy "remote sensing" w opisie nie są odczytywane jako warunki pracy. Dzięki temu wklejona oferta jest oceniana także pod kątem miejsca pracy i wynagrodzenia. Gdy tekst podaje dwie różne wartości, na przykład dwa różne przedziały płacy, honestcv zostawia je jako nieznane. To reguły przybliżone: gdy tekst jest niejasny, fakt zostaje nieznany, ale nietypowe zdanie wciąż może zostać źle odczytane, więc zanim zaufasz wynikowi, sprawdź te fakty w raporcie.

Jeśli w schowku jest sam link, honestcv go nie zaimportuje, bo nie otwiera stron. Otwórz ofertę, skopiuj jej tekst i uruchom `add-offer --paste` jeszcze raz. Sam link zapiszesz przez `honestcv add-offer --url LINK`.

### Powiadomienia e-mail z ofertami (.eml)

Większość portali wysyła nowe oferty mailem. Zapisz taki mail jako plik `.eml` w folderze `~/honestcv/offers/inbox/`. honestcv przeczyta go przy następnym skanowaniu.

- Gmail: otwórz wiadomość, potem menu wiadomości, potem Download message (Pobierz wiadomość).
- Outlook: otwórz wiadomość, potem menu wiadomości, potem Save as (Zapisz jako).
- Apple Mail: zaznacz wiadomość, potem menu File (Plik), potem Save As (Zachowaj jako).

Nazwy w menu różnią się między wersjami. Szukaj "pobierz wiadomość" albo "zapisz jako" i zostaw typ pliku `.eml`.

Z maila honestcv bierze tylko oferty. Plik `.eml`, który zapiszesz w `offers/inbox/` (albo który zapisze tam Twój agent), zostaje Twoim plikiem, razem z nagłówkami, czyli nadawcą i odbiorcami. honestcv czyta go przy każdym skanowaniu, a do raportów i do plików w `out/` trafiają tylko wyciągnięte z niego oferty. Mail leży tam, gdzie go zapisano, dopóki go nie usuniesz. Możesz też uruchomić `honestcv add-offer alert.eml`: honestcv zapisze wtedy w `offers/inbox/` same oferty jako plik JSON i nie skopiuje maila.

Oferta z samym tytułem i linkiem trafia do sekcji "Needs the full text": żeby ją ocenić, otwórz link i wklej tekst przez `add-offer --paste`.

### Zapisana strona i plik tekstowy

- Strona z ofertą: w przeglądarce Zapisz stronę jako (Ctrl+S, na Macu Cmd+S), potem `honestcv add-offer strona.html`. Safari: zapisz jako Źródło strony (Page Source), nie jako Archiwum www (Web Archive).
- Plik tekstowy: jeden plik to jedna oferta. W TextEdit na Macu wybierz najpierw Format, potem Zamień na zwykły tekst (Make Plain Text), i dopiero zapisz.

## Co wychodzi z Twojego komputera

Zanim uruchomisz pierwszy skan:

- Do Himalayas trafiają jako hasła wyszukiwania: nazwy umiejętności, które honestcv rozpoznał w Twoim CV (z własnej, wbudowanej listy, plus te dodane w `profile.skills_add`), oraz stanowiska wpisane w `honestcv.yml` (`profile.target_titles`) albo Twoje `search.keywords`, jeśli je ustawisz, a także kraj, który ustawisz. Żadne zdanie, stanowisko, firma ani data odczytane z Twojego CV nie są wysyłane.
- RemoteOK, We Work Remotely i Arbeitnow dostają zwykłe zapytania bez haseł wyszukiwania; oferty filtruje honestcv na Twoim komputerze. Greenhouse, Lever i Ashby dostają tylko nazwy tablic firm, które wpiszesz w ustawieniach.
- Przed pierwszym zapytaniem do źródła honestcv czyta plik robots.txt tej strony.
- Nic więcej z Twojego CV, rejestru ani e-maili nie opuszcza komputera. Brak telemetrii i analityki. honestcv nie korzysta z modeli AI.

## 6. Skanowanie

```
$ honestcv scan
```

honestcv pobiera oferty z publicznych API z listy `honestcv sources`, dokłada oferty z `offers/inbox/`, ocenia wszystko i otwiera raport. Ten ekran pochodzi z `honestcv scan --offline` z samym przykładowym plikiem 8 ofert z folderu demo (`honestcv add-offer ~/honestcv-demo/offers/inbox/sample-offers.json`) i z ustawieniami opisanymi w [START-HERE.md](START-HERE.md) (po angielsku), dlatego źródła mają stan `skipped`. Zwykłe skanowanie pokazuje przy nich `ok` i liczbę ofert. U Ciebie liczby będą inne: jeśli dodano tylko jedną ofertę, zobaczysz `inbox 1 file, 1 offer`.

```
$ honestcv scan --offline
Sources   himalayas skipped · remoteok skipped · weworkremotely skipped · arbeitnow skipped · inbox 1 file, 8 offers
Gates     filtered 4: deny-list 2 · commission only 1 · expired 1
Scored    4 offers x 1 CV · surfaced 4 · needs the full text 0 · new since last scan 4
Status    PASS
Report    out/report-latest.html  (also out/report-2026-10-11.md)
Matches   out/matches.csv  +4 rows (one per surfaced offer) · out/latest.json
```

Gdy któreś źródło odmówi dostępu, nie da się odczytać jego pliku robots.txt albo przyśle dane, których honestcv nie umie odczytać, status brzmi PARTIAL i podaje nazwę źródła. Brak wyników z takiego źródła nigdy nie wygląda jak "brak dopasowań".

## 7. Rejestr osiągnięć i highlights pod ofertę

Najpierw szkic rejestru Twoich osiągnięć (ledger) z CV:

```
$ honestcv ledger draft
Drafted 12 claims from 1 CV into ledger.yml. All are marked draft: true.
...
```

Potem highlights pod jedną ofertę. Numer oferty bierzesz z raportu (`out/report-latest.html`):

```
$ honestcv tailor 1
Offer   Data Analyst · Tallgrove Analytics (81%, CORE, best CV maya-cv.md)
Mode    deterministic: selects and orders your ledger sentences, never rewrites them
Picked  2 claims · 6 required skills have no ledger backing: sql, python, looker studio, excel, airflow, dbt (listed in gaps.md, added nowhere)
Check   REVIEW (read back from disk)
Wrote 2 bullets to paste into your CV
        out/tailored/2026-10-11-tallgrove-analytics-data-analyst/
        highlights.md  highlights.cited.md  check.md  gaps.md
Before you send the final CV or letter, run: honestcv check FILE
```

Wyniki leżą w folderze `out/tailored/<data>-<firma>-<tytuł oferty>/` w Twoim folderze roboczym. `highlights.md` to plik, z którego wklejasz punkty do CV; `gaps.md` wymienia wymagania oferty, których rejestr nie potwierdza. honestcv niczego nie dopisuje do Twojego CV: wybiera i porządkuje zdania z rejestru, a ich nie przepisuje.

## 8. Sprawdzenie CV albo listu przed wysłaniem

Sprawdź dokument, który chcesz wysłać, niezależnie od tego, kto go napisał. Tu jest to plik `highlights.md` z kroku 7:

```
$ honestcv check ~/honestcv/out/tailored/2026-10-11-tallgrove-analytics-data-analyst/highlights.md
REVIEW · 0 lines blocked · 2 lines to review · 0 lines passed

line 3  "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
  DRAFT_CLAIM        built-churn-prediction-model-python is a draft: your own CV line, not yet backed by evidence; confirm it in ledger.yml (REVIEW)  [claims[7].draft]
  safe: "Built a churn prediction model in Python with pandas and scikit-learn as a course capstone."
...
Your file was not changed. Fix the lines yourself, then run the check again before sending.
```

BLOCK oznacza, że zdanie obiecuje więcej, niż potwierdza Twój ledger: popraw je przed wysłaniem. REVIEW prosi o rzut oka. `check` nigdy nie zmienia Twojego pliku. Polskie zdania też są sprawdzane, na przykład "wdrożyłam" albo "wdrożenie" oznacza przekazanie czegoś do użytku i wymaga na to dowodu w ledgerze.

## Najczęstsze komunikaty

| Komunikat (po angielsku) | Co znaczy i co zrobić |
|---|---|
| `No workspace here.` | Nie ma tu folderu roboczego. Uruchom `honestcv init` albo dodaj `--workspace ŚCIEŻKA`. |
| `If the file name has spaces, put it in quotes or drag the file into this window.` | Nazwa pliku ma spacje. Weź ją w cudzysłów albo przeciągnij plik do okna terminala. |
| `Save it as PDF or DOCX, then try again.` | Tego formatu (.doc, .odt, .pages, .rtf) honestcv nie czyta. Zapisz CV jako PDF albo DOCX. |
| `This PDF is protected.` | PDF ma hasło albo blokadę. Wyeksportuj go jeszcze raz bez zabezpieczeń albo jako DOCX. |
| `This PDF has no text layer (it is an image).` | PDF to skan, czyli obrazek bez tekstu. Wyeksportuj CV z edytora jako PDF z tekstem albo DOCX. |
| `Polish letters in this PDF were read wrongly.` | Polskie litery w PDF odczytały się źle. Wyeksportuj CV jako DOCX albo PDF z tekstem. |
| `Your ledger is empty. Run: honestcv ledger draft` | Rejestr osiągnięć jest pusty. `honestcv ledger draft` zrobi szkic z Twojego CV. |
| `Tailoring is blocked until there are no ERROR lines.` | W ledgerze jest błąd (linia ERROR). `honestcv ledger lint` pokaże, gdzie i co poprawić. |
| `refused access (HTTP 403)` | Źródło ofert odmówiło dostępu. honestcv nie próbuje tego obejść. Spróbuj później albo dodaj oferty z pliku. |
| `That is only a link. honestcv does not open links.` | W schowku jest sam link. Otwórz ofertę w przeglądarce, skopiuj jej tekst i uruchom `honestcv add-offer --paste`. Sam link zapiszesz przez `honestcv add-offer --url LINK`. |
| `No offers from Polish job boards: honestcv does not fetch them.` | Nie dodano żadnej oferty z polskich portali. Zapisz maile z ofertami do `offers/inbox/` albo użyj `honestcv add-offer --paste`. |

Gdy coś nie działa, `honestcv doctor` sprawdzi folder roboczy, ustawienia, CV i ledger, niczego nie zmieniając.

honestcv służy do Twojego prywatnego szukania pracy. Nie publikuj jego wyników dalej. Repozytorium nie zawiera oprogramowania własnościowego, obchodzenia licencji, zbierania danych logowania ani obchodzenia uwierzytelniania. Za przestrzeganie regulaminów portali i API, z których korzystasz, w tym stron, z których dodajesz oferty, odpowiadasz Ty.

---

[README (English)](README.md) · Kuliberda Labs · kuliberda.ai
