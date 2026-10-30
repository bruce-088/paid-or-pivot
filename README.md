# Paid or Pivot

Every phase ends one of two ways: someone paid, or you change course.

Four AI agents that take a business idea from research to launch, marketing and scaling, built around one goal: **your first paying customer, with the least friction**.

| Agent | You give it | It gives you |
|---|---|---|
| **Research & Validation** | An idea (plain words or a bbuilder export) | Who buys, evidence of the problem, competitors and prices (with sources), the riskiest assumptions, a 7-day test that asks for money, and where to find your first 20 prospects |
| **Launch** | The research + your notes | The offer and price, how to deliver it by hand, how to take payment without code, landing-page copy, outreach messages, and a 7-day plan to the first payment |
| **Marketing** | Everything so far + your results | What's working, three channel experiments with stop rules, offer tweaks, a referral ask, and a weekly scorecard |
| **Scaling** | Everything so far + your numbers | Unit economics, the biggest bottleneck, what to automate or hand off first, pricing and retention fixes, and a 30-day plan |

Each agent searches the web, cites its sources, and ends with **GO, NO-GO or PIVOT**. You do the real-world work, write down what happened, and decide when to move on. The agents never send, post or buy anything themselves.

## Setup (5 minutes)

You need Python 3.10+ (`python3 --version`; on a Mac with 3.9, install 3.12 from [python.org](https://www.python.org/downloads/)).

```bash
git clone https://github.com/bruce-088/paid-or-pivot && cd paid-or-pivot
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Then pick how the agents reach Claude:

- **Your Claude subscription (Pro or Max).** Install [Claude Code](https://claude.com/claude-code), run `claude` once and sign in. That's it: with no API key set, the agents run through Claude Code and use your plan's limits. A research run can take a noticeable bite of those limits, since it does many web searches. This is meant for your own use; if you ever offer this tool to other people, each of them needs their own login or key.
- **The Anthropic API (pay per use).** `cp .env.example .env` and paste a key from [platform.claude.com](https://platform.claude.com).

If both are set up, the API key wins; force one with `--engine claude-code` or `--engine api`.

In a new terminal window, run `cd paid-or-pivot && source .venv/bin/activate` before using the command.

## Use it

```bash
# 1. Research an idea
paid-or-pivot research "AI desk that answers event-venue inquiries and chases stalled proposals"

#    ...or start from a bbuilder idea (Mya → export the Polsia brief)
paid-or-pivot research --bbuilder polsia-company-venue-inquiry-desk.json

# 2. Run the money test it suggests. Write the results in ventures/<name>/notes.md

# 3. Next phases, when the previous one said GO
paid-or-pivot launch    <name>
paid-or-pivot marketing <name>
paid-or-pivot scaling   <name>

# Where every idea stands
paid-or-pivot status
```

Everything lives in plain files you can read and edit:

```
ventures/venue-inquiry-desk/
  idea.md           the idea
  notes.md          what actually happened (you write this; every agent reads it)
  01-research.md    one report per phase
  02-launch.md
  03-marketing.md
  04-scaling.md
  history/          earlier versions when you re-run a phase
```

**The notes file is the feedback loop.** "Sent 30 messages, 4 replies, 1 paid $450" in `notes.md` makes the next agent's plan far better than any assumption.

- **Re-run a phase** any time with the same command (for research, use the venture's folder name). The old report moves to `history/`.
- **Skip the GO check** with `--force` if you want a later phase even though the earlier one said NO-GO or PIVOT.
- **Change the model** with `--model claude-opus-5-5` or `PAID_OR_PIVOT_MODEL` in `.env`. The default is `claude-sonnet-5-5`.

## Cost

- **Subscription:** no extra charge; each run counts against your plan's usage limits, shared with your other Claude use.
- **API:** Claude tokens plus web searches ($10 per 1,000 searches; the agents are capped at 6–12 searches per run). Expect roughly tens of cents per run with the default model. This is an estimate; check your usage in the Claude Console.

## Privacy

`ventures/` is in `.gitignore`, so your plans and notes stay on your machine and aren't pushed by accident. Remove that line if you want them in the repo.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests use a fake Claude client, so they don't need an API key and cost nothing.

The code is small on purpose: `paid_or_pivot/prompts/` holds the four agents' instructions (edit these to change how they think), `agents.py` lists them, `venture.py` handles the files, `runner.py` calls Claude, and `cli.py` is the command line.
