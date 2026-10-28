# Mã nguồn các script

Chép từng đoạn vào `~/.local/bin/<tên>` rồi `chmod +x`. Thứ tự dùng: `agy-as` (chạy agy trong profile cô lập) → `agy-mode` / `agy-sync` (mode, tên, role) → `agy-ask` / `claude-ask` / `agy-up` (giao việc). `agy-ask`/`claude-ask` là file thật trong `bin/`, không chép từ đây.

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
# An office's boss (AGY_BOSS=1, set by Habblaud): link the agy-boss plugin into the profile once; its SessionStart
# hook loads the boss rules only when AGY_BOSS=1, so the link is harmless when the account works as a worker later.
if [ "${AGY_BOSS:-}" = 1 ]; then
  plug="$home/.gemini/config/plugins/agy-boss"
  [ -e "$plug" ] || { mkdir -p "${plug%/*}" && ln -sfn "${AGY_HUB:-$HOME/Workspace/agy-hub}/agy-plugin/agy-boss" "$plug"; }
fi

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

## agy-ask và claude-ask

Một nguồn Python cho hai lệnh: [`bin/worker-ask`](../bin/worker-ask) (cần `python3`). Cài:

```bash
install -m 755 bin/worker-ask ~/.local/bin/agy-ask && install -m 644 bin/office_rules.py ~/.local/bin/ \
  && install -m 755 bin/rules-hook ~/.local/bin/ && ln -sf agy-ask ~/.local/bin/claude-ask
```

- `agy-ask accN "<prompt>"` giao việc cho worker Antigravity, `claude-ask <tài khoản> "<prompt>"` cho worker Claude Code
  (`.claude-work`, `claude:.claude-work` hoặc `work`; id worker = `claude:.claude-work`). In ra **chỉ câu trả lời cuối**
  (`--max`, mặc định 2000 ký tự). Tùy chọn: `--mode plan|accept-edits|auto|default`, `--effort` (agy: low/medium/high;
  Claude: low…max), `--model` (agy chỉ Gemini 3.8 Flash), `--role`, `--cwd`, `--timeout`.
- **Nội quy office** ([`bin/office_rules.py`](../bin/office_rules.py), bản Python của `shared/rules.ts` trong Habblaud;
  `tests/rules.vectors.json` là bản chép `shared/rules.vectors.json`, cả hai bộ test chạy cùng các ca, đổi bên nào thì
  chép lại): đọc `rules` của office trong `team.json` (không có = mặc định). Mỗi việc giao: chèn `rulesText(...,
  'workers')` lên đầu prompt (Board của Habblaud chỉ hiện phần việc); từ chối (mã 2, một dòng `RuleViolation` `by:"ask"`
  trong `~/.agy_profiles/rules-log.jsonl`) khi `--cwd` ngoài phạm vi ghi (`cwd`), office đã chạy đủ `limits.maxJobs`
  việc (`jobs`, đếm từ log: dòng start chưa có end, < 30 phút; việc chạy ngầm thì PID còn sống), worker dùng ≥
  `limits.quotaStop` % (`quota`; lấy từ snapshot của Habblaud, không có thì file usage của tài khoản Claude, không biết
  thì cho qua). Lệnh: `agy-ask rules [--office X] [--who boss|workers]`, `agy-ask rules check <path> [--write]` /
  `--cmd "..."` (mã 0 được, 2 bị chặn), `agy-ask rules propose "<luật>" --why "..." [--for workers|boss|all]` (gửi
  `POST /api/team {office, propose}`; văn phòng không chạy thì báo lỗi rõ).
- **`bin/rules-hook`**: hook PreToolUse. Worker Claude của `claude-ask` (chạy ngầm, và việc qua văn phòng có office)
  nhận nó qua `--settings` + `AGY_OFFICE`/`AGY_WORKER`; boss Claude nhận qua mod `habblaud` (Habblaud, `rules-hook
  boss` + kiểm từng tool call). Edit/Write/MultiEdit/NotebookEdit → `checkPath(write)`, Read/Grep/Glob → `read`, Bash →
  `checkCommand`; không ai được sửa `team.json`/`rules-log.jsonl` (cả lệnh Bash nhắc tên và ghi, cố gắng tối đa). Chặn =
  JSON deny theo tài liệu + một dòng log `by:"hook"`; lỗi bất kỳ = cho qua (stderr). ~20 ms. Cũng hiểu sự kiện
  PreToolUse của agy (`{"toolCall": {name, args}}` → `{"decision": "deny", "reason"}`); plugin
  [`agy-plugin/office-rules`](../agy-plugin/office-rules) **chưa cài** vào hồ sơ agy (cần người dùng đồng ý, như
  agy-boss): `ln -s ~/Workspace/agy-hub/agy-plugin/office-rules ~/.agy_profiles/accN/.gemini/config/plugins/`.
  Đây là rào chắn chống nhầm lẫn, không phải sandbox (cùng user Unix).
