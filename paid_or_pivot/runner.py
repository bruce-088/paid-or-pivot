"""Runs one agent: sends the venture's files to Claude with web search,
streams the answer to the terminal, and saves it with its sources."""
from __future__ import annotations

import os
import sys
from datetime import date
from typing import TextIO

from paid_or_pivot.agents import Agent
from paid_or_pivot.venture import Venture, parse_decision

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 32000  # thinking counts toward this; streaming avoids timeouts
MAX_CONTINUATIONS = 5


def make_client():
    """The real Anthropic client. Imported lazily so tests don't need the SDK."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY (in your shell or a .env file) before running an agent.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Install the dependencies first: pip install -e .") from exc
    return anthropic.Anthropic()


def _field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def collect_sources(blocks) -> list[tuple[str, str]]:
    """Unique (title, url) pairs from the citations Claude attached to its text."""
    seen: dict[str, str] = {}
    for block in blocks:
        if _field(block, "type") != "text":
            continue
        for citation in _field(block, "citations") or []:
            url = _field(citation, "url")
            if url and url not in seen:
                seen[url] = _field(citation, "title") or url
    return [(title, url) for url, title in seen.items()]


def run_agent(
    venture: Venture,
    agent: Agent,
    client=None,
    model: str | None = None,
    out: TextIO = sys.stdout,
) -> tuple[str, dict]:
    """Run one phase. Returns (saved markdown, info) and writes the file."""
    client = client or make_client()
    model = model or os.environ.get("PAID_OR_PIVOT_MODEL") or DEFAULT_MODEL
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": agent.max_searches}]
    user_message = {"role": "user", "content": venture.context_for(agent)}
    messages = [user_message]
    blocks: list = []
    stop_reason = None
    searches = 0

    for _ in range(MAX_CONTINUATIONS + 1):
        with client.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            system=agent.prompt,
            messages=messages,
            tools=tools,
        ) as stream:
            for text in stream.text_stream:
                out.write(text)
                out.flush()
            message = stream.get_final_message()
        blocks.extend(message.content)
        stop_reason = message.stop_reason
        usage = _field(message, "usage")
        server_use = _field(usage, "server_tool_use") if usage is not None else None
        searches += _field(server_use, "web_search_requests", 0) or 0
        if stop_reason != "pause_turn":
            break
        # A long research turn can pause; send it back so Claude carries on.
        messages = [user_message, {"role": "assistant", "content": list(blocks)}]
    out.write("\n")

    body = "".join(_field(b, "text", "") for b in blocks if _field(b, "type") == "text").strip()
    sources = collect_sources(blocks)
    header = f"<!-- {agent.name} agent · {model} · {date.today().isoformat()} -->\n\n"
    footer = ""
    if sources:
        footer += "\n\n## Sources (from web search)\n\n" + "\n".join(f"- [{t}]({u})" for t, u in sources)
    if stop_reason == "max_tokens":
        footer += "\n\n> Note: the answer hit the length limit and may be cut off. Run this phase again."
    elif stop_reason == "pause_turn":
        footer += "\n\n> Note: the research paused too many times and may be incomplete. Run this phase again."
    markdown = header + body + footer + "\n"
    venture.write_output(agent, markdown)
    return markdown, {
        "decision": parse_decision(body),
        "sources": len(sources),
        "searches": searches,
        "stop_reason": stop_reason,
    }
