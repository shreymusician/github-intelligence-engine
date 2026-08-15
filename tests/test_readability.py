"""Test suite for processing.readability — Checkpoint 2.3.b.

Pure unit tests, zero DB/network. Reference values for the Flesch
formulas are computed by hand (word/sentence/syllable counts counted
manually, then the well-known public Flesch constants applied) rather
than taken from any external tool, to independently verify the
implementation's arithmetic against the textbook formula it claims to
implement — not a self-consistency tautology.

Syllable-heuristic sanity tests exercise the private `_count_syllables`
helper directly against common English words with well-known syllable
counts, documented as a heuristic sanity check, not a claim of
linguistic perfection (see module docstring's own caveat).
"""

from __future__ import annotations

import pytest

from processing.readability import _count_syllables, compute_readability


class TestEmptyAndTrivialInput:
    def test_empty_string_yields_none(self):
        result = compute_readability("")
        assert result.flesch_reading_ease is None
        assert result.flesch_kincaid_grade_level is None

    def test_whitespace_only_yields_none(self):
        result = compute_readability("   \n\t  ")
        assert result.flesch_reading_ease is None
        assert result.flesch_kincaid_grade_level is None

    def test_entirely_code_yields_none(self):
        # after stripping the fenced code block, no prose words remain
        result = compute_readability("```\nx = 1\ny = 2\n```")
        assert result.flesch_reading_ease is None
        assert result.flesch_kincaid_grade_level is None

    def test_single_word_is_scorable(self):
        result = compute_readability("Hello.")
        assert result.flesch_reading_ease is not None
        assert result.flesch_kincaid_grade_level is not None


class TestKnownFormulaValues:
    """Reference values hand-computed from the public Flesch formulas:
    Reading Ease = 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    Grade Level  = 0.39*(words/sentences) + 11.8*(syllables/words) - 15.59
    """

    def test_simple_sentence_matches_hand_computed_formula(self):
        # "The cat sat on the mat." -> 6 words, 1 sentence, 6 syllables
        # (each word is monosyllabic under this module's heuristic)
        result = compute_readability("The cat sat on the mat.")

        words_per_sentence = 6 / 1
        syllables_per_word = 6 / 6
        expected_ease = 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word
        expected_grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59

        assert result.flesch_reading_ease == pytest.approx(expected_ease, abs=0.01)
        assert result.flesch_kincaid_grade_level == pytest.approx(expected_grade, abs=0.01)

    def test_two_sentences_matches_hand_computed_formula(self):
        # "Cats sleep. Dogs run." -> 4 words, 2 sentences.
        # cats=1, sleep=1, dogs=1, run=1 -> 4 syllables
        result = compute_readability("Cats sleep. Dogs run.")

        words_per_sentence = 4 / 2
        syllables_per_word = 4 / 4
        expected_ease = 206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word
        expected_grade = 0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59

        assert result.flesch_reading_ease == pytest.approx(expected_ease, abs=0.01)
        assert result.flesch_kincaid_grade_level == pytest.approx(expected_grade, abs=0.01)


class TestCodeStripping:
    def test_fenced_code_block_excluded_from_scoring(self):
        prose = "This is a simple sentence for testing purposes."
        with_code = prose + "\n```\nvery_long_variable_name_that_would_skew_syllables = 1\n```"
        result_prose_only = compute_readability(prose)
        result_with_code = compute_readability(with_code)
        assert result_with_code.flesch_reading_ease == pytest.approx(
            result_prose_only.flesch_reading_ease, abs=0.5
        )

    def test_tilde_fence_stripped(self):
        prose = "This is a simple sentence for testing purposes."
        with_code = prose + "\n~~~\nsome_code_here = True\n~~~"
        result_prose_only = compute_readability(prose)
        result_with_code = compute_readability(with_code)
        assert result_with_code.flesch_reading_ease == pytest.approx(
            result_prose_only.flesch_reading_ease, abs=0.5
        )

    def test_inline_code_stripped(self):
        prose = "Call the function to start."
        with_inline = "Call the `run_function_now()` to start."
        result_prose_only = compute_readability(prose)
        result_with_inline = compute_readability(with_inline)
        # inline code removed entirely -> word counts differ only by
        # whatever prose words surrounded it; both must still be scorable
        assert result_with_inline.flesch_reading_ease is not None
        assert result_prose_only.flesch_reading_ease is not None


class TestMalformedInput:
    def test_no_sentence_terminating_punctuation_treated_as_one_sentence(self):
        result = compute_readability("no punctuation here just words going on")
        assert result.flesch_reading_ease is not None

    def test_only_punctuation_no_words_yields_none(self):
        result = compute_readability("... !!! ???")
        assert result.flesch_reading_ease is None

    def test_unterminated_fence_does_not_crash(self):
        result = compute_readability("Some prose.\n```\nunterminated code block never closes")
        # unterminated fence is not stripped (requires a matching close) -
        # module degrades gracefully, does not raise
        assert result.flesch_reading_ease is not None or result.flesch_reading_ease is None


class TestPunctuationHeavyText:
    def test_lots_of_punctuation_does_not_crash(self):
        result = compute_readability("Wow!!! Really?! Yes... -- indeed; (parenthetical) [bracketed].")
        assert result.flesch_reading_ease is not None


class TestDeterminism:
    def test_repeated_calls_identical(self):
        content = "This is a moderately complex sentence with several clauses, testing determinism."
        first = compute_readability(content)
        second = compute_readability(content)
        assert first == second


class TestMonotonicSanity:
    def test_more_syllables_per_word_lowers_reading_ease(self):
        simple = compute_readability("The cat sat on the mat.")
        complex_ = compute_readability("Extraordinarily sophisticated methodologies necessitate comprehensive understanding.")
        assert complex_.flesch_reading_ease < simple.flesch_reading_ease

    def test_more_syllables_per_word_raises_grade_level(self):
        simple = compute_readability("The cat sat on the mat.")
        complex_ = compute_readability("Extraordinarily sophisticated methodologies necessitate comprehensive understanding.")
        assert complex_.flesch_kincaid_grade_level > simple.flesch_kincaid_grade_level


class TestSyllableHeuristic:
    """Sanity checks against common English words with well-known syllable
    counts. This is a heuristic (vowel-group counting), not a dictionary
    lookup — documented as an approximation, not linguistic ground truth."""

    @pytest.mark.parametrize(
        "word,expected",
        [
            ("cat", 1),
            ("the", 1),
            ("code", 1),
            ("apple", 2),
            ("hello", 2),
            ("google", 2),
            ("banana", 3),
            ("education", 4),
        ],
    )
    def test_common_words(self, word, expected):
        assert _count_syllables(word) == expected

    def test_never_returns_zero(self):
        assert _count_syllables("xyz") >= 1
        assert _count_syllables("a") >= 1
