"""Environment smoke tests for Checkpoint 0.1.

These tests validate that the local development environment is wired up
correctly: the Python package imports, and — once Docker services are
running — PostgreSQL and Redis are reachable.

Connectivity tests skip (rather than fail) when services are not yet
running, so this suite is green immediately after `pip install`, and
becomes a real verification once `docker compose up` succeeds.
"""

import os

import pytest


def test_src_package_imports() -> None:
    """The src package must import without errors."""
    import src

    assert src.__version__ == "0.1.0"


def test_postgres_connection() -> None:
    """PostgreSQL must accept connections using .env configuration."""
    psycopg = pytest.importorskip("psycopg")

    conn_info = {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "dbname": os.getenv("POSTGRES_DB", "repository_intelligence"),
        "user": os.getenv("POSTGRES_USER", "rie_dev"),
        "password": os.getenv("POSTGRES_PASSWORD", "rie_dev_password"),
    }

    try:
        with psycopg.connect(**conn_info, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                assert cur.fetchone() == (1,)
    except psycopg.OperationalError:
        pytest.skip(
            "PostgreSQL not reachable — run `docker compose -f docker/docker-compose.yml up -d`"
        )


def test_redis_connection() -> None:
    """Redis must accept connections using .env configuration."""
    redis = pytest.importorskip("redis")

    try:
        client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            socket_connect_timeout=3,
        )
        assert client.ping() is True
    except redis.exceptions.ConnectionError:
        pytest.skip(
            "Redis not reachable — run `docker compose -f docker/docker-compose.yml up -d`"
        )
