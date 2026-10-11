"""Arbeitnow adapter."""

import datetime as dt
import html
import json

from honestcv.config import SearchConfig
from honestcv.importers.urls import map_contract, to_date
from honestcv.models import Offer
from honestcv.sources.base import expect, make_offer, map_entries
from honestcv.sources.http import HttpClient

URL = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 3


class ArbeitnowAdapter:
    """Pages 1 to 3 of the job board feed; stops early when the answer has no next page."""

    id = "arbeitnow"

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]:
        offers: list[Offer] = []
        for page in range(1, MAX_PAGES + 1):
            payload = expect(json.loads(http.get(self.id, URL, params={"page": page}).text), dict, "the answer")
            offers += map_entries(expect(payload.get("data"), list, "the job list"), self._offer)
            if not (payload.get("links") or {}).get("next"):
                break
        return list({o.id: o for o in offers}.values())

    @staticmethod
    def _offer(job: dict) -> Offer | None:
        tags = ", ".join(str(t) for t in job.get("tags") or [])
        return make_offer(
            "arbeitnow", "Arbeitnow", job["title"], job.get("url"),
            html_text=str(job.get("description") or "") + (f"<p>Tags: {html.escape(tags)}</p>" if tags else ""),
            company=job.get("company_name"), location=str(job.get("location") or "").strip() or None,
            remote="remote" if job.get("remote") else None, contract=map_contract(" ".join(map(str, job.get("job_types") or []))),
            posted_at=to_date(job.get("created_at")),
        )  # fmt: skip
