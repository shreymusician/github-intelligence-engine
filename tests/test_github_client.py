"""Test suite for github_client and config.settings — Checkpoint 1.1.g.

Covers Checkpoints 1.1.a (config), 1.1.c (REST client), 1.1.d (rate
limiter), 1.1.e (retry), and 1.1.f (GraphQL client) with deterministic,
network-independent tests. All HTTP interaction is faked via minimal
Session/Response doubles — no real network call is made by this file, and
no test depends on a real GITHUB_TOKEN being present.

This does not replace the live verification already performed and
documented in CHECKPOINT_1_1{A,C,D,E,F}_REPORT.md — it protects those
already-verified behaviours from regressing.
"""

from __future__ import annotations

import logging

import pytest
import requests

import config.settings as settings_module
from config.settings import ConfigurationError, Settings
from github_client.exceptions import (
    AuthenticationError,
    GitHubAPIError,
    GitHubClientError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from github_client.graphql import GitHubGraphQLClient
from github_client.rate_limiter import DEFAULT_SAFETY_MARGIN, RateLimitInfo, RateLimiter
from github_client.rest import GitHubRESTClient
from github_client.retry import RetryPolicy, with_retry

TEST_TOKEN = "ghp_test_token_never_logged_1234567890"


def make_settings(**overrides) -> Settings:
    fields = {"github_token": TEST_TOKEN, "log_level": "INFO"}
    fields.update(overrides)
    return Settings(**fields)


# ---------------------------------------------------------------------------
# Fakes — minimal requests.Session/Response doubles, no real network I/O.
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code, json_data=None, headers=None, json_error=False):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("response body is not valid JSON")
        return self._json_data


class FakeSession:
    """Returns a fixed response/exception and records every call it receives."""

    def __init__(self, response=None, exception=None):
        self._response = response
        self._exception = exception
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._exception is not None:
            raise self._exception
        return self._response

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self._exception is not None:
            raise self._exception
        return self._response


# ---------------------------------------------------------------------------
# config.settings (Checkpoint 1.1.a)
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_settings_env(tmp_path, monkeypatch):
    """Fully decouples config.settings from the real .env / shell environment."""
    monkeypatch.setattr(settings_module, "_ENV_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.setattr(settings_module, "_settings", None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    return monkeypatch


class TestSettings:
    def test_valid_token_and_default_log_level(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "sometoken")
        settings = settings_module.load_settings()
        assert settings.github_token == "sometoken"
        assert settings.log_level == "INFO"

    def test_missing_token_raises_configuration_error(self, isolated_settings_env):
        with pytest.raises(ConfigurationError, match="GITHUB_TOKEN"):
            settings_module.load_settings()

    def test_whitespace_only_token_treated_as_missing(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "   ")
        with pytest.raises(ConfigurationError, match="GITHUB_TOKEN"):
            settings_module.load_settings()

    def test_invalid_log_level_raises_configuration_error(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "sometoken")
        isolated_settings_env.setenv("LOG_LEVEL", "NOT_A_LEVEL")
        with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
            settings_module.load_settings()

    def test_log_level_case_insensitive_and_normalized(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "sometoken")
        isolated_settings_env.setenv("LOG_LEVEL", "debug")
        settings = settings_module.load_settings()
        assert settings.log_level == "DEBUG"

    def test_get_settings_returns_cached_singleton(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "sometoken")
        first = settings_module.get_settings()
        second = settings_module.get_settings()
        assert first is second

    def test_repr_never_contains_token(self, isolated_settings_env):
        isolated_settings_env.setenv("GITHUB_TOKEN", "super-secret-value")
        settings = settings_module.load_settings()
        assert "super-secret-value" not in repr(settings)
        assert "***redacted***" in repr(settings)


# ---------------------------------------------------------------------------
# github_client.exceptions (exception mapping)
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_all_exceptions_derive_from_github_client_error(self):
        for exc_cls in (
            RepositoryNotFoundError,
            AuthenticationError,
            RateLimitExceededError,
            GitHubAPIError,
        ):
            assert issubclass(exc_cls, GitHubClientError)

    def test_repository_not_found_error_carries_owner_repo(self):
        exc = RepositoryNotFoundError("octocat", "missing-repo")
        assert exc.owner == "octocat"
        assert exc.repo == "missing-repo"
        assert "octocat/missing-repo" in str(exc)

    def test_authentication_error_default_message(self):
        exc = AuthenticationError()
        assert "401" in str(exc)

    def test_rate_limit_exceeded_error_carries_reset_at(self):
        exc = RateLimitExceededError(reset_at=1234567890)
        assert exc.reset_at == 1234567890
        assert "1234567890" in str(exc)

    def test_github_api_error_carries_optional_status_code(self):
        exc = GitHubAPIError("boom", status_code=500)
        assert exc.status_code == 500
        with_no_status = GitHubAPIError("boom")
        assert with_no_status.status_code is None


# ---------------------------------------------------------------------------
# github_client.rate_limiter (Checkpoint 1.1.d)
# ---------------------------------------------------------------------------


class TestRateLimitInfo:
    def test_from_headers_parses_all_fields(self):
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": "1700000000",
            "X-RateLimit-Used": "1",
            "X-RateLimit-Resource": "core",
        }
        info = RateLimitInfo.from_headers(headers)
        assert info == RateLimitInfo(limit=5000, remaining=4999, reset_at=1700000000, used=1, resource="core")

    def test_from_headers_missing_required_header_returns_none(self):
        headers = {"X-RateLimit-Limit": "5000"}
        assert RateLimitInfo.from_headers(headers) is None

    def test_from_headers_defaults_resource_to_core(self):
        headers = {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "4999",
            "X-RateLimit-Reset": "1700000000",
            "X-RateLimit-Used": "1",
        }
        info = RateLimitInfo.from_headers(headers)
        assert info.resource == "core"


