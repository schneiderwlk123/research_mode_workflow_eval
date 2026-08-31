---
name: creed-lite
description: "Use when starting documentation, feature, test, debug, or release work in this repository, or when unsure which development route applies. Routes work through the compact S/A/B workflow defined in AGENTS.md."
---

# Creed Lite

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, ignore this skill.
</SUBAGENT-STOP>

Announce `Using creed-lite, route: <row>` first, then follow only that row's chain. Read the repository `AGENTS.md` before acting.

## Route Table

| Task shape | Chain |
|------------|-------|
| Docs, comments, config, or formatting only (Route S) | implement directly → self-check |
| Bug, test/CI failure, or unexpected behavior | debug → TDD → review → delivery |
| Small feature with clear requirements (Route A) | inline plan → TDD → review → delivery |
| Cross-file feature or unclear requirements (Route B) | explore if needed → design Q&A → plan file → TDD → review → delivery |

## Stage Rules

**Explore:** For Route B tasks with many named files, methods, classes, or schemas, inspect every named anchor and verify repository-answerable claims with file-and-line evidence. Report facts rather than guesses.

**Design Q&A:** Ask only unresolved judgment questions. Batch them in one message with lettered choices, exactly one recommendation, and the outcome, mechanism, and cost of each option. Do not ask the user for facts available in the repository. Wait for design approval before coding when choices materially change behavior.

**Plan:** Before coding, list exact files, reused components, tests, risks, and scope. Create `docs/creed/plans/YYYY-MM-DD-<feature>.md` for Route B or work containing at least three implementation tasks. Each task must identify its files and RED→GREEN steps. If executable code exists, include any required `docs/arch/code-map.md` update in the same slice.

**TDD:** Run a behavior-focused test and observe the expected failure before writing production code. Add the smallest implementation that makes it pass, then refactor while green. Cover normal, boundary, invalid-input, and partial-failure behavior as applicable. Mock only external boundaries and prefer small fakes.

**Debug:** State an evidence-backed root cause before fixing it. Add and run a failing regression test first. Do not claim success without fresh verification from the current turn.

**Review:** Inspect the actual diff against the approved scope. Check correctness, test quality, failure isolation, data leakage, reproducibility, evaluator versioning, large-file memory behavior, and removable complexity. Report findings as Critical, Important, or Minor; Critical and Important findings block delivery.

**Delivery:** Report changed behavior, files, commands actually run, results, skipped checks, and remaining risks. After review and validation, commit every completed change batch with one intent, a concise Conventional Commit subject, and a detailed body explaining motivation, behavior, key choices, verification, and risks. Inspect staged files for secrets before committing. Never commit on protected branches (`main`, `master`, `pro`, or `test`) and push only with explicit user authorization. Provide a PR title, summary, and concrete test-plan checklist when relevant.

## Checklist

- [ ] Route announced and only its stages executed
- [ ] Approved scope and repository invariants preserved
- [ ] Required RED→GREEN evidence captured
- [ ] `docs/arch/code-map.md` updated when code navigation changed
- [ ] Actual diff reviewed
- [ ] Verified change batch committed with a detailed message
- [ ] Final report includes behavior, files, validation, skipped checks, and risks
