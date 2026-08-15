"""Flesch readability scoring — Checkpoint 2.3.b.

Given the raw text of one already-persisted README (Checkpoint 1.4.b/
1.4.c), computes Flesch readability metrics — the methodology
`IMPLEMENTATION_ROADMAP.md` explicitly names for Checkpoint 2.3
("Readability score computed (Flesch-Kincaid)", line 778). This module
never calls GitHub, never touches PostgreSQL, never decides section/word
counts (2.3.a's job), and never raises for malformed input.

Both Flesch metrics are implemented, not one arbitrarily chosen, because
"Flesch-Kincaid" names a family of two related formulas computed from the
same underlying (words-per-sentence, syllables-per-word) inputs — the
project's own documents never disambiguate which one is meant, and
implementing both (per your explicit direction) removes the need to
guess:

- **Flesch Reading Ease** (0-100, higher = easier to read).
- **Flesch-Kincaid Grade Level** (approximate US school-grade number).

`repository_metrics.readme_readability_score` (Migration 006) is a single
unscaled `Numeric` column, so only one of these two values is persisted
by 2.3.d — Flesch Reading Ease, because it is the more standard "a
readability *score*" framing (a bounded, higher-is-better number) that
matches how the roadmap and design docs describe this as a documentation
*quality* signal. Flesch-Kincaid Grade Level is implemented and fully
tested here as a pure function but is NOT persisted anywhere — writing it
into `repository_metrics.extended_features` was considered and
deliberately rejected for this checkpoint: the roadmap's Definition of
Done only requires "a" readability score, and adding a second persisted
value not explicitly asked for would be exactly the kind of unprompted
scope growth this checkpoint was told to avoid. It remains available in
`ReadabilityScores.flesch_kincaid_grade_level` for any future checkpoint
that wants it.

Preprocessing (a deliberate, minimal simplification — not a Markdown
parser): fenced code blocks and inline code spans are stripped from the
text before readability scoring, because code text is not prose and
badly distorts sentence/word/syllable counts. Nothing else is stripped
(headings, emphasis markers, lists, links are left as-is) — the frozen
design does not specify a Markdown-cleaning rule beyond this, and this
is the simplest deterministic preprocessing step that meaningfully
protects the formula's inputs from non-prose text.

Syllable counting uses a standard heuristic (counting vowel-group runs,
with a trailing-silent-`e` adjustment) — not a dictionary lookup, since
no such dependency is available or required by the frozen design. This
is an approximation, same as most practical Flesch implementations;
exact syllable counts require a pronunciation dictionary, out of scope.

Stdlib-only (`re`, `dataclasses`) — no new third-party dependency,
matching 2.2.a/2.3.a's precedent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
_WORD_RE = re.compile(r"[A-Za-z']+")
_VOWEL_GROUP_RE = re.compile(r"[aeiouy]+")


def _strip_code(content: str) -> str:
    """Remove fenced code blocks and inline code spans before readability
    scoring. An unterminated fence (no closing ``` /~~~) is left as-is by
    this regex (it requires a matching close to strip) — the unstripped
    code text would then be scored as if it were prose, a known,
    documented degrade-gracefully limitation rather than a crash."""
    without_fences = _CODE_FENCE_RE.sub(" ", content)
    return _INLINE_CODE_RE.sub(" ", without_fences)


def _count_syllables(word: str) -> int:
    """Heuristic syllable count: number of vowel-group runs, decremented
    by one for a trailing silent `e` (but never below 1). Every word has
    at least one syllable."""
    lowered = word.lower()
    groups = _VOWEL_GROUP_RE.findall(lowered)
    count = len(groups)
    if lowered.endswith("e") and not lowered.endswith("le") and count > 1:
        count -= 1
    return max(count, 1)


@dataclass(frozen=True)
class ReadabilityScores:
    """Both Flesch metrics for one README's prose text (code stripped).

    `None` for both fields when there is no scorable text (empty input,
    or input that strips down to nothing, e.g. a README that is entirely
    one big code block) — readability is undefined for zero words, not
    zero, matching this module's "never fabricate a value" discipline.
    """

    flesch_reading_ease: float | None
    flesch_kincaid_grade_level: float | None


def compute_readability(content: str) -> ReadabilityScores:
    """Compute Flesch Reading Ease and Flesch-Kincaid Grade Level for one
    README's text. Never raises."""
    text = _strip_code(content)
    words = _WORD_RE.findall(text)
    if not words:
        return ReadabilityScores(flesch_reading_ease=None, flesch_kincaid_grade_level=None)

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    sentence_count = len(sentences) or 1  # untermined/no punctuation: treat as one sentence

    word_count = len(words)
    syllable_count = sum(_count_syllables(word) for word in words)

    words_per_sentence = word_count / sentence_count
    syllables_per_word = syllable_count / word_count

    reading_ease = 206.835 - (1.015 * words_per_sentence) - (84.6 * syllables_per_word)
    grade_level = (0.39 * words_per_sentence) + (11.8 * syllables_per_word) - 15.59

    return ReadabilityScores(
        flesch_reading_ease=round(reading_ease, 2),
        flesch_kincaid_grade_level=round(grade_level, 2),
    )
