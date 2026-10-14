import asyncio
import os
import sys
import pty
import fcntl
import termios
import struct
import signal
import json
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import re
import shutil
import subprocess

BASE_DIR = Path(__file__).resolve().parent
# AGY_DATA_DIR / AGY_PROFILES_DIR let you run a second, throw-away hub (tests, demos) without touching the real one.
DATA_DIR = Path(os.environ.get("AGY_DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "static"
PROFILES_DIR = Path(os.environ.get("AGY_PROFILES_DIR", Path.home() / ".agy_profiles"))
SCRIPTS_DIR = BASE_DIR / "scripts"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

TASKS_FILE = DATA_DIR / "tasks.json"
WORKSPACES_FILE = DATA_DIR / "workspaces.json"
PROFILES_METADATA_FILE = DATA_DIR / "profiles.json"
ORCHESTRATOR_CONFIG_FILE = DATA_DIR / "orchestrator.json"

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def load_orchestrator_config() -> Dict[str, Any]:
    if not ORCHESTRATOR_CONFIG_FILE.exists():
        default_cfg = {"orchestrator_profile": "acc1"}
        save_orchestrator_config(default_cfg)
        return default_cfg
    try:
        with open(ORCHESTRATOR_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"orchestrator_profile": "acc1"}

def save_orchestrator_config(data: Dict[str, Any]):
    with open(ORCHESTRATOR_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

_EMAIL_CACHE: Dict[str, str] = {}

def detect_profile_email(acc: str) -> Optional[str]:
    log_dir = PROFILES_DIR / acc / ".gemini" / "antigravity-cli" / "log"
    if not log_dir.exists():
        return None
    try:
        # Sort by mtime descending (most recently created log file first!)
        log_files = sorted(log_dir.glob("cli-*.log"), key=os.path.getmtime, reverse=True)
        for log_file in log_files[:20]:
            try:
                with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(500):
                        line = f.readline()
                        if not line:
                            break
                        if "applyAuthResult:" in line and "email=" in line:
                            m = re.search(r"email=([^\s,]+)", line)
                            if m:
                                return m.group(1)
            except Exception:
                pass
    except Exception:
        pass
    return None

def load_profiles_metadata() -> Dict[str, Dict[str, Any]]:
    if not PROFILES_METADATA_FILE.exists():
        return {}
    try:
        with open(PROFILES_METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_profiles_metadata(data: Dict[str, Dict[str, Any]]):
    with open(PROFILES_METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

app = FastAPI(title="Agent Farm", version="1.0.0")

# The hub can run arbitrary commands on this machine, so it must only answer the page it serves itself.
# (No wildcard CORS: any website you visit could otherwise call http://localhost:8888 from your browser.)
BIND_HOST = os.environ.get("AGY_BIND_HOST", "127.0.0.1")
_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}

def _hostname(netloc: str) -> str:
    netloc = netloc.strip().lower()
    if netloc.startswith("["):                      # [::1]:8888
        return netloc[1:netloc.find("]")]
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc

def request_rejection(headers) -> Optional[str]:
    """Why a browser request must be refused, or None. Non-browser clients (curl, the orchestrator CLI) send no Origin."""
    host = headers.get("host", "")
    if BIND_HOST in _LOOPBACK_NAMES and _hostname(host) not in _LOOPBACK_NAMES:
        return "Host không hợp lệ (chống DNS rebinding)"
    origin = headers.get("origin")
    if origin and origin != "null":
        origin_netloc = origin.split("://", 1)[-1].lower()
        if origin_netloc != host.strip().lower():
            return "Origin khác nguồn của hub (cross-origin bị chặn)"
    elif origin == "null":
        return "Origin null bị chặn"
    return None

@app.middleware("http")
async def _same_origin_only(request, call_next):
    reason = request_rejection(request.headers)
    if reason:
        return JSONResponse(status_code=403, content={"detail": reason})
    return await call_next(request)

@app.middleware("http")
async def _no_stale_ui(request, call_next):
    """The UI is a single file we edit often; make browsers revalidate it instead of reusing a stale copy."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# -----------------------------------------------------------------------------
# Data Models & Persistence
# -----------------------------------------------------------------------------
class TaskModel(BaseModel):
    id: Optional[str] = None
    title: str
    description: Optional[str] = ""
    stage: str = "backlog"  # backlog, planning, implementing, review, done
    assigned_profile: Optional[str] = "acc1"
    workspace_dir: Optional[str] = str(Path.home())
    mode: Optional[str] = "plan"  # plan, accept-edits, default, custom
    custom_command: Optional[str] = ""
    created_at: Optional[float] = None
    updated_at: Optional[float] = None

class CreateSessionRequest(BaseModel):
    name: str
    profile_id: str  # e.g. "acc1", "acc2", "system"
    cwd: Optional[str] = None
    command: Optional[str] = None
    task_id: Optional[str] = None
    rows: Optional[int] = 28
    cols: Optional[int] = 80

class CreateProfileRequest(BaseModel):
    profile_id: str
    name: Optional[str] = None
    description: Optional[str] = ""

class CreateWorktreeRequest(BaseModel):
    repo_path: str
    branch_name: str
    target_path: str
    base_branch: Optional[str] = "main"

class DispatchPromptRequest(BaseModel):
    target_profile: str  # "acc2", "acc3", or "all"
    prompt: str
    cwd: Optional[str] = None
    mode: Optional[str] = "accept-edits"  # "plan", "accept-edits", "interactive"
    auto_start: Optional[bool] = True
    task_id: Optional[str] = None
    title: Optional[str] = None
    force: Optional[bool] = False
    session_id: Optional[str] = None  # target one specific terminal when a profile has several

class PlanItem(BaseModel):
    profile_id: str
    prompt: str
    title: Optional[str] = None
    mode: Optional[str] = "accept-edits"
    cwd: Optional[str] = None

class RunPlanRequest(BaseModel):
    goal: Optional[str] = ""
    items: List[PlanItem]

class OrchestratorControlRequest(BaseModel):
    target_profile: str
    action: str  # "interrupt", "esc", "enter", "clear", "terminate"

class OrchestratorSettingsRequest(BaseModel):
    orchestrator_profile: str

def load_tasks() -> List[Dict[str, Any]]:
    if not TASKS_FILE.exists():
        # Default starter workflow tasks
        initial_tasks = [
            {
                "id": str(uuid.uuid4())[:8],
                "title": "Setup Architecture & Task Decomposition",
                "description": "Use Account 1 in Planning Mode to outline high-level specs and API contracts.",
                "stage": "planning",
                "assigned_profile": "acc1",
                "workspace_dir": str(Path.home()),
                "mode": "plan",
                "created_at": time.time(),
                "updated_at": time.time(),
            },
            {
                "id": str(uuid.uuid4())[:8],
                "title": "Implement Feature Core & Services",
                "description": "Use Account 2 in Accept-Edits Mode to build the core logic.",
                "stage": "implementing",
                "assigned_profile": "acc2",
                "workspace_dir": str(Path.home()),
                "mode": "accept-edits",
                "created_at": time.time(),
                "updated_at": time.time(),
            },
            {
                "id": str(uuid.uuid4())[:8],
                "title": "Write Unit Tests & Run Verifications",
                "description": "Use Account 3 or 4 to run test suites and verify the result.",
                "stage": "review",
                "assigned_profile": "acc3",
                "workspace_dir": str(Path.home()),
                "mode": "default",
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        ]
        save_tasks(initial_tasks)
        return initial_tasks
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_tasks(tasks: List[Dict[str, Any]]):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)

def load_workspaces() -> List[str]:
    default_ws = [str(Path.home()), str(Path.home() / "Workspace")]
    if not WORKSPACES_FILE.exists():
        save_workspaces(default_ws)
        return default_ws
    try:
        with open(WORKSPACES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_ws

def save_workspaces(ws: List[str]):
    with open(WORKSPACES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(dict.fromkeys(ws)), f, indent=2, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Terminal / PTY Session Manager
# -----------------------------------------------------------------------------

# Variables that point at the desktop user's keyring/D-Bus/XDG dirs. If they leak into
# an isolated session, agy or gnome-keyring can reach the real (shared) credential store.
NON_ISOLATED_PROFILES = ("system", "default", "claude")
CLAUDE_PROFILE = "claude"
DISPATCH_LOG_FILE = DATA_DIR / "dispatch_log.json"
DISPATCH_LOG_MAX = 300

LEAKY_ENV_VARS = (
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
    "DBUS_SESSION_BUS_ADDRESS", "DBUS_STARTER_ADDRESS", "DBUS_STARTER_BUS_TYPE",
    "GNOME_KEYRING_CONTROL", "GNOME_KEYRING_PID", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
    "GPG_AGENT_INFO", "KEYRING_SECRET", "SESSION_MANAGER",
)

def build_session_env(profile_id: Optional[str]) -> Dict[str, str]:
    """Environment for a PTY session. With a profile id, credentials are fully confined to
    ~/.agy_profiles/<profile_id>; otherwise the user's normal environment is used."""
    env = os.environ.copy()
    if profile_id:
        for k in LEAKY_ENV_VARS:
            env.pop(k, None)
        prof_home = PROFILES_DIR / profile_id
        env["HOME"] = str(prof_home)
        env["XDG_CONFIG_HOME"] = str(prof_home / ".config")
        env["XDG_DATA_HOME"] = str(prof_home / ".local" / "share")
        env["XDG_CACHE_HOME"] = str(prof_home / ".cache")
        env["XDG_STATE_HOME"] = str(prof_home / ".local" / "state")
        # Keep the real binaries reachable now that ~ no longer is the user's home.
        env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    else:
        env["HOME"] = str(Path.home())
    # A hub started from inside another Claude Code session would pass its child-session markers on and
    # make the embedded Claude behave as a nested session (e.g. transcript saving off).
    for k in [k for k in env if k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE"]:
        env.pop(k, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["LANG"] = "en_US.UTF-8"
    return env


# -----------------------------------------------------------------------------
# Per-profile runtime: ONE private D-Bus session + unlocked keyring daemon shared by every
# terminal of that profile. Several agents on the same Google account therefore talk to a
# single keyring instead of racing on the same keyring file from separate daemons.
# -----------------------------------------------------------------------------
DBUS_DAEMON = "/usr/bin/dbus-daemon" if os.path.exists("/usr/bin/dbus-daemon") else (shutil.which("dbus-daemon") or "dbus-daemon")

def _pid_matches(pid: int, needle: str) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return needle in f.read().decode("utf-8", errors="ignore")
    except Exception:
        return False

class ProfileRuntime:
    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.home = PROFILES_DIR / profile_id
        self.run_dir = self.home / ".run"
        self.sock = self.run_dir / "bus"
        self.state_file = self.run_dir / "runtime.json"
        self.address = f"unix:path={self.sock}"
        self.bus: Optional[subprocess.Popen] = None
        self.keyring: Optional[subprocess.Popen] = None
        self.bus_pid: Optional[int] = None
        self.keyring_pid: Optional[int] = None

    def _env(self) -> Dict[str, str]:
        env = build_session_env(self.profile_id)
        env["DBUS_SESSION_BUS_ADDRESS"] = self.address
        return env

    def _load_state(self):
        try:
            st = json.loads(self.state_file.read_text())
            self.bus_pid, self.keyring_pid = st.get("bus_pid"), st.get("keyring_pid")
        except Exception:
            pass

    def alive(self) -> bool:
        return bool(self.bus_pid and self.keyring_pid and self.sock.exists()
                    and _pid_matches(self.bus_pid, "dbus-daemon") and _pid_matches(self.keyring_pid, "gnome-keyring-daemon"))

    def adopt(self) -> bool:
        """Re-use daemons left running by a previous hub (they outlive the hub so persistent tmux terminals keep their keyring)."""
        self._load_state()
        return self.alive() and self._secrets_on_bus()

    def _secrets_on_bus(self) -> bool:
        try:
            out = subprocess.run(["busctl", f"--address={self.address}", "--no-pager", "list"],
                                 capture_output=True, text=True, timeout=3, env=self._env()).stdout
            return "org.freedesktop.secrets" in out
        except FileNotFoundError:
            time.sleep(1.5)  # no busctl: give the daemon time to register instead of probing
            return True
        except Exception:
            return False

    def start(self):
        self.stop()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        (self.home / ".local" / "share" / "keyrings").mkdir(parents=True, exist_ok=True)
        try:
            self.sock.unlink()
        except FileNotFoundError:
            pass
        env = self._env()
        self.bus = subprocess.Popen([DBUS_DAEMON, "--session", f"--address={self.address}", "--nofork", "--nopidfile"],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.bus_pid = self.bus.pid
        for _ in range(50):
            if self.sock.exists():
                break
            time.sleep(0.1)
        else:
            self.stop()
            raise RuntimeError(f"dbus-daemon của {self.profile_id} không khởi động được")
        self.keyring = subprocess.Popen(["gnome-keyring-daemon", "--unlock", "--foreground", "--components=secrets"],
                                        env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)
        self.keyring_pid = self.keyring.pid
        try:
            self.keyring.stdin.write(b"\n")  # empty password, same as the previous per-session wrapper
            self.keyring.stdin.close()
        except Exception:
            pass
        for _ in range(25):
            if self.keyring.poll() is not None:
                self.stop()
                raise RuntimeError(f"gnome-keyring-daemon của {self.profile_id} đã thoát ngay khi khởi động")
            if self._secrets_on_bus():
                break
            time.sleep(0.2)
        self.state_file.write_text(json.dumps({"bus_pid": self.bus_pid, "keyring_pid": self.keyring_pid, "address": self.address}))

    def stop(self):
        """Stop this profile's daemons - ours, or ones recorded by a previous hub run."""
        if not (self.bus_pid or self.keyring_pid):
            self._load_state()
        for proc, pid, needle in ((self.keyring, self.keyring_pid, "gnome-keyring-daemon"), (self.bus, self.bus_pid, "dbus-daemon")):
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=3)
                elif pid and _pid_matches(pid, needle):
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                try:
                    if proc:
                        proc.kill()
                except Exception:
                    pass
        self.keyring = self.bus = None
        self.keyring_pid = self.bus_pid = None
        for f in (self.sock, self.state_file):
            try:
                f.unlink()
            except Exception:
                pass

class RuntimeManager:
    def __init__(self):
        self.runtimes: Dict[str, ProfileRuntime] = {}

    def ensure(self, profile_id: str) -> str:
        rt = self.runtimes.get(profile_id)
        if not rt or not rt.alive():
            rt = ProfileRuntime(profile_id)
            if not rt.adopt():
                rt.start()
            self.runtimes[profile_id] = rt
        return rt.address

    def stop(self, profile_id: str):
        rt = self.runtimes.pop(profile_id, None) or ProfileRuntime(profile_id)
        rt.stop()

    def stop_all(self, keep: Optional[Set[str]] = None):
        for pid in list(self.runtimes):
            if not keep or pid not in keep:
                self.stop(pid)

runtime_manager = RuntimeManager()

# -----------------------------------------------------------------------------
# tmux: every terminal runs inside a tmux session on a private socket, and the PTY the browser talks
# to is just a tmux *client*. Restarting the hub only kills the clients - the agents keep running and
# the new hub re-attaches to them.
# -----------------------------------------------------------------------------
LOCAL_TMUX = Path.home() / ".local" / "share" / "agy-hub" / "tmux" / "bin" / "tmux"
TMUX_BIN = shutil.which("tmux") or (str(LOCAL_TMUX) if LOCAL_TMUX.exists() else None)
HUB_PORT = int(os.environ.get("AGY_PORT", "8888"))
# One tmux server per hub port, so two hubs never adopt each other's terminals. Port 8888 keeps the original name.
TMUX_SOCKET = "agyhub" if HUB_PORT == 8888 else f"agyhub-{HUB_PORT}"
TMUX_PREFIX = "agy-"
SESSIONS_FILE = DATA_DIR / ("sessions.json" if HUB_PORT == 8888 else f"sessions-{HUB_PORT}.json")
TMUX_SERVER_OPTIONS = [
    ["set-option", "-g", "status", "off"],
    ["set-option", "-g", "prefix", "None"],           # no tmux key bindings: every key goes to the agent
    ["set-option", "-g", "prefix2", "None"],
    ["set-option", "-sg", "escape-time", "0"],
    ["set-option", "-g", "history-limit", "50000"],
    ["set-option", "-g", "default-terminal", "xterm-256color"],
    ["set-option", "-ga", "terminal-overrides", ",xterm*:smcup@:rmcup@"],  # keep xterm.js scrollback usable
    ["set-option", "-ga", "terminal-features", ",xterm*:RGB"],
    ["set-option", "-g", "mouse", "off"],
    ["set-option", "-g", "focus-events", "on"],
    ["set-option", "-g", "remain-on-exit", "off"],
]

def _tmux_cmd(*args: str) -> List[str]:
    return [TMUX_BIN, "-L", TMUX_SOCKET, "-f", "/dev/null", *args]

def tmux_has_session(name: str) -> bool:
    try:
        return subprocess.run(_tmux_cmd("has-session", "-t", name), capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False

def tmux_list_sessions() -> List[str]:
    try:
        r = subprocess.run(_tmux_cmd("list-sessions", "-F", "#{session_name}"), capture_output=True, text=True, timeout=5)
        return [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(TMUX_PREFIX)] if r.returncode == 0 else []
    except Exception:
        return []

def tmux_kill_session(name: str):
    try:
        subprocess.run(_tmux_cmd("kill-session", "-t", name), capture_output=True, timeout=5)
    except Exception:
        pass

def tmux_new_session(name: str, cwd: str, rows: int, cols: int, env: Dict[str, str], shell_cmd: str):
    argv = _tmux_cmd("start-server")
    for opt in TMUX_SERVER_OPTIONS:
        argv += [";"] + opt
    # `env -i` so the pane gets exactly the isolated environment we built, not the tmux server's own.
    argv += [";", "new-session", "-d", "-s", name, "-x", str(cols), "-y", str(rows), "-c", cwd, "--",
             "env", "-i", *[f"{k}={v}" for k, v in env.items()], "/bin/bash", "-l", "-c", shell_cmd]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "tmux new-session thất bại")

def tmux_client_env() -> Dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()), "TERM": "xterm-256color",
            "COLORTERM": "truecolor", "LANG": "en_US.UTF-8"}

def _load_sessions_meta() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except Exception:
        return {}

def save_session_meta(sess: "PTYSession"):
    meta = _load_sessions_meta()
    meta[sess.session_id] = {"name": sess.name, "profile_id": sess.profile_id, "cwd": sess.cwd, "command": sess.command,
                             "task_id": sess.task_id, "created_at": sess.created_at, "rows": sess.rows, "cols": sess.cols}
    SESSIONS_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

def forget_session_meta(session_id: str):
    meta = _load_sessions_meta()
    if meta.pop(session_id, None) is not None:
        SESSIONS_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

class PTYSession:
    def __init__(self, session_id: str, name: str, profile_id: str, cwd: str, command: str, task_id: Optional[str] = None, rows: int = 28, cols: int = 80,
                 adopt: bool = False, created_at: Optional[float] = None):
        self.session_id = session_id
        self.adopt = adopt                       # re-attach to a tmux session left by a previous hub run
        self.tmux_name = f"{TMUX_PREFIX}{session_id}"
        self.persistent = False                  # True when running inside tmux
        self._terminating = False
        self._respawns = 0
        self.name = name
        self.profile_id = profile_id
        self.cwd = cwd
        self.command = command
        self.task_id = task_id
        self.rows = max(10, rows)
        self.cols = max(40, cols)
        self.created_at = created_at or time.time()
        self.status = "running"
        self.exit_code: Optional[int] = None
        
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self.buffer = deque(maxlen=300_000)  # ~300KB history buffer
        self.websockets: Set[WebSocket] = set()
        self.loop = asyncio.get_running_loop()

    def _spawn(self, argv: List[str], child_env: Dict[str, str], cwd: str):
        """Open a PTY and exec argv on it (the agent itself, or a tmux client attached to it)."""
        master, slave = pty.openpty()
        self.master_fd = master
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        # Initial terminal size matches the actual client pane
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

        pid = os.fork()
        if pid == 0:
            os.close(master)
            os.setsid()
            try:
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
            except Exception:
                pass
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            os.close(slave)
            try:
                os.chdir(cwd)
            except Exception:
                pass
            os.execvpe(argv[0], argv, child_env)
            sys.exit(1)

        os.close(slave)
        self.pid = pid
        self.loop.add_reader(self.master_fd, self._on_master_read)

    def start(self):
        target_cwd = self.cwd if (self.cwd and os.path.isdir(self.cwd)) else str(Path.home())
        argv = ["/bin/bash", "-l", "-c", self.command or "agy"]
        child_env: Dict[str, str] = {}
        shell_cmd = self.command or "agy"

        if not self.adopt:
            is_isolated_profile = bool(self.profile_id and self.profile_id not in NON_ISOLATED_PROFILES)
            env = build_session_env(self.profile_id if is_isolated_profile else None)
            if is_isolated_profile:
                (PROFILES_DIR / self.profile_id / ".local" / "share" / "keyrings").mkdir(parents=True, exist_ok=True)
                env["AGY_ISOLATED_DBUS"] = self.profile_id
                try:
                    env["DBUS_SESSION_BUS_ADDRESS"] = runtime_manager.ensure(self.profile_id)
                except Exception as e:  # fall back to a private per-terminal session (old behaviour)
                    print(f"[runtime] {self.profile_id}: dùng dbus-run-session riêng vì lỗi: {e}")
            # If an isolated profile has no shared bus and the command has no dbus-run-session, wrap it in a private one
            if is_isolated_profile and "DBUS_SESSION_BUS_ADDRESS" not in env and "dbus-run-session" not in shell_cmd:
                shell_cmd = f'dbus-run-session -- bash -c "eval \\$(echo \'\' | gnome-keyring-daemon --unlock --components=secrets 2>/dev/null); {shell_cmd}"'
            argv = ["/bin/bash", "-l", "-c", shell_cmd]
            child_env = env

        if TMUX_BIN:
            try:
                if not self.adopt:
                    tmux_new_session(self.tmux_name, target_cwd, self.rows, self.cols, child_env, shell_cmd)
                self.persistent = True
                # tmux probes the terminal on attach; with echo on, the replies would be printed as stray "^[[?1;2c" text.
                argv = ["/bin/sh", "-c", 'stty -echo 2>/dev/null; exec "$@"', "sh", *_tmux_cmd("attach-session", "-t", self.tmux_name)]
                child_env = tmux_client_env()
            except Exception as e:
                if self.adopt:
                    raise
                print(f"[tmux] {self.session_id}: chạy không có tmux vì lỗi: {e}")
        self._client = (argv, child_env, target_cwd)
        self._spawn(argv, child_env, target_cwd)

    def _on_master_read(self):
        try:
            chunk = os.read(self.master_fd, 8192)
            if not chunk:
                self._handle_exit()
                return
            self.buffer.extend(chunk)
            # Broadcast to connected WS
            dead_ws = set()
            for ws in list(self.websockets):
                try:
                    asyncio.create_task(ws.send_bytes(chunk))
                except Exception:
                    dead_ws.add(ws)
            self.websockets.difference_update(dead_ws)
        except BlockingIOError:
            pass
        except OSError:
            # On Linux PTY, closing slave raises OSError (EIO). That means process has exited!
            self._handle_exit()

    def _respawn_client(self) -> bool:
        """The PTY client died but the tmux session lives (e.g. the client was killed): attach a fresh client."""
        if not self.persistent or self._terminating or self._respawns >= 3:
            return False
        time.sleep(0.2)  # let tmux finish removing a session whose command just ended
        if not tmux_has_session(self.tmux_name):
            return False
        self._respawns += 1
        try:
            if self.master_fd is not None:
                self.loop.remove_reader(self.master_fd)
                os.close(self.master_fd)
            if self.pid is not None:
                try:
                    os.waitpid(self.pid, os.WNOHANG)
                except Exception:
                    pass
            self._spawn(*self._client)
            return True
        except Exception:
            return False

    def _handle_exit(self):
        if self.status != "stopped" and self._respawn_client():
            return
        if self.status != "stopped":
            self.status = "stopped"
            # Clear email cache so newly authenticated emails are picked up immediately
            _EMAIL_CACHE.pop(self.profile_id, None)
            if self.persistent:
                tmux_kill_session(self.tmux_name)
            forget_session_meta(self.session_id)
            if self.master_fd is not None:
                try:
                    self.loop.remove_reader(self.master_fd)
                    os.close(self.master_fd)
                except Exception:
                    pass
                self.master_fd = None
            if self.pid is not None:
                try:
                    _, status = os.waitpid(self.pid, os.WNOHANG)
                    self.exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else (status >> 8)
                except Exception:
                    self.exit_code = 0
            # Notify clients
            exit_msg = f"\r\n\x1b[33m[Session '{self.name}' exited with code {self.exit_code}]\x1b[0m\r\n".encode("utf-8")
            self.buffer.extend(exit_msg)
            for ws in list(self.websockets):
                try:
                    asyncio.create_task(ws.send_bytes(exit_msg))
                except Exception:
                    pass

    def write_input(self, data: bytes):
        if self.master_fd is not None and self.status == "running":
            try:
                os.write(self.master_fd, data)
            except Exception:
                pass

    def resize(self, rows: int, cols: int):
        if self.master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    def terminate(self):
        self._terminating = True
        if self.persistent:
            tmux_kill_session(self.tmux_name)
        self.status = "stopped"
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                pass
        self._handle_exit()

    def get_clean_output(self, lines: int = 50) -> str:
        if not self.buffer:
            return ""
        raw_text = bytes(self.buffer).decode("utf-8", errors="replace")
        clean_text = ANSI_ESCAPE_RE.sub("", raw_text)
        split_lines = [l.strip("\r") for l in clean_text.splitlines() if l.strip()]
        if lines and len(split_lines) > lines:
            split_lines = split_lines[-lines:]
        return "\n".join(split_lines)

    def to_dict(self) -> Dict[str, Any]:
        if self.status == "running" and self.pid:
            try:
                p = psutil.Process(self.pid)
                if p.status() == psutil.STATUS_ZOMBIE:
                    self._handle_exit()
            except (psutil.NoSuchProcess, Exception):
                self._handle_exit()
        return {
            "session_id": self.session_id,
            "name": self.name,
            "profile_id": self.profile_id,
            "cwd": self.cwd,
            "command": self.command,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "status": self.status,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "persistent": self.persistent,
            "connected_clients": len(self.websockets),
        }

class SessionRegistry:
    def __init__(self):
        self.sessions: Dict[str, PTYSession] = {}

    def create(self, name: str, profile_id: str, cwd: str, command: str, task_id: Optional[str] = None, rows: int = 28, cols: int = 80) -> PTYSession:
        sid = str(uuid.uuid4())[:8]
        session = PTYSession(
            session_id=sid,
            name=name,
            profile_id=profile_id,
            cwd=cwd,
            command=command,
            task_id=task_id,
            rows=rows,
            cols=cols
        )
        session.start()
        self.sessions[sid] = session
        if session.persistent:
            save_session_meta(session)
        return session

    def adopt_existing(self) -> List["PTYSession"]:
        """Re-attach to tmux sessions that outlived the previous hub process."""
        if not TMUX_BIN:
            return []
        meta = _load_sessions_meta()
        adopted: List[PTYSession] = []
        live = set()
        for tname in tmux_list_sessions():
            sid = tname[len(TMUX_PREFIX):]
            m = meta.get(sid, {})
            try:
                session = PTYSession(session_id=sid, name=m.get("name", f"Khôi phục {sid}"), profile_id=m.get("profile_id", "system"),
                                     cwd=m.get("cwd") or str(Path.home()), command=m.get("command", ""), task_id=m.get("task_id"),
                                     rows=m.get("rows", 28), cols=m.get("cols", 80), adopt=True, created_at=m.get("created_at"))
                session.start()
            except Exception as e:
                print(f"[tmux] không gắn lại được {tname}: {e}")
                continue
            self.sessions[sid] = session
            adopted.append(session)
            live.add(sid)
        stale = [k for k in meta if k not in live]
        if stale:
            for k in stale:
                meta.pop(k, None)
            SESSIONS_FILE.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        return adopted

    def get(self, sid: str) -> Optional[PTYSession]:
        return self.sessions.get(sid)

    def get_by_profile(self, profile_id: str, status: Optional[str] = "running") -> Optional[PTYSession]:
        for s in reversed(list(self.sessions.values())):
            if s.profile_id == profile_id:
                if status is None or s.status == status:
                    return s
        return None

    def remove(self, sid: str):
        session = self.sessions.get(sid)
        if session:
            session.terminate()
            del self.sessions[sid]

    def list_all(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.sessions.values()]

registry = SessionRegistry()

# -----------------------------------------------------------------------------
# REST API Endpoints
# -----------------------------------------------------------------------------
@app.get("/api/doctor")
async def doctor():
    """First-run self check: what this machine has / lacks for the hub to work."""
    path = build_session_env(None).get("PATH", "")
    have = lambda name: bool(shutil.which(name, path=path))
    profiles = await get_profiles()
    logged_in = sum(1 for p in profiles if p.get("configured"))
    checks = [
        {"id": "os", "label": "Linux", "ok": sys.platform.startswith("linux"), "required": True,
         "hint": "Hub dùng pty/D-Bus/gnome-keyring của Linux. Trên Windows hãy chạy trong WSL2."},
        {"id": "python", "label": "Python ≥ 3.10", "ok": sys.version_info >= (3, 10), "required": True,
         "hint": "Cài Python 3.10 trở lên."},
        {"id": "agy", "label": "Antigravity CLI (agy)", "ok": have("agy"), "required": True,
         "hint": "Cài agy và đảm bảo lệnh `agy` chạy được trong terminal."},
        {"id": "dbus", "label": "dbus-daemon", "ok": os.path.exists(DBUS_DAEMON) or have("dbus-daemon"), "required": True,
         "hint": "sudo apt install dbus"},
        {"id": "keyring", "label": "gnome-keyring", "ok": have("gnome-keyring-daemon"), "required": True,
         "hint": "sudo apt install gnome-keyring (mỗi account cần keyring riêng để không lẫn token)"},
        {"id": "tmux", "label": "tmux (terminal không mất khi restart hub)", "ok": bool(TMUX_BIN), "required": False,
         "hint": "sudo apt install tmux"},
        {"id": "claude", "label": "Claude Code (làm Orchestrator)", "ok": have("claude"), "required": False,
         "hint": "Cài Claude Code nếu muốn dùng nó điều phối; hoặc dùng một account agy làm Orchestrator."},
        {"id": "accounts", "label": f"Account agy đã đăng nhập ({logged_in}/{len(profiles)})", "ok": logged_in > 0, "required": False,
         "hint": "Tab Tài Khoản → 🔑 Đăng nhập cho từng account."},
    ]
    return {"checks": checks, "ready": all(c["ok"] for c in checks if c["required"])}

@app.get("/api/system")
async def get_system_stats():
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path.home()))
    
    # Check running agy processes
    agy_procs = 0
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if "agy" in cmd and "server.py" not in cmd:
                agy_procs += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {
        "cpu_percent": cpu,
        "ram_percent": mem.percent,
        "ram_used_gb": round((mem.total - mem.available) / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "home": str(Path.home()),
        "active_sessions": len(registry.sessions),
        "running_agy_procs": agy_procs
    }

@app.get("/api/profiles")
async def get_profiles():
    # Scan ~/.agy_profiles and check active accounts
    profiles = []
    # Always include standard accounts acc1..acc6
    known_accounts = [f"acc{i}" for i in range(1, 7)]
    
    existing_dirs = set()
    if PROFILES_DIR.exists():
        for d in PROFILES_DIR.iterdir():
            if d.is_dir() and d.name.startswith("acc"):
                existing_dirs.add(d.name)

    all_accs = sorted(list(set(known_accounts).union(existing_dirs)))

    metadata = load_profiles_metadata()
    for acc in all_accs:
        acc_dir = PROFILES_DIR / acc
        gemini_dir = acc_dir / ".gemini"
        keyrings_dir = acc_dir / ".local" / "share" / "keyrings"
        is_configured = gemini_dir.exists()
        
        # Check if any session is actively running on this profile
        active_session = next((s for s in registry.sessions.values() if s.profile_id == acc and s.status == "running"), None)
        
        # Detect or load saved Google email
        meta = metadata.get(acc, {})
        detected_email = detect_profile_email(acc)
        email = meta.get("email") or detected_email or "Chưa đăng nhập"
        
        # Default suggested roles
        default_roles = {
            "acc1": "Architect / Planner",
            "acc2": "Core Implementer",
            "acc3": "Feature Coder",
            "acc4": "QA / Tester",
            "acc5": "Reviewer & Docs",
            "acc6": "Batch / Standby"
        }
        role_tag = meta.get("role_tag") or default_roles.get(acc, "Coder")
        alias = meta.get("alias") or f"Account {acc.replace('acc', '')}"

        profiles.append({
            "id": acc,
            "name": alias,
            "email": email,
            "detected_email": detected_email,
            "role_tag": role_tag,
            "path": str(acc_dir),
            "configured": is_configured and (detected_email is not None or bool(meta.get("email"))),
            "has_keyring": keyrings_dir.exists(),
            "status": "busy" if active_session else "ready",
            "active_session_id": active_session.session_id if active_session else None
        })

    return profiles

@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: str, data: Dict[str, Any]):
    meta = load_profiles_metadata()
    if profile_id not in meta:
        meta[profile_id] = {}
    if "alias" in data:
        meta[profile_id]["alias"] = data["alias"]
    if "email" in data:
        meta[profile_id]["email"] = data["email"]
    if "role_tag" in data:
        meta[profile_id]["role_tag"] = data["role_tag"]
    save_profiles_metadata(meta)
    return {"status": "ok", "profile": meta[profile_id]}

@app.post("/api/profiles")
async def create_profile(req: CreateProfileRequest):
    prof_id = req.profile_id.strip()
    if not prof_id:
        raise HTTPException(status_code=400, detail="Profile ID is required")
    prof_path = PROFILES_DIR / prof_id
    prof_path.mkdir(parents=True, exist_ok=True)
    return {"message": f"Profile {prof_id} created", "path": str(prof_path)}

def reset_profile_auth(profile_id: str) -> Optional[str]:
    """Move a profile's auth state (agy config + keyring) to _backup/ so the next login starts
    clean. Nothing is deleted; returns the backup dir, or None if there was nothing to move."""
    import shutil
    runtime_manager.stop(profile_id)  # its keyring daemon still holds the old keyring in memory
    prof = PROFILES_DIR / profile_id
    items = [prof / ".gemini", prof / ".config", prof / ".local" / "share" / "keyrings"]
    existing = [i for i in items if i.exists()]
    if not existing:
        return None
    dest = PROFILES_DIR / "_backup" / f"{profile_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    for i in existing:
        target = dest / i.relative_to(prof)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(i), str(target))
    dest.chmod(0o700)
    _EMAIL_CACHE.pop(profile_id, None)
    return str(dest)

@app.post("/api/profiles/{profile_id}/login")
async def login_profile(profile_id: str, reset: bool = True):
    if not re.fullmatch(r"acc\d+", profile_id):
        raise HTTPException(status_code=400, detail="Invalid profile id")
    if registry.get_by_profile(profile_id, status="running"):
        raise HTTPException(status_code=409, detail=f"Profile {profile_id} đang có session chạy; dừng nó trước khi đăng nhập lại.")
    backup = reset_profile_auth(profile_id) if reset else None
    # Spawn an interactive terminal session running the login script
    script_path = SCRIPTS_DIR / "login_profile.sh"
    prof_num = profile_id.replace("acc", "")
    cmd = f"bash '{script_path}' {prof_num}"
    session = registry.create(
        name=f"Login: {profile_id}",
        profile_id=profile_id,
        cwd=str(Path.home()),
        command=cmd,
        task_id=None
    )
    return {"session": session.to_dict(), "auth_backup": backup}

_HUB_LOCK = None

def acquire_hub_lock():
    """Only one hub may manage a given profiles directory: they share per-account D-Bus/keyring daemons."""
    global _HUB_LOCK
    import fcntl
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(PROFILES_DIR / ".hub.lock", "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.seek(0)
        other = fh.read().strip() or "?"
        raise RuntimeError(f"Đã có một AGY Hub khác (PID {other}) đang quản lý {PROFILES_DIR}. Dừng nó trước, "
                           f"hoặc chạy bản thử với AGY_PROFILES_DIR/AGY_DATA_DIR riêng.")
    fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
    _HUB_LOCK = fh  # keep the descriptor open for the life of the process

@app.on_event("startup")
async def _adopt_persistent_sessions():
    acquire_hub_lock()
    adopted = registry.adopt_existing()
    if adopted:
        print(f"[tmux] đã gắn lại {len(adopted)} terminal còn sống từ lần chạy trước")
    in_use = {x.profile_id for x in adopted}
    # Daemons of profiles with no surviving terminal are garbage from the previous run.
    for d in PROFILES_DIR.iterdir():
        if d.is_dir() and d.name.startswith("acc") and d.name not in in_use:
            runtime_manager.stop(d.name)

@app.on_event("shutdown")
async def _stop_profile_runtimes():
    # Terminals living in tmux keep running after the hub exits, so their D-Bus/keyring daemons must stay up too.
    keep = {x.profile_id for x in registry.sessions.values() if x.persistent and x.status == "running"}
    runtime_manager.stop_all(keep=keep)
    # also daemons this process only adopted (never tracked) or that a crashed run left behind
    for d in PROFILES_DIR.iterdir():
        if d.is_dir() and d.name.startswith("acc") and d.name not in keep and d.name not in runtime_manager.runtimes:
            ProfileRuntime(d.name).stop()

@app.get("/api/sessions")
async def list_sessions():
    return registry.list_all()

@app.get("/api/sessions/{session_id}/output")
async def get_session_output(session_id: str, lines: int = 30):
    sess = registry.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": sess.status, "output": sess.get_clean_output(lines=lines)}

@app.post("/api/sessions")
async def create_session(req: CreateSessionRequest):
    cwd = req.cwd or str(Path.home())
    cmd = req.command or "agy"
    session = registry.create(
        name=req.name,
        profile_id=req.profile_id,
        cwd=cwd,
        command=cmd,
        task_id=req.task_id,
        rows=req.rows or 28,
        cols=req.cols or 80
    )
    # Save workspace to recent
    ws = load_workspaces()
    if cwd not in ws:
        ws.insert(0, cwd)
        save_workspaces(ws[:15])
    return session.to_dict()

@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    registry.remove(session_id)
    return {"status": "ok", "deleted": session_id}

@app.post("/api/sessions/{session_id}/restart")
async def restart_session(session_id: str):
    old_session = registry.get(session_id)
    if not old_session:
        raise HTTPException(status_code=404, detail="Session not found")
    name = old_session.name
    profile_id = old_session.profile_id
    cwd = old_session.cwd
    command = old_session.command
    task_id = old_session.task_id
    rows = old_session.rows
    cols = old_session.cols
    registry.remove(session_id)
    new_session = registry.create(
        name=name,
        profile_id=profile_id,
        cwd=cwd,
        command=command,
        task_id=task_id,
        rows=rows,
        cols=cols
    )
    return new_session.to_dict()

@app.get("/api/tasks")
async def get_tasks():
    return load_tasks()

@app.post("/api/tasks")
async def create_task(task: TaskModel):
    tasks = load_tasks()
    task_dict = task.dict()
    task_dict["id"] = str(uuid.uuid4())[:8]
    task_dict["created_at"] = time.time()
    task_dict["updated_at"] = time.time()
    tasks.append(task_dict)
    save_tasks(tasks)
    return task_dict

@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, task_update: Dict[str, Any]):
    tasks = load_tasks()
    for t in tasks:
        if t["id"] == task_id:
            for k, v in task_update.items():
                if k != "id":
                    t[k] = v
            t["updated_at"] = time.time()
            save_tasks(tasks)
            return t
    raise HTTPException(status_code=404, detail="Task not found")

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    tasks = load_tasks()
    tasks = [t for t in tasks if t["id"] != task_id]
    save_tasks(tasks)
    return {"status": "ok"}

@app.get("/api/workspaces")
async def get_recent_workspaces():
    return load_workspaces()

@app.post("/api/workspaces")
async def add_workspace(data: Dict[str, str]):
    path = data.get("path")
    if path and os.path.isdir(path):
        ws = load_workspaces()
        if path not in ws:
            ws.insert(0, path)
            save_workspaces(ws[:15])
        return {"status": "ok", "workspaces": ws}
    raise HTTPException(status_code=400, detail="Invalid directory path")

@app.get("/api/git/worktrees")
async def get_git_worktrees(repo_path: str):
    if not os.path.isdir(repo_path):
        raise HTTPException(status_code=400, detail="Repository path does not exist")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "list", "--porcelain",
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return {"error": stderr.decode("utf-8", errors="replace")}
        
        lines = stdout.decode("utf-8").splitlines()
        worktrees = []
        current_wt = {}
        for line in lines:
            if line.startswith("worktree "):
                if current_wt:
                    worktrees.append(current_wt)
                current_wt = {"path": line.split(" ", 1)[1]}
            elif line.startswith("branch "):
                current_wt["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
            elif line.startswith("HEAD "):
                current_wt["head"] = line.split(" ", 1)[1][:8]
        if current_wt:
            worktrees.append(current_wt)
        return {"worktrees": worktrees}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/git/worktrees")
async def create_git_worktree(req: CreateWorktreeRequest):
    if not os.path.isdir(req.repo_path):
        raise HTTPException(status_code=400, detail="Repo path does not exist")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "-b", req.branch_name, req.target_path, req.base_branch,
            cwd=req.repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=400, detail=stderr.decode("utf-8", errors="replace"))
        return {"message": "Worktree created successfully", "path": req.target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------------
# Orchestrator API Endpoints (Master Lead Agent acc1 controlling acc2..acc6)
# -----------------------------------------------------------------------------
ALL_AGENT_IDS = [f"acc{i}" for i in range(1, 7)]
WAITING_RE = re.compile(r"(\(y/n\)|\[y/N\]|\[Y/n\]|Do you want to|Allow this|Approve|Press Enter to|Proceed\?)", re.I)

def detect_session_state(sess: Optional["PTYSession"]) -> str:
    """Heuristic state from the terminal tail: idle (no session) / stopped / busy / waiting / ready."""
    if not sess:
        return "idle"
    if sess.status != "running":
        return "stopped"
    tail = sess.get_clean_output(lines=12)
    if not tail.strip():
        return "starting"
    if "Generating" in tail or "Thinking" in tail or "esc to interrupt" in tail.lower():
        return "busy"
    if WAITING_RE.search(tail):
        return "waiting"
    return "ready"

def load_dispatch_log() -> List[Dict[str, Any]]:
    if not DISPATCH_LOG_FILE.exists():
        return []
    try:
        with open(DISPATCH_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_dispatch_log(entries: List[Dict[str, Any]]):
    with open(DISPATCH_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(entries[-DISPATCH_LOG_MAX:], f, indent=2, ensure_ascii=False)

def orchestrator_targets() -> List[str]:
    orch = load_orchestrator_config().get("orchestrator_profile", "acc1")
    return [a for a in ALL_AGENT_IDS if a != orch]

@app.get("/api/orchestrator/status")
async def get_orchestrator_status():
    config = load_orchestrator_config()
    orch_prof = config.get("orchestrator_profile", "acc1")
    active_workers = sum(1 for pid in ALL_AGENT_IDS if registry.get_by_profile(pid, status="running"))
    claude_sess = registry.get_by_profile(CLAUDE_PROFILE, status="running")
    return {
        "orchestrator_profile": orch_prof,
        "total_profiles": 6,
        "active_running_sessions": active_workers,
        "active_total_sessions": len(registry.sessions),
        "claude_session_id": claude_sess.session_id if claude_sess else None,
    }

@app.get("/api/orchestrator/workers")
async def get_orchestrator_workers():
    config = load_orchestrator_config()
    orch_prof = config.get("orchestrator_profile", "acc1")
    profiles = await get_profiles()
    email_count: Dict[str, int] = {}
    for p in profiles:
        if "@" in (p.get("email") or ""):
            email_count[p["email"]] = email_count.get(p["email"], 0) + 1
    log = load_dispatch_log()
    workers = []
    for p in profiles:
        pid = p["id"]
        sess = registry.get_by_profile(pid, status="running") or registry.get_by_profile(pid, status=None)
        last_job = next((e for e in reversed(log) if e.get("profile_id") == pid), None)
        workers.append({
            "profile_id": pid,
            "name": p["name"],
            "email": p["email"],
            "role_tag": p["role_tag"],
            "is_orchestrator": (pid == orch_prof),
            "duplicate_email": email_count.get(p["email"], 0) > 1,
            "logged_in": bool(p.get("configured")),
            "status": ("running" if sess and sess.status == "running" else (sess.status if sess else "idle")),
            "state": detect_session_state(sess),
            "session_id": sess.session_id if sess else None,
            "command": sess.command if sess else None,
            "cwd": sess.cwd if sess else None,
            "last_output": sess.get_clean_output(lines=15) if sess else "",
            "last_job": last_job,
        })
    return workers

async def _dispatch_one(tgt: str, prompt: str, mode: Optional[str], cwd: Optional[str],
                        auto_start: bool, task_id: Optional[str] = None, title: Optional[str] = None,
                        force: bool = False, session_id: Optional[str] = None) -> Dict[str, Any]:
    if tgt not in ALL_AGENT_IDS:
        return {"profile_id": tgt, "status": "failed", "error": f"Unknown profile {tgt}"}
    sess = registry.get_by_profile(tgt, status="running")
    if session_id:
        sess = registry.get(session_id)
        if not sess or sess.profile_id != tgt or sess.status != "running":
            return {"profile_id": tgt, "status": "failed", "error": f"Session {session_id} không chạy trên {tgt}"}
    # Dispatch types ESC + prompt into the terminal; doing that to a worker that is mid-task or
    # waiting for an approval would corrupt its current job, so refuse unless forced.
    state = detect_session_state(sess) if sess else "idle"
    if state in ("busy", "waiting") and not force:
        return {"profile_id": tgt, "session_id": sess.session_id, "status": "failed",
                "error": f"{tgt} đang {'chờ bạn duyệt' if state == 'waiting' else 'bận xử lý'}; chờ xong hoặc dùng force"}
    if not sess and auto_start:
        cmd = "agy"
        if mode and mode != "interactive":
            cmd = f"agy --mode {mode}"
        sess = registry.create(name=f"{tgt}-{mode or 'Worker'}", profile_id=tgt,
                               cwd=cwd or str(Path.home()), command=cmd, task_id=task_id)
        await asyncio.sleep(2.0)

    entry = {
        "id": uuid.uuid4().hex[:8], "ts": time.time(), "profile_id": tgt, "title": title or prompt[:60],
        "prompt": prompt, "mode": mode, "cwd": cwd, "task_id": task_id,
        "session_id": sess.session_id if sess else None, "status": "sent", "result": None,
    }
    if sess and sess.status == "running":
        sess.write_input(b"\x1b")
        await asyncio.sleep(0.05)
        sess.write_input(f"{prompt}\r".encode("utf-8"))
    else:
        entry["status"] = "failed"
        entry["error"] = "Session is not running and could not be started"
    log = load_dispatch_log()
    log.append(entry)
    save_dispatch_log(log)
    if task_id and entry["status"] == "sent":
        tasks = load_tasks()
        for t in tasks:
            if t.get("id") == task_id:
                t["last_dispatch_id"] = entry["id"]
                t["assigned_profile"] = tgt
                t["updated_at"] = time.time()
        save_tasks(tasks)
    out = {"profile_id": tgt, "session_id": entry["session_id"], "status": entry["status"],
           "prompt": prompt, "dispatch_id": entry["id"]}
    if entry.get("error"):
        out["error"] = entry["error"]
    return out

@app.post("/api/orchestrator/dispatch")
async def dispatch_orchestrator_prompt(req: DispatchPromptRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    targets = orchestrator_targets() if req.target_profile == "all" else [req.target_profile]
    results = [await _dispatch_one(t, prompt, req.mode, req.cwd, bool(req.auto_start), req.task_id, req.title, bool(req.force), req.session_id)
               for t in targets]
    return {"results": results}

@app.post("/api/orchestrator/plan/run")
async def run_orchestrator_plan(req: RunPlanRequest):
    """Create one Kanban task per plan item and dispatch each to its worker."""
    if not req.items:
        raise HTTPException(status_code=400, detail="Plan has no items")
    busy = [i.profile_id for i in req.items]
    dupes = sorted({b for b in busy if busy.count(b) > 1})
    if dupes:
        raise HTTPException(status_code=400, detail=f"Mỗi worker chỉ nhận 1 việc/lượt, bị trùng: {', '.join(dupes)}")
    results = []
    for item in req.items:
        tasks = load_tasks()
        tid = uuid.uuid4().hex[:8]
        now = time.time()
        tasks.append({
            "id": tid, "title": item.title or item.prompt[:60], "description": item.prompt,
            "stage": "planning" if item.mode == "plan" else "implementing",
            "assigned_profile": item.profile_id, "workspace_dir": item.cwd or str(Path.home()),
            "mode": item.mode or "accept-edits", "goal": req.goal, "created_at": now, "updated_at": now,
        })
        save_tasks(tasks)
        results.append(await _dispatch_one(item.profile_id, item.prompt.strip(), item.mode, item.cwd,
                                           True, tid, item.title))
    return {"results": results}

@app.get("/api/orchestrator/log")
async def get_dispatch_log(limit: int = 50):
    return list(reversed(load_dispatch_log()))[:limit]

@app.post("/api/orchestrator/log/{dispatch_id}/collect")
async def collect_dispatch_result(dispatch_id: str, lines: int = 60):
    """Snapshot the worker's terminal tail as the result of this dispatch."""
    log = load_dispatch_log()
    entry = next((e for e in log if e.get("id") == dispatch_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Dispatch not found")
    sess = registry.get_by_profile(entry["profile_id"], status=None)
    if not sess:
        raise HTTPException(status_code=404, detail="Worker session no longer exists")
    entry["result"] = sess.get_clean_output(lines=lines)
    entry["collected_at"] = time.time()
    entry["state_at_collect"] = detect_session_state(sess)
    save_dispatch_log(log)
    return entry

@app.post("/api/orchestrator/claude")
async def start_claude_orchestrator():
    """Embed Claude Code as the orchestrator: one PTY running `claude` inside the hub directory."""
    sess = _claude_orch_session()
    if not sess:
        sess = registry.create(name=CLAUDE_ORCH_NAME, profile_id=CLAUDE_PROFILE,
                               cwd=str(BASE_DIR), command="claude")
    return sess.to_dict()

class ClaudeSendRequest(BaseModel):
    text: str

_CLAUDE_AUTH_CACHE: Dict[str, Any] = {"ts": 0.0, "value": None}

def _claude_auth_status() -> Dict[str, Any]:
    """Login state of the Claude Code CLI on this machine (`claude auth status`), cached for 30s."""
    now = time.time()
    if _CLAUDE_AUTH_CACHE["value"] is not None and now - _CLAUDE_AUTH_CACHE["ts"] < 30:
        return _CLAUDE_AUTH_CACHE["value"]
    result: Dict[str, Any] = {"logged_in": None, "email": None, "plan": None}
    try:
        env = build_session_env(None)
        out = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=10, env=env)
        data = json.loads(out.stdout or "{}")
        result = {"logged_in": bool(data.get("loggedIn")), "email": data.get("email"), "plan": data.get("subscriptionType")}
    except Exception:
        pass  # claude not installed / timed out: report "unknown" rather than "logged out"
    _CLAUDE_AUTH_CACHE.update(ts=now, value=result)
    return result

CLAUDE_ORCH_NAME = "Claude Orchestrator"

def _claude_orch_session(running_only: bool = True) -> Optional["PTYSession"]:
    for sess in reversed(list(registry.sessions.values())):
        if sess.profile_id == CLAUDE_PROFILE and sess.name == CLAUDE_ORCH_NAME and (not running_only or sess.status == "running"):
            return sess
    return None

@app.get("/api/orchestrator/claude")
async def get_claude_orchestrator(lines: int = 14):
    sess = _claude_orch_session() or _claude_orch_session(running_only=False)
    auth = await asyncio.to_thread(_claude_auth_status)
    return {
        "auth": auth,
        "session_id": sess.session_id if sess else None,
        "status": sess.status if sess else "idle",
        "state": detect_session_state(sess),
        "output": sess.get_clean_output(lines=lines) if sess else "",
    }

@app.post("/api/orchestrator/claude/send")
async def send_to_claude(req: ClaudeSendRequest):
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nội dung trống")
    sess = _claude_orch_session()
    if not sess:
        raise HTTPException(status_code=409, detail="Claude Orchestrator chưa chạy; bấm Khởi chạy trước")
    # No ESC here: it would interrupt a reply Claude is already generating.
    sess.write_input((text + "\r").encode("utf-8"))
    return {"status": "sent"}

@app.get("/api/orchestrator/workers/{profile_id}/output")
async def get_worker_output(profile_id: str, lines: int = 50, raw: bool = False):
    sess = registry.get_by_profile(profile_id, status=None)
    if not sess:
        raise HTTPException(status_code=404, detail=f"No session found for {profile_id}")

    if raw:
        output = bytes(sess.buffer).decode("utf-8", errors="replace")
    else:
        output = sess.get_clean_output(lines=lines)

    return {
        "profile_id": profile_id,
        "session_id": sess.session_id,
        "status": sess.status,
        "state": detect_session_state(sess),
        "exit_code": sess.exit_code,
        "lines": lines,
        "output": output
    }

@app.post("/api/orchestrator/control/{profile_id}")
async def control_worker(profile_id: str, req: OrchestratorControlRequest):
    sess = registry.get_by_profile(profile_id, status="running")
    if not sess:
        raise HTTPException(status_code=404, detail=f"No running session for {profile_id}")

    act = req.action.lower()
    if act == "interrupt":
        sess.write_input(b"\x03")
    elif act == "esc":
        sess.write_input(b"\x1b")
    elif act == "enter":
        sess.write_input(b"\r")
    elif act == "clear":
        sess.write_input(b"\x0c")
    elif act == "terminate":
        sess.terminate()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action {act}")

    return {"status": "ok", "action": act, "profile_id": profile_id}

@app.put("/api/orchestrator/settings")
async def update_orchestrator_settings(req: OrchestratorSettingsRequest):
    if req.orchestrator_profile not in ALL_AGENT_IDS + [CLAUDE_PROFILE]:
        raise HTTPException(status_code=400, detail="orchestrator_profile phải là acc1..acc6 hoặc claude")
    save_orchestrator_config({"orchestrator_profile": req.orchestrator_profile})
    return {"status": "ok", "orchestrator_profile": req.orchestrator_profile}

# -----------------------------------------------------------------------------
# WebSocket for xterm.js PTY Stream
# -----------------------------------------------------------------------------
@app.websocket("/ws/terminal/{session_id}")
async def terminal_websocket(websocket: WebSocket, session_id: str):
    if request_rejection(websocket.headers):  # browsers do not apply CORS to WebSockets; check Origin ourselves
        await websocket.close(code=1008, reason="Forbidden origin")
        return
    session = registry.get(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    await websocket.accept()
    session.websockets.add(websocket)

    # Replay buffered output. The history can contain terminal queries (device attributes, cursor position)
    # that the browser terminal would answer again, typing the answers into the running agent; the marker
    # below lets the client ignore its own auto-replies until the replay has been rendered.
    try:
        if session.buffer:
            await websocket.send_bytes(bytes(session.buffer))
        await websocket.send_text('{"type":"replay_done"}')
    except Exception:
        pass

    try:
        while True:
            msg = await websocket.receive()
            if "bytes" in msg and msg["bytes"]:
                session.write_input(msg["bytes"])
            elif "text" in msg and msg["text"]:
                text_data = msg["text"]
                # Only check control JSON if it looks like a JSON object
                if text_data.startswith('{"type":'):
                    try:
                        parsed = json.loads(text_data)
                        if isinstance(parsed, dict) and parsed.get("type") == "resize":
                            session.resize(rows=int(parsed.get("rows", 28)), cols=int(parsed.get("cols", 100)))
                            continue
                    except Exception:
                        pass
                session.write_input(text_data.encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        session.websockets.discard(websocket)

# -----------------------------------------------------------------------------
# Mount Frontend Static Files
# -----------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8888, help="Port (default 8888)")
    args = parser.parse_args()
    os.environ["AGY_PORT"] = str(args.port)
    os.environ["AGY_BIND_HOST"] = args.host  # uvicorn re-imports this module, so pass it through the environment
    print(f"\n=======================================================")
    print(f"🚀 Agent Farm running at:")
    print(f"👉 http://{args.host}:{args.port}")
    print(f"=======================================================\n")
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False)