class TestRateLimiter:
    def test_default_safety_margin(self):
        assert DEFAULT_SAFETY_MARGIN == 50

    @pytest.mark.parametrize(
        "remaining,expected",
        [(51, False), (50, True), (0, True)],
    )
    def test_is_exhausted_boundary(self, remaining, expected):
        limiter = RateLimiter(safety_margin=50)
        info = RateLimitInfo(limit=5000, remaining=remaining, reset_at=0, used=0, resource="core")
        assert limiter.is_exhausted(info) is expected

    def test_seconds_until_reset_uses_injected_clock(self):
        limiter = RateLimiter(time_fn=lambda: 1000.0)
        info = RateLimitInfo(limit=5000, remaining=0, reset_at=1090, used=5000, resource="core")
        assert limiter.seconds_until_reset(info) == 90.0

    def test_seconds_until_reset_floored_at_zero(self):
        limiter = RateLimiter(time_fn=lambda: 2000.0)
        info = RateLimitInfo(limit=5000, remaining=0, reset_at=1000, used=5000, resource="core")
        assert limiter.seconds_until_reset(info) == 0.0

    def test_wait_if_needed_sleeps_when_exhausted(self):
        sleeps = []
        limiter = RateLimiter(safety_margin=50, sleep_fn=sleeps.append, time_fn=lambda: 1000.0)
        info = RateLimitInfo(limit=5000, remaining=0, reset_at=1075, used=5000, resource="core")
        waited = limiter.wait_if_needed(info)
        assert waited is True
        assert sleeps == [75.0]

    def test_wait_if_needed_does_not_sleep_when_healthy(self):
        sleeps = []
        limiter = RateLimiter(safety_margin=50, sleep_fn=sleeps.append)
        info = RateLimitInfo(limit=5000, remaining=4999, reset_at=0, used=1, resource="core")
        waited = limiter.wait_if_needed(info)
        assert waited is False
        assert sleeps == []

    def test_wait_if_needed_none_input_is_safe(self):
        sleeps = []
        limiter = RateLimiter(sleep_fn=sleeps.append)
        assert limiter.wait_if_needed(None) is False
        assert sleeps == []

    def test_negative_safety_margin_rejected(self):
        with pytest.raises(ValueError):
            RateLimiter(safety_margin=-1)


