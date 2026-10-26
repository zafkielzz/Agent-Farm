---
name: agy-orchestrator
description: Orchestrate `agy` (Antigravity CLI) workers through herdr - open isolated per-account panes, send prompts, wait for completion, read results. Use when the user asks to delegate/giao việc/chia việc to other agents, check what a worker returned, or run work in parallel across Google accounts (acc1..acc6).
---

# agy orchestrator (herdr)

You drive `agy` workers, one Google account per profile (`~/.agy_profiles/accN`), inside herdr panes. Hard or complex work stays with you; workers get heavy-but-routine work (bulk edits, reading many files, tests from a spec, running suites, searching). Trivial one-liners: do them yourself.

## Golden rule: never run bare `agy`
A bare `agy` uses the machine's real account and can mix tokens. Always start workers with `agy-up` / `agy-as` (both in `~/.local/bin`, sources in `docs/scripts.md`): they give each account its own HOME, D-Bus session and gnome-keyring.

## Cheapest path first: `agy-ask`
For one self-contained job (read/summarize files, search, write a test from a spec) do NOT open a pane:
```bash
agy-ask acc2 "<prompt>" [--mode plan|accept-edits|auto|default] [--role qa|review|impl|...] [--cwd DIR] [--max 3000] [--timeout 900] [agy flags, e.g. --effort low]
```
It runs `agy-as acc2 -p ... --output-format stream-json` (isolated account) and prints only the final answer, capped at `--max` chars; default mode `plan` is read-only, use `--mode accept-edits` when the worker must edit files. It blocks until done (30s-3min): run it with `run_in_background`, and several in parallel on different accounts. Use panes (`agy-up`, below) only when the job needs several steps, supervision, approvals, or the user wants to watch.

It stops at the `result` event (agy -p often answers and then never exits), kills the process group, deletes that call's conversation `.db*` files, and exits 1 with `TIMEOUT` after `--timeout` seconds. Killing `agy-ask` (Bash timeout, TaskStop) also kills its worker. Always pass `--role` (a short label for this job: `impl`, `qa`, `review`, `research`, `data`...): the office (Habblaud) shows it as the worker's title while it works; without it the account's default from `roles.conf` is shown. Every call logs `start`/`end` lines with status, seconds and token `usage` to `~/.agy_profiles/agy-ask.log.jsonl`: read that file to see what a job really cost.

### What drives worker cost (measured 2026-10-26)
- **Steps, not mode.** Each agent step re-sends the whole context: read-only jobs ran 9-29 steps and 50-180k input tokens; even "reply OK" costs ~7.5k (tool list + rules). Plan vs default mode on the same 3 read-only jobs: 330k vs 349k tokens total, same answer quality, no files changed. The ~3.5x plan-mode cost seen 2026-10-18 was batch labelling with the data in the prompt, where plan mode drafted a plan instead of answering; for that kind of job call `agy-as -p` without a mode (see `13_l2_run_fast.py`).
- **Cheapest lever:** name exact files, line ranges or paste the snippet in the prompt so the worker does not explore. `--effort low` for trivial lookups (untested on quality).

### agy-ask pitfalls (learned 2026-10-16)
- **Preflight every account before real work:** `agy-ask accN "Run: echo ok && pwd" --mode accept-edits --cwd DIR --max 200`. Headless mode cannot prompt, so a missing `permissions.allow` (`command(*)` etc.) in `~/.agy_profiles/accN/.gemini/antigravity-cli/settings.json` makes the worker exit at once with `jetski: no output produced ... auto-denied`. Only acc2-acc4 had it at first; acc5/acc6 did not. Do not edit those settings yourself (blocked as self-modification): give the user the command.
- **`--cwd` must be the directory the worker writes to.** A worker told to write outside its cwd hung ~14 min with no files and no error. Give each worker cwd = its output dir, and say "only write inside DIR".
- **`--mode auto` is not available on these accounts** (falls back to default with a warning); use `accept-edits`.
- **Parallel writers:** never let two workers edit the same file (e.g. a shared log). Have each write `_log_fragment_<acc>.md`; you review and merge.
- **Stall check, not a chatty monitor:** the backgrounded `agy-ask` already notifies on exit. Do not stream every new file (empty out files, `.pyc` each cost a full turn). Check for stalls with a plain `ls -la --time-style=+%m-%d_%H:%M DIR` after ~10 min; `pgrep`/settings reads may get blocked by the safety classifier, `ls` does not.
- **Verify judgement-heavy output.** Workers (Gemini Flash) are reliable for mechanical work (scripts from a spec, counting, tests) but over-claim on judgement: in one audit ~1/4 of "High" findings were false or inflated (e.g. announced record dates flagged as leakage; 1,305 "hindsight" summaries were really 37). Re-check every High finding yourself or with a second worker before reporting.
- Small checks (< ~5 min) are faster to do yourself than to delegate.
- **Batch scripts must call `agy-as accN`, never `agy` with only `HOME` changed.** A worker-written runner set `HOME=~/.agy_profiles/accN` but inherited the parent's D-Bus/keyring, so all 6 "accounts" ran on acc2 and hit its quota together (found via `/proc/<pid>/environ`). In a smoke test, prove *which* account answered (e.g. one account out of quota while another still works), not just that a call succeeds. Also batch several items per call (Phase 2 used 4-5 articles / 12k chars): each `agy -p` call has a large fixed overhead.
- **Killing runners:** `pkill -f`/`pgrep -f` with a pattern also matches your own shell's command line; kill by explicit PID. Runners sleeping on a quota wait may ignore TERM; KILL is safe only while they are idle (results are appended line by line).
- **Language:** write worker prompts in English and ask for the stdout answer in English (Vietnamese costs ~30-50% more tokens, and only you read it; you summarize for the user in their language). Deliverable files meant for people (logs, READMEs, reports) stay in the user's language: say so in the prompt. Quotes from source documents stay verbatim, never translated.

