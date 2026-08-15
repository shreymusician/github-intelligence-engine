"""Test suite for processing.readme_parser — Checkpoint 2.3.a.

Pure unit tests, zero DB/network, mirroring tests/test_manifest_parser.py's
style: one TestXxx class per concern, calling extract_readme_features()
directly and asserting on the returned ReadmeFeatures.
"""

from __future__ import annotations

from processing.readme_parser import CANONICAL_SECTIONS, extract_readme_features


class TestEmptyAndTrivialInput:
    def test_empty_string(self):
        result = extract_readme_features("")
        assert result.word_count == 0
        assert result.section_count == 0
        assert result.code_example_count == 0
        assert result.detected_sections == ()

    def test_whitespace_only(self):
        result = extract_readme_features("   \n\n\t  \n  ")
        assert result.word_count == 0
        assert result.section_count == 0
        assert result.code_example_count == 0

    def test_very_short_readme_no_headings(self):
        result = extract_readme_features("A tiny project.")
        assert result.word_count == 3
        assert result.section_count == 0
        assert result.code_example_count == 0


class TestWordCount:
    def test_counts_whitespace_separated_tokens(self):
        result = extract_readme_features("one two three four five")
        assert result.word_count == 5

    def test_multiple_lines_and_blank_lines(self):
        content = "line one here\n\nline two here\n"
        result = extract_readme_features(content)
        assert result.word_count == 6

    def test_punctuation_heavy_text_still_counted_as_tokens(self):
        content = "Hello, world! This -- is punctuation-heavy... text; really?"
        result = extract_readme_features(content)
        assert result.word_count == len(content.split())
        assert result.word_count > 0


class TestHeadingLevels:
    def test_h1_through_h6_all_detected(self):
        content = "\n".join(f"{'#' * level} Installation" for level in range(1, 7))
        result = extract_readme_features(content)
        assert "installation" in result.detected_sections
        assert result.section_count == 1  # same section, six headings -> one canonical match

    def test_heading_requires_space_after_hashes(self):
        # "#NoSpace" is not a valid ATX heading per this module's regex
        result = extract_readme_features("#NoSpace\nsome body text")
        assert result.section_count == 0

    def test_trailing_hashes_stripped(self):
        result = extract_readme_features("## Installation ##")
        assert "installation" in result.detected_sections

    def test_non_canonical_heading_not_detected(self):
        result = extract_readme_features("## Acknowledgements\nthanks to everyone")
        assert result.section_count == 0
        assert result.detected_sections == ()


class TestCanonicalSections:
    def test_all_six_canonical_sections_detected(self):
        # one literal synonym per section, exercising the synonym-matching
        # path rather than assuming heading text equals the slug
        content = "\n".join(
            [
                "## Installation",
                "## Usage",
                "## API Reference",
                "## Examples",
                "## Contributing",
                "## License",
            ]
        )
        result = extract_readme_features(content)
        assert result.section_count == 6
        assert set(result.detected_sections) == set(CANONICAL_SECTIONS)

    def test_synonym_variants_match(self):
        content = "## Getting Started\n## How to Use\n## Docs\n## Demo\n## How to Contribute\n## Licence"
        result = extract_readme_features(content)
        assert result.section_count == 6

    def test_case_insensitive_matching(self):
        result = extract_readme_features("## INSTALLATION")
        assert "installation" in result.detected_sections

    def test_near_miss_not_fuzzy_matched(self):
        # "Installer" is not in the curated synonym list for "installation" -
        # this module does not fuzzy-match, per 2.2.b's precedent.
        result = extract_readme_features("## Installer Notes")
        assert result.section_count == 0


class TestDuplicateHeadings:
    def test_duplicate_heading_same_section_counted_once(self):
        content = "## Installation\nsome text\n## Installation\nmore text"
        result = extract_readme_features(content)
        assert result.section_count == 1
        assert result.detected_sections == ("installation",)

    def test_duplicate_synonyms_for_same_section_counted_once(self):
        content = "## Install\n## Installation\n## Setup"
        result = extract_readme_features(content)
        assert result.section_count == 1


class TestCodeFences:
    def test_single_backtick_fence_counted(self):
        content = "text\n```\ncode here\n```\nmore text"
        result = extract_readme_features(content)
        assert result.code_example_count == 1

    def test_tilde_fence_counted(self):
        content = "text\n~~~\ncode here\n~~~\nmore text"
        result = extract_readme_features(content)
        assert result.code_example_count == 1

    def test_multiple_fenced_blocks_counted(self):
        content = "```\nblock one\n```\ntext\n```\nblock two\n```"
        result = extract_readme_features(content)
        assert result.code_example_count == 2

    def test_heading_inside_fence_not_detected_as_heading(self):
        content = "```\n## Installation\n```\nnormal text"
        result = extract_readme_features(content)
        assert result.section_count == 0
        assert result.code_example_count == 1

    def test_unterminated_fence_degrades_gracefully(self):
        content = "## Installation\n```\nunterminated code\n## Usage\nmore code never closed"
        result = extract_readme_features(content)
        # opening fence counted once; "## Usage" swallowed as still-inside-code
        assert result.code_example_count == 1
        assert result.section_count == 1
        assert result.detected_sections == ("installation",)

    def test_fenced_language_hint_still_recognized_as_fence(self):
        content = "```python\ncode\n```"
        result = extract_readme_features(content)
        assert result.code_example_count == 1


class TestInlineCode:
    def test_inline_code_not_counted_as_code_example(self):
        content = "Use the `foo()` function to do things."
        result = extract_readme_features(content)
        assert result.code_example_count == 0


class TestMalformedInput:
    def test_only_fence_markers_no_content(self):
        result = extract_readme_features("```\n```")
        assert result.code_example_count == 1

    def test_heading_with_only_hashes_no_text(self):
        result = extract_readme_features("###\nbody text")
        assert result.section_count == 0

    def test_mixed_line_endings(self):
        content = "## Installation\r\nsome text\r\n## Usage\r\n"
        result = extract_readme_features(content)
        assert result.section_count == 2


class TestNormalReadme:
    def test_realistic_readme(self):
        content = """# My Project

A short description of the project.

## Installation

```bash
pip install my-project
```

## Usage

```python
import my_project
my_project.run()
```

## Contributing

Pull requests welcome.

## License

MIT
"""
        result = extract_readme_features(content)
        assert result.section_count == 4  # installation, usage, contributing, license
        assert set(result.detected_sections) == {"installation", "usage", "contributing", "license"}
        assert result.code_example_count == 2


class TestDeterminism:
    def test_repeated_calls_identical(self):
        content = "## Installation\n```\ncode\n```\nSome usage text here."
        first = extract_readme_features(content)
        second = extract_readme_features(content)
        assert first == second

    def test_boundary_single_word(self):
        result = extract_readme_features("Word")
        assert result.word_count == 1
        assert result.section_count == 0
        assert result.code_example_count == 0
