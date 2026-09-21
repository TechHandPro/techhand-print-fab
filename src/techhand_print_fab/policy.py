"""Refuse 1:1 copies of proprietary commercial products.

Original parts, author-designed fixtures, and interoperable geometry from the
caller's own measurements are in scope. A request whose purpose is to clone a
commercial product is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from techhand_print_fab.results import envelope, failure

REPRODUCTION_ORIGINAL = "original"
REPRODUCTION_FIXTURE = "interoperable_fixture"
REPRODUCTION_CLONE = "proprietary_clone"
ALLOWED_REPRODUCTION = frozenset(
    {REPRODUCTION_ORIGINAL, REPRODUCTION_FIXTURE, REPRODUCTION_CLONE}
)

REFUSAL_MESSAGE = (
    "Refused: this server will not make a 1:1 copy of a proprietary commercial "
    "product. Describe an original part, a fixture you authored, or interoperable "
    "geometry from your own measurements."
)

_FLAGS = re.IGNORECASE | re.DOTALL
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exact_copy", re.compile(r"\bexact\s+(?:copy|replica|clone|duplicate)\b", _FLAGS)),
    (
        "one_to_one",
        re.compile(
            r"\b(?:1\s*:\s*1|one[\s-]to[\s-]one)\b.{0,48}\b(?:copy|clone|replica|duplicate)\b",
            _FLAGS,
        ),
    ),
    (
        "one_to_one_reversed",
        re.compile(
            r"\b(?:copy|clone|replica|duplicate)\b.{0,48}\b(?:1\s*:\s*1|one[\s-]to[\s-]one)\b",
            _FLAGS,
        ),
    ),
    ("counterfeit", re.compile(r"\b(?:counterfeit|knock[\s-]?off)\b", _FLAGS)),
    ("proprietary_clone", re.compile(r"\bproprietary\s+clone\b", _FLAGS)),
    ("identical_replica", re.compile(r"\bidentical\s+(?:copy|replica)\b", _FLAGS)),
    (
        "pirate_model",
        re.compile(r"\bpirated?\b.{0,24}\b(?:stl|model|cad|part|product)\b", _FLAGS),
    ),
    (
        "commercial_copy",
        re.compile(
            r"\b(?:copy|clone|duplicate|replicate)\b.{0,60}\b(?:commercial|proprietary|oem)\b"
            r".{0,24}\b(?:product|part|model|design)\b",
            _FLAGS,
        ),
    ),
    (
        "commercial_copy_reversed",
        re.compile(
            r"\b(?:commercial|proprietary|oem)\b.{0,40}\b(?:product|part|model|design)\b"
            r".{0,60}\b(?:copy|clone|duplicate|replicate)\b",
            _FLAGS,
        ),
    ),
    (
        "reverse_engineer_commercial",
        re.compile(
            r"\breverse[\s-]?engineer\b.{0,80}\b(?:commercial|proprietary|oem|patented)\b",
            _FLAGS,
        ),
    ),
)


@dataclass(frozen=True)
class Refusal:
    code: str


def normalize_reproduction(reproduction: str) -> str:
    value = (reproduction or "").strip().lower() or REPRODUCTION_ORIGINAL
    if value not in ALLOWED_REPRODUCTION:
        raise ValueError(
            "reproduction must be original, interoperable_fixture, or proprietary_clone"
        )
    return value


def assess(*texts: str, reproduction: str) -> Refusal | None:
    """Return a refusal when the ask is a proprietary clone. reproduction is pre-validated."""
    if reproduction == REPRODUCTION_CLONE:
        return Refusal(code="reproduction_proprietary_clone")
    blob = "\n".join(text for text in texts if text)
    for code, pattern in _PATTERNS:
        if pattern.search(blob):
            return Refusal(code=code)
    return None


def screen(*texts: str, reproduction: str = REPRODUCTION_ORIGINAL) -> dict[str, object] | None:
    """None when the request may proceed. Otherwise a tool payload."""
    try:
        normalized = normalize_reproduction(reproduction)
    except ValueError as exc:
        return failure(str(exc))
    refusal = assess(*texts, reproduction=normalized)
    if refusal is None:
        return None
    return refusal_payload(refusal)


def refusal_payload(refusal: Refusal) -> dict[str, object]:
    return envelope(
        ok=False,
        refused=True,
        policy="proprietary_clone",
        policy_code=refusal.code,
        message=REFUSAL_MESSAGE,
    )
