#!/usr/bin/env bash
''':'
exec python3 "$0" "$@"
'''
# ==============================================================================
# Antigravity Orchestrator CLI - Điều Phối Multi-Agent Swarm (acc1 -> acc2..acc6)
# ==============================================================================

import argparse
import re
import json
import sys
import time
from urllib import request, error

DEFAULT_HUB_URL = "http://127.0.0.1:8888"

# What each agy mode means for the work you hand over (so the orchestrator does not ask a read-only worker to edit).
MODE_HINT = {
    "plan": "  (chỉ đọc/lập kế hoạch - sẽ KHÔNG tự sửa file)",
    "accept-edits": "  (tự sửa và lưu file)",
    "auto": "",
    "interactive": "  (hỏi duyệt từng bước)",
}

# Terminal UIs are full of rulers and spinner frames; they cost the orchestrator tokens and carry no information.
_DECOR_LINE = re.compile(r"^[\s─━═\-_=│┃┌┐└┘├┤┬┴┼╭╮╰╯▏▕⠀-⣿]*$")

def compact(text: str) -> str:
    """Drop decoration-only lines and collapse consecutive duplicates."""
    out = []
    for line in text.splitlines():
        if _DECOR_LINE.match(line):
            continue
        line = line.rstrip()
        if out and out[-1] == line:
            continue
        out.append(line)
    return "\n".join(out)

MIN_SETTLE = 2.0  # the hub calls a terminal "busy" while it printed something in the last 2s

def http_get(url: str):
    req = request.Request(url, headers={"User-Agent": "agy-orchestrator-cli"})
    try:
        with request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_msg)
            return {"error": err_json.get("detail", str(e))}
        except Exception:
            return {"error": err_msg or str(e)}
    except Exception as e:
        return {"error": str(e)}

def http_post(url: str, data: dict):
    payload = json.dumps(data).encode("utf-8")
    req = request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "agy-orchestrator-cli"})
    try:
        with request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        err_msg = e.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_msg)
            return {"error": err_json.get("detail", str(e))}
        except Exception:
            return {"error": err_msg or str(e)}
    except Exception as e:
        return {"error": str(e)}

def cmd_status(args):
    url = f"{args.hub}/api/orchestrator/status"
    res = http_get(url)
    if "error" in res:
        print(f"❌ Lỗi kết nối AGY Hub ({args.hub}): {res['error']}")
        sys.exit(1)
    print("\n================================================================================")
    print(f"👑 AGY FLOW ORCHESTRATOR STATUS ({args.hub})")
    print("================================================================================")
    print(f"👉 Orchestrator Profile:    {res.get('orchestrator_profile', 'acc1')}")
    print(f"👥 Tổng số Account Profile: {res.get('total_profiles', 6)}")
    print(f"⚡ Số Worker đang chạy:     {res.get('active_running_sessions', 0)} / {res.get('total_profiles', 6)}")
    print(f"🖥️ Tổng số Terminal Session: {res.get('active_total_sessions', 0)}")
    print("================================================================================\n")

def cmd_list(args):
    url = f"{args.hub}/api/orchestrator/workers"
    workers = http_get(url)
    if isinstance(workers, dict) and "error" in workers:
        print(f"❌ Lỗi: {workers['error']}")
        sys.exit(1)

    print("\n================================================================================")
    print(f"🤖 DANH SÁCH 6 AGENT PROFILES & TRẠNG THÁI HIỆN TẠI")
    print("================================================================================")
    for w in workers:
        role_icon = "👑 [ORCHESTRATOR]" if w.get("is_orchestrator") else "🛠️ [WORKER]"
        status = w.get("status", "idle")
        status_icon = "🟢 RUNNING" if status == "running" else ("🔴 STOPPED" if status == "stopped" else "⚪ IDLE")
        print(f"\n{role_icon} Profile: {w['profile_id']} ({w.get('name', '')})")
        print(f"   📧 Google Email:  {w.get('email', 'Chưa đăng nhập')}")
        if w.get("role_tag"):
            print(f"   🏷️ Ghi chú vai trò: {w['role_tag']}")
        if w.get("mode"):
            print(f"   🔧 Chế độ agy:    {w['mode']}{MODE_HINT.get(w['mode'], '')}")
        print(f"   📊 Trạng thái:    {status_icon} (Session: {w.get('session_id') or 'None'})")
        if w.get('cwd'):
            print(f"   📂 Workspace:     {w['cwd']}")
        
        last_out = (w.get("last_output") or "").strip()
        if last_out:
            lines = last_out.splitlines()[-4:]
            print("   📜 Terminal gần nhất:")
            for l in lines:
                print(f"      | {l}")
    print("\n================================================================================\n")

