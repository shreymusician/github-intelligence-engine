"""README structural extraction — Checkpoint 2.3.a.

Given the raw text of one already-persisted `repository_content` row with
`content_type='readme'` (Checkpoint 1.4.b/1.4.c), extracts the structural
features `repository_metrics` (Migration 006) already has columns for:
`readme_word_count`, `readme_section_count`, `readme_code_example_count`.
This module never calls GitHub, never touches PostgreSQL, never scores
readability (2.3.b's job), and never raises for malformed input.

Explicitly NOT this module's job:
- Readability scoring (processing/readability.py, 2.3.b).
- Deciding a documentation "quality score" (2.3.c — deferred; the frozen
  schema does not clearly specify where that value belongs).
- Persisting anything to PostgreSQL (2.3.d).
- Orchestrating this across many repositories (2.3.d).

Design decisions, each a deliberate simplification per this checkpoint's
"simplest deterministic approach, not a complex Markdown parser" scope
(documented here rather than left implicit):

- **Word count** is `len(content.split())` over the raw README text,
  unmodified — no Markdown stripping. This intentionally counts Markdown
  syntax characters that are attached to words (e.g. `**bold**` counts as
  one token) as part of the word count. A "true" prose word count would
  require stripping Markdown formatting first, which is exactly the kind
  of Markdown-parsing complexity this checkpoint was told to avoid absent
  an explicit requirement for it. `readme_word_count` is a rough size
  signal, not a linguistic word count.

- **Section detection** only recognizes ATX-style headings (`#`..`######`
  at the start of a line). Setext-style headings (a line of text followed
  by a line of `===` or `---`) are NOT detected — `---` alone is
  ambiguous with a Markdown horizontal rule, and resolving that
  ambiguity correctly requires more Markdown-parsing machinery than this
  checkpoint's scope calls for. This is a known, documented limitation:
  a README using only Setext headings would show zero detected sections.

- **Canonical sections** are exactly the six examined in the 2.3 design
  review (`CHECKPOINT_2_3_DESIGN_REVIEW.md` Sec6): Installation, Usage,
  API/Documentation, Examples, Contributing, License. A heading is
  matched against a small, curated synonym list per section via exact
  string equality after normalization (lowercase, punctuation stripped,
  whitespace collapsed) — no fuzzy matching, no semantic/embedding
  matching, mirroring 2.2.b's "accuracy over coverage" precedent exactly.
  A section already detected via one heading is not double-counted if a
  second heading also matches it (duplicate headings resolve to the same
  canonical section, not additional count) — `readme_section_count` is a
  count of *distinct recognized canonical sections* (0-6), not a count of
  heading occurrences.

- **Code example count** counts fenced code blocks (```` ``` ```` or
  `~~~` delimited) by counting open-fence transitions; inline code
  (single backticks) is not counted as a "code example." Fence-aware
  scanning also means headings that happen to appear *inside* a fenced
  code block are correctly not treated as real headings. An odd number
  of fences (a malformed/unterminated code block) degrades gracefully:
  everything after the last unmatched opening fence is treated as still
  "inside code" through end of file — no error is raised, no crash, and
  the block that was opened is still counted once.

Every function is stdlib-only (`re`, `dataclasses`) — no new third-party
dependency, matching 2.2.a's precedent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FENCE_RE = re.compile(r"^(```+|~~~+)")
_ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")

# Six canonical sections, per CHECKPOINT_2_3_DESIGN_REVIEW.md Sec6 — a
# small, curated synonym set per section, matched by exact string equality
# after normalization. Not exhaustive by design; extending this list is a
# taxonomy decision, not something to grow ad hoc (mirrors 2.2.b Sec6's
# "Unknown-Package Policy").
CANONICAL_SECTIONS: dict[str, frozenset[str]] = {
    "installation": frozenset({"installation", "install", "getting started", "setup"}),
    "usage": frozenset({"usage", "how to use", "example usage"}),
    "api_documentation": frozenset({"api", "api reference", "api documentation", "documentation", "docs"}),
    "examples": frozenset({"examples", "example", "demo", "demos"}),
    "contributing": frozenset({"contributing", "contribution", "contributions", "how to contribute"}),
    "license": frozenset({"license", "licence"}),
}


def _normalize_heading(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the same
    normalization discipline as 2.2.b's `_normalize`, applied to heading
    text instead of package names."""
    normalized = _NON_ALNUM_RE.sub("", text.lower())
    return _WHITESPACE_RE.sub(" ", normalized).strip()


@dataclass(frozen=True)
class _MarkdownStructure:
    headings: tuple[str, ...]
    code_block_count: int


def _scan_structure(content: str) -> _MarkdownStructure:
    """One fence-aware pass over the README's lines: collects ATX heading
    text (skipping anything inside a fenced code block) and counts fenced
    code blocks. A single pass avoids scanning the content twice for two
    related structural facts."""
    headings: list[str] = []
    code_block_count = 0
    in_fence = False
    fence_marker = ""

    for line in content.splitlines():
        stripped = line.strip()
        fence_match = _FENCE_RE.match(stripped)
        if fence_match:
            marker = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_marker = marker[0]  # '`' or '~', ignoring exact run length
                code_block_count += 1
            elif marker[0] == fence_marker:
                in_fence = False
                fence_marker = ""
            continue

        if in_fence:
            continue

        heading_match = _ATX_HEADING_RE.match(line)
        if heading_match:
            headings.append(heading_match.group(2))

    return _MarkdownStructure(headings=tuple(headings), code_block_count=code_block_count)


def _detect_sections(headings: tuple[str, ...]) -> frozenset[str]:
    normalized_headings = {_normalize_heading(heading) for heading in headings}
    detected: set[str] = set()
    for section_slug, synonyms in CANONICAL_SECTIONS.items():
        if normalized_headings & synonyms:
            detected.add(section_slug)
    return frozenset(detected)


@dataclass(frozen=True)
class ReadmeFeatures:
    """Structural features extracted from one README's raw text.

    Populated fields map directly onto `repository_metrics` columns
    (Migration 006): `word_count` -> `readme_word_count`, `section_count`
    -> `readme_section_count`, `code_example_count` ->
    `readme_code_example_count`. `detected_sections` is intermediate
    detail (which canonical sections were found) kept for testing and
    verification only — it is not a `repository_metrics` column and is
    not persisted separately; only its length (`section_count`) is.
    """

    word_count: int
    section_count: int
    code_example_count: int
    detected_sections: tuple[str, ...]


def extract_readme_features(content: str) -> ReadmeFeatures:
    """Extract structural features from one README's raw text.

    Never raises. Empty/whitespace-only content yields all-zero features
    — "no content" is a valid, non-error outcome (a README file that
    exists but is empty), matching 2.2.a's `parse_manifest` convention
    for empty input.
    """
    if not content.strip():
        return ReadmeFeatures(word_count=0, section_count=0, code_example_count=0, detected_sections=())

    structure = _scan_structure(content)
    detected = _detect_sections(structure.headings)
    word_count = len(content.split())

    return ReadmeFeatures(
        word_count=word_count,
        section_count=len(detected),
        code_example_count=structure.code_block_count,
        detected_sections=tuple(sorted(detected)),
    )
