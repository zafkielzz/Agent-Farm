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

Viết bằng Python (cần `python3`): đọc `stream-json`, dừng ở sự kiện `result`, kill cả nhóm process và xoá file hội thoại của lần gọi; mỗi lần gọi ghi một dòng `start` (role, mode, prompt; trước khi agy chạy) và một dòng `end` (trạng thái, số giây, token) vào `~/.agy_profiles/agy-ask.log.jsonl`, cả hai kèm PID của agy-ask. `--role` là nhãn tự do cho việc (qa, review, impl...), Habblaud hiện nó làm tiêu đề của worker khi đang làm.

```python
#!/usr/bin/env python3
"""agy-ask <accN> "<prompt>" [--mode plan|accept-edits|auto|default] [--role NAME] [--cwd DIR] [--max N] [--timeout SEC] [agy flags]
One-shot worker call through agy-as (isolated account): prints ONLY the final answer, capped at N chars
(default 3000). Cheapest way to delegate a self-contained job; no pane, no screen reads.

agy -p often prints its answer and then never exits, so the call reads --output-format stream-json and stops at
the "result" event, kills the whole process group, and deletes that call's conversation files (a killed call leaves
a ~32 MB .db-wal behind). Each call appends a "start" line (role, mode, prompt) before agy starts and an
"end" line (status, seconds, token usage) to ~/.agy_profiles/agy-ask.log.jsonl, both with the agy-ask PID (the
parent of the agy process). --role is a free label for the job (e.g. qa, review, impl) that the office shows.
"""
import glob, json, os, signal, subprocess, sys, threading, time

HOME = os.path.expanduser("~")
LOG = f"{HOME}/.agy_profiles/agy-ask.log.jsonl"
USAGE = __doc__.splitlines()[0]


def die(msg):
    print(f"agy-ask: {msg}", file=sys.stderr)
    sys.exit(1)


def parse(argv):
    if len(argv) < 2:
        die(f"usage: {USAGE}")
    a = {"acc": argv[0], "prompt": argv[1], "mode": "plan", "role": "", "max": 3000, "cwd": os.getcwd(), "timeout": 900, "extra": []}
    rest = argv[2:]
    while rest:
        k = rest.pop(0)
        if k in ("--mode", "--role", "--max", "--cwd", "--timeout"):
            if not rest:
                die(f"{k} needs a value")
            v = rest.pop(0)
            a[k[2:]] = int(v) if k in ("--max", "--timeout") else v
        else:
            a["extra"].append(k)  # passed to agy as is (e.g. --model X, --effort low)
    return a


def log(entry):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    a = parse(sys.argv[1:])
    if not os.path.isdir(a["cwd"]):
        die(f"no such directory {a['cwd']}")
    cmd = ["agy-as", a["acc"], "-p", a["prompt"], "--output-format", "stream-json"]
    if a["mode"] != "default":
        cmd += ["--mode", a["mode"]]
    cmd += a["extra"]
    env = dict(os.environ, PATH=f"{HOME}/.local/bin:" + os.environ.get("PATH", ""))

    t0 = time.time()
    log({"event": "start", "ts": t0, "acc": a["acc"], "pid": os.getpid(), "mode": a["mode"], "role": a["role"],
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
    log({"event": "end", "ts": time.time(), "acc": a["acc"], "pid": os.getpid(), "cid": st["cid"], "status": status,
         "sec": round(time.time() - t0), "turns": (r or {}).get("num_turns"), "usage": (r or {}).get("usage")})
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