def _dispatch_only(args):
    url = f"{args.hub}/api/orchestrator/dispatch"
    payload = {
        "target_profile": args.target,
        "prompt": args.prompt,
        "mode": args.mode,
        "cwd": args.cwd,
        "auto_start": not args.no_auto_start,
        "force": getattr(args, "force", False),
        "session_id": getattr(args, "session", None),
        "clear_popups": not getattr(args, "no_clear", False)
    }
    print(f"🚀 Đang gửi lệnh đến {args.target}...")
    res = http_post(url, payload)
    if "error" in res:
        print(f"❌ Lỗi gửi lệnh: {res['error']}")
        sys.exit(1)

    results = res.get("results", [])
    sent_ok = False
    for r in results:
        status = r.get("status")
        if status == "sent":
            print(f"✅ Gửi thành công đến {r['profile_id']} (Session: {r.get('session_id')}, chế độ: {r.get('mode') or '?'}{MODE_HINT.get(r.get('mode'), '')})!")
            print(f"   📝 Prompt: \"{r.get('prompt')}\"")
            sent_ok = True
        else:
            print(f"❌ Thất bại trên {r['profile_id']}: {r.get('error', 'Unknown error')}")
    return sent_ok

def cmd_dispatch(args):
    ok = _dispatch_only(args)
    if getattr(args, "wait", False):
        if not ok:
            sys.exit(1)
        if args.target == "all":
            print("ℹ️ --wait chỉ dùng cho 1 worker; bỏ qua.")
            return
        cmd_wait(args)

def cmd_broadcast(args):
    args.target = "all"
    cmd_dispatch(args)

def cmd_read(args):
    url = f"{args.hub}/api/orchestrator/workers/{args.target}/output?lines={args.lines}&raw={str(args.raw).lower()}"
    if not args.watch:
        res = http_get(url)
        if "error" in res:
            print(f"❌ Lỗi: {res['error']}")
            sys.exit(1)
        print(f"\n--- [Terminal {args.target} • Trạng thái: {res.get('status')} • {res.get('lines', 0)} dòng] ---")
        print(res.get("output", "") if args.full else compact(res.get("output", "")))
        print("--------------------------------------------------------------------------------\n")
    else:
        print(f"👀 Đang theo dõi live terminal của {args.target} (Bấm Ctrl+C để dừng)...")
        last_text = ""
        try:
            while True:
                res = http_get(url)
                if "error" not in res:
                    new_text = res.get("output", "")
                    if new_text != last_text:
                        diff = new_text[len(last_text):] if new_text.startswith(last_text) else new_text
                        sys.stdout.write(diff)
                        sys.stdout.flush()
                        last_text = new_text
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nĐã dừng theo dõi.")

def _latest_dispatch(hub: str, target: str):
    """Most recent successfully sent dispatch to this profile (the hub log is newest-first)."""
    log = http_get(f"{hub}/api/orchestrator/log?limit=50")
    if isinstance(log, list):
        for e in log:
            if e.get("profile_id") == target and e.get("status") == "sent":
                return e
    return None

