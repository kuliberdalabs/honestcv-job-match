"""Himalayas adapter."""

import datetime as dt
import json
from collections.abc import Sequence

from honestcv.config import SearchConfig
from honestcv.importers.urls import country_code, make_salary, map_contract, to_date
from honestcv.models import Offer
from honestcv.sources.base import expect, make_offer, map_entries
from honestcv.sources.http import HttpClient

URL = "https://himalayas.app/jobs/api/search"
MAX_KEYWORDS, MAX_PAGES = 3, 3
_LEVELS = {"entry-level": "junior", "entry level": "junior", "junior": "junior", "mid-level": "mid", "mid level": "mid", "senior": "senior"}


class HimalayasAdapter:
    """Up to 3 keywords x 3 pages of 20 jobs. `countries` (ISO codes) narrows the search only when exactly one is given."""

    id = "himalayas"

    def __init__(self, countries: Sequence[str] = ()):
        self.countries = list(countries)

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]:
        offers: list[Offer] = []
        for keyword in search.keywords[:MAX_KEYWORDS] or [""]:
            base = {k: v for k, v in (("q", keyword), ("country", self.countries[0] if len(self.countries) == 1 else "")) if v}
            params: dict | None = base
            for _ in range(MAX_PAGES):
                payload = expect(json.loads(http.get(self.id, URL, params=params).text), dict, "the answer")
                jobs = expect(payload.get("jobs"), list, "the job list")
                offers += map_entries(jobs, self._offer)
                params = self._next(payload, base, len(jobs))
                if params is None:
                    break
        return list({o.id: o for o in offers}.values())

    @staticmethod
    def _next(payload: dict, base: dict, count: int) -> dict | None:
        """Cursor paging (nextCursor) when the answer has it, else the older offset paging."""
        if payload.get("nextCursor"):
            return {**base, "cursor": payload["nextCursor"]}
        offset, limit, total = payload.get("offset"), payload.get("limit"), payload.get("totalCount")
        if count and all(isinstance(n, int) for n in (offset, limit, total)) and offset + limit < total:
            return {**base, "offset": offset + limit}
        return None

    @staticmethod
    def _offer(job: dict) -> Offer | None:
        places = [str(p) for p in job.get("locationRestrictions") or []]
        levels = {_LEVELS[s.casefold()] for s in map(str, job.get("seniority") or []) if s.casefold() in _LEVELS}
        return make_offer(
            "himalayas", "Himalayas", job["title"], job.get("guid") or job.get("applicationLink"),
            html_text=job.get("description") or job.get("excerpt"), company=job.get("companyName"), apply_url=job.get("applicationLink"),
            location="Worldwide" if not places else ", ".join(places) if len(places) <= 5 else f"{len(places)} countries",
            countries=[c for c in map(country_code, places) if c], remote="remote", contract=map_contract(job.get("employmentType")),
            level=levels.pop() if len(levels) == 1 else None,
            salary=make_salary(job.get("minSalary"), job.get("maxSalary"), job.get("currency"), job.get("salaryPeriod")),
            posted_at=to_date(job.get("pubDate")), valid_through=to_date(job.get("expiryDate")),
        )  # fmt: skip