# ---------------------------------------------------------------------------
# github_client.retry (Checkpoint 1.1.e)
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_delay_for_attempt_exponential_no_jitter(self):
        policy = RetryPolicy(initial_delay=1.0, multiplier=2.0, jitter=0.0)
        assert [policy.delay_for_attempt(i) for i in range(3)] == [1.0, 2.0, 4.0]

    def test_delay_for_attempt_jitter_upper_bound(self):
        policy = RetryPolicy(initial_delay=2.0, multiplier=2.0, jitter=0.5, random_fn=lambda: 1.0)
        assert policy.delay_for_attempt(0) == pytest.approx(3.0)

    def test_call_retries_retryable_failure_then_succeeds(self):
        sleeps = []
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise GitHubAPIError("transient")
            return "ok"

        policy = RetryPolicy(max_attempts=5, initial_delay=1.0, jitter=0.0, sleep_fn=sleeps.append)
        result = policy.call(flaky)
        assert result == "ok"
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]

    @pytest.mark.parametrize(
        "exc",
        [
            RepositoryNotFoundError("o", "r"),
            AuthenticationError(),
            RateLimitExceededError(reset_at=None),
            ValueError("unexpected bug"),
        ],
    )
    def test_call_never_retries_non_retryable_exceptions(self, exc):
        sleeps = []
        calls = {"n": 0}

        def always_fails():
            calls["n"] += 1
            raise exc

        policy = RetryPolicy(max_attempts=5, sleep_fn=sleeps.append)
        with pytest.raises(type(exc)):
            policy.call(always_fails)
        assert calls["n"] == 1
        assert sleeps == []

    def test_call_raises_after_max_attempts_exhausted(self):
        sleeps = []
        calls = {"n": 0}

        def always_fails():
            calls["n"] += 1
            raise GitHubAPIError("persistent failure")

        policy = RetryPolicy(max_attempts=3, initial_delay=1.0, jitter=0.0, sleep_fn=sleeps.append)
        with pytest.raises(GitHubAPIError):
            policy.call(always_fails)
        assert calls["n"] == 3
        assert sleeps == [1.0, 2.0]

    def test_with_retry_decorator_passes_arguments_through(self):
        calls = []

        @with_retry(RetryPolicy(max_attempts=2, sleep_fn=lambda s: None))
        def fetch(owner, repo, *, flag=False):
            calls.append((owner, repo, flag))
            return f"{owner}/{repo}:{flag}"

        assert fetch("octocat", "Hello-World", flag=True) == "octocat/Hello-World:True"
        assert calls == [("octocat", "Hello-World", True)]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_attempts": 0},
            {"initial_delay": -1},
            {"multiplier": 0.5},
            {"jitter": 1.5},
        ],
    )
    def test_constructor_validation_rejects_invalid_values(self, kwargs):
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)


# ---------------------------------------------------------------------------
# github_client.rest.GitHubRESTClient (Checkpoint 1.1.c)
# ---------------------------------------------------------------------------


def rest_repo_payload(**overrides):
    payload = {
        "id": 1296269,
        "name": "Hello-World",
        "full_name": "octocat/Hello-World",
        "description": "My first repository on GitHub!",
        "language": "Python",
        "license": {"spdx_id": "MIT"},
        "archived": False,
        "fork": False,
        "created_at": "2011-01-26T19:01:12Z",
        "pushed_at": "2011-01-26T19:14:43Z",
        "owner": {
            "id": 1,
            "login": "octocat",
            "type": "User",
            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
        },
    }
    payload.update(overrides)
    return payload


RATE_LIMIT_HEADERS = {
    "X-RateLimit-Limit": "5000",
    "X-RateLimit-Remaining": "4999",
    "X-RateLimit-Reset": "1700000000",
    "X-RateLimit-Used": "1",
    "X-RateLimit-Resource": "core",
}