## Start / stop
```bash
agy-up acc2 [--cwd DIR] [agy flags]      # opens workspace "acc2", runs `agy-as acc2 ...`, prints the pane id (e.g. w2:p1)
herdr workspace list                      # what is open
herdr workspace close <w2>                # close a worker
herdr server stop                         # stop everything (agy-up restarts the server on demand)
```
`agy-up` starts the herdr server if needed. Pick workers by account; two panes on one account share that account's quota.

## Drive a worker
```bash
herdr agent prompt <pane> "<prompt>" --wait --until idle   # send, block until it settles
herdr agent read <pane> --lines 40                         # read its screen (ANSI-stripped)
herdr agent list                                           # status of every agent: working / blocked / idle / done
herdr agent wait <pane> --until blocked --until idle --timeout 120000
herdr agent send-keys <pane> esc | enter | down | right    # key names only, not text
herdr pane send-text <pane> "/help"                        # raw text (then send-keys enter)
herdr agent explain <pane>                                 # why herdr thinks it is idle/blocked
```
Run long waits with `run_in_background`. `blocked` means the worker waits for an approval or a question: `read` it and tell the user, do not guess. Verify what matters yourself before reporting it as done.
Save context: ask the worker for a short answer (<= 25 lines) and read only that; do not paste raw screens back.

## agy modes and flags (`agy --help`)
- `--mode plan` - read-only: researches and writes a plan **artifact**, does not run shell commands or edit code, then waits for approval. Never give it implementation work. **herdr's `agent prompt` to a plan-mode pane arrives as `/plan <prompt>`.**
- `--mode accept-edits` - edits files without asking; `--mode auto` - auto-approves more; plain `agy` - interactive, asks for approval (shows up as `blocked`).
- `--dangerously-skip-permissions` auto-approves every tool call: only on the user's explicit say-so.
- `--model <m>`, `--effort low|medium|high|xhigh|max`, `-c/--continue`, `--conversation <id>`, `--add-dir DIR`, `--project`, `--sandbox`.
- Non-interactive one-shot: `agy-as accN -p "<prompt>" --output-format text|json|stream-json` (`--print-timeout`, `--json-schema`). No pane needed; cheapest for a single self-contained job.
- Subcommands: `agy agent(s)` lists custom agents, `agy changelog`.

## agy slash commands (type with `pane send-text`, then `send-keys enter`)
`/help` (full list, 46 items), `/clear` (new conversation), `/model`, `/effort`, `/config`, `/context` (token use), `/credits` (quota), `/diff`, `/codesearch <q>`, `/add-dir`, `/agents`, `/artifact` (review plan artifacts), `/btw` (side question), `/fork`, `/copy`, `/open`, `/permissions`, `/hooks`, `/mcp`, `/plugin`, `/remote-control`, `/changelog`, `/logout`, `/exit`. Menus like `/help` take focus: close with `esc` before the next prompt. Run `/clear` between unrelated jobs.

## When to delegate
You plan; workers execute. Always write the plan, scope and definition of done yourself first: workers share no context, so a vague prompt gives a vague result.
- **Delegate** when the spec is clear and the work is mechanical and long: code from a spec, writing/running tests, bulk edits, reading many files and summarizing, data gathering. Long output costs the worker's quota, not yours.
- **Keep** design, hard debugging, security-sensitive changes, merging results, final review, and anything under a few lines (a delegation round costs more than doing it).
- Pick by role (`acc2-impl` code, `acc3-qa` tests, `acc4-review` read-only review); self-contained one-shots go through `agy-ask`.

## Worker-to-worker work
Do not let workers chat freely: no one supervises it, loops burn two accounts' quota, and failures are hard to trace. In order of preference:
1. **Handoff file** - worker A writes its result to a file, you then task worker B to read it (cheap, leaves a trail, you see every step). Default.
2. **Through you** - read A's result, trim it, pass it to B.
3. **Direct** (`herdr agent prompt` from one worker to another) only for a tight loop such as implement -> run tests -> fix, with a hard cap (e.g. 3 rounds). Not verified that workers can call `herdr` (they have no Herdr skill); test once before relying on it.

## Roles and modes
Workers are named `<acc>-<role>` from `~/.agy_profiles/roles.conf` (e.g. `acc2-impl`, `acc3-qa`, `acc4-review`); pick the worker by role, not pane id. After any (re)start run `agy-sync` (names + default mode). Change mode in-session with `agy-mode <name> default|accept-edits|auto|plan` (shift+tab cycle; idle agents only). Check the mode before sending write work: `--mode` at launch is not always applied. Worker answer rules live in `worker-rules/GEMINI.md` (symlinked to each profile as `GEMINI.md`). Workers commit locally only; you review and push.

## Rules
- agy `idle`/`done` does NOT prove the worker finished: Antigravity can sit idle while background tasks or subagents still run. Read the answer before reporting done.
- Do not run bare `herdr` to explore (it opens the TUI); use `herdr --help` / `herdr <group>`.
- Driving herdr from outside a herdr pane works (socket) but is off-label: herdr's own skill (`herdr --skill`) assumes the agent runs inside a pane (`HERDR_ENV=1`). Do not install that skill alongside this one.
- Same repo, several writers: separate git worktrees (`herdr worktree ...`) or disjoint files.
- Prompts: self-contained (workers share no context): goal, files/dirs, constraints, definition of done, "reply briefly". Name a tool only when correctness depends on it ("run `date`").
- Check the login at start: the pane banner shows the account email. Two panes showing the same email under different accN = login mix-up; tell the user.
