# Agent Farm

Run and orchestrate **many AI coding agents from one web page** — each in a real terminal, each logged in to its own account, with one agent acting as the **orchestrator** that splits work and hands it to the others.

> 🇻🇳 Tiếng Việt: [README.vi.md](README.vi.md)

![Agent Farm](docs/screenshot.png)
<sub>Terminals shown run demo shell commands.</sub>

> **Unofficial community project.** Not affiliated with or endorsed by Google or Anthropic.
> **Platform: Linux** (Windows only through WSL2, untested; macOS unsupported). See [Requirements](#requirements).

## What it does

* **Terminal Studio** – an orchestrator pane on top, worker terminals below (one tile per session). Fixed-size tiles in a scrolling page; one slider resizes every worker at once, another resizes the orchestrator. Drag ⋮⋮ to rearrange; drag a worker onto the orchestrator pane to promote it.
* **Per-account isolation** – every [Antigravity CLI (`agy`)](#agy-vs-claude-code) account gets its own `HOME`, its own D-Bus session and its own `gnome-keyring` (`~/.agy_profiles/accN/`), so tokens never mix. Several terminals can share one account.
* **Orchestrator** – Claude Code (or any `agy` account) receives your goal, splits it and dispatches tasks to workers through a small CLI (`scripts/orchestrator.py`), then reads their terminals back.
* **Synchronized typing** – tick some tiles and whatever you type in one is sent to all of them.
* **Terminals survive hub restarts** – each runs inside tmux; the hub re-attaches on start and the browser reconnects by itself.
* **Dispatch drawer** – plan table (one task per worker), progress board, and a log of every command with its result.
* **First-run help** – the *Start* button checks your machine (`agy`, `dbus`, `gnome-keyring`, `tmux`, `claude`, logged-in accounts) and tells you what is missing.

### `agy` vs Claude Code

| | `agy` (Antigravity CLI) | Claude Code |
|---|---|---|
| Vendor / models | Google (Gemini) | Anthropic (Claude) |
| Accounts | One Google account per profile in `~/.agy_profiles/` | The machine's Claude login (`~/.claude`) |
| Role in the hub | Worker (or orchestrator) | Default orchestrator |
| Modes | `agy --mode plan` · `accept-edits` · plain `agy` (interactive) | Its own modes (Shift+Tab) |

The same email may be used for both services; their tokens and quotas are completely separate.

## Requirements

* **Linux** with `python3 >= 3.10`, `bash`, `dbus-daemon`, `gnome-keyring`:
  ```bash
  sudo apt install dbus gnome-keyring tmux
  ```
* `agy` on your `PATH`; `claude` too if you want Claude Code as orchestrator.
* `tmux` is optional but recommended (without it, restarting the hub closes the terminals).
* **Windows**: not supported natively (the hub uses `pty`/`fcntl`, tmux, D-Bus and gnome-keyring). WSL2 should work with the packages above; it has not been tested. **macOS**: unsupported.

## Install & run

```bash
git clone <this repo> && cd <repo>
pip install -r requirements.txt
./start.sh            # http://127.0.0.1:8888   (./start.sh 9000 for another port)
./stop.sh             # stops only the process listening on that port
```

Open the page, press **Start** and follow the three steps: log in your accounts (*Accounts* tab → 🔑), pick an orchestrator in the top pane (＋ Claude Code), open workers with **Terminal mới**.
Editing `server.py` needs a restart; editing `static/index.html` only needs a page reload.

## Orchestrating from the command line

Claude Code reads `CLAUDE.md` and `.claude/skills/agy-orchestrator/` in this repo and uses:

```bash
python3 scripts/orchestrator.py list                          # accounts, state (busy / waiting / ready)
python3 scripts/orchestrator.py dispatch -t acc2 -p "..."     # type a prompt into a worker (--session <id> to pick a terminal; --wait to also wait for the result)
python3 scripts/orchestrator.py dispatch -t acc2 -p "..." --result-file --wait --result   # cheapest: worker writes its answer to a file, only that is printed
python3 scripts/orchestrator.py read -t acc2 -n 40            # read the worker's terminal screen
python3 scripts/orchestrator.py wait -t acc2 --timeout 120    # wait until the worker's terminal has gone quiet (default 3s of silence)
python3 scripts/orchestrator.py collect --id <dispatch_id>    # store a snapshot as that task's result (shown on the web)
python3 scripts/orchestrator.py control -t acc2 -a interrupt  # esc / enter / interrupt / terminate
```

Dispatching types the prompt into the worker's terminal exactly like a person would. Results are read back from the terminal screen (ANSI-stripped), not from a structured channel. A worker counts as *busy* while its terminal is producing output and *ready* once it has been silent for a few seconds, so a worker that is waiting for your approval also looks quiet (the hub flags approval prompts it recognizes as *waiting*). Hub refuses to dispatch to a busy or approval-waiting worker unless `--force` is given. An idle worker first gets one ESC (to close a popup that would swallow the prompt); if the terminal then shows no reaction at all, `wait` exits with code 4 and says the prompt probably did not arrive.

### What to delegate

The orchestrator is the planner: **hard or complex work (design, tricky debugging, security-sensitive changes, merging results, final review) stays with it.** Workers get work that is heavy but routine (bulk edits, reading many files, writing tests from a spec, running suites, searching). That is also where delegation pays off: the worker's long, noisy work consumes *its* quota instead of the orchestrator's, and `--result-file --wait --result` returns only a few lines to the orchestrator's context. For trivial tasks, doing it directly is cheaper than a delegation round trip. The same rules are in `.claude/skills/agy-orchestrator/SKILL.md`.

## Terminals that survive restarts (tmux)

Each terminal runs in a tmux session on a private socket (`agyhub`, or `agyhub-<port>` for other ports); the page is just a client attached to it. Stopping or even `kill -9`-ing the hub only drops the clients. Per-account D-Bus/keyring daemons (`~/.agy_profiles/accN/.run/`) stay alive with them and are cleaned up when an account has no terminal left. Session metadata lives in `data/sessions.json`.
If `tmux` is not on `PATH`, `~/.local/share/agy-hub/tmux/bin/tmux` is used when present; otherwise terminals simply die with the hub.

## Security

The hub can run arbitrary commands as you. It only answers its own page (cross-origin requests, foreign `Host` headers and foreign WebSocket origins are rejected) and binds to `127.0.0.1` by default. **Do not expose it to a network** – there is no authentication.
Never commit `~/.agy_profiles/` or `~/.claude/`. `data/` holds your paths, prompts and emails and is git-ignored.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AGY_PROFILES_DIR` | `~/.agy_profiles` | where per-account homes live (one hub per directory, enforced with a lock) |
| `AGY_DATA_DIR` | `./data` | tasks, session metadata, dispatch log |

Use both to run a throw-away second hub (tests, demos) without touching your real one.

## Project layout

```text
server.py                 FastAPI + WebSocket PTY, sessions, tmux, per-account runtimes
static/index.html         the whole UI (Vue 3 + Tailwind + xterm.js, no build step)
static/vendor/            vendored front-end libraries (works offline) – see its README
scripts/orchestrator.py   CLI used by the orchestrator agent
scripts/login_profile.sh  OAuth login for one account
.claude/skills/           skill that teaches Claude Code to orchestrate
data/                     local state (git-ignored)
```
