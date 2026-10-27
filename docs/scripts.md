# Mã nguồn các script

Chép từng đoạn vào `~/.local/bin/<tên>` rồi `chmod +x`. Thứ tự dùng: `agy-as` (chạy agy trong profile cô lập) → `agy-mode` / `agy-sync` (mode, tên, role) → `agy-ask` / `agy-up` (giao việc).

## agy-as

```bash
#!/usr/bin/env bash
# agy-as <accN> [agy args...] - run `agy` confined to ~/.agy_profiles/<accN>
# (own HOME/XDG dirs, own D-Bus session, own unlocked gnome-keyring). The daemons are
# shared by every terminal of that account and reused if already running (same layout as the hub).
set -eu
acc="${1:?usage: agy-as <accN> [agy args...]}"; shift
home="$HOME/.agy_profiles/$acc"
[ -d "$home" ] || { echo "agy-as: no such profile $home" >&2; exit 1; }
real_bin="$HOME/.local/bin"

for v in XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS DBUS_STARTER_ADDRESS DBUS_STARTER_BUS_TYPE \
         GNOME_KEYRING_CONTROL GNOME_KEYRING_PID SSH_AUTH_SOCK SSH_AGENT_PID GPG_AGENT_INFO SESSION_MANAGER; do
  unset "$v"
done
export HOME="$home" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share" \
       XDG_CACHE_HOME="$home/.cache" XDG_STATE_HOME="$home/.local/state"
export PATH="$real_bin:$PATH"
run="$home/.run"; sock="$run/bus"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$sock"
mkdir -p "$run" "$XDG_DATA_HOME/keyrings"

bus_alive() { [ -S "$sock" ] && dbus-send --session --dest=org.freedesktop.DBus --print-reply \
                /org/freedesktop/DBus org.freedesktop.DBus.GetId >/dev/null 2>&1; }
if ! bus_alive; then
  rm -f "$sock"
  setsid dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --nofork --nopidfile >/dev/null 2>&1 &
  for _ in $(seq 50); do [ -S "$sock" ] && break; sleep 0.1; done
  echo '' | setsid gnome-keyring-daemon --unlock --foreground --components=secrets >/dev/null 2>&1 &
  sleep 1
fi
exec agy "$@"
```

## agy-ask

Viết bằng Python (cần `python3`): đọc `stream-json`, dừng ở sự kiện `result`, kill cả nhóm process và xoá file hội thoại của lần gọi; mỗi lần gọi ghi một dòng `start` (role, mode, model, effort, PID của Claude đã gọi, prompt; trước khi agy chạy) và một dòng `end` (trạng thái, số giây, token) vào `~/.agy_profiles/agy-ask.log.jsonl`, cả hai kèm PID của agy-ask. `--role` là nhãn tự do cho việc (qa, review, impl...), Habblaud hiện nó làm tiêu đề của worker khi đang làm. Worker chỉ chạy Gemini 3.8 Flash: `--model` khác bị từ chối, `--effort` chỉ nhận low/medium/high. Không có `--mode`/`--effort` thì dùng mặc định của worker trong `~/.agy_profiles/defaults.json` (chỉnh trong Habblaud). Luật đội (`~/.agy_profiles/team.json`, chỉnh trong Habblaud; không có file = không có luật): đã phong boss thì chỉ phiên Claude đó (PID là tổ tiên của lời gọi) được giao việc, và chỉ cho tài khoản đã đăng ký làm worker; từ chối thì exit 2. Việc giao từ văn phòng (Habblaud đặt `AGY_ASK_FROM_OFFICE=1`, gọi tay cũng dùng được) không bị chặn. `agy-ask team` in boss và danh sách worker.

