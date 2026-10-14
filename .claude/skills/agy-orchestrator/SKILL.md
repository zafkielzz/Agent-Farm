---
name: agy-orchestrator
description: Act as orchestrator for the AGY Hub multi-agent swarm (acc1..acc6) - dispatch prompts to agy terminals, read their output, wait for completion, interrupt them. Use when the user asks to delegate/giao việc/chia việc to other agents or terminals, check what a worker returned, or run work in parallel across accounts.
---

# AGY Hub Orchestrator

You drive the worker agents (`agy` CLI sessions, profiles `acc1`..`acc6`) running in the Agent Farm hub at `http://127.0.0.1:8888`, through `scripts/orchestrator.py` (run from the repository root, where this skill lives).

## Preflight
1. `python3 scripts/orchestrator.py status` - if it errors, start the hub: `./start.sh` (run in background).
2. `python3 scripts/orchestrator.py list` - see which profiles exist, their roles (acc1 Architect, acc2 Core Implementer, acc3-4 Feature/QA, acc5-6 Reviewer/Batch) and which are running.
3. `data/orchestrator.json` names the profile the hub treats as orchestrator; `broadcast` skips it. Never dispatch to that profile unless the user asks.

## Commands
```bash
python3 scripts/orchestrator.py dispatch -t acc2 -p "<prompt>" -m accept-edits|plan|interactive [--cwd DIR]
python3 scripts/orchestrator.py broadcast -p "<prompt>"
python3 scripts/orchestrator.py read -t acc2 -n 60          # last N lines of the worker terminal
python3 scripts/orchestrator.py wait -t acc2 --timeout 300  # block until idle, prints tail
python3 scripts/orchestrator.py log --limit 20               # dispatch history with ids
python3 scripts/orchestrator.py collect --id <dispatch_id>  # store terminal tail as that job's result (shown in web UI)
python3 scripts/orchestrator.py control -t acc2 -a interrupt|esc|enter|clear|terminate
```

## Workflow
1. Break the user's goal into independent tasks; pick workers by role. Give each worker a **self-contained** prompt (workers share no context with you): goal, exact files/dirs (`--cwd`), constraints, definition of done, and "reply with a short summary when finished".
2. Different workers editing the same repo must use separate git worktrees (hub tab "Git Worktrees") or disjoint files.
3. Dispatch, then `wait` (use `run_in_background` for long waits, several workers in parallel), then `read -n 80` to collect results. `wait` is heuristic (looks for a stable screen with a `>` prompt and no `Generating...`) - always `read` to confirm real completion.
4. Verify worker output yourself (read changed files / run tests) before reporting it as done. Report per-worker outcomes faithfully, including failures.
5. If a worker is stuck: `read`, then `control -a esc` or `interrupt`, then re-dispatch with a clearer prompt. Use `terminate` only when asked.

## Notes
- `accept-edits` lets the worker write files without asking; use `plan` for read-only planning. Prefer `plan` first for risky/large tasks.
- Output is the ANSI-stripped terminal screen buffer, not a structured result; ask workers to end with a clearly marked summary (e.g. `RESULT:`) so it is easy to find.
- Dispatch sends ESC then the prompt + Enter, so it interrupts any in-progress input on that worker.

## Web UI (Terminal Studio: top pane = orchestrator, workers below, "Điều phối" drawer)
The user can also assign work from the hub's **Terminal Studio** tab, "Điều phối" drawer (plan table -> `POST /api/orchestrator/plan/run`, one job per worker per run). Every dispatch is logged in `data/dispatch_log.json` and shown there. After a worker finishes, run `collect` so the result is visible in the UI. A worker's `state` is idle/starting/busy/waiting/ready/stopped (heuristic); `waiting` means it is asking for approval - `read` it and tell the user.
When the orchestrator profile is `claude` (this agent, started from the UI), all acc1..acc6 are workers.
Two profiles sharing a Google email (`duplicate_email` in the workers API) is a login mix-up: tell the user to re-auth, and don't dispatch to both.

## Several terminals per account
One profile may have several running terminals (they share one D-Bus + keyring, so tokens stay consistent). `dispatch -t accN` goes to the most recent running terminal of that profile; to hit a specific one use `dispatch -t accN --session <session_id>` (ids: `GET /api/sessions`). Two terminals on one account share that Google account's quota.
