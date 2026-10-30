"""Command line: one command per agent, plus status.

  paid-or-pivot research "an idea in plain words"
  paid-or-pivot research --bbuilder polsia-company-x.json
  paid-or-pivot launch <venture>
  paid-or-pivot marketing <venture>
  paid-or-pivot scaling <venture>
  paid-or-pivot status [<venture>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from paid_or_pivot import runner
from paid_or_pivot.agents import AGENTS, BY_KEY, previous
from paid_or_pivot.venture import Venture, VentureError

DEFAULT_DIR = "ventures"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env support: KEY=value lines, without overriding the shell."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paid-or-pivot", description="Four agents from idea to first paying customer and beyond.")
    parser.add_argument("--dir", default=os.environ.get("PAID_OR_PIVOT_DIR", DEFAULT_DIR), help="where venture folders live (default: ./ventures)")
    parser.add_argument("--model", default=None, help=f"Claude model (default: {runner.DEFAULT_MODEL}, or PAID_OR_PIVOT_MODEL)")
    parser.add_argument(
        "--engine",
        choices=runner.ENGINES,
        default=None,
        help="api = your ANTHROPIC_API_KEY; claude-code = your Claude subscription via the Claude Code CLI; "
        "auto (default) = API if a key is set, otherwise Claude Code",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    research = sub.add_parser("research", help="Research & Validation: is this idea worth launching?")
    source = research.add_mutually_exclusive_group(required=True)
    source.add_argument("idea", nargs="?", help="the idea in plain words, or an existing venture's name to re-run")
    source.add_argument("--bbuilder", type=Path, help="a Polsia company brief exported from bbuilder")
    source.add_argument("--idea-file", type=Path, help="a text or markdown file describing the idea")

    for agent in AGENTS[1:]:
        p = sub.add_parser(agent.key, help=f"{agent.name} agent")
        p.add_argument("venture", help="the venture's folder name (see: paid-or-pivot status)")
        p.add_argument("--force", action="store_true", help="run even if the previous phase didn't say GO")

    status = sub.add_parser("status", help="list ventures and where each one stands")
    status.add_argument("venture", nargs="?")
    return parser


def _venture_for_research(args, root: Path) -> Venture:
    if args.bbuilder:
        return Venture.from_bbuilder_file(root, args.bbuilder)
    if args.idea_file:
        try:
            text = args.idea_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise VentureError(f"Couldn't read {args.idea_file}: {exc}") from exc
        return Venture.from_text(root, text)
    if (root / args.idea / "idea.md").is_file():
        return Venture.load(root, args.idea)  # re-run research on an existing venture
    return Venture.from_text(root, args.idea)


def _check_previous(venture: Venture, agent, force: bool) -> None:
    before = previous(agent)
    if before is None:
        return
    if not venture.output(before).strip():
        raise VentureError(f"Run the {before.name} phase first: paid-or-pivot {before.key} {venture.slug}")
    decision = venture.decision(before)
    if decision and decision[0] == "GO":
        return
    verdict = decision[0] if decision else "no decision"
    if not force:
        raise VentureError(
            f"The {before.name} phase said {verdict}. Update notes.md and re-run it, "
            f"or add --force to continue anyway."
        )


def print_status(root: Path, slug: str | None, out=sys.stdout) -> None:
    ventures = [Venture.load(root, slug)] if slug else Venture.all(root)
    if not ventures:
        out.write(f"No ventures in {root} yet. Start one with: paid-or-pivot research \"your idea\"\n")
        return
    for venture in ventures:
        out.write(f"{venture.slug}\n")
        for agent in AGENTS:
            if not venture.output(agent).strip():
                out.write(f"  {agent.name:<22} not run\n")
                continue
            decision = venture.decision(agent)
            label = f"{decision[0]} — {decision[1]}" if decision else "done (no decision line)"
            out.write(f"  {agent.name:<22} {label}\n")
        out.write(f"  folder: {venture.path}\n")


def main(argv: list[str] | None = None, client=None, out=sys.stdout) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    root = Path(args.dir)
    try:
        if args.command == "status":
            print_status(root, args.venture, out)
            return 0
        agent = BY_KEY[args.command]
        if agent.key == "research":
            venture = _venture_for_research(args, root)
        else:
            venture = Venture.load(root, args.venture)
            _check_previous(venture, agent, args.force)
        out.write(f"▶ {agent.name} agent · {venture.slug}\n\n")
        _, info = runner.run_agent(venture, agent, client=client, model=args.model, out=out, engine=args.engine)
    except VentureError as exc:
        out.write(f"Error: {exc}\n")
        return 1

    saved = venture.path / agent.output_file
    out.write(f"\nSaved {saved} ({info['sources']} sources, {info['searches']} web searches)\n")
    decision = info["decision"]
    if decision is None:
        out.write("The agent didn't end with a DECISION line; read the file and decide yourself.\n")
    else:
        out.write(f"Decision: {decision[0]} — {decision[1]}\n")
        nxt = AGENTS.index(agent) + 1
        if decision[0] == "GO" and nxt < len(AGENTS):
            out.write(
                f"Next: do the work, write what happened in {venture.path / 'notes.md'}, "
                f"then run: paid-or-pivot {AGENTS[nxt].key} {venture.slug}\n"
            )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