def cmd_wait(args):
    """Wait until the worker has gone quiet after its last dispatch.

    A working agent keeps redrawing its terminal (spinner, streamed text) and an idle one is silent, so "no new
    output for --settle seconds" is a far steadier signal than looking for a prompt character on screen.
    """
    entry = _latest_dispatch(args.hub, args.target)
    sid = entry.get("session_id") if entry else None
    url = (f"{args.hub}/api/sessions/{sid}/output?lines=25" if sid
           else f"{args.hub}/api/orchestrator/workers/{args.target}/output?lines=25")
    baseline = (entry.get("bytes_after_send") or entry.get("bytes_at_send")) if entry else None
    settle = max(args.settle, MIN_SETTLE)
    print(f"⏳ Đang đợi {args.target} xử lý xong (im lặng {settle:g}s thì coi là xong; tối đa {args.timeout}s)...")
    start = time.time()
    first_reaction = None
    reacted = baseline is None            # without a baseline we cannot tell, so do not insist on a reaction
    res = {}
    while time.time() - start < args.timeout:
        res = http_get(url)
        if "error" in res:
            time.sleep(1.0)
            continue
        if res.get("status") != "running":
            print(f"❌ {args.target} đã dừng.")
            sys.exit(1)
        if "idle_seconds" not in res:
            print("❌ Hub đang chạy là bản cũ (chưa có thông tin hoạt động của terminal). Hãy restart hub rồi thử lại.")
            sys.exit(3)
        if not reacted and res.get("bytes_total", 0) > baseline:
            reacted = True
            first_reaction = time.time() - start
        # No new output at all after the prompt was typed: an agent that got it starts drawing (spinner/thinking) within
        # seconds. Silence here usually means the prompt was swallowed (an overlay or popup had the focus).
        if not reacted and time.time() - start > args.start_timeout:
            print(f"⚠️ {args.target} KHÔNG phản ứng sau {args.start_timeout:.0f}s: prompt có thể chưa vào ô nhập "
                  f"(popup/overlay đang mở, hoặc worker đang chờ duyệt?).")
            print(f"   Thử: `read -t {args.target}` xem màn hình, `control -t {args.target} -a esc`, rồi gửi lại.")
            sys.exit(4)
        if reacted and res.get("idle_seconds", 0) >= settle:
            waited = time.time() - start
            idle = res.get("idle_seconds", 0)
            waiting = res.get("state") == "waiting"
            note = "  ⚠️ đang chờ bạn duyệt/xác nhận" if waiting else ""
            print(f"✅ {args.target} đã im lặng {idle:.1f}s. Trạng thái: {'waiting' if waiting else 'ready'}.{note}")
            # where the time went (measured by polling): useful to judge whether a delegation was worth it
            worked = max(waited - idle - (first_reaction or 0), 0)
            react = f"{first_reaction:.1f}s" if first_reaction is not None else "?"
            print(f"⏱ Từ lúc bắt đầu đợi: bắt đầu phản ứng sau {react} · agent làm việc ~{worked:.1f}s · chờ im lặng {idle:.1f}s · tổng {waited:.1f}s")
            print("\n--- [Màn hình hiện tại] ---")
            screen = res.get("output", "") if args.full else compact(res.get("output", ""))
            print("\n".join(screen.splitlines()[-args.tail:]))
            print("---------------------------\n")
            return
        time.sleep(0.25)
    print(f"⚠️ Hết {args.timeout}s mà {args.target} vẫn chưa im lặng (trạng thái: {res.get('state')}). Dùng `read` để xem.")
    sys.exit(2)

def cmd_control(args):
    url = f"{args.hub}/api/orchestrator/control/{args.target}"
    payload = {"target_profile": args.target, "action": args.action}
    res = http_post(url, payload)
    if "error" in res:
        print(f"❌ Lỗi: {res['error']}")
        sys.exit(1)
    print(f"✅ Đã gửi tín hiệu {args.action} đến {args.target}.")

def cmd_log(args):
    res = http_get(f"{args.hub}/api/orchestrator/log?limit={args.limit}")
    if isinstance(res, dict) and "error" in res:
        print(f"❌ Lỗi: {res['error']}")
        sys.exit(1)
    for e in res:
        when = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
        print(f"[{e['id']}] {when} {e['profile_id']} {e['status']:6} {e['title']}")

def cmd_collect(args):
    res = http_post(f"{args.hub}/api/orchestrator/log/{args.id}/collect", {})
    if "error" in res:
        print(f"❌ Lỗi: {res['error']}")
        sys.exit(1)
    print(f"--- [{res['profile_id']} • state={res.get('state_at_collect')}] ---")
    print(res.get("result", ""))

