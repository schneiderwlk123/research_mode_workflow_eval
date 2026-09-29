# Repository Guidelines

## Session Startup

Every new session must restore project context before planning or editing. This file is already in context
(it is imported by `CLAUDE.md`, or read directly), so do not re-read it. Everything you need to start is
already here or in one file:

- Read `docs/arch/code-map.md` for the module layout, entry points, and test locations.
- Action items and known gaps are in "Current Actions and Known Gaps" below — this file alone is enough
  to decide what to do next.
- Read `docs/PROJECT_STATUS.md` only when you need the current milestone or verification-baseline numbers.

Do NOT read `初步入库评测指标.md` at startup — it is 3,000+ lines. It is the single authoritative source
for every metric caliber; read only the relevant section when actually working on a metric. Its head
carries a wide-table inclusion index, and its tail appendix holds the wide-table cell contract, the
cancelled/deferred metric register, the open business-confirmation items, and the full-sample baseline.

Then read only the current task's relevant specification, tests, and metric section. Read `mcp_tool_manual.json` only when tool names, parameters, or schemas are needed. For operational work (log salvage, full wide-table recompute, report generation), read the matching manual under `docs/runbooks/` before running scripts. Inspect `git status` before editing and preserve unrelated user changes.

After a verified batch: record milestone or verification-baseline changes in `docs/PROJECT_STATUS.md`, and update the action items below when the next step or a blocker changes.

## Current Actions and Known Gaps

Keep this section current — it is the only place that holds the next step and open blockers, so a session
that reads this file alone must be able to plan. Do not restate metric definitions here; the single
authoritative source is `初步入库评测指标.md`.

Next actions:

- **7.11 `high_risk_turn_profile` retired on 2026-09-29** — no action needed unless it is revived. Its frozen
  threshold `steps > 30 ∧ api_retry_rate > 0.4` was calibrated under the retired `request_id_suffix_v1`
  semantics; under `fingerprint_dup_v2` the research sample's retry rate caps at 0.3333, so `> 0.4` is
  structurally unreachable (7,582 rows → 1 hit, death enrichment 0.00x). The metric was never registered
  in the wide table, had zero consumers, and was never accepted. Implementation, tests (22 cases) and the
  two `__init__` exports were deleted; the decision is registered in `初步入库评测指标.md` appendix 2.1.
  Reviving it requires re-selecting the threshold against the current distribution *and* a fresh proposal
  (candidate thresholds would land in the 0.15–0.3 band). The same applies to the bad-case high-risk set
  that was once derived from it — that set now comes from the report's independent `be≥3 ∨ te>10` caliber.
- 25 metrics have no wide-table column. 11 of them are implemented and tested but were never wired in:
  完全重复调用率、必填参数完整率、Schema 合法率、参数值准确率、路由 P/R、子任务冗余率、意图识别、时间范围提取、
  陈述支持率、平均模型步骤时延、超时率. 3 were once registered and withdrawn on request; the rest are
  unencoded, cancelled, or non-metrics (7.11 moved into the cancelled bucket on 2026-09-29). The inclusion
  index at the head of `初步入库评测指标.md` breaks down all of them by reason.
- Awaiting acceptance confirmation: `citation_traceability_rate` (out of the wide table),
  `turn_completion_rate`, `continuation_restart_count`, `llm_error_count`, `tool_error_rate`,
  `error_recovery_rate`, `compaction_event_count`, `api_retry_rate`, plus
  re-acceptance of `per_tool_call_latency_ms` / `max_tool_latency_share` after the respond-leak fix.

Known gaps:

- No unified Trace model: each of the 32 collectors defines its own `Normalized*Trace` and reuse is by
  cross-import. All 33 handle both the new chat canvas and the research event stream, so the gap is the
  absent shared model, not old-format field reads.
- No config loader: `configs/evaluation.example.toml` is referenced by no code; runtime configuration is
  environment variables only.
- No production tool SLA catalog, so 超时率 returns `missing_tool_sla`; the only SLA in-repo is the example
  policy. Never substitute illustrative thresholds into a production metric.
- No Golden Benchmark or calibration set: `tests/fixtures/` holds only `.gitkeep`, and there is no
  deliverable-reading capability.
- Statement-support embedding thresholds (`high_similarity=0.82` / `low_similarity=0.45`) are uncalibrated
  defaults; production needs a pinned model commit and a pre-warmed cache on the running node.
- The routing standard is generated from an LLM + MCP Schema and is not a human-frozen calibration set; it
  is a traceable judge proxy, not a Golden Benchmark.
- Strict JSON Schema constrains output shape only; each evaluator must still re-validate cross-field business
  relations. Only the intent and routing metrics have a one-shot business repair retry.
