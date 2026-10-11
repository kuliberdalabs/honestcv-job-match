"""Error hierarchy. Every error carries a message, a hint (how to fix it) and, when known, a file and line.

Exit codes: 0 ok · 1 user error · 2 invalid config/ledger · 3 partial collection · 4 check BLOCK · 5 internal failure.
Friendly errors never echo pydantic's input_value, because it can hold CV or ledger text.
"""

from __future__ import annotations

from typing import Any

EXIT_OK = 0
EXIT_USER = 1
EXIT_INVALID = 2
EXIT_PARTIAL = 3
EXIT_BLOCK = 4
EXIT_INTERNAL = 5


class HonestError(Exception):
    exit_code: int = EXIT_USER

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        path: str | None = None,
        line: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.path = path
        self.line = line

    def __str__(self) -> str:
        return self.message

    def where(self) -> str | None:
        """'honestcv.yml line 12', 'honestcv.yml' or None."""
        if self.path and self.line:
            return f"{self.path} line {self.line}"
        return self.path

class ConfigError(HonestError):
    exit_code = EXIT_INVALID

class LedgerError(HonestError):
    exit_code = EXIT_INVALID

class WorkspaceError(HonestError):
    exit_code = EXIT_USER

class SourceRefused(HonestError):
    """401/403/404 from a source, or robots.txt disallows the path."""
    exit_code = EXIT_PARTIAL

class SourceDrift(HonestError):
    """HTTP 200 with data the adapter could not read."""
    exit_code = EXIT_PARTIAL

class FetchNotAllowed(HonestError):
    """URL on a host outside the registry allow-list."""
    exit_code = EXIT_USER


# ---- pydantic validation errors, without the input values ----

_TYPE_TEXT = {
    "missing": "is required", "extra_forbidden": "is not a known key", "string_type": "must be text",
    "none_required": "must be empty", "bool_type": "must be true or false", "bool_parsing": "must be true or false",
    **dict.fromkeys(("list_type", "tuple_type", "set_type"), "must be a list"),
    **dict.fromkeys(("dict_type", "model_type", "model_attributes_type"), "must be a section of keys"),
    **dict.fromkeys(("int_type", "int_parsing", "int_from_float"), "must be a whole number"),
    **dict.fromkeys(("float_type", "float_parsing"), "must be a number"),
    **dict.fromkeys(("date_type", "date_parsing", "date_from_datetime_parsing", "date_from_datetime_inexact"), "must be a date like 2026-10-10"),
}


def format_loc(loc: tuple[Any, ...]) -> str:
    """('scoring', 'criteria', 2, 'weight') -> 'scoring.criteria[2].weight'."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out


def validation_messages(exc: Any) -> list[tuple[tuple[Any, ...], str]]:
    """(location, problem) pairs from a pydantic ValidationError, never including the input value.

    The problem text reads after the key name: "must be a list", "must be one of: a, b".
    """
    out: list[tuple[tuple[Any, ...], str]] = []
    try:
        details = exc.errors(include_url=False, include_input=False)
    except TypeError:  # pydantic releases without include_input
        details = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    for err in details:
        loc = tuple(p for p in err.get("loc", ()) if not (isinstance(p, str) and p.startswith("function-")))
        typ = err.get("type", "")
        ctx = err.get("ctx") or {}
        if typ == "literal_error":
            expected = str(ctx.get("expected", "")).replace("'", "")
            text = f"must be one of: {expected}" if expected else "has a value that is not allowed"
        elif typ in _TYPE_TEXT:
            text = _TYPE_TEXT[typ]
        elif typ.startswith("greater_than") or typ.startswith("less_than"):
            text = "is out of range"
        else:
            # pydantic's msg describes the rule, not the input; drop anything after a colon to be safe.
            text = "is not valid (" + str(err.get("msg", "")).split(":")[0].strip().lower() + ")"
        out.append((loc, text))
    return out
