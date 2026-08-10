"""Test suite for acquisition.content_extractor — Checkpoint 1.4.b.

Deterministic tests exercising extract_content() against real filesystem
trees built under pytest's tmp_path fixture - no clone, no network, no
PostgreSQL. Mirrors the style established in tests/test_clone_workspace.py
(Checkpoint 1.4.a).
"""

from __future__ import annotations

import hashlib
import logging
import os

import pytest

from acquisition.content_extractor import ExtractedFile, extract_content


def by_type(results: list[ExtractedFile], content_type: str) -> ExtractedFile | None:
    matches = [r for r in results if r.content_type == content_type]
    assert len(matches) <= 1, f"expected at most one {content_type!r}, got {len(matches)}"
    return matches[0] if matches else None


class TestReadmeDetection:
    def test_readme_md_present(self, tmp_path):
        (tmp_path / "README.md").write_text("# Hello\n\nWorld.", encoding="utf-8", newline="")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme is not None
        assert readme.file_path == "README.md"
        assert readme.content == "# Hello\n\nWorld."

    def test_readme_absent(self, tmp_path):
        (tmp_path / "notes.txt").write_text("irrelevant", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "readme") is None

    @pytest.mark.parametrize(
        "filename",
        ["readme.md", "Readme.md", "README.MD", "ReadMe", "readme", "README.txt", "readme.RST"],
    )
    def test_readme_casing_and_extension_variants_detected(self, tmp_path, filename):
        (tmp_path / filename).write_text("content", encoding="utf-8")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme is not None
        assert readme.file_path == filename

    def test_duplicate_readme_candidates_resolved_by_priority(self, tmp_path):
        (tmp_path / "README.txt").write_text("txt version", encoding="utf-8")
        (tmp_path / "README.md").write_text("md version", encoding="utf-8")
        (tmp_path / "README.rst").write_text("rst version", encoding="utf-8")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme is not None
        assert readme.file_path == "README.md"  # .md wins over .rst and .txt
        assert readme.content == "md version"
        # exactly one readme row, never multiple
        assert len([r for r in results if r.content_type == "readme"]) == 1

    def test_no_extension_readme_lowest_priority(self, tmp_path):
        (tmp_path / "README").write_text("bare version", encoding="utf-8")
        (tmp_path / "README.txt").write_text("txt version", encoding="utf-8")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme.file_path == "README.txt"

    def test_unsupported_readme_like_name_ignored(self, tmp_path):
        (tmp_path / "README.markdown").write_text("not a recognized extension", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "readme") is None