```python
#!/usr/bin/env python3
"""agy-ask <accN> "<prompt>" [--mode plan|accept-edits|auto|default] [--effort low|medium|high] [--role NAME] [--cwd DIR] [--max N] [--timeout SEC] [agy flags]
One-shot worker call through agy-as (isolated account): prints ONLY the final answer, capped at N chars
(default 3000). Cheapest way to delegate a self-contained job; no pane, no screen reads.

agy -p often prints its answer and then never exits, so the call reads --output-format stream-json and stops at
the "result" event, kills the whole process group, and deletes that call's conversation files (a killed call leaves
a ~32 MB .db-wal behind). Each call appends a "start" line (role, mode, prompt) before agy starts and an
"end" line (status, seconds, token usage) to ~/.agy_profiles/agy-ask.log.jsonl, both with the agy-ask PID (the
parent of the agy process). --role is a free label for the job (e.g. qa, review, impl) that the office shows.
Workers only run Gemini 3.8 Flash (the account default): --model accepts only gemini-3.8-flash[-low|-medium|-high],
and --effort only the levels Flash has (low, medium, high). Without --mode / --effort the call uses the worker's
defaults from ~/.agy_profiles/defaults.json ({"acc2": {"mode": "plan", "effort": "low"}}, set from Habblaud), else
mode plan and the account's default effort.

Team rule (~/.agy_profiles/team.json, edited in Habblaud; no file = no rule): with a boss appointed, only that Claude
Code session (its PID is an ancestor of this call) may give jobs, and only to the accounts registered as workers.
Jobs sent from the office (Habblaud sets AGY_ASK_FROM_OFFICE=1, also usable by hand) skip the rule.
`agy-ask team` prints the boss and the workers.
"""
import glob, json, os, signal, subprocess, sys, threading, time

HOME = os.path.expanduser("~")
EFFORTS = ("low", "medium", "high")  # Gemini 3.8 Flash thinking levels
MODEL = "gemini-3.8-flash"  # the only model workers use
LOG = f"{HOME}/.agy_profiles/agy-ask.log.jsonl"
DEFAULTS = f"{HOME}/.agy_profiles/defaults.json"
TEAM = f"{HOME}/.agy_profiles/team.json"
USAGE = __doc__.splitlines()[0]


def die(msg):
    print(f"agy-ask: {msg}", file=sys.stderr)
    sys.exit(1)


def parse(argv):
    if len(argv) < 2:
        die(f"usage: {USAGE}")
    a = {"acc": argv[0], "prompt": argv[1], "mode": "", "role": "", "max": 3000, "cwd": os.getcwd(), "timeout": 900, "extra": []}
    rest = argv[2:]
    while rest:
        k = rest.pop(0)
        if k in ("--mode", "--role", "--max", "--cwd", "--timeout"):
            if not rest:
                die(f"{k} needs a value")
            v = rest.pop(0)
            a[k[2:]] = int(v) if k in ("--max", "--timeout") else v
        else:
            a["extra"].append(k)  # passed to agy as is (e.g. --effort low)
    effort, model = flag(a["extra"], "--effort"), flag(a["extra"], "--model")
    if "--effort" in a["extra"] and effort not in EFFORTS:
        die(f"--effort must be one of {', '.join(EFFORTS)}")
    if "--model" in a["extra"] and model not in [MODEL] + [f"{MODEL}-{e}" for e in EFFORTS]:
        die(f"workers only use {MODEL} (got --model {model!r}); choose the thinking level with --effort")
    d = defaults(a["acc"])
    a["mode"] = a["mode"] or d.get("mode") or "plan"
    if "--effort" not in a["extra"] and d.get("effort") in EFFORTS:
        a["extra"] += ["--effort", d["effort"]]
    return a


def defaults(acc):
    """The worker's defaults for calls that do not say (defaults.json), or {}."""
    try:
        with open(DEFAULTS) as f:
            d = json.load(f).get(acc)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def team():
    """The team file ({"boss": {..., "pid", "name"}, "workers": [...]}), or None (no file: no rule)."""
    try:
        with open(TEAM) as f:
            t = json.load(f)
        return t if isinstance(t, dict) else None
    except (OSError, ValueError):
        return None


def check_team(acc, caller):
    """Refuses (exit 2) a job the team rule does not allow; jobs from the office are always allowed."""
    t = team()
    if t is None or os.environ.get("AGY_ASK_FROM_OFFICE") == "1":
        return
    boss = t.get("boss") if isinstance(t.get("boss"), dict) else None
    workers = t.get("workers")
    if boss:
        name = boss.get("name") or boss.get("id") or "?"
        if not caller or caller != boss.get("pid"):
            print(f"agy-ask: only the boss {name} may give jobs (appointed in Habblaud); this call is not from it", file=sys.stderr)
            sys.exit(2)
        if acc == boss.get("account"):
            print(f"agy-ask: {acc} is the boss's own account", file=sys.stderr)
            sys.exit(2)
    if isinstance(workers, list) and acc not in workers:
        listed = ", ".join(workers) or "none"
        print(f"agy-ask: {acc} is not a registered worker (workers: {listed}; add it in Habblaud)", file=sys.stderr)
        sys.exit(2)


def print_team():
    t = team()
    if t is None:
        print("no team file: any caller may give jobs to any account")
        return
    boss = t.get("boss") if isinstance(t.get("boss"), dict) else None
    print(f"boss: {boss.get('name') or boss.get('id')} (pid {boss.get('pid')})" if boss else "boss: none (anyone may give jobs)")
    print(f"workers: {', '.join(t.get('workers') or []) or 'none'}")


def claude_pid():
    """PID of the Claude Code process above this call (the orchestrator that asked), or None."""
    pid = os.getppid()
    for _ in range(16):
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == "claude":
                    return pid
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if pid <= 1:
            return None
    return None


def flag(extra, name):
    """Value of an agy flag passed through (e.g. --model X), or ""."""
    return extra[extra.index(name) + 1] if name in extra[:-1] else ""


def log(entry):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    if sys.argv[1:] == ["team"]:
        return print_team()
    a = parse(sys.argv[1:])
    caller = claude_pid()
    check_team(a["acc"], caller)
    if not os.path.isdir(a["cwd"]):
        die(f"no such directory {a['cwd']}")
    cmd = ["agy-as", a["acc"], "-p", a["prompt"], "--output-format", "stream-json"]
    if a["mode"] != "default":
        cmd += ["--mode", a["mode"]]
    cmd += a["extra"]
    env = dict(os.environ, PATH=f"{HOME}/.local/bin:" + os.environ.get("PATH", ""))

    t0 = time.time()
    log({"event": "start", "ts": t0, "acc": a["acc"], "pid": os.getpid(), "mode": a["mode"], "role": a["role"],
         "model": flag(a["extra"], "--model"), "effort": flag(a["extra"], "--effort"), "claude_pid": caller,
         "cwd": a["cwd"], "prompt": a["prompt"][:200]})
    p = subprocess.Popen(cmd, cwd=a["cwd"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, start_new_session=True)
    st = {"cid": None, "result": None, "other": []}

    def cleanup(*_):
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if st["cid"]:
            conv = f"{HOME}/.agy_profiles/{a['acc']}/.gemini/antigravity-cli/conversations/{st['cid']}.db"
            for f in glob.glob(conv + "*"):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def on_signal(sig, _):  # caller gave up (Bash timeout, Ctrl-C): take the worker down with us
        cleanup()
        log({"event": "end", "ts": time.time(), "acc": a["acc"], "pid": os.getpid(), "cid": st["cid"], "status": "KILLED",
             "sec": round(time.time() - t0)})
        sys.exit(128 + sig)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    def reader():
        for ln in p.stdout:
            if ln.startswith('{"event":"init"'):
                st["cid"] = json.loads(ln).get("conversation_id")
            elif ln.startswith('{"event":"result"'):
                st["result"] = json.loads(ln).get("result", {})
                return
            elif not ln.startswith('{"event":'):
                st["other"].append(ln.rstrip())  # errors, quota messages

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    th.join(a["timeout"])
    cleanup()

    r = st["result"]
    status = (r or {}).get("status") or ("TIMEOUT" if th.is_alive() else "NO_RESULT")
    end = {"event": "end", "ts": time.time(), "acc": a["acc"], "pid": os.getpid(), "cid": st["cid"], "status": status,
           "sec": round(time.time() - t0), "turns": (r or {}).get("num_turns"), "usage": (r or {}).get("usage")}
    if status != "SUCCESS":  # last error lines (e.g. 429 "Individual quota reached"): Habblaud shows quota hits
        end["error"] = ("\n".join(st["other"][-5:]) or (r or {}).get("response") or "")[-300:]
    log(end)
    if status != "SUCCESS":
        print(f"agy-ask: agy {status} after {round(time.time() - t0)}s:", file=sys.stderr)
        print("\n".join(st["other"][-5:]) or (r or {}).get("response", ""), file=sys.stderr)
        sys.exit(1)
    out = r.get("response", "").rstrip("\n")
    print(out if len(out) <= a["max"] else f"{out[:a['max']]}\n[... cut at {a['max']} chars]")


if __name__ == "__main__":
    main()
```

