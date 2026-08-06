# ADR-004: Defer BigQuery / GitHub Archive Integration for Version 1

**Status:** Accepted
**Date:** 2026-08-06
**Checkpoint:** 1.2 (deferred, architecture preserved)

## Context

`IMPLEMENTATION_ROADMAP.md` sequences Checkpoint 1.2 ("GitHub Archive Integration") immediately after Checkpoint 1.1 (GitHub REST + GraphQL client, now complete — see `CHECKPOINT_1_1_FINAL_REPORT.md`) and lists it as a prerequisite of Checkpoint 1.3 (Repository Acquisition Pipeline). `SYSTEM_ARCHITECTURE.md` §7 Challenge 1 names GitHub's REST/GraphQL rate limit (5,000 requests/hour, authenticated) as the project's first-priority engineering risk against a corpus of 500M+ repositories, and names GitHub Archive (Google BigQuery's public mirror of GitHub's event stream) as the mitigation: no rate limit, full historical data, SQL-queryable at scale.

Before beginning implementation of Checkpoint 1.2, a design-verification review (Phase 1 of the standard engineering workflow used throughout this project) surfaced that no Google Cloud credentials, `google-cloud-bigquery` package, `gcloud` CLI, or service-account configuration exist anywhere in this environment — Checkpoint 1.2's own stated prerequisite ("Google Cloud authentication configured") was unmet, with no existing evidence of intent to set it up.

## Problem

Does Checkpoint 1.2, as literally scoped and sequenced in the roadmap, need to be implemented now, before Checkpoint 1.3, in order for the project to reach its actual Version 1 target?

Re-reading `SYSTEM_ARCHITECTURE.md` §8 (Version 1 Definition) against §7 Challenge 1's own stated math answers this directly:

- Version 1's acquisition target is **100–1,000 "carefully selected" repositories** (§8, "What Version 1 Includes"; also Checkpoint 1.3's own success metric, "100 repositories in database").
- Challenge 1's cited constraint is 5,000 authenticated requests/hour — comfortably sufficient to *select and fetch* 1,000 repositories via GitHub's own REST Search API (`GET /search/repositories`), using the client already built in Checkpoint 1.1, with no rate-limit pressure at this scale.
- BigQuery/GitHub Archive's stated value — "no rate limits," bootstrapping "100K-500K highest-quality repositories" (`TECHNICAL_IMPLEMENTATION_IDEAS.md` §1) — is explicitly a mitigation for a scale problem that does not exist yet at V1's 100–1,000 repository target. It belongs to `SYSTEM_ARCHITECTURE.md` §9 ("Future Expansion: Roadmap to Scale," the 10,000+ repository / Version 2 tier).

Implementing Checkpoint 1.2 now would require standing up a new external dependency — a GCP project, billing enablement, service-account credential management, a new SDK (`google-cloud-bigquery`), and a cost-governance mechanism (the roadmap's own "Possible Challenges" names "Cost management for queries" and "No budget overruns" as a named risk) — none of which advances the project toward its next real milestone (Checkpoint 1.3's 100 repositories).

## Decision

**Defer Checkpoint 1.2 for Version 1. Do not remove, cancel, or redesign it.**

- Checkpoint 1.3 (Repository Acquisition Pipeline) proceeds using GitHub's REST Search API (via the Checkpoint 1.1 client) for repository selection, not GitHub Archive/BigQuery.
- Checkpoint 1.2's full specification (purpose, deliverables, Definition of Done, prerequisites) remains intact in `IMPLEMENTATION_ROADMAP.md`, annotated as deferred, so implementation can resume from the same specification later without redesign.
- `SYSTEM_ARCHITECTURE.md` is left unmodified (it is frozen) — this ADR is the authoritative record of the deviation between that document's aspirational Stage 1 narrative ("Use GitHub Archive for initial load") and V1's actual implementation path. `SYSTEM_ARCHITECTURE.md`'s Data Collection Layer diagram still correctly names GitHub Archive (BigQuery) as a first-class future data source.

## Alternatives Considered

**A. Implement Checkpoint 1.2 now, as originally sequenced.**
Rejected. Would require GCP account setup, billing, and credential management for a capability that does not unblock Checkpoint 1.3 or any nearer-term checkpoint. Directly contradicts the project's established discipline (seen throughout Checkpoints 0.1–1.1) of building exactly what the current milestone needs and deferring the rest — e.g., Checkpoint 1.1.a built a deliberately minimal `config/settings.py` scoped to exactly two variables rather than the full Checkpoint 0.4 configuration system speculatively.

