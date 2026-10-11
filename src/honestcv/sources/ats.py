"""Greenhouse, Lever and Ashby adapter: the public job boards of companies the person names."""

import datetime as dt
import html
import json
import re
from urllib.parse import quote

from honestcv.config import SearchConfig
from honestcv.errors import SourceRefused
from honestcv.importers.urls import country_code, make_salary, map_contract, to_date
from honestcv.models import Offer
from honestcv.sources.base import expect, make_offer, map_entries
from honestcv.sources.http import HttpClient, SourceError

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


class _Boards:
    """One request per configured board name. A board that fails is reported in `problems`; the others still count."""

    id = ""
    empty_message = "no companies are listed under search.companies"

    def __init__(self) -> None:
        self.problems: list[str] = []

    def targets(self, search: SearchConfig) -> list[tuple[str, str]]:
        raise NotImplementedError

    def board(self, http: HttpClient, name: str, host: str) -> list[Offer]:
        raise NotImplementedError

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]:
        self.problems = []
        offers: list[Offer] = []
        for name, host in dict.fromkeys(self.targets(search)):
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
                self.problems.append(f"'{name[:30]}' is not a valid board name")
                continue
            try:
                offers += self.board(http, name, host)
            except (SourceRefused, SourceError) as exc:
                self.problems.append(f"{name}: {exc.message}")
        return list({o.id: o for o in offers}.values())

    def get_json(self, http: HttpClient, url: str, params: dict | None = None) -> dict | list:
        return json.loads(http.get(self.id, url, params=params).text)


class GreenhouseAdapter(_Boards):
    id = "greenhouse"

    def targets(self, search: SearchConfig) -> list[tuple[str, str]]:
        return [(name, "boards-api.greenhouse.io") for name in search.companies.greenhouse]

    def board(self, http: HttpClient, name: str, host: str) -> list[Offer]:
        data = expect(self.get_json(http, f"https://{host}/v1/boards/{quote(name, safe='')}/jobs", {"content": "true"}), dict, "the answer")
        return map_entries(expect(data.get("jobs"), list, "the job list"), lambda job: self._offer(name, job))

    @staticmethod
    def _offer(name: str, job: dict) -> Offer | None:
        company, place = job.get("company_name") or name, str((job.get("location") or {}).get("name") or "")
        number = job.get("requisition_id")
        return make_offer(
            "greenhouse", f"{company} careers page (Greenhouse)", job["title"], job.get("absolute_url"),
            html_text=html.unescape(str(job.get("content") or "")), company=company, location=place or None,
            remote="remote" if re.search(r"\bremote\b", place, re.IGNORECASE) else None,
            posted_at=to_date(job.get("first_published") or job.get("updated_at")),
            requisition_id=f"{name}:{number}" if number else None,
        )  # fmt: skip


class LeverAdapter(_Boards):
    id = "lever"

    def targets(self, search: SearchConfig) -> list[tuple[str, str]]:
        return [(n, "api.lever.co") for n in search.companies.lever] + [(n, "api.eu.lever.co") for n in search.companies.lever_eu]

    def board(self, http: HttpClient, name: str, host: str) -> list[Offer]:
        data = expect(self.get_json(http, f"https://{host}/v0/postings/{quote(name, safe='')}", {"mode": "json"}), list, "the job list")
        return map_entries(data, lambda job: self._offer(name, job))

    @staticmethod
    def _offer(name: str, job: dict) -> Offer | None:
        category, pay = job.get("categories") or {}, job.get("salaryRange") or {}
        lists = "".join(f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>" for lst in job.get("lists") or [])
        code = country_code(job.get("country"))
        return make_offer(
            "lever", f"{name} careers page (Lever)", job["text"], job.get("hostedUrl"),
            html_text=f"{job.get('description') or ''}{lists}{job.get('additional') or ''}", company=name, apply_url=job.get("applyUrl"),
            location=category.get("location") or ", ".join(category.get("allLocations") or []) or None,
            countries=[code] if code else [], remote=_WORKPLACE.get(str(job.get("workplaceType")).casefold()),
            contract=map_contract(category.get("commitment")), salary=make_salary(pay.get("min"), pay.get("max"), pay.get("currency"), pay.get("interval")),
            posted_at=to_date(job.get("createdAt")),
        )  # fmt: skip


class AshbyAdapter(_Boards):
    id = "ashby"

    def targets(self, search: SearchConfig) -> list[tuple[str, str]]:
        return [(name, "api.ashbyhq.com") for name in search.companies.ashby]

    def board(self, http: HttpClient, name: str, host: str) -> list[Offer]:
        url = f"https://{host}/posting-api/job-board/{quote(name, safe='')}"
        data = expect(self.get_json(http, url, {"includeCompensation": "true"}), dict, "the answer")
        jobs = [j for j in expect(data.get("jobs"), list, "the job list") if not isinstance(j, dict) or j.get("isListed", True)]
        return map_entries(jobs, lambda job: self._offer(name, job))

    @staticmethod
    def _offer(name: str, job: dict) -> Offer | None:
        address = (job.get("address") or {}).get("postalAddress") or {}
        parts = [job.get("location"), *(s.get("location") for s in job.get("secondaryLocations") or [])]
        pay = next((c for c in (job.get("compensation") or {}).get("summaryComponents") or []
                    if c.get("compensationType") == "Salary" and c.get("currencyCode")), {})  # fmt: skip
        code = country_code(address.get("addressCountry"))
        return make_offer(
            "ashby", f"{name} careers page (Ashby)", job["title"], job.get("jobUrl"), html_text=job.get("descriptionHtml"), company=name,
            apply_url=job.get("applyUrl"), location=", ".join(dict.fromkeys(p for p in parts if p)) or None,
            countries=[code] if code else [], remote=_WORKPLACE.get(str(job.get("workplaceType")).casefold()),
            contract=map_contract(job.get("employmentType")),
            salary=make_salary(pay.get("minValue"), pay.get("maxValue"), pay.get("currencyCode"), pay.get("interval")),
            posted_at=to_date(job.get("publishedAt")),
        )  # fmt: skip