class TestManifestDetection:
    def test_single_manifest_present(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.0\n", encoding="utf-8", newline="")

        results = extract_content(tmp_path)

        manifest = by_type(results, "requirements_txt")
        assert manifest is not None
        assert manifest.file_path == "requirements.txt"
        assert manifest.content == "flask==2.0\n"

    def test_multiple_manifests_present(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("flask==2.0", encoding="utf-8")
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "package_json") is not None
        assert by_type(results, "requirements_txt") is not None
        assert by_type(results, "cargo_toml") is not None
        assert len(results) == 3

    @pytest.mark.parametrize(
        "filename,content_type",
        [
            ("package.json", "package_json"),
            ("requirements.txt", "requirements_txt"),
            ("pyproject.toml", "pyproject_toml"),
            ("setup.py", "setup_py"),
            ("setup.cfg", "setup_cfg"),
            ("Pipfile", "pipfile"),
            ("Gemfile", "gemfile"),
            ("go.mod", "go_mod"),
            ("Cargo.toml", "cargo_toml"),
            ("composer.json", "composer_json"),
        ],
    )
    def test_each_supported_manifest_type_detected(self, tmp_path, filename, content_type):
        (tmp_path / filename).write_text("manifest content", encoding="utf-8")

        results = extract_content(tmp_path)

        manifest = by_type(results, content_type)
        assert manifest is not None
        assert manifest.file_path == filename

    def test_manifest_filename_matching_is_case_sensitive(self, tmp_path):
        # A file named "Package.json" (wrong case) must NOT be detected as
        # package.json - manifests are matched by exact on-disk name, not
        # case-insensitively (unlike README).
        (tmp_path / "Package.json").write_text("{}", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "package_json") is None

    def test_nested_manifest_in_subdirectory_not_detected(self, tmp_path):
        # Root-level only, by design (see module docstring) - a manifest
        # inside a subdirectory (e.g. a vendored dependency, or a monorepo
        # sub-package) is not picked up in this sub-checkpoint's scope.
        subdir = tmp_path / "backend"
        subdir.mkdir()
        (subdir / "requirements.txt").write_text("flask==2.0", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "requirements_txt") is None
        assert results == []

    def test_unrecognized_files_ignored(self, tmp_path):
        (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
        (tmp_path / ".gitignore").write_text("*.pyc", encoding="utf-8")
        (tmp_path / "main.py").write_text("print('hi')", encoding="utf-8")

        results = extract_content(tmp_path)

        assert results == []


class TestContentIntegrity:
    def test_empty_file_extracted_with_zero_size(self, tmp_path):
        (tmp_path / "README.md").write_text("", encoding="utf-8")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme is not None
        assert readme.content == ""
        assert readme.content_size_bytes == 0
        assert readme.content_hash == hashlib.sha256(b"").hexdigest()

    def test_utf8_content_with_multibyte_characters(self, tmp_path):
        text = "# Café résumé 日本語 emoji 🎉"
        (tmp_path / "README.md").write_text(text, encoding="utf-8")

        results = extract_content(tmp_path)

        readme = by_type(results, "readme")
        assert readme.content == text
        assert readme.content_size_bytes == len(text.encode("utf-8"))
        assert readme.content_size_bytes > len(text)  # multibyte chars present

    def test_invalid_utf8_file_skipped(self, tmp_path):
        (tmp_path / "README.md").write_bytes(b"\xff\xfe\x00\x01invalid utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "readme") is None

    def test_content_hash_deterministic(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.0", encoding="utf-8")

        first = extract_content(tmp_path)
        second = extract_content(tmp_path)

        manifest1 = by_type(first, "requirements_txt")
        manifest2 = by_type(second, "requirements_txt")
        assert manifest1.content_hash == manifest2.content_hash
        assert manifest1.content_hash == hashlib.sha256(b"flask==2.0").hexdigest()

    def test_content_hash_changes_when_content_changes(self, tmp_path):
        path = tmp_path / "requirements.txt"
        path.write_text("flask==2.0", encoding="utf-8")
        first = by_type(extract_content(tmp_path), "requirements_txt")

        path.write_text("flask==3.0", encoding="utf-8")
        second = by_type(extract_content(tmp_path), "requirements_txt")

        assert first.content_hash != second.content_hash

    def test_byte_size_correct_for_ascii(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("abc", encoding="utf-8")

        result = by_type(extract_content(tmp_path), "requirements_txt")

        assert result.content_size_bytes == 3

    def test_file_path_uses_forward_slashes(self, tmp_path):
        (tmp_path / "README.md").write_text("x", encoding="utf-8")

        result = by_type(extract_content(tmp_path), "readme")

        assert "/" not in result.file_path or os.sep != "\\"
        assert "\\" not in result.file_path


class TestSafePathHandling:
    def test_symlinked_readme_pointing_outside_clone_dir_skipped(self, tmp_path):
        outside = tmp_path.parent / "outside_secret.txt"
        outside.write_text("should not be readable via symlink", encoding="utf-8")
        link = tmp_path / "README.md"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not permitted in this environment")

        results = extract_content(tmp_path)

        assert by_type(results, "readme") is None
        outside.unlink()

    def test_symlinked_manifest_skipped(self, tmp_path):
        outside = tmp_path.parent / "outside_manifest.json"
        outside.write_text("{}", encoding="utf-8")
        link = tmp_path / "package.json"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not permitted in this environment")

        results = extract_content(tmp_path)

        assert by_type(results, "package_json") is None
        outside.unlink()

    def test_is_safe_regular_file_rejects_symlink_without_requiring_os_symlink_support(self, tmp_path, monkeypatch):
        # Exercises the symlink-rejection branch deterministically via
        # monkeypatching Path.is_symlink, independent of whether this host
        # OS/user actually has permission to create real symlinks (Windows
        # requires Developer Mode or admin rights, unavailable in this
        # environment - confirmed by the two tests above skipping).
        from pathlib import Path as PathClass

        import acquisition.content_extractor as extractor_module

        (tmp_path / "README.md").write_text("content", encoding="utf-8")
        monkeypatch.setattr(PathClass, "is_symlink", lambda self: True)

        results = extractor_module.extract_content(tmp_path)

        assert results == []


class TestFileSizeLimit:
    def test_oversized_file_skipped(self, tmp_path, monkeypatch):
        import acquisition.content_extractor as extractor_module

        monkeypatch.setattr(extractor_module, "_MAX_FILE_SIZE_BYTES", 10)
        (tmp_path / "README.md").write_text("this text is definitely longer than ten bytes", encoding="utf-8")

        results = extract_content(tmp_path)

        assert by_type(results, "readme") is None


class TestLogging:
    def test_extraction_completion_logged(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        (tmp_path / "README.md").write_text("hi", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")

        extract_content(tmp_path)

        assert "content_extraction_completed" in caplog.text
        assert "files_extracted=2" in caplog.text

    def test_undecodable_file_logged_as_warning(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING)
        (tmp_path / "README.md").write_bytes(b"\xff\xfe invalid")

        extract_content(tmp_path)

        assert "content_extraction_skipped_undecodable" in caplog.text
