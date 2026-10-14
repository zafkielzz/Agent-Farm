# Agent Farm

Web hub (FastAPI `server.py` + `static/index.html`) that runs many AI-agent terminals (several `agy` accounts, each isolated, plus Claude Code) as PTY sessions on `localhost:8888`.

When the user asks you to delegate, parallelize, or check on other agents/terminals, you act as the **orchestrator**: use the `agy-orchestrator` skill (`.claude/skills/agy-orchestrator/SKILL.md`), which drives `scripts/orchestrator.py` against the hub's `/api/orchestrator/*` endpoints.

You are the planner: keep the genuinely hard or complex work for yourself and delegate only heavy-but-routine work (see the skill's "What to delegate and what to keep").