- Token snapshots cannot be summed across layers; each metric fixes one of the two authoritative sources
  (chat Agent message, research step-finish).
- The full wide table is research-only (7,582 rows, all `research`); chat-mode numbers live in a separate
  74-Trace sample and the two populations must not be compared.
- `continuation_restart_count` is structurally 0 for every row of the full wide table:
  `stream_compute_metrics.select_query_version` feeds one file per OSS prefix and a single file carries one
  `turn.start`, so restarts are unobservable there. Measuring restarts needs the multi-段 merge used by the
  2026-09-17 sample; the two calibers must not be mixed.

## Project Structure & Module Organization

The evaluation pipeline is `ingestion -> normalization -> evaluation -> aggregation -> reporting`. Keep raw traces immutable and evaluations reproducible.

- `src/collectors/`: input readers and format adapters.
- `src/evaluators/`: deterministic and model-based evaluators.
- `src/reporters/`: aggregation and report generation.
- `src/storage/`: data and artifact access.
- `configs/`: commit-safe configuration examples.
- `docs/arch/`: architecture, log schemas, and the code map.
- `tests/`: behavior tests mirroring `src/`; sanitized fixtures belong in `tests/fixtures/`.

Never commit `data/`, `artifacts/`, or `reports/` content.

## Build, Test, and Development Commands

Configured tools are pytest, Ruff, and mypy via the repo venv. Install and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,oss]"
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest_tmp -q   # system temp dir has an access lock
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check src tests scripts
.\.venv\Scripts\mypy.exe src tests
```

The report script needs matplotlib, which the project venv does not have; run it with anaconda python
(see `.agents/skills/metric-report/SKILL.md`).

## Coding Style & Naming Conventions

Target Python 3.11+ with type annotations. Follow `.editorconfig`: four-space Python indentation and two-space Markdown, YAML, and TOML indentation. Use `snake_case` for modules and identifiers and `PascalCase` for classes. Preserve names such as `trace_id`, `chat_id`, `evaluation_run_id`, and `evaluator_version`.

Use clear Chinese comments and docstrings for business rules, boundary conditions, compatibility logic, error isolation, inputs, outputs, exceptions, and side effects. Explain why the logic exists and how failures can be located; do not repeat self-explanatory statements. Prefer diagnosable exceptions and structured logs carrying stable identifiers over comment-only debugging guidance.

## Development Workflow

Use `.agents/skills/creed-lite/SKILL.md` for documentation, feature, test, debugging, and release work. Announce one route before editing:

- **Route S:** docs, comments, config, or formatting; implement directly and self-check.
- **Route A:** small, clear feature without dependency, schema, persistence, concurrency, or security changes; plan inline, use TDD, and review the diff.
- **Route B:** cross-file feature, new module, unclear requirement, or sensitive architectural change; resolve design choices, state the implementation plan inline, use TDD, and perform strict review.

For bugs, establish an evidenced root cause and failing regression test first. Approval covers the declared files and behavior; request renewed approval for dependencies or expanded scope.

Before coding every metric, assess chat mode and research mode separately. Label each mode as directly computable,
conditionally computable, or not computable. For a mode that cannot be calculated, state the missing fields
or reliable mapping, define a safe `NOT_COMPUTABLE` reason, and never estimate or silently omit that mode.
After implementation, verify and report each mode separately before requesting metric acceptance.

Maintain `docs/arch/code-map.md` once executable code exists. Every change that adds, removes, moves, or materially changes modules, entry points, call chains, configuration paths, schemas, diagnostic locations, or corresponding tests must update the map in the same commit. Keep it concise and path-based; do not record fragile line numbers.

## Testing Guidelines

Name tests `test_<behavior>.py`. Cover normal, boundary, invalid-input, and partial-failure behavior. Assert observable outcomes, use minimal sanitized fixtures, and never weaken assertions to pass tests.

## Commit & Pull Request Guidelines

Commit every completed and verified change batch. Use a short imperative Conventional Commit subject, such as `feat: add tool-call integrity evaluator`, followed by a detailed body covering motivation, behavior changes, important implementation choices, verification actually run, and remaining risks or skipped checks. Keep one intent per commit, review staged files for secrets, and never use placeholder messages. Push only when explicitly requested. PRs should state behavior, affected stages, verification, linked issues, and risks. Update `docs/arch/` before cross-file features.

## Security & Data Handling

Never commit secrets, browser credentials, real user data, raw traces, or generated reports. Redact tool arguments and model content before persistence. Isolate malformed data without silently discarding it, retaining only safe diagnostic context.