class TestGitHubRESTClient:
    def test_successful_fetch_parses_repository_and_owner(self):
        session = FakeSession(FakeResponse(200, rest_repo_payload(), RATE_LIMIT_HEADERS))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        repo = client.get_repository("octocat", "Hello-World")

        assert repo.github_id == 1296269
        assert repo.full_name == "octocat/Hello-World"
        assert repo.primary_language == "Python"
        assert repo.license_spdx_id == "MIT"
        assert repo.is_archived is False
        assert repo.owner.login == "octocat"
        assert repo.owner.account_type == "User"
        assert repo.rate_limit == RateLimitInfo(5000, 4999, 1700000000, 1, "core")

    def test_request_uses_bearer_token_and_required_headers(self):
        session = FakeSession(FakeResponse(200, rest_repo_payload(), RATE_LIMIT_HEADERS))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        client.get_repository("octocat", "Hello-World")

        headers = session.calls[0]["headers"]
        assert headers["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert headers["Accept"] == "application/vnd.github+json"
        assert "X-GitHub-Api-Version" in headers
        assert session.calls[0]["url"] == "https://api.github.com/repos/octocat/Hello-World"

    def test_404_raises_repository_not_found_error(self):
        session = FakeSession(FakeResponse(404, {}, {}))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(RepositoryNotFoundError) as exc_info:
            client.get_repository("octocat", "missing")
        assert exc_info.value.owner == "octocat"
        assert exc_info.value.repo == "missing"

    def test_401_raises_authentication_error(self):
        session = FakeSession(FakeResponse(401, {}, {}))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(AuthenticationError):
            client.get_repository("octocat", "Hello-World")

    def test_403_with_exhausted_quota_raises_rate_limit_exceeded_error(self):
        headers = dict(RATE_LIMIT_HEADERS, **{"X-RateLimit-Remaining": "0", "X-RateLimit-Used": "5000"})
        session = FakeSession(FakeResponse(403, {}, headers))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(RateLimitExceededError) as exc_info:
            client.get_repository("octocat", "Hello-World")
        assert exc_info.value.reset_at == 1700000000

    def test_403_without_exhausted_quota_raises_generic_api_error(self):
        session = FakeSession(FakeResponse(403, {}, RATE_LIMIT_HEADERS))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError) as exc_info:
            client.get_repository("octocat", "Hello-World")
        assert exc_info.value.status_code == 403

    def test_unexpected_status_raises_generic_api_error(self):
        session = FakeSession(FakeResponse(500, {}, {}))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError) as exc_info:
            client.get_repository("octocat", "Hello-World")
        assert exc_info.value.status_code == 500

    def test_missing_expected_field_raises_generic_api_error(self):
        broken_payload = rest_repo_payload()
        del broken_payload["full_name"]
        session = FakeSession(FakeResponse(200, broken_payload, RATE_LIMIT_HEADERS))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.get_repository("octocat", "Hello-World")

    def test_network_failure_raises_generic_api_error(self):
        session = FakeSession(exception=requests.ConnectionError("DNS failure"))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.get_repository("octocat", "Hello-World")

    def test_no_rate_limit_headers_returns_none_rate_limit(self):
        session = FakeSession(FakeResponse(200, rest_repo_payload(), {}))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        repo = client.get_repository("octocat", "Hello-World")
        assert repo.rate_limit is None


# ---------------------------------------------------------------------------
# github_client.graphql.GitHubGraphQLClient (Checkpoint 1.1.f)
# ---------------------------------------------------------------------------


def graphql_rate_limit_payload(**overrides):
    payload = {"limit": 5000, "cost": 1, "remaining": 4999, "resetAt": "2026-08-06T01:23:45Z"}
    payload.update(overrides)
    return payload


def graphql_repository_payload(**overrides):
    payload = {
        "databaseId": 1296269,
        "name": "Hello-World",
        "nameWithOwner": "octocat/Hello-World",
        "description": "My first repository on GitHub!",
        "primaryLanguage": {"name": "Python"},
        "licenseInfo": {"spdxId": "MIT"},
        "isArchived": False,
        "isFork": False,
        "createdAt": "2011-01-26T19:01:12Z",
        "pushedAt": "2011-01-26T19:14:43Z",
        "owner": {
            "login": "octocat",
            "__typename": "User",
            "databaseId": 1,
            "avatarUrl": "https://avatars.githubusercontent.com/u/1?v=4",
        },
    }
    payload.update(overrides)
    return payload


def graphql_success_body(repository, rate_limit=None):
    return {"data": {"repository": repository, "rateLimit": rate_limit or graphql_rate_limit_payload()}}