## agy-up

```bash
#!/usr/bin/env bash
# agy-up <accN> [--cwd DIR] [agy args...] - open an herdr workspace labelled <accN> running
# `agy-as <accN> ...` (isolated account) and print its pane id. Never runs bare `agy`.
set -eu
export PATH="$HOME/.local/bin:$PATH"
acc="${1:?usage: agy-up <accN> [--cwd DIR] [agy args...]}"; shift
cwd="$PWD"
if [ "${1:-}" = "--cwd" ]; then cwd="$2"; shift 2; fi

herdr status server >/dev/null 2>&1 || true
if ! herdr status 2>/dev/null | grep -q 'status: running'; then
  (setsid herdr server >/dev/null 2>&1 &)
  for _ in $(seq 30); do herdr status 2>/dev/null | grep -q 'status: running' && break; sleep 0.2; done
fi

pane=$(herdr workspace create --cwd "$cwd" --label "$acc" |
       python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
herdr pane run "$pane" "agy-as $acc $*" >/dev/null
for _ in $(seq 60); do   # wait until herdr recognises the agent
  herdr agent list | python3 -c 'import sys,json; p="'"$pane"'"; sys.exit(0 if any(a["pane_id"]==p for a in json.load(sys.stdin)["result"]["agents"]) else 1)' && { echo "$pane"; exit 0; }
  sleep 0.5
done
echo "agy-up: $acc did not start in $pane (check: herdr pane read $pane)" >&2; exit 1
```

