# Repository Guidelines

## Session Startup

Every new session must restore project context before planning or editing. Read, in order:

1. `AGENTS.md`
2. `docs/START_HERE.md`
3. `docs/PROJECT_STATUS.md`
4. `docs/arch/code-map.md`
5. `初步入库评测指标.md`（根目录；评测指标的权威口径来源）

Then read only the current task's relevant specification, tests, and metric section. Read `mcp_tool_manual.json` only when tool names, parameters, or schemas are needed. For operational work (log salvage, full wide-table recompute, report generation), read the matching manual under `docs/runbooks/` before running scripts. Inspect `git status` before editing, preserve unrelated user changes, and update `docs/PROJECT_STATUS.md` after a verified batch changes the milestone, next action, blockers, or verification baseline.

## Project Structure & Module Organization

The evaluation pipeline is `ingestion -> normalization -> evaluation -> aggregation -> reporting`. Keep raw traces immutable and evaluations reproducible.

- `src/collectors/`: input readers and format adapters.
- `src/evaluators/`: deterministic and model-based evaluators.
- `src/reporters/`: aggregation and report generation.
- `src/storage/`: data and artifact access.
- `configs/`: commit-safe configuration examples.
- `docs/arch/` and `docs/specs/`: architecture and acceptance criteria.
- `tests/`: behavior tests mirroring `src/`; sanitized fixtures belong in `tests/fixtures/`.

Never commit `data/`, `artifacts/`, or `reports/` content.

## Build, Test, and Development Commands

No executable package or toolchain is configured yet. Add reproducible install, test, lint, format, type-check, and run commands with the first code change. Document only configured tools, such as `python -m pytest`, `ruff check .`, and `mypy src tests`.

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