- **Worker Claude khởi động gọn** (mặc định của `claude-ask`; `--full` để giữ nguyên cấu hình tài khoản): chạy ngầm thêm
  `--strict-mcp-config --disable-slash-commands --tools Bash,Read,Edit,Write,WebFetch,WebSearch --append-system-prompt
  "<luật worker: chỉ trả kết quả, gọn, dưới --max ký tự>"`; qua văn phòng body gửi `lean: true` và server thêm đúng các
  cờ đó (`leanClaudeArgs` trong `server/workers/service.ts`). Đo 28/10 (Claude Code 2.1.312, Opus 5.5): ngữ cảnh lúc
  khởi động chạy ngầm 23,7k → 7,9k token (Workflow ~8,2k, Agent ~2,3k, MCP Claude Docs ~1,7k, skill ~0,8k); việc thật
  "tìm định nghĩa" chạy ngầm 47k → 17k token, qua văn phòng (terminal tương tác) 82k → 20k. Giao cho chính tài khoản
  của mình thì CLI in ghi chú: tiết kiệm ngữ cảnh, không tiết kiệm quota. Log chạy ngầm có thêm
  `cache_creation_tokens` và `cost_usd`.
- **Qua văn phòng** (Habblaud ở `$HABBLAUD_URL`, mặc định `http://127.0.0.1:4747`): `POST /api/workers/ask` đưa việc vào
  terminal thường trực của worker (mỗi việc một hội thoại mới), rồi chờ `GET /api/workers/jobs/<id>?wait=60` (người
  gọi không tốn token khi chờ). SIGTERM/Ctrl-C khi đang chờ → `POST /api/workers/cancel {job}`; hết `--timeout` cũng hủy.
- **Chạy ngầm** khi văn phòng không chạy hoặc trả 404/503 (bản Habblaud cũ): agy `-p --output-format stream-json` (dừng ở
  sự kiện `result`, kill cả nhóm process, xóa file hội thoại) hoặc `claude -p --output-format json
  --no-session-persistence` (bỏ biến môi trường của phiên Claude đang gọi). Mỗi lần ghi dòng `start`/`end` (`via:
  "headless"`, `worker`, `acc` = id worker) vào `~/.agy_profiles/agy-ask.log.jsonl`; việc qua văn phòng thì server ghi.
- **Luật đội** (`~/.agy_profiles/team.json` v3, chỉnh trong Habblaud; không có file = không có luật): file gồm các office,
  mỗi office một đội cho một thư mục dự án (`{"offices": [{id, name, folder, boss, workers}]}`; file v2 `{boss, workers}`
  đọc thành một office `main`). Boss của office (boss Claude: PID là tổ tiên của lời gọi; boss agy: có tổ tiên `agy` với
  `HOME=~/.agy_profiles/<acc của boss>`) chỉ giao cho worker của office đó, không cho chính tài khoản của mình; body gửi
  `office` (id). Không phải boss office nào: bị chặn nếu có office có boss; không office nào có boss thì giao được cho
  worker thuộc ít nhất một office. Việc giao từ văn phòng (`AGY_ASK_FROM_OFFICE=1` + `AGY_OFFICE=<id>`, gọi tay cũng dùng
  được) không bị chặn. `agy-ask team` in từng office (thư mục, boss, worker) và đánh dấu office mà người gọi làm boss.
- **Boss agy** mở từ Habblaud chạy trong thư mục office với `AGY_BOSS=1`, `AGY_OFFICE=<id>`: `agy-as` liên kết plugin
  [`agy-plugin/agy-boss`](../agy-plugin/agy-boss) vào hồ sơ (`.gemini/config/plugins/agy-boss`), hook `SessionStart` của
  nó chèn `boss/AGENTS.md` + tên/thư mục office làm system message (không có `AGY_BOSS=1` thì không chèn gì). Worker chạy
  ngầm từ shell của boss không nhận hai biến này.
- **Mã thoát:** 0 xong; 1 lỗi/hết giờ; 2 luật đội chặn; 3 BUSY/PAUSED (người dùng đang cầm lái worker đó, hoặc nó đang
  làm việc khác: `agy-ask --wait-release <worker>` rồi gọi lại, hoặc chọn worker khác); 4 việc bị hủy từ văn phòng.
- `agy-ask --wait-release <worker> [--timeout S]`: chờ tới khi người dùng trả lái (không có văn phòng thì về ngay).
- Không có `--mode`/`--effort` thì dùng mặc định của worker trong `~/.agy_profiles/defaults.json` (chỉnh trong
  Habblaud), không có nữa thì agy `plan`, Claude `default`.

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
