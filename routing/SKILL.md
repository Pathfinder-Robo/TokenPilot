---
name: argon-routing
description: Delegate suitable substantive implementation, debugging, refactoring, tests, and code analysis to the configured Google Argon CLI worker at high effort, guided by the live routing preference. Codex owns independent acceptance and integration. Skip trivial edits and non-coding tasks.
---

# Argon routing

Use the configured external CLI worker for suitable coding work. Codex owns task boundaries, independent acceptance, integration, and fallback. This does not change the models accepted by native `spawn_agent`.

## Read the live preference

Before every substantive coding task and again before launching a worker, run:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/argon-routing/scripts/routing_control.py"
```

TokenPilot updates `${CODEX_HOME:-$HOME/.codex}/argon/routing.json`. Read it fresh. Explicit task instructions and project restrictions take precedence. Changes affect new tasks; do not interrupt active workers because the slider moved.

The integer `gemini_percent` guides delegation, not token spending or a random percentage of requests:

- **0:** GPT only; the launcher blocks new Gemini runs.
- **1–24:** Delegate large isolated work only.
- **25–49:** GPT led; delegate substantial self-contained work when useful.
- **50–74:** Balanced; delegate complete self-contained implementations while Codex handles small or tightly coupled work.
- **75–99:** Gemini preferred for most suitable substantive coding, including backend logic and tests.
- **100:** Delegate every suitable substantive implementation task. Start with the complete implementation scope and retain only work that needs Codex's tools, trusted integration, or independent acceptance.

## Delegate complete outcomes

Prefer a coherent feature, bug fix, or refactor with clear acceptance criteria over a single-file slice. File boundaries should follow the work: a worker may own backend logic, UI, supporting types, and task-relevant tests together. Multi-file work and difficult work are suitable when the necessary context can be isolated and behavior independently checked. Do not reserve backend implementation or test authoring for Codex by habit.

Before implementing at high preference, identify the largest coherent scope the worker can complete with its tools. If part requires a browser, connector, unavailable runtime, or trusted integration, Codex performs that part and supplies the resulting evidence or fixtures. An inability to run a particular check does not alone exclude implementation; Codex can run it independently. Keep small edits direct when preparing a worker would outweigh the work.

Read relevant recent entries in the configured Codex home's `argon/outcomes.jsonl`. Expand scope after repeated independent acceptance with little rework. Narrow scope for recurring implementation defects or missing context. Treat transport/API failures as operational failures, not evidence of poor coding ability. Do not infer general capability from one smoke test.

## Prepare and run

Prepare an isolated copy containing only task-relevant source, configuration, and fixtures. Keep the original working tree, unrelated files, supervisor tests, and result directories outside the worker workspace. Write a task brief with requirements, owned files or directories, relevant interfaces, available runtime, and acceptance criteria. Ask the worker to finish the implementation and useful tests, report checks and remaining limitations, and iterate on failures within its available tools.

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/argon-routing/scripts/run_worker.py" \
  --workspace /absolute/path/to/task-copy \
  --task-file /absolute/path/to/task.txt \
  --result-dir /absolute/path/to/new-results \
  --python /absolute/path/to/python
```

Omit `--python` to use the launcher's interpreter. `--check-config` reports configuration without calling Google; `--mode review` provides file-reading tools only. Existing `GEMINI_API_KEY` and `GEMINI_MODEL` environment variables override saved values; use diagnostic flags to verify the intended configuration without printing credentials. Do not silently switch model or provider if Argon is unavailable.

Use **high** effort and the launcher's default allowances: 262144 output/thinking tokens per request, 4000000 cumulative tokens, 40 interactions, 3600 seconds overall, and 900 seconds per API interaction. These are ceilings, not usage targets. Omit limit overrides for normal implementation; do not copy the experiments' two-turn or 16k–40k token limits. Reduce allowances only for an explicit user constraint or a deliberately bounded diagnostic, and state the reason. Lower effort only when the user explicitly requests it or prioritizes latency. Use asynchronous/background orchestration so a long worker run does not prevent progress updates.

Available tools are scoped file reads/writes and sandboxed Python. General shell, browsers, and connectors are not worker tools. macOS uses sandbox-exec; Linux uses bubblewrap with a read-only sandbox root, read-only system/runtime mounts, a writable workspace, and separate network namespace. Nested sandbox restrictions may require an approved host-level broker launch; preserve the worker's inner isolation. If execution is unavailable, use `--file-tools-only` and let Codex run project checks. Verify runtime dependencies rather than disabling isolation.

Keep the trusted interpreter and virtual environment outside the writable worker copy. Runtime metadata is queried by the coordinator and its narrow prefixes exposed read-only; candidate code must not replace the interpreter. Symlink-based virtual environments retain their original launcher path.

## Protect task context

Use only the current user's applicable authorization. The setup installer records `--authorize-code-export` in `argon/authorization.json`; apply that standing authorization to necessary task context without asking repeatedly. A copied skill is not authorization. The launcher refuses absent or false records; re-enable revoked authorization only with explicit user approval. Explicit project restrictions and later instructions still apply.

Exclude secrets, `.env` files, unrelated personal data, and full chat histories. Credentials belong in the configured Codex home's `argon/credentials.env`, outside the worker workspace.

## Accept, recover, or take over

Read actual diffs, `result.json`, `final.txt`, and relevant tool results. `status=completed` is a self-report. Inspect the implementation and run independent behavioral checks, including important boundaries and integration. Run tests from the intended directory after confirming the files exist. Model-authored tests alone do not establish acceptance.

For a localized defect, repair it directly when trivial, or give the worker one focused correction when it can usefully finish its scope. For a bounded unfinished run, inspect the recorded state and use `--resume-result-dir` to continue pending tool results in a new result directory when supported. For a recorded transient 429/502/503/504, inspect the error before explicitly using `--resume-api-error`. Do not resume completed runs.

For transport failures such as `IncompleteRead`, inspect saved artifacts and interaction state before choosing recovery. Use a supported recovery path or one focused retry when it can make progress; do not invent resume support, repeatedly restart without progress, or immediately infer that the implementation must move to Codex. Take over when recovery fails, the defect repeats, or tools/context no longer fit. Preserve useful partial work and explain the actual reason for takeover.

Integrate reviewed changes while preserving unrelated user edits. Append an outcome record with date, category, thinking level, worker status, independent acceptance, defects, Codex rework, outcome, and result directory. Record assigned scope and fallback reason when applicable; store no code or secrets. Distinguish worker-authored implementation from Codex implementation and independent checks. Use repeated acceptance and rework to improve future task selection, without optimizing for token volume.

Tell the user what Argon implemented, what Codex verified or finished, and any remaining limitation. The launcher records numeric usage sources in `argon/runs.jsonl`; the viewer reads interaction usage as it arrives and does not read the saved API key.