class TestGitHubGraphQLClient:
    def test_successful_fetch_parses_repository_and_owner(self):
        session = FakeSession(FakeResponse(200, graphql_success_body(graphql_repository_payload())))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        repo = client.get_repository("octocat", "Hello-World")

        assert repo.github_id == 1296269
        assert repo.full_name == "octocat/Hello-World"
        assert repo.primary_language == "Python"
        assert repo.license_spdx_id == "MIT"
        assert repo.owner.login == "octocat"
        assert repo.owner.account_type == "User"

    def test_rate_limit_parsed_point_based(self):
        session = FakeSession(
            FakeResponse(
                200,
                graphql_success_body(
                    graphql_repository_payload(), graphql_rate_limit_payload(limit=5000, remaining=4990)
                ),
            )
        )
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        repo = client.get_repository("octocat", "Hello-World")

        assert repo.rate_limit.resource == "graphql"
        assert repo.rate_limit.limit == 5000
        assert repo.rate_limit.remaining == 4990
        assert repo.rate_limit.used == 10  # limit - remaining

    def test_request_uses_bearer_token_and_required_headers(self):
        session = FakeSession(FakeResponse(200, graphql_success_body(graphql_repository_payload())))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        client.get_repository("octocat", "Hello-World")

        call = session.calls[0]
        assert call["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
        assert call["url"] == "https://api.github.com/graphql"
        assert call["json"]["variables"] == {"owner": "octocat", "name": "Hello-World"}

    def test_execute_returns_generic_data_object(self):
        session = FakeSession(FakeResponse(200, {"data": {"viewer": {"login": "octocat"}}}))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        data = client.execute("query { viewer { login } }")
        assert data == {"viewer": {"login": "octocat"}}

    def test_not_found_error_raises_repository_not_found_error(self):
        body = {
            "data": {"repository": None},
            "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "Could not resolve"}],
        }
        session = FakeSession(FakeResponse(200, body))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(RepositoryNotFoundError) as exc_info:
            client.get_repository("octocat", "missing")
        assert exc_info.value.owner == "octocat"
        assert exc_info.value.repo == "missing"

    def test_non_not_found_graphql_error_raises_generic_api_error(self):
        body = {"errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}]}
        session = FakeSession(FakeResponse(200, body))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.execute("query { viewer { login } }")

    def test_401_raises_authentication_error(self):
        session = FakeSession(FakeResponse(401, {}))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(AuthenticationError):
            client.execute("query { viewer { login } }")

    def test_unexpected_status_raises_generic_api_error(self):
        session = FakeSession(FakeResponse(500, {}))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError) as exc_info:
            client.execute("query { viewer { login } }")
        assert exc_info.value.status_code == 500

    def test_malformed_json_response_raises_generic_api_error(self):
        session = FakeSession(FakeResponse(200, None, json_error=True))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.execute("query { viewer { login } }")

    def test_response_missing_data_and_errors_raises_generic_api_error(self):
        session = FakeSession(FakeResponse(200, {}))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.execute("query { viewer { login } }")

    def test_network_failure_raises_generic_api_error(self):
        session = FakeSession(exception=requests.ConnectionError("DNS failure"))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.execute("query { viewer { login } }")

    def test_missing_expected_field_raises_generic_api_error(self):
        broken_repo = graphql_repository_payload()
        del broken_repo["nameWithOwner"]
        session = FakeSession(FakeResponse(200, graphql_success_body(broken_repo)))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        with pytest.raises(GitHubAPIError):
            client.get_repository("octocat", "Hello-World")


# ---------------------------------------------------------------------------
# Cross-cutting: retry + rate limiter composed around both clients (mocked)
# ---------------------------------------------------------------------------


class TestClientRetryComposition:
    def test_rest_client_wrapped_with_retry_recovers_from_transient_failure(self):
        responses = [
            FakeResponse(500, {}, {}),
            FakeResponse(200, rest_repo_payload(), RATE_LIMIT_HEADERS),
        ]

        class FlakySession:
            def __init__(self):
                self.n = 0

            def get(self, *args, **kwargs):
                response = responses[self.n]
                self.n += 1
                return response

        client = GitHubRESTClient(settings=make_settings(), session=FlakySession())

        @with_retry(RetryPolicy(max_attempts=3, initial_delay=0, sleep_fn=lambda s: None))
        def fetch():
            return client.get_repository("octocat", "Hello-World")

        repo = fetch()
        assert repo.full_name == "octocat/Hello-World"

    def test_graphql_client_wrapped_with_retry_recovers_from_transient_failure(self):
        responses = [
            FakeResponse(200, {"errors": [{"type": "FORBIDDEN"}]}),
            FakeResponse(200, graphql_success_body(graphql_repository_payload())),
        ]

        class FlakySession:
            def __init__(self):
                self.n = 0

            def post(self, *args, **kwargs):
                response = responses[self.n]
                self.n += 1
                return response

        client = GitHubGraphQLClient(settings=make_settings(), session=FlakySession())

        @with_retry(RetryPolicy(max_attempts=3, initial_delay=0, sleep_fn=lambda s: None))
        def fetch():
            return client.get_repository("octocat", "Hello-World")

        repo = fetch()
        assert repo.full_name == "octocat/Hello-World"

    def test_rest_404_wrapped_with_retry_is_not_retried(self):
        session = FakeSession(FakeResponse(404, {}, {}))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        @with_retry(RetryPolicy(max_attempts=5, sleep_fn=lambda s: None))
        def fetch():
            return client.get_repository("octocat", "missing")

        with pytest.raises(RepositoryNotFoundError):
            fetch()
        assert len(session.calls) == 1

    def test_rate_limiter_accepts_rate_limit_from_both_clients(self):
        rest_session = FakeSession(FakeResponse(200, rest_repo_payload(), RATE_LIMIT_HEADERS))
        rest_client = GitHubRESTClient(settings=make_settings(), session=rest_session)
        rest_repo = rest_client.get_repository("octocat", "Hello-World")

        graphql_session = FakeSession(FakeResponse(200, graphql_success_body(graphql_repository_payload())))
        graphql_client = GitHubGraphQLClient(settings=make_settings(), session=graphql_session)
        graphql_repo = graphql_client.get_repository("octocat", "Hello-World")

        limiter = RateLimiter(safety_margin=50)
        assert limiter.is_exhausted(rest_repo.rate_limit) is False
        assert limiter.is_exhausted(graphql_repo.rate_limit) is False


# ---------------------------------------------------------------------------
# No token leakage (logging integration)
# ---------------------------------------------------------------------------


class TestNoTokenLeakage:
    def test_rest_client_never_logs_token(self, caplog):
        caplog.set_level(logging.DEBUG)
        session = FakeSession(FakeResponse(200, rest_repo_payload(), RATE_LIMIT_HEADERS))
        client = GitHubRESTClient(settings=make_settings(), session=session)

        client.get_repository("octocat", "Hello-World")

        assert TEST_TOKEN not in caplog.text
        assert f"Bearer {TEST_TOKEN}" not in caplog.text

    def test_graphql_client_never_logs_token(self, caplog):
        caplog.set_level(logging.DEBUG)
        session = FakeSession(FakeResponse(200, graphql_success_body(graphql_repository_payload())))
        client = GitHubGraphQLClient(settings=make_settings(), session=session)

        client.get_repository("octocat", "Hello-World")

        assert TEST_TOKEN not in caplog.text
        assert f"Bearer {TEST_TOKEN}" not in caplog.text

    def test_error_paths_never_log_token(self, caplog):
        caplog.set_level(logging.DEBUG)
        rest_session = FakeSession(exception=requests.ConnectionError("DNS failure"))
        rest_client = GitHubRESTClient(settings=make_settings(), session=rest_session)
        with pytest.raises(GitHubAPIError):
            rest_client.get_repository("octocat", "Hello-World")

        graphql_session = FakeSession(exception=requests.ConnectionError("DNS failure"))
        graphql_client = GitHubGraphQLClient(settings=make_settings(), session=graphql_session)
        with pytest.raises(GitHubAPIError):
            graphql_client.execute("query { viewer { login } }")

        assert TEST_TOKEN not in caplog.text
