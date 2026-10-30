# CLAUDE.md — Paid or Pivot

Goal: get a 100% online business to its **first paying customer with the least friction**, then grow it. Keep this project simple; that's a deliberate choice.

## What it is
Four agents, one per phase, run from the command line (`paid-or-pivot <phase>`):
Research & Validation → Launch → Marketing → Scaling. Each sends the venture's files to Claude (`claude-sonnet-5-5` by default) with the web search tool and saves one markdown report ending in `DECISION: GO | NO-GO | PIVOT`. The founder does the real-world work, records results in `ventures/<slug>/notes.md`, and decides when to run the next phase.

## Layout
- `paid_or_pivot/prompts/*.md` — each agent's instructions (`shared.md` applies to all). Most changes to agent behavior belong here, not in code.
- `agents.py` — the four agents, order, output file, search cap
- `venture.py` — venture folders, bbuilder import, DECISION parsing
- `runner.py` — the Claude call: streaming, `pause_turn` continuation, sources from citations
- `cli.py` — commands and the "previous phase said GO" check (`--force` overrides)

## Rules
- Agents only draft and recommend. Don't add tools that send email, post, buy or deploy.
- Don't add a coordinator agent or a database. Commander/Tracker/Predictor live in a separate system; integrate later only if it doesn't add friction (they can read `ventures/` files).
- Tests use a fake client (`tests/conftest.py`); never call the real API in tests. Run `pytest`.
