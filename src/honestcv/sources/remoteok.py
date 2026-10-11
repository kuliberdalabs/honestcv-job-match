"""RemoteOK adapter."""

import datetime as dt
import html
import json

from honestcv.config import SearchConfig
from honestcv.importers.urls import make_salary, to_date
from honestcv.models import Offer
from honestcv.sources.base import expect, make_offer, map_entries
from honestcv.sources.http import HttpClient


class RemoteOkAdapter:
    """One request for the whole feed. Element 0 is a legal notice and is skipped. The feed has no search,
    so keywords are applied afterwards by collect()."""

    id = "remoteok"

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]:
        data = expect(json.loads(http.get(self.id, "https://remoteok.com/api").text), list, "the job list")
        entries = data[1:] if data and isinstance(data[0], dict) and "legal" in data[0] else data
        return list({o.id: o for o in map_entries(entries, self._offer)}.values())

    @staticmethod
    def _offer(job: dict) -> Offer | None:
        tags = ", ".join(str(t) for t in job.get("tags") or [])
        return make_offer(
            "remoteok", "RemoteOK", job["position"], job.get("url"),
            html_text=str(job.get("description") or "") + (f"<p>Tags: {html.escape(tags)}</p>" if tags else ""),
            company=job.get("company"), apply_url=job.get("apply_url"), location=str(job.get("location") or "").strip() or None,
            remote="remote", salary=make_salary(job.get("salary_min"), job.get("salary_max"), None, None),
            posted_at=to_date(job.get("date") or job.get("epoch")),
        )  # fmt: skip
