---
name: agy-orchestrator
description: Act as orchestrator for the AGY Hub multi-agent swarm (acc1..acc6) - dispatch prompts to agy terminals, read their output, wait for completion, interrupt them. Use when the user asks to delegate/giao việc/chia việc to other agents or terminals, check what a worker returned, or run work in parallel across accounts.
---

# AGY Hub Orchestrator

You drive the worker agents (`agy` CLI sessions, profiles `acc1`..`acc6`) running in the Agent Farm hub at `http://127.0.0.1:8888`, through `scripts/orchestrator.py` (run from the repository root, where this skill lives).

## Preflight
1. `python3 scripts/orchestrator.py status` - if it errors, start the hub: `./start.sh` (run in background).
2. `python3 scripts/orchestrator.py list` - see which profiles exist, which are running, their **agy mode** and an optional role note. **Mode `plan` is read-only: such a worker will NOT edit files**, so do not hand it implementation work (give that to an `accept-edits` or `interactive` worker, or ask the user).
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

## What to delegate and what to keep (read this first)
You are the planner and coordinator, so **anything that is genuinely hard or complex stays with you**. Workers are for work that is *heavy but routine*.

- **Keep for yourself:** architecture and design decisions, planning and splitting the goal, tricky or ambiguous debugging, anything needing deep reasoning or judgment, security-sensitive changes, reconciling or merging several workers' results, and the final review. Also trivial one-liners (a date, `pwd`, simple arithmetic).
- **Delegate:** mechanical or high-volume work with a clear definition of done: reading and summarizing many files, bulk or boilerplate edits, writing tests from a clear spec, running a test suite and reporting failures, searching a codebase, repeating the same change in several places. Independent pieces can run in parallel on different workers.
- Hard problem that also has a large mechanical part: you do the thinking, then hand the mechanical part over with exact instructions.
- Verify what matters: for risky parts read the changed files or re-run the tests yourself instead of trusting a summary.

## Workflow
1. Break the user's goal into independent tasks; pick workers by their mode (and the role note, if the user set one). Give each worker a **self-contained** prompt (workers share no context with you): goal, exact files/dirs (`--cwd`), constraints, definition of done, and "reply with a short summary when finished".
2. Different workers editing the same repo must use separate git worktrees (hub tab "Git Worktrees") or disjoint files.
3. **Save your own tokens** (the point of delegating): for a worker that is not in `plan` mode use
   `dispatch -t accN -p "..." --result-file --wait --result`. The worker writes its final answer to a small file and the call prints only that (a few lines), not the screen. For a `plan` worker (read-only) tell it to end with one `RESULT: ...` line and use `--wait --result`; the output is then just that block. Never paste a worker's raw output back into your context, and do not `read` the screen unless the answer looks wrong. With an `interactive` worker the file write may stop at an approval prompt (`CHỜ DUYỆT`): then tell the user.
   For one worker prefer a single call: `dispatch -t accN -p "..." --wait` (sends, waits until the terminal goes quiet, prints the compacted screen) - one tool call instead of dispatch + wait + read. For several workers in parallel dispatch each, then `wait`. Dispatch, then `wait` (use `run_in_background` for long waits, several workers in parallel), then collect results (`wait` already prints the compacted tail of the screen; use `read -n 40` only if you need more, `--full` to see rulers/spinners too). `wait` returns when the worker's terminal has produced no new output for `--settle` seconds (default 3) after the dispatch: a working agent keeps redrawing, an idle one is silent. That also happens when the agent is waiting for your approval, so `read` the screen to confirm it really finished (the output says if the hub saw an approval prompt).
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

## Writing prompts for workers
- Workers are capable models: state the task plainly, add only what they cannot know (directory, files, constraints, what "done" means) and ask for a short answer. Long role-play or step-by-step coaching mostly adds latency.
- Name a tool only when correctness depends on it (a model has no clock: for "what is today's date" say "run `date`"; for facts about the repo say "read the file").
- A marker such as `RESULT:` is optional. It helps only when the answer is buried in long output; do not read the screen before `wait` returns, otherwise you may see the worker's own thinking text instead of the answer.

## Timing rules (learned from real runs)
- **Never use a fixed `sleep`** to wait for a worker. It costs its full length whether or not the worker is done and hides a lost prompt. Use `dispatch ... --wait` or `wait`.
- Short, simple task: add `--settle 2` (the minimum). Measured on real `agy`: while it works it redraws constantly (no silent gap above ~0.2s, even while a tool runs), so silence really means "finished". `wait` prints a timing breakdown (reaction / work / quiet).
- **Exit code 4 = no reaction**: the prompt probably never reached the input box (an overlay such as "Artifacts", a slash menu, or an approval prompt had the focus). `read` the worker, `control -t accN -a esc`, then resend once. Dispatch already sends one ESC to an idle worker first (`--no-clear` disables it). If a second try also gets no reaction, tell the user instead of looping.
- Exit code 2 = timeout: `read` to see what the worker is doing; do not blindly re-dispatch.
- "Working..." / spinner glyphs left on the screen do not mean busy: they stay as stale text after the agent finished. Trust the `wait` result (`✅ ... đã im lặng`), not those words, and do not `read` again to double-check unless the answer looks wrong.
- Do not delegate trivial work (today's date, simple arithmetic, a single `pwd`): running it yourself takes seconds, a worker round trip takes tens of seconds. Delegate work that is heavy or can run in parallel.

## Keeping your own context small
- Give workers the whole task in one self-contained prompt; every correction round costs you several turns.
- Ask for short results (<= 25 lines). The result file is capped by `--max-chars` (default 3000).
- Run `/clear` between unrelated jobs: every turn of yours re-reads the whole conversation.

