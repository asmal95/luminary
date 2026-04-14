# Reliability and Scalability Improvements

## Scope

This document consolidates the reliability/scalability hardening work for Luminary across:

1. LLM response parsing robustness
2. MR processing concurrency
3. Retry policy precision
4. Test coverage expansion
5. Observability improvements

It is intended as the single reference for further iteration.

## Implemented Changes

### 1) LLM parsing hardening

Updated: `src/luminary/application/review_service.py`

- Added resilient JSON extraction with balanced bracket scanning:
  - fenced blocks are preferred (` ```json ... ``` `),
  - fallback extracts first balanced JSON object/array from noisy text.
- Changed parsing flow to:
  - try raw extracted JSON first,
  - apply `_fix_common_json_errors()` only if direct parse fails.
- Added strict validation that `comments` must be a list.
- Reduced noisy fallback payloads by truncating long raw responses in fallback comments.

### 2) MR concurrency with deterministic ordering

Updated: `src/luminary/application/mr_review_service.py`, `src/luminary/domain/config/limits.py`, `src/luminary/domain/config/app.py`, `src/luminary/cli.py`

- Added `limits.max_concurrent_files` (default `1`, range `1..16`).
- Implemented parallel file review using `ThreadPoolExecutor` for LLM-bound work.
- Preserved deterministic behavior:
  - results are indexed and sorted by original file order before posting/summary.
- Kept GitLab posting sequential in this phase to reduce API contention risk.

### 3) Retry policy tightening

Updated: `src/luminary/infrastructure/retry.py`, `src/luminary/infrastructure/http_client.py`, `src/luminary/infrastructure/gitlab/client.py`

- Removed broad retry-by-default behavior.
- GitLab retries now target transient classes only:
  - retryable GitLab errors by status policy (429/5xx),
  - transient network/timeouts where applicable.
- HTTP retries now avoid non-retryable request setup errors (invalid URL/schema/header).
- For unknown status cases:
  - HTTP errors without response are not retried by default,
  - GitLab errors without status retry only when message indicates transient network/server condition.

### 4) Test coverage expansion

Updated tests:

- `tests/test_review_service.py`
  - added noisy preamble parsing cases,
  - fenced JSON preference case,
  - invalid `comments` shape case,
  - fallback truncation case.
- `tests/test_gitlab_client.py`
  - non-GitLab programming error fail-fast case,
  - GitLab no-status non-transient no-retry case,
  - GitLab no-status transient retry case.
- `tests/test_http_client_retry.py`
  - explicit 403 no-retry case,
  - connection error retry case,
  - invalid URL no-retry case.
- `tests/test_retry_policy.py` (new)
  - direct tests for retry policy helpers.
- `tests/test_mr_review_service_integration.py`
  - deterministic ordering under concurrency,
  - observability stats presence checks.
- `tests/test_config_validation.py`
  - validation coverage for `max_concurrent_files`.

### 5) Observability improvements

Updated: `src/luminary/infrastructure/http_client.py`, `src/luminary/infrastructure/gitlab/client.py`, `src/luminary/application/mr_review_service.py`

- Added structured retry logs with fields such as:
  - `component`, `operation`, `attempt`, `max_attempts`, `status_code`.
- Added completion logs with:
  - `duration_ms`, `retry_count`.
- Added MR-level operational stats:
  - `post_success_rate`,
  - `llm_fallback_count`,
  - `review_duration_ms_total`,
  - `review_duration_ms_avg`.

## Configuration

Use in `.ai-reviewer.yml`:

```yaml
limits:
  max_files: 50
  max_lines: 10000
  max_context_tokens: 8000
  chunk_overlap_size: 200
  max_concurrent_files: 1
```

Recommended progression:

- Start with `max_concurrent_files: 1` (baseline).
- Move to `2-4` on stable projects.
- Increase carefully while watching rate limits and retry behavior.

## Operational Checklist

For each environment:

1. Run with `--verbose` in dry-run mode on representative MR.
2. Inspect logs for:
   - retry frequency,
   - parsing fallback frequency,
   - per-file review durations.
3. Verify `post_success_rate` remains high.
4. Tune `max_concurrent_files` only after stable baseline.

## Next Logical Iterations

1. Add optional separate concurrency limit for GitLab posting.
2. Add central metrics exporter (Prometheus/OpenTelemetry), not only structured logs.
3. Unify JSON extraction utility across review and validator code paths.
4. Add e2e scenarios for provider-specific malformed outputs and heavy MR rate-limiting.