## agy-mode

```bash
#!/usr/bin/env bash
# agy-mode <agent> <default|accept-edits|auto|plan>
# Cycle an agy agent's mode with shift+tab until the status line shows the target.
# Cycle order: default -> accept-edits -> auto -> plan -> default. Only run on an idle agent.
set -eu
agent="${1:?usage: agy-mode <agent> <default|accept-edits|auto|plan>}"; want="${2:?mode}"
cur() {
  local line; line=$(herdr agent read "$agent" --source visible --lines 6 | grep -E '^\? for shortcuts' | tail -1)
  case "$line" in *accept-edits*) echo accept-edits;; *" auto "*) echo auto;; *" plan "*) echo plan;; *) echo default;; esac
}
for _ in 1 2 3 4 5; do
  [ "$(cur)" = "$want" ] && { echo "$agent: $want"; exit 0; }
  herdr agent send-keys "$agent" shift+tab >/dev/null; sleep 0.7
done
echo "agy-mode: could not reach $want (now $(cur))" >&2; exit 1
```

## agy-sync

```bash
#!/usr/bin/env bash
# agy-sync - name every running agy agent "<acc>-<role>" and set its default mode, from ~/.agy_profiles/roles.conf.
# Re-run after (re)starting an agent: names are cleared when an agent exits. Mode is only changed on idle agents.
set -eu
conf="$HOME/.agy_profiles/roles.conf"
herdr agent list | python3 -c '
import sys,json
for a in json.load(sys.stdin)["result"]["agents"]:
    if a["agent"]=="agy": print(a["pane_id"], a["agent_status"])' |
while read -r pane st; do
  pid=$(herdr pane process-info --pane "$pane" | python3 -c '
import sys,json
for p in json.load(sys.stdin)["result"]["process_info"]["foreground_processes"]:
    if p["name"]=="agy": print(p["pid"]); break')
  acc=$(tr '\0' '\n' </proc/$pid/environ | sed -n 's|^HOME=.*/\.agy_profiles/||p')
  read -r _ role mode < <(grep -E "^$acc[[:space:]]" "$conf" || echo "$acc ? default")
  name="$acc-$role"
  herdr agent rename "$pane" "$name" >/dev/null
  if [ "$st" = idle ] || [ "$st" = done ]; then agy-mode "$name" "$mode" >/dev/null && m=$mode || m="mode?"; else m="(busy, mode skipped)"; fi
  echo "$pane  $name  $m"
done
```

## roles.conf

Đặt tại `~/.agy_profiles/roles.conf`.

```
# acc  role      default-mode   (agent name becomes <acc>-<role>)
acc1 architect   plan
acc2 impl        accept-edits
acc3 qa          accept-edits
acc4 review      plan
```
