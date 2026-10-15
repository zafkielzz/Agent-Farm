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

```bash
#!/usr/bin/env bash
# agy-ask <accN> "<prompt>" [--mode plan|accept-edits|auto] [--cwd DIR] [--max N] [agy flags]
# One-shot worker call through agy-as (isolated account): prints ONLY the final answer, capped at
# N chars (default 3000). Cheapest way to delegate a self-contained job; no pane, no screen reads.
set -eu
export PATH="$HOME/.local/bin:$PATH"
acc="${1:?usage: agy-ask <accN> \"<prompt>\" [--mode M] [--cwd DIR] [--max N]}"; prompt="${2:?missing prompt}"; shift 2
mode=plan; max=3000; cwd="$PWD"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) mode="$2"; shift 2;;
    --max)  max="$2"; shift 2;;
    --cwd)  cwd="$2"; shift 2;;
    *) break;;
  esac
done
cd "$cwd"
out=$(agy-as "$acc" -p "$prompt" --mode "$mode" --output-format text "$@" 2>&1) || { echo "agy-ask: agy failed:" >&2; printf '%s\n' "$out" | tail -5 >&2; exit 1; }
if [ "${#out}" -gt "$max" ]; then printf '%s\n[... cut at %s chars]\n' "${out:0:$max}" "$max"; else printf '%s\n' "$out"; fi
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
