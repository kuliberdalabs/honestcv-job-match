"""We Work Remotely RSS adapter."""

import datetime as dt
import html
import re
import xml.etree.ElementTree as ET

from honestcv.config import SearchConfig
from honestcv.errors import SourceDrift, SourceRefused
from honestcv.importers.urls import country_code, map_contract, to_date
from honestcv.models import Offer
from honestcv.sources.base import make_offer, map_entries
from honestcv.sources.http import HttpClient, SourceError

FEED = "https://weworkremotely.com/remote-jobs.rss"
CATEGORY = "https://weworkremotely.com/categories/{}.rss"


class WeWorkRemotelyAdapter:
    """The main RSS feed plus the category feeds named in search.weworkremotely_feeds. A feed that has a
    DOCTYPE or an entity declaration is rejected, not parsed. Problems with a category feed go to `problems`."""

    id = "weworkremotely"

    def __init__(self) -> None:
        self.problems: list[str] = []

    def fetch(self, http: HttpClient, search: SearchConfig, today: dt.date) -> list[Offer]:
        self.problems = []
        feeds = [("", FEED)]
        for name in dict.fromkeys(search.weworkremotely_feeds):
            if re.fullmatch(r"[a-z0-9-]{1,80}", name):
                feeds.append((name, CATEGORY.format(name)))
            else:
                self.problems.append(f"feed '{name[:30]}' ignored: names use lower-case letters, digits and hyphens")
        offers: list[Offer] = []
        for name, url in feeds:
            try:
                offers += self._feed(http.get(self.id, url).text)
            except (SourceRefused, SourceError, SourceDrift) as exc:
                if not name:
                    raise
                self.problems.append(f"feed {name}: {exc.message}")
        return list({o.id: o for o in offers}.values())

    def _feed(self, text: str) -> list[Offer]:
        if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
            raise SourceDrift("The feed contains a DOCTYPE or entity declaration, which honestcv rejects.")
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise SourceDrift("The feed is not valid XML.") from None
        channel = root.find("channel")
        if root.tag != "rss" or channel is None:
            raise SourceDrift("The feed is not an RSS channel.")
        return map_entries(channel.findall("item"), self._offer)

    @staticmethod
    def _offer(item: ET.Element) -> Offer | None:
        def text(tag: str) -> str:
            return (item.findtext(tag) or "").strip()

        company, colon, title = text("title").partition(": ")
        if not colon:
            company, title = "", text("title")
        region, skills = text("region"), text("skills")
        code = None if re.search(r"anywhere|world", region, re.IGNORECASE) else country_code(text("country"))
        return make_offer(
            "weworkremotely", "We Work Remotely", title, text("link") or text("guid"),
            html_text=text("description") + (f"<p>Skills: {html.escape(skills)}</p>" if skills else ""), company=company or None,
            location=region or None, countries=[code] if code else [], remote="remote", contract=map_contract(text("type")),
            posted_at=to_date(text("pubDate")), valid_through=to_date(text("expires_at")),
        )  # fmt: skip
