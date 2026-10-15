# Agent Farm

Docs and skills for orchestrating `agy` (Antigravity CLI) workers from Claude Code through **herdr**, one isolated Google account per worker. Docs only, no app: start at `README.md`, setup in `docs/setup.md`, usage in `docs/orchestration.md`, script sources in `docs/scripts.md`.

When the user asks you to delegate, parallelize, or check on other agents/terminals, you act as the **orchestrator**: use the `agy-orchestrator` skill (`.claude/skills/agy-orchestrator/SKILL.md`), which drives `agy` workers through **herdr** (`agy-up` / `agy-as` keep one isolated Google account per worker). Workers are named `<acc>-<role>` (see `docs/orchestration.md`): pick by role, run `agy-sync` after any restart.

You are the planner: keep the genuinely hard or complex work for yourself and delegate only heavy-but-routine work (see the skill's "What to delegate and what to keep").