**B. Implement a scoped-down "thin" version of 1.2 now (generic BigQuery client only, no query builder), to have it ready ahead of need.**
Rejected. Still requires GCP credentials to exist and be live-verified (consistent with this project's standing rule that every checkpoint is verified against the real system, not mocked-only) — the credential/billing setup cost is paid either way, for a capability with no current consumer. Building infrastructure ahead of a real caller has been explicitly avoided elsewhere in this project (e.g., `CHECKPOINT_1_1F_REPORT.md`'s GraphQL client scope explicitly excludes speculative queries beyond what `get_repository()` needs).

**C. Remove Checkpoint 1.2 from the roadmap entirely.**
Rejected by explicit instruction. BigQuery remains the architecturally correct bootstrap strategy once repository acquisition needs to scale past what REST/GraphQL rate limits support — removing it would require re-deriving this design later instead of resuming from a preserved specification.

**D. Defer Checkpoint 1.2, preserve its specification, update dependent checkpoints' prerequisites accordingly.** — **Chosen.**
Matches the actual engineering need (V1 doesn't require it), preserves the long-term architecture (nothing is redesigned or lost), and keeps the roadmap internally consistent (Checkpoint 1.3 no longer blocked on an unmet prerequisite).

## Consequences

**Positive:**
- ✅ Checkpoint 1.3 can begin immediately, using infrastructure that already exists (Checkpoint 1.1's REST client) and is already live-verified.
- ✅ No new external dependency (GCP account, billing, credentials) introduced before it's actually load-bearing.
- ✅ No cost-governance mechanism needs to be designed and verified prematurely.
- ✅ Checkpoint 1.2's specification is preserved verbatim in the roadmap — resuming it later is a normal "start this checkpoint" action, not a redesign.

**Negative:**
- ⚠️ Repository selection for V1 is REST-Search-API-based, which is less powerful for complex historical/trend-based selection criteria (e.g., "repositories that gained the most stars in a specific 6-month window three years ago") than a BigQuery event-stream query would be. Acceptable for V1's "100–1,000 carefully selected" scope; revisit if selection criteria genuinely require it before Version 2's scale threshold is reached (see Future Reversal Conditions).
- ⚠️ `IMPLEMENTATION_ROADMAP.md`'s Milestone 1 checkpoint count (1/7 complete, 1/7 deferred, 5/7 not started) no longer maps to a simple "N of 7 checkpoints" completion percentage — the roadmap now explicitly tracks a deferred state, not just done/not-done.

## Future Reversal Conditions

Resume Checkpoint 1.2 (un-defer it) when **any** of the following becomes true:

1. **Scale trigger:** The repository corpus needs to grow beyond what comfortably fits within GitHub REST/GraphQL rate limits — concretely, approaching the `SYSTEM_ARCHITECTURE.md` §9 Version 2 tier (10,000+ repositories), where bulk selection via REST Search API pagination becomes impractically slow or rate-limit-constrained.
2. **Capability trigger:** A concrete need emerges for GitHub Archive's historical event-stream data specifically (not just more repositories) — e.g., Checkpoint 3.6 (Technology Trend Analysis) requiring multi-year adoption trend queries that REST/GraphQL cannot answer (they only expose current state, not historical event history).
3. **Explicit user decision:** You decide the long-term value (e.g., cost-effective bulk analytics, research use cases named in `claude.md`) justifies setting up GCP/BigQuery ahead of either trigger above.

When any condition is met, implementation should resume directly from Checkpoint 1.2's preserved specification in `IMPLEMENTATION_ROADMAP.md`, following the same Phase 1 (Design Verification) → Phase 2 (Engineering Plan) → sub-checkpoint workflow used for every other checkpoint in this project — including re-resolving the dataset-choice ambiguity flagged during this review (GitHub Archive's event-stream dataset vs. `bigquery-public-data.github_repos`, which the frozen docs do not disambiguate).

## Related Decisions

- Checkpoint 1.1 (GitHub API Client) — provides the REST Search API capability that makes this deferral viable; see `CHECKPOINT_1_1_FINAL_REPORT.md`.
- Checkpoint 1.3 (Repository Acquisition Pipeline) — the immediate beneficiary of this deferral; its prerequisites updated in `IMPLEMENTATION_ROADMAP.md` to drop the 1.2 dependency.
- `SYSTEM_ARCHITECTURE.md` §7 Challenge 1, §8, §9 — the source of the rate-limit-vs-scale analysis this decision is based on.

## Implementation Details

No code or schema changes result from this ADR. Documentation changes only:
- `IMPLEMENTATION_ROADMAP.md` — Checkpoint 1.2 marked deferred (specification preserved); Checkpoint 1.3's prerequisites updated; Milestone 1 status/summary table updated to reflect 1.1 complete, 1.2 deferred, 1.3 active.
- This ADR.