def main():
    parser = argparse.ArgumentParser(description="AGY Orchestrator Multi-Agent Swarm CLI")
    parser.add_argument("--hub", default=DEFAULT_HUB_URL, help=f"Địa chỉ AGY Hub (mặc định {DEFAULT_HUB_URL})")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # status
    p_status = subparsers.add_parser("status", help="Xem tổng quan hệ thống Orchestrator")
    p_status.set_defaults(func=cmd_status)

    # list / workers
    p_list = subparsers.add_parser("list", aliases=["workers"], help="Danh sách trạng thái 6 agents")
    p_list.set_defaults(func=cmd_list)

    # dispatch
    p_disp = subparsers.add_parser("dispatch", help="Gửi prompt/lệnh cho 1 agent cụ thể")
    p_disp.add_argument("--target", "-t", required=True, help="Profile nhận lệnh (vd: acc2, acc3...)")
    p_disp.add_argument("--prompt", "-p", required=True, help="Nội dung prompt hoặc lệnh gửi đi")
    p_disp.add_argument("--mode", "-m", default="accept-edits", choices=["accept-edits", "plan", "interactive"], help="Chế độ chạy agy (mặc định: accept-edits)")
    p_disp.add_argument("--cwd", default=None, help="Thư mục làm việc")
    p_disp.add_argument("--no-auto-start", action="store_true", help="Không tự khởi chạy session nếu đang tắt")
    p_disp.add_argument("--session", default=None, help="Chỉ định 1 terminal cụ thể (session id) khi profile có nhiều terminal")
    p_disp.add_argument("--no-clear", action="store_true", help="Không gửi 1 phím ESC (đóng popup/overlay) trước khi gõ prompt")
    p_disp.add_argument("--wait", action="store_true", help="Giao xong thì đợi worker im lặng rồi in màn hình (gộp dispatch + wait thành 1 lệnh)")
    p_disp.add_argument("--settle", type=float, default=3.0, help="(với --wait) số giây im lặng thì coi là xong (tối thiểu 2)")
    p_disp.add_argument("--timeout", type=int, default=300, help="(với --wait) thời gian chờ tối đa")
    p_disp.add_argument("--start-timeout", type=float, default=10.0, help="(với --wait) quá số giây này mà terminal không có output mới nào thì báo prompt có thể bị mất")
    p_disp.add_argument("--tail", type=int, default=12, help="(với --wait) số dòng cuối in ra")
    p_disp.add_argument("--full", action="store_true", help="(với --wait) giữ nguyên mọi dòng")
    p_disp.add_argument("--force", action="store_true", help="Gửi dù worker đang bận/chờ duyệt (có thể phá việc đang làm)")
    p_disp.set_defaults(func=cmd_dispatch)

    # broadcast
    p_bcast = subparsers.add_parser("broadcast", help="Gửi prompt cho TẤT CẢ worker agents cùng lúc")
    p_bcast.add_argument("--prompt", "-p", required=True, help="Nội dung prompt gửi đi")
    p_bcast.add_argument("--mode", "-m", default="accept-edits", choices=["accept-edits", "plan", "interactive"])
    p_bcast.add_argument("--cwd", default=None, help="Thư mục làm việc")
    p_bcast.add_argument("--no-auto-start", action="store_true")
    p_bcast.set_defaults(func=cmd_broadcast)

    # read
    p_read = subparsers.add_parser("read", help="Đọc kết quả trả về từ terminal của 1 agent")
    p_read.add_argument("--target", "-t", required=True, help="Profile muốn đọc (vd: acc2)")
    p_read.add_argument("--lines", "-n", type=int, default=30, help="Số dòng muốn đọc (mặc định 30)")
    p_read.add_argument("--raw", action="store_true", help="Giữ nguyên mã màu ANSI")
    p_read.add_argument("--full", action="store_true", help="Giữ nguyên mọi dòng (kể cả đường kẻ/spinner)")
    p_read.add_argument("--watch", "-w", action="store_true", help="Theo dõi live theo thời gian thực")
    p_read.set_defaults(func=cmd_read)

    # wait
    p_wait = subparsers.add_parser("wait", help="Chờ cho đến khi agent hoàn tất xử lý prompt")
    p_wait.add_argument("--target", "-t", required=True, help="Profile muốn đợi (vd: acc2)")
    p_wait.add_argument("--timeout", type=int, default=300, help="Thời gian chờ tối đa (giây)")
    p_wait.add_argument("--settle", type=float, default=3.0, help="Số giây terminal không có output mới thì coi là xong (mặc định 3, tối thiểu 2)")
    p_wait.add_argument("--start-timeout", type=float, default=10.0, help="Quá số giây này mà terminal không có output mới nào sau khi gửi thì báo prompt có thể bị mất")
    p_wait.add_argument("--tail", type=int, default=12, help="Số dòng cuối in ra khi xong")
    p_wait.add_argument("--full", action="store_true", help="Giữ nguyên mọi dòng (kể cả đường kẻ/spinner)")
    p_wait.set_defaults(func=cmd_wait)

    # interrupt / ctrl-c
    p_ctrl = subparsers.add_parser("control", help="Gửi tín hiệu điều khiển (interrupt, esc, enter, terminate)")
    p_ctrl.add_argument("--target", "-t", required=True, help="Profile mục tiêu (vd: acc2)")
    p_ctrl.add_argument("--action", "-a", required=True, choices=["interrupt", "esc", "enter", "clear", "terminate"], help="Hành động điều khiển")
    p_ctrl.set_defaults(func=cmd_control)

    p_log = subparsers.add_parser("log", help="Nhật ký các lệnh đã giao (mới nhất trước)")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.set_defaults(func=cmd_log)

    p_col = subparsers.add_parser("collect", help="Chụp terminal của worker làm kết quả cho 1 dispatch (id lấy từ `log`)")
    p_col.add_argument("--id", required=True)
    p_col.set_defaults(func=cmd_collect)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
