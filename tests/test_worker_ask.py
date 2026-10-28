#!/usr/bin/env python3
"""Tests of the team rule in bin/worker-ask (agy-ask / claude-ask) against a fake office.

Run: python3 tests/test_worker_ask.py. Nothing real runs: HABBLAUD_AGY_PROFILES is a temp folder, HABBLAUD_URL a
fake server that answers every job at once, and the boss callers are fake `claude` / `agy` scripts (the rule finds
the caller by the command name of its ancestors)."""
import http.server, json, os, shutil, subprocess, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "bin", "worker-ask")


class Office(http.server.BaseHTTPRequestHandler):
    bodies = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Office.bodies.append(body)
        self.reply({"job": {"id": "j1", "status": "done", "answer": "pong"}})

    def reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


# A fake agent: writes team.json from $TEAM_TEMPLATE with its own PID for __PID__, then runs the command as a child
# (not exec: it must stay an ancestor).
# Headless (`claude -p ...`): answers at once with its arguments as the result.
FAKE = """#!/bin/bash
if [ "$1" = "-p" ]; then
  exec python3 -c 'import json, sys; print(json.dumps({"subtype": "success", "result": json.dumps(sys.argv[1:]), "usage": {}}))' "$@"
fi
if [ -n "${TEAM_TEMPLATE:-}" ]; then printf '%s' "${TEAM_TEMPLATE//__PID__/$$}" > "$HABBLAUD_AGY_PROFILES/team.json"; fi
"$@"
exit $?
"""


