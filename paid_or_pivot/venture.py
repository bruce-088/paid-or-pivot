"""A venture is a folder of plain files: the idea, the founder's notes, and
one deliverable per phase. Anyone (or any other tool) can read and edit them.

ventures/<slug>/
  idea.md          the idea, written once when the venture is created
  bbuilder.json    the original bbuilder brief, when the idea came from there
  notes.md         what actually happened; the founder edits this
  01-research.md   ... one file per phase, written by the agents
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from paid_or_pivot.agents import AGENTS, Agent

DECISION_RE = re.compile(r"^\s*\**DECISION:?\**\s*:?\s*(GO|NO-GO|PIVOT)\b(.*)$", re.IGNORECASE | re.MULTILINE)

NOTES_TEMPLATE = """# Notes

Write down what actually happens: tests run, messages sent, replies, calls,
payments, objections, numbers. Every agent reads this file before it works,
so the more real results it holds, the better the next phase gets.

"""


class VentureError(Exception):
    pass


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60].strip("-") or "venture"


def parse_decision(text: str) -> tuple[str, str] | None:
    """Return (GO|NO-GO|PIVOT, reason) from the last DECISION line, if any."""
    matches = DECISION_RE.findall(text or "")
    if not matches:
        return None
    verdict, reason = matches[-1]
    return verdict.upper(), reason.strip(" -—:").strip()


def idea_from_bbuilder(brief: dict) -> tuple[str, str]:
    """Turn a bbuilder Polsia company brief (JSON) into (name, idea markdown)."""
    if not isinstance(brief, dict):
        raise VentureError("bbuilder file must contain a JSON object")
    name = str(brief.get("name") or "").strip()
    if not name:
        raise VentureError("bbuilder brief has no name")
    lines = [f"# {name}", ""]
    for label, key in (
        ("Description", "description"),
        ("Target market", "target_market"),
        ("Value proposition", "value_prop"),
        ("Mission", "mission"),
        ("Product type", "product_type"),
        ("Industry", "industry"),
    ):
        value = str(brief.get(key) or "").strip()
        if value:
            lines.append(f"**{label}:** {value}")
    plans = (brief.get("pricing_model") or {}).get("plans") if isinstance(brief.get("pricing_model"), dict) else None
    prices = [
        f"{p.get('name') or 'Plan'} ${p['price']}/mo"
        for p in plans or []
        if isinstance(p, dict) and isinstance(p.get("price"), int | float)
    ]
    if prices:
        lines.append(f"**Pricing idea:** {', '.join(prices)}")
    goals = brief.get("goals")
    if isinstance(goals, dict) and isinstance(goals.get("first_30_days"), list):
        lines.append("**First 30 days (from bbuilder):** " + "; ".join(str(g) for g in goals["first_30_days"]))
    if len(lines) == 2:
        raise VentureError("bbuilder brief has no description to research")
    lines += ["", "_Imported from bbuilder. Its fit score is a keyword heuristic, not research._"]
    return name, "\n".join(lines) + "\n"


class Venture:
    def __init__(self, path: Path):
        self.path = Path(path)

    @property
    def slug(self) -> str:
        return self.path.name

    # ─── creating and finding ──────────────────────────────────────────────

    @classmethod
    def create(cls, root: Path, name: str, idea_markdown: str, bbuilder: dict | None = None) -> Venture:
        path = Path(root) / slugify(name)
        if path.exists():
            raise VentureError(f"A venture named '{path.name}' already exists in {root}")
        path.mkdir(parents=True)
        (path / "idea.md").write_text(idea_markdown, encoding="utf-8")
        (path / "notes.md").write_text(NOTES_TEMPLATE, encoding="utf-8")
        if bbuilder is not None:
            (path / "bbuilder.json").write_text(json.dumps(bbuilder, indent=2), encoding="utf-8")
        return cls(path)

    @classmethod
    def from_text(cls, root: Path, text: str) -> Venture:
        text = text.strip()
        if not text:
            raise VentureError("The idea is empty")
        first_line = text.splitlines()[0]
        name = " ".join(first_line.split()[:8])
        return cls.create(root, name, f"# Idea\n\n{text}\n")

    @classmethod
    def from_bbuilder_file(cls, root: Path, file: Path) -> Venture:
        try:
            brief = json.loads(Path(file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VentureError(f"Couldn't read bbuilder file {file}: {exc}") from exc
        name, idea = idea_from_bbuilder(brief)
        return cls.create(root, name, idea, bbuilder=brief)

    @classmethod
    def load(cls, root: Path, slug: str) -> Venture:
        path = Path(root) / slug
        if not (path / "idea.md").is_file():
            raise VentureError(f"No venture '{slug}' in {root}")
        return cls(path)

    @staticmethod
    def all(root: Path) -> list[Venture]:
        root = Path(root)
        if not root.is_dir():
            return []
        return [Venture(p) for p in sorted(root.iterdir()) if (p / "idea.md").is_file()]

    # ─── reading ───────────────────────────────────────────────────────────

    def read(self, name: str) -> str:
        file = self.path / name
        return file.read_text(encoding="utf-8") if file.is_file() else ""

    def output(self, agent: Agent) -> str:
        return self.read(agent.output_file)

    def decision(self, agent: Agent) -> tuple[str, str] | None:
        return parse_decision(self.output(agent))

    def context_for(self, agent: Agent) -> str:
        """Everything the agent should read: the idea, the founder's notes and
        every earlier phase's deliverable."""
        parts = [f"# Venture: {self.slug}", "", "## The idea", self.read("idea.md").strip()]
        notes = self.read("notes.md").replace(NOTES_TEMPLATE, "").strip()
        parts += ["", "## Founder's notes (ground truth)", notes or "_No notes yet: nothing has been tried._"]
        for earlier in AGENTS[: AGENTS.index(agent)]:
            text = self.output(earlier).strip()
            if text:
                parts += ["", f"## Earlier phase: {earlier.name}", text]
        parts += ["", f"Now do the {agent.name} phase."]
        return "\n".join(parts)

    def write_output(self, agent: Agent, text: str) -> Path:
        file = self.path / agent.output_file
        if file.exists():
            history = self.path / "history"
            history.mkdir(exist_ok=True)
            n = len(list(history.glob(f"{file.stem}-*.md"))) + 1
            file.rename(history / f"{file.stem}-{n}.md")
        file.write_text(text, encoding="utf-8")
        return file
