# Agent Farm

A Claude Code session becomes the **boss** (`/boss`) and gives jobs to **workers**: other Antigravity (`agy`) or Claude
Code accounts. These are the scripts, Claude Code skills and commands for that, on their own. They are developed in
[Vi-Habblaud](https://github.com/zafkielzz/Vi-Habblaud) (`tools/agy`), a pixel office that also shows the boss and its
workers at work; this repository is a copy of that folder, so open issues and changes there.

**Linux only** (on Windows, use WSL2). Everything works without Habblaud: jobs run headless, and `/boss`, `/workers`,
`/offices` edit the offices from the terminal. What Habblaud adds: a terminal per worker you can watch, the office on a
web page, and the office rules hook for the boss itself (workers get it either way).

## How it works

These scripts are how the boss gives its workers jobs. `agy-ask` and `claude-ask` send a
job to an Antigravity (`agy`) or Claude Code account and print only the final answer, so the boss spends no tokens on a
worker's reasoning. Every job is a new conversation.

Needs Linux, Python 3, Claude Code, and for Antigravity workers the `agy` CLI, `dbus-daemon` and `gnome-keyring-daemon`.

## Install

```bash
git clone https://github.com/zafkielzz/Agent-Farm.git && cd Agent-Farm
./install.sh
```

It links everything from this folder into `~/.local/bin`, the Claude Code skills and each agy profile, so editing a file
here changes the installed one. Then, once per Antigravity account:

```bash
mkdir -p ~/.agy_profiles/acc1
agy-as acc1        # sign in with Google when asked
./install.sh   # again, so the new profile gets the rules plugin
```

Never run a bare `agy`: it uses the machine's own account. Profiles hold sign-in tokens: keep `~/.agy_profiles` out of
any repository.

## What is here

| Path | What it does |
|---|---|
| `bin/agy-as` | Runs `agy` in one account's profile (own HOME, D-Bus, keyring) |
| `bin/worker-ask` | `agy-ask` and `claude-ask`: give a job, print its answer |
| `bin/office_rules.py` | Office rules, the Python twin of `shared/rules.ts` |
| `bin/rules-hook` | PreToolUse hook that enforces the office rules |
| `bin/office-boss` | Appoint the calling Claude Code session the boss of the office for its folder (the deepest office holding it, else a new one with every signed-in worker; in `~`: the office "cli" with no folder limit), or of a named office; `off` steps down; `list` / `rm` manage the offices. A running office re-reads `team.json` within a second |
| `commands/boss.md` | The Claude Code command `/boss [office\|off]` (linked into `~/.claude/commands`): runs `office-boss` for the session you type it in, so you appoint a boss from the terminal |
| `commands/quota.md` | `/quota`: every worker's 5-hour and weekly use (agy `/usage`, no tokens; kept in `~/.habblaud/usage/agy:accN.json` for agy-ask's quota stop); in a boss session, workers near the limit leave the office and benched ones come back after their reset |
| `commands/workers.md` | `/workers`: the workers of this session's office by account, each session with its state (working now / free / not signed in), then the accounts that can be added and the ones that can be cloned. `/workers add <account>…` takes accounts that are not in the office yet (`acc3` or `3`, `claude:.claude-work`). `/workers clone <account> [N]` gives N more sessions (1 by default) of an account already there, or of the boss's own Claude account (`claude` = `~/.claude`): clones (`acc2#2`, `claude:.claude#2`), each with its own terminal and BUSY, on the account's quota. `/workers rm <id>…` removes one session (`acc2#2`), an account with all its sessions (`acc2`) or everyone (`all`) |
| `commands/office.md` | `/office`: every command used in an office (`/boss`, `/workers` and its verbs, `/quota`, `/offices`, `agy-ask`, `claude-ask`), one example each (`office-boss help`) |
| `commands/offices.md` | `/offices [rm <office>]`: the open offices and the ended ones a resume brings back (an office closes with its boss's session: `office_rules.tidy`, run by agy-ask, office-boss and `rules-hook boss` at every prompt) |
| `agy-plugin/` | `office-rules` (the hook inside agy) and `agy-boss` (loads the boss rules) |
| `boss/AGENTS.md` | Rules for an Antigravity boss |
| `worker-rules/GEMINI.md` | Rules for Antigravity workers |
| `skills/` | `agy-orchestrator` and `office-rules`, for a Claude Code boss |

## Using it

```bash
agy-ask team    # the offices, their folders, bosses and workers
agy-ask acc2 "<prompt>" --role impl --mode accept-edits --effort medium --cwd <project>
claude-ask .claude-work "<prompt>" --role review --mode plan --effort high --cwd <project>
agy-ask rules   # the office's rules
```

With Habblaud running the job goes to the worker's terminal in the office, which you can watch; otherwise it runs
headless. Exit codes: 0 done, 1 failed or timed out, 2 refused by the team or office rules, 3 worker busy, 4 cancelled
from the office.

Offices live in `~/.agy_profiles/team.json`, edited by `/boss`, `/workers` and `/offices` or in Habblaud. Optional `~/.agy_profiles/roles.conf` sets each
account's role and default mode (`acc2 impl accept-edits`; role `off` hides an account), and `names.conf` a display name
(`acc2 Alice`).

## Tests

```bash
python3 -m pytest tests
```

The rules run the same cases as the TypeScript code (`tests/rules.vectors.json`, a copy of Habblaud's `shared/rules.vectors.json`).
