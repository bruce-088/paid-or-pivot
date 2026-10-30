"""The four agents, one per phase, in the order an idea moves through them."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    output_file: str
    max_searches: int

    @property
    def prompt(self) -> str:
        return _read_prompt("shared.md") + "\n\n" + _read_prompt(f"{self.key}.md")


def _read_prompt(name: str) -> str:
    return resources.files("paid_or_pivot.prompts").joinpath(name).read_text(encoding="utf-8").strip()


AGENTS: tuple[Agent, ...] = (
    Agent("research", "Research & Validation", "01-research.md", max_searches=12),
    Agent("launch", "Launch", "02-launch.md", max_searches=6),
    Agent("marketing", "Marketing", "03-marketing.md", max_searches=8),
    Agent("scaling", "Scaling", "04-scaling.md", max_searches=6),
)

BY_KEY = {agent.key: agent for agent in AGENTS}


def previous(agent: Agent) -> Agent | None:
    index = AGENTS.index(agent)
    return AGENTS[index - 1] if index > 0 else None