class TeamRule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="worker-ask-test-")
        cls.root = os.path.join(cls.tmp, "profiles")
        for acc in ("acc1", "acc2", "acc5"):
            os.makedirs(os.path.join(cls.root, acc))
        cls.bin = os.path.join(cls.tmp, "bin")
        os.makedirs(cls.bin)
        for name in ("agy-ask", "claude-ask"):
            shutil.copy(SRC, os.path.join(cls.bin, name))
        for name in ("claude", "agy"):
            path = os.path.join(cls.bin, name)
            with open(path, "w") as f:
                f.write(FAKE)
            os.chmod(path, 0o755)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Office)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.tmp)

    def setUp(self):
        Office.bodies.clear()
        try:
            os.remove(os.path.join(self.root, "team.json"))
        except FileNotFoundError:
            pass

    def run_ask(self, args, via=None, team=None, env=None, tool="agy-ask"):
        """(exit code, stdout, stderr) of `<tool> <args>`, started by the fake `via` agent when given."""
        e = {k: v for k, v in os.environ.items() if not k.startswith(("AGY_", "TEAM_TEMPLATE"))}
        e.update(HABBLAUD_AGY_PROFILES=self.root, HABBLAUD_URL=f"http://127.0.0.1:{self.server.server_port}")
        if team is not None:
            if via:
                e["TEAM_TEMPLATE"] = json.dumps(team)
            else:
                with open(os.path.join(self.root, "team.json"), "w") as f:
                    json.dump(team, f)
        e.update(env or {})
        cmd = [os.path.join(self.bin, tool)] + args
        if via:
            cmd = [os.path.join(self.bin, via)] + cmd
        p = subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=30, cwd=self.tmp)
        return p.returncode, p.stdout, p.stderr

    @staticmethod
    def claude_boss(name="Raul"):
        return {"kind": "claude", "id": ".claude:1", "account": ".claude", "pid": "__PID__", "name": name}

    def v3(self, boss2=None, boss1=None):
        return {"offices": [
            {"id": "o1", "name": "Habblaud", "folder": "/x/habblaud", "boss": boss1, "workers": ["acc1"]},
            {"id": "o2", "name": "News", "folder": "/x/news", "boss": boss2, "workers": ["acc2", "claude:.claude-w"]},
        ]}

    def ask(self, worker, via=None, team=None, env=None, tool="agy-ask"):
        if team is not None and via:
            template = json.dumps(team).replace('"__PID__"', "__PID__")
            return self.run_ask_raw([worker, "ping"], via, template, env, tool)
        return self.run_ask([worker, "ping"], via, team, env, tool)

    def run_ask_raw(self, args, via, template, env, tool):
        return self.run_ask(args, via, None, dict(env or {}, TEAM_TEMPLATE=template), tool)

    # -------------------------------------------------------------- the boss of an office

    def test_boss_gives_jobs_to_its_office_with_the_office_id(self):
        code, out, err = self.ask("acc2", via="claude", team=self.v3(boss2=self.claude_boss()))
        self.assertEqual((code, out.strip()), (0, "pong"), err)
        self.assertEqual(Office.bodies[-1]["office"], "o2")
        self.assertEqual(Office.bodies[-1]["by"], "boss")

    def test_claude_worker_of_the_office(self):
        code, out, err = self.ask("w", via="claude", team=self.v3(boss2=self.claude_boss()), tool="claude-ask")
        self.assertEqual(code, 0, err)
        self.assertEqual(Office.bodies[-1]["worker"], "claude:.claude-w")
        self.assertEqual(Office.bodies[-1]["office"], "o2")

    def test_boss_refused_for_another_offices_worker(self):
        code, _, err = self.ask("acc1", via="claude", team=self.v3(boss2=self.claude_boss()))
        self.assertEqual(code, 2)
        self.assertIn("not a worker of your office News (o2)", err)
        self.assertEqual(Office.bodies, [])

    def test_boss_refused_for_its_own_account(self):
        team = self.v3(boss2={"kind": "agy", "acc": "acc2", "name": "Ana"})
        team["offices"][1]["workers"].append("acc2")
        code, _, err = self.run_ask(["acc2", "ping"], via="agy", team=team, env={"HOME": os.path.join(self.root, "acc2")})
        self.assertEqual(code, 2)
        self.assertIn("boss's own account", err)

    def test_agy_boss_found_by_its_profile_home(self):
        team = self.v3(boss1={"kind": "agy", "acc": "acc5", "name": "Ana"})
        env = {"HOME": os.path.join(self.root, "acc5"), "AGY_BOSS": "1", "AGY_OFFICE": "o1"}
        code, out, err = self.run_ask(["acc1", "ping"], via="agy", team=team, env=env)
        self.assertEqual((code, out.strip()), (0, "pong"), err)
        self.assertEqual(Office.bodies[-1]["office"], "o1")

    def test_not_a_boss_refused_while_an_office_has_one(self):
        code, _, err = self.ask("acc1", team=self.v3(boss2={"kind": "claude", "account": ".claude", "pid": 1, "name": "Raul"}))
        self.assertEqual(code, 2)
        self.assertIn("only a boss may give jobs (bosses: Raul", err)

    # -------------------------------------------------------------- no boss anywhere

    def test_no_boss_any_offices_worker_with_its_first_office(self):
        team = self.v3()
        team["offices"][0]["workers"].append("acc2")
        code, _, err = self.ask("acc2", team=team)
        self.assertEqual(code, 0, err)
        self.assertEqual(Office.bodies[-1]["office"], "o1")

    def test_no_boss_unregistered_worker_refused(self):
        code, _, err = self.ask("acc5", team=self.v3())
        self.assertEqual(code, 2)
        self.assertIn("acc5 is not a registered worker (workers: acc1, acc2, claude:.claude-w", err)

    def test_no_team_file_no_rule_no_office(self):
        code, _, err = self.ask("acc5")
        self.assertEqual(code, 0, err)
        self.assertNotIn("office", Office.bodies[-1])

    # -------------------------------------------------------------- jobs from the office, v2 files

    def test_office_jobs_skip_the_rule_and_carry_the_office(self):
        env = {"AGY_ASK_FROM_OFFICE": "1", "AGY_OFFICE": "o2"}
        code, _, err = self.ask("acc5", team=self.v3(boss2=self.claude_boss()), env=env)
        self.assertEqual(code, 0, err)
        self.assertEqual((Office.bodies[-1]["office"], Office.bodies[-1]["by"]), ("o2", "you"))

    def test_v2_file_is_office_main(self):
        team = {"boss": self.claude_boss(), "workers": ["acc1"]}
        code, _, err = self.ask("acc1", via="claude", team=team)
        self.assertEqual(code, 0, err)
        self.assertEqual(Office.bodies[-1]["office"], "main")
        code, _, err = self.ask("acc2", via="claude", team=team)
        self.assertEqual(code, 2)

    # -------------------------------------------------------------- agy-ask team

    def test_team_prints_offices_and_marks_yours(self):
        template = json.dumps(self.v3(boss2=self.claude_boss())).replace('"__PID__"', "__PID__")
        code, out, err = self.run_ask(["team"], via="claude", env={"TEAM_TEMPLATE": template})
        self.assertEqual(code, 0, err)
        self.assertIn("office Habblaud (o1): /x/habblaud\n  boss: none\n  workers: acc1", out)
        self.assertIn("office News (o2): /x/news  <- you are its boss\n  boss: Raul (Claude Code, account claude:.claude)", out)
        self.assertNotIn("you: not a boss", out)

    # -------------------------------------------------------------- lean Claude workers

    def headless_args(self, extra=(), env=None):
        """The arguments a headless claude-ask gives `claude` (no office, no team file)."""
        os.makedirs(os.path.join(self.tmp, ".claude-w"), exist_ok=True)
        e = {"HOME": self.tmp, "HABBLAUD_URL": "http://127.0.0.1:9", "PATH": f"{self.bin}:{os.environ['PATH']}", **(env or {})}
        code, out, err = self.run_ask(["w", "ping", "--max", "500", *extra], env=e, tool="claude-ask")
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def test_claude_headless_starts_lean_with_the_worker_rule(self):
        args, _ = self.headless_args()
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--disable-slash-commands", args)
        self.assertEqual(args[args.index("--tools") + 1], "Bash,Read,Edit,Write,WebFetch,WebSearch")
        self.assertIn("under 500 characters", args[args.index("--append-system-prompt") + 1])

    def test_claude_full_and_own_tools(self):
        args, _ = self.headless_args(["--full"])
        self.assertNotIn("--strict-mcp-config", args)
        self.assertNotIn("--full", args)
        args, _ = self.headless_args(["--tools", "Read"])
        self.assertEqual(args.count("--tools"), 1)
        self.assertEqual(args[args.index("--tools") + 1], "Read")

    def test_claude_office_job_says_lean(self):
        code, _, err = self.ask("w", tool="claude-ask")
        self.assertEqual(code, 0, err)
        self.assertIs(Office.bodies[-1]["lean"], True)
        code, _, err = self.run_ask(["w", "ping", "--full"], tool="claude-ask")
        self.assertNotIn("lean", Office.bodies[-1])
        self.ask("acc1")
        self.assertNotIn("lean", Office.bodies[-1])

    def test_claude_same_account_note(self):
        code, _, err = self.run_ask(["w", "ping"], via="claude", env={"CLAUDE_CONFIG_DIR": os.path.expanduser("~/.claude-w")}, tool="claude-ask")
        self.assertEqual(code, 0, err)
        self.assertIn("is the account you run on", err)
        code, _, err = self.run_ask(["w", "ping"], via="claude", env={"CLAUDE_CONFIG_DIR": "/elsewhere"}, tool="claude-ask")
        self.assertNotIn("account you run on", err)

    def test_team_not_a_boss(self):
        code, out, _ = self.run_ask(["team"], team=self.v3(boss2={"kind": "claude", "account": ".claude", "pid": 1}))
        self.assertIn("you: not a boss\n", out)


class BossRulesHook(unittest.TestCase):
    """agy-plugin/agy-boss/boss-rules: the SessionStart hook that makes an agy started by Habblaud a boss."""
    HOOK = os.path.join(HERE, "..", "agy-plugin", "agy-boss", "boss-rules")

    def hook(self, env):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "team.json"), "w") as f:
                json.dump({"offices": [{"id": "o7", "name": "Glass shop", "folder": "/x/glass", "workers": []}]}, f)
            e = {k: v for k, v in os.environ.items() if not k.startswith("AGY_")}
            e.update(env, HABBLAUD_AGY_PROFILES=root)
            p = subprocess.run([sys.executable, self.HOOK], input="{}", env=e, capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_nothing_without_agy_boss(self):
        self.assertEqual(self.hook({}), {})

    def test_boss_rules_and_office(self):
        msg = self.hook({"AGY_BOSS": "1", "AGY_OFFICE": "o7"})["injectSteps"][0]["systemMessage"]["systemMessage"]
        self.assertTrue(msg.startswith("# You are the boss here"))
        self.assertIn("boss of the office Glass shop (id o7), project folder /x/glass.", msg)


if __name__ == "__main__":
    unittest.main(verbosity=1)
