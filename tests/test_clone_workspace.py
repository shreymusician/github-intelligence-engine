"""Test suite for acquisition.clone_workspace — Checkpoint 1.4.a.

Deterministic, mocked tests exercising RepositoryCloner against a fake
subprocess.run double — no real git clone, no real network for normal
test execution. Mirrors the style established in
tests/test_github_client.py (Checkpoint 1.1.g) and
tests/test_storage.py (Checkpoint 1.3.d).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from acquisition.clone_workspace import RepositoryCloner
from acquisition.exceptions import RepositoryCloneError
from config.settings import Settings


def make_settings(**overrides) -> Settings:
    fields = {"github_token": "x", "log_level": "INFO"}
    fields.update(overrides)
    return Settings(**fields)


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestFullNameValidation:
    def test_malformed_full_name_rejected_before_subprocess_call(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)) or FakeCompletedProcess())
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RepositoryCloneError) as excinfo:
            with cloner.clone("no-slash-here"):
                pass

        assert calls == []  # subprocess.run never invoked
        assert "malformed" in str(excinfo.value).lower()

    @pytest.mark.parametrize(
        "bad_name",
        ["", "/repo", "owner/", "owner/repo/extra", "../../etc/passwd", "owner repo/x", "owner/../x"],
    )
    def test_various_malformed_names_rejected(self, monkeypatch, bad_name):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess())
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RepositoryCloneError):
            with cloner.clone(bad_name):
                pass

    def test_valid_full_name_accepted(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess())
        cloner = RepositoryCloner(settings=make_settings())

        with cloner.clone("octocat/Hello-World") as path:
            assert isinstance(path, Path)


class TestSubprocessSafety:
    def test_git_invoked_with_argument_list_not_shell_string(self, monkeypatch):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())

        with cloner.clone("octocat/Hello-World"):
            pass

        assert isinstance(captured["argv"], list)
        assert captured["argv"][0] == "git"
        assert captured["argv"][1] == "clone"
        assert "--depth" in captured["argv"]
        assert "1" in captured["argv"]
        assert "--single-branch" in captured["argv"]
        assert "--no-tags" in captured["argv"]
        assert captured["kwargs"]["shell"] is False

    def test_clone_url_is_unauthenticated_and_correct(self, monkeypatch):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())

        with cloner.clone("octocat/Hello-World"):
            pass

        url = [a for a in captured["argv"] if a.startswith("https://")][0]
        assert url == "https://github.com/octocat/Hello-World.git"
        assert "@" not in url  # no embedded credentials

    def test_timeout_passed_to_subprocess(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(subprocess, "run", lambda argv, **k: captured.update(k) or FakeCompletedProcess())
        cloner = RepositoryCloner(settings=make_settings(), timeout_seconds=42)

        with cloner.clone("octocat/Hello-World"):
            pass

        assert captured["timeout"] == 42


class TestCloneFailureHandling:
    def test_nonzero_exit_code_raises_typed_error(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=128, stderr="fatal: repository not found")
        )
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RepositoryCloneError) as excinfo:
            with cloner.clone("octocat/does-not-exist"):
                pass

        assert excinfo.value.full_name == "octocat/does-not-exist"
        assert "128" in str(excinfo.value)

    def test_timeout_expired_raises_typed_error(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git clone", timeout=5)

        monkeypatch.setattr(subprocess, "run", raise_timeout)
        cloner = RepositoryCloner(settings=make_settings(), timeout_seconds=5)

        with pytest.raises(RepositoryCloneError) as excinfo:
            with cloner.clone("octocat/Hello-World"):
                pass

        assert "timed out" in str(excinfo.value).lower()

    def test_git_not_found_raises_typed_error(self, monkeypatch):
        def raise_oserror(*a, **k):
            raise FileNotFoundError("git executable not found")

        monkeypatch.setattr(subprocess, "run", raise_oserror)
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RepositoryCloneError):
            with cloner.clone("octocat/Hello-World"):
                pass


class TestWorkspaceCleanup:
    def test_directory_removed_after_successful_clone(self, monkeypatch):
        captured_path = {}

        def fake_run(argv, **kwargs):
            dest_dir = argv[-1]
            captured_path["path"] = dest_dir
            Path(dest_dir, "marker.txt").write_text("cloned")
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())

        with cloner.clone("octocat/Hello-World") as path:
            assert path.exists()
            assert (path / "marker.txt").exists()

        assert not Path(captured_path["path"]).exists()  # cleaned up after the `with` block

    def test_directory_removed_after_clone_failure(self, monkeypatch):
        captured_path = {}

        def fake_run(argv, **kwargs):
            dest_dir = argv[-1]
            captured_path["path"] = dest_dir
            Path(dest_dir, "partial.txt").write_text("partial clone")
            return FakeCompletedProcess(returncode=1, stderr="network error")

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RepositoryCloneError):
            with cloner.clone("octocat/Hello-World"):
                pass

        assert not Path(captured_path["path"]).exists()

    def test_directory_removed_even_if_caller_raises_inside_with_block(self, monkeypatch):
        captured_path = {}

        def fake_run(argv, **kwargs):
            captured_path["path"] = argv[-1]
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())

        with pytest.raises(RuntimeError):
            with cloner.clone("octocat/Hello-World"):
                raise RuntimeError("caller-side failure during extraction")

        assert not Path(captured_path["path"]).exists()

    def test_temp_directory_is_outside_the_project_repository(self, monkeypatch):
        captured_path = {}

        def fake_run(argv, **kwargs):
            captured_path["path"] = argv[-1]
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings())
        project_root = Path(__file__).resolve().parent.parent

        with cloner.clone("octocat/Hello-World") as path:
            resolved = path.resolve()
            assert project_root not in resolved.parents and resolved != project_root


class TestNoTokenLeakage:
    def test_token_never_appears_in_subprocess_argv_or_logs(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        secret = "ghp_super_secret_token_value"
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return FakeCompletedProcess()

        monkeypatch.setattr(subprocess, "run", fake_run)
        cloner = RepositoryCloner(settings=make_settings(github_token=secret))

        with cloner.clone("octocat/Hello-World"):
            pass

        assert all(secret not in str(arg) for arg in captured["argv"])
        assert secret not in caplog.text

    def test_error_paths_never_log_token(self, monkeypatch, caplog):
        caplog.set_level(logging.DEBUG)
        secret = "ghp_super_secret_token_value"
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompletedProcess(returncode=1, stderr="boom"))
        cloner = RepositoryCloner(settings=make_settings(github_token=secret))

        with pytest.raises(RepositoryCloneError):
            with cloner.clone("octocat/Hello-World"):
                pass

        assert secret not in caplog.text


class TestSettingsIntegration:
    def test_default_timeout_from_settings(self):
        settings = make_settings(content_clone_timeout_seconds=99)
        cloner = RepositoryCloner(settings=settings)
        assert cloner._timeout_seconds == 99

    def test_explicit_timeout_overrides_settings(self):
        settings = make_settings(content_clone_timeout_seconds=99)
        cloner = RepositoryCloner(settings=settings, timeout_seconds=5)
        assert cloner._timeout_seconds == 5

    def test_settings_repr_redacts_token_and_shows_new_field(self):
        settings = make_settings(content_clone_timeout_seconds=30)
        assert "content_clone_timeout_seconds=30" in repr(settings)
        assert "***redacted***" in repr(settings)
