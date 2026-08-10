"""Checkpoint 1.3.e — live acquisition run driver (throwaway script, not part of delivered code).

Runs the real, unmodified AcquisitionPipeline against the real GitHub API
and the real local PostgreSQL database. No production code is imported
differently than a real caller would use it.
"""
from __future__ import annotations

import sys
import time

from acquisition.pipeline import AcquisitionPipeline
from acquisition.selection import RepositorySelector, SelectionCriteria
from acquisition.storage import RepositoryWriter
from github_client.rate_limiter import RateLimiter
from github_client.rest import GitHubRESTClient
from github_client.retry import RetryPolicy

max_results = int(sys.argv[1]) if len(sys.argv) > 1 else 25
min_stars = int(sys.argv[2]) if len(sys.argv) > 2 else 50000

client = GitHubRESTClient()
selector = RepositorySelector(client)
writer = RepositoryWriter()
rate_limiter = RateLimiter()
retry_policy = RetryPolicy()

pipeline = AcquisitionPipeline(client, selector, writer, rate_limiter=rate_limiter, retry_policy=retry_policy)

criteria = SelectionCriteria(
    languages=("python",),
    min_stars=min_stars,
    max_results=max_results,
    exclude_forks=True,
    exclude_archived=True,
)

print(f"=== Starting run: max_results={max_results} min_stars={min_stars} ===", flush=True)
start = time.monotonic()
result = pipeline.run(criteria)
elapsed = time.monotonic() - start

print(f"=== Run complete in {elapsed:.1f}s ===")
print("stats:", result.stats)
print("repository_ids count:", len(result.repository_ids))
print("failures count:", len(result.failures))
for f in result.failures:
    print("  FAILURE:", f.full_name, f.stage, f.error_type, f.message)
print(f"avg_seconds_per_candidate={elapsed / max(result.stats.candidates_selected, 1):.2f}")
