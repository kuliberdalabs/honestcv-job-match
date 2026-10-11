"""Deterministic tailoring: select ledger sentences, write them with citation markers, check the
file from disk, remove blocked lines, and write the file to paste with the markers stripped."""

import datetime as dt

from pydantic import BaseModel

from honestcv import checker
from honestcv.errors import HonestError, LedgerError
from honestcv.ledger import lint_ledger
from honestcv.models import CheckResult, Claim, Ledger, MatchResult, Offer, Profile, RuleCode
from honestcv.report.markdown import esc
from honestcv.textutil import hit, safe_name
from honestcv.workspace import Workspace, atomic_write

MAX_SENTENCES = 8
_BUG = "This is a bug in honestcv. Run again with --debug and open an issue."


class TailorResult(BaseModel):
    out_dir: str
    check: CheckResult
    rounds: int
    removed: list[tuple[int, str, RuleCode]]
    gaps: list[str]


def select_claims(offer: Offer, match: MatchResult, ledger: Ledger) -> list[tuple[Claim, str]]:
    """Eligible claims ranked by how many offer requirements their skills and allowed text cover; claims with no
    overlap are left out; at most 8 sentences, each copied verbatim from the ledger. Ties go by claim id.
    A skills line is not a highlight: it stays in the CV."""
    terms = [r.term for r in match.requirements] or [*offer.skills, *offer.skills_nice]
    terms = list(dict.fromkeys(checker.canonical_skill(t) for t in terms))
    ranked = []
    for c in ledger.claims:
        sentences = [s for s in c.allowed if not c.draft or checker.exact_form(s) == checker.exact_form(c.summary)]
        sentences = [s for s in sentences if not checker.is_skills_line(s)]
        if c.kind == "ceiling" or c.visibility != "cv" or not sentences:
            continue
        skills = {checker.canonical_skill(s) for s in c.skills}
        text = " ".join(sentences).lower()
        overlap = sum(1 for t in terms if t in skills or hit(text, t))
        if overlap:
            ranked.append((-overlap, c.id, c, sentences))
    picked = [(c, s) for _, _, c, sentences in sorted(ranked, key=lambda r: (r[0], r[1])) for s in sentences]
    return picked[:MAX_SENTENCES]


def _render(offer: Offer, picks: list[tuple[Claim, str]]) -> str:
    title = esc(offer.title) + (f" · {esc(offer.company)}" if offer.company else "")  # offer text: no links, no images
    return f"# Highlights for {title}\n\n" + "".join(f"- {s} <!-- c:{c.id} -->\n" for c, s in picks)


def tailor(
    offer: Offer, match: MatchResult, profile: Profile, ledger: Ledger, ws: Workspace, *, today: dt.date
) -> TailorResult:
    """Write highlights.cited.md, check it from disk (removing BLOCK lines until none is left), then write
    highlights.md (markers stripped), check.md and gaps.md into out/tailored/<date>-<company>-<title>/."""
    errors = [x for x in lint_ledger(ledger, ws.root, today) if x.severity == "ERROR"]
    if errors:
        raise LedgerError(f"Tailoring is blocked: ledger.yml has {len(errors)} ERROR line{'s' if len(errors) > 1 else ''}.",
                          hint="Run: honestcv ledger lint, fix each ERROR, then try again.", path="ledger.yml", line=errors[0].line)  # fmt: skip
    name = safe_name(" ".join(x for x in (offer.company, offer.title) if x))
    out = ws.out / "tailored" / f"{today.isoformat()}-{name}"
    cited = out / "highlights.cited.md"
    atomic_write(cited, _render(offer, select_claims(offer, match, ledger)))
    removed: list[tuple[int, str, RuleCode]] = []
    rounds = 0
    while True:
        rounds += 1
        result = checker.check_document(cited, ledger, offer=offer, requirements=match.requirements, require_markers=True,
                                        ws_root=ws.root, today=today)  # fmt: skip
        blocked: dict[int, RuleCode] = {}
        for f in result.flags:
            if f.severity == "BLOCK" and f.rule != "LEDGER_LINT":
                blocked.setdefault(f.line, f.rule)
        if not blocked:
            break
        lines = cited.read_text(encoding="utf-8").split("\n")
        if rounds > len(lines):
            raise HonestError("Tailoring could not remove every blocked line.", hint=_BUG)
        removed += [(no, checker.strip_markers(lines[no - 1]).strip().removeprefix("- "), rule) for no, rule in sorted(blocked.items())]
        atomic_write(cited, "\n".join(line for no, line in enumerate(lines, 1) if no not in blocked))
    cited_text = cited.read_text(encoding="utf-8")
    plain = out / "highlights.md"
    atomic_write(plain, checker.strip_markers(cited_text))
    if plain.read_text(encoding="utf-8") != checker.strip_markers(cited.read_text(encoding="utf-8")):
        raise HonestError("highlights.md does not match highlights.cited.md without its markers.", hint=_BUG)
    report = checker.render_check(result, markdown=True)
    if removed:
        report += "\nRemoved before writing highlights.md:\n" + "".join(f'  line {no}  "{esc(text)}"  {rule}\n' for no, text, rule in removed)
    atomic_write(out / "check.md", f"# Claims check of highlights.cited.md\n\n{report}")
    gaps = "".join(f"- {esc(g)}\n" for g in result.gaps) or "- none\n"
    atomic_write(out / "gaps.md", f"# Gaps for {esc(offer.title)}\n\nNot added anywhere. Offer requirements your ledger does not back:\n\n{gaps}")
    return TailorResult(out_dir=out.relative_to(ws.root).as_posix(), check=result, rounds=rounds, removed=removed, gaps=result.gaps)
