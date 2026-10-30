"""Runs one agent: sends the venture's files to Claude with web search,
streams the answer to the terminal, and saves it with its sources.

Two engines do the same job:
- "api": the Anthropic API with your ANTHROPIC_API_KEY (pay per use).
- "claude-code": the Claude Code CLI, signed in with your own Claude
  subscription (usage counts against your plan's limits). For personal use.
"auto" (the default) uses the API when a key is set, otherwise Claude Code.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from datetime import date
from typing import TextIO

from paid_or_pivot.agents import Agent
from paid_or_pivot.venture import Venture, VentureError, parse_decision

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 32000  # thinking counts toward this; streaming avoids timeouts
MAX_CONTINUATIONS = 5
ENGINES = ("auto", "api", "claude-code")
LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")

CLAUDE_CODE_NOTE = """

When using the web:
- You have a budget of about {max_searches} web searches. Spend them on what changes the decision.
- Cite every source inline as a markdown link, like [Perfect Venue pricing](https://example.com/pricing).
- Don't add your own sources list; one is built from your links automatically. The DECISION line stays last."""


class RunnerError(VentureError):
    """The engine couldn't produce a report."""


def make_client():
    """The real Anthropic client. Imported lazily so tests don't need the SDK."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY (in your shell or a .env file) before running an agent.")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("Install the dependencies first: pip install -e .") from exc
    return anthropic.Anthropic()


def choose_engine(requested: str | None = None, client=None) -> str:
    engine = requested or os.environ.get("PAID_OR_PIVOT_ENGINE") or "auto"
    if engine not in ENGINES:
        raise RunnerError(f"Unknown engine '{engine}'. Use one of: {', '.join(ENGINES)}")
    if engine != "auto":
        return engine
    if client is not None or os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "claude-code"
    raise RunnerError(
        "No way to reach Claude yet. Either install Claude Code and sign in with your subscription "
        "(https://claude.com/claude-code, then run `claude` once), or set ANTHROPIC_API_KEY in .env."
    )


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


def links_in(text: str) -> list[tuple[str, str]]:
    """Unique (title, url) pairs from markdown links in the report. When a URL
    is linked more than once, keep its most descriptive (longest) link text."""
    seen: dict[str, str] = {}
    for title, url in LINK_RE.findall(text or ""):
        if len(title) > len(seen.get(url, "")):
            seen[url] = title
    return [(title, url) for url, title in seen.items()]


# ─── engine: Anthropic API ───────────────────────────────────────────────────


def _run_api(venture: Venture, agent: Agent, client, model: str, out: TextIO) -> dict:
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

    notes = []
    if stop_reason == "max_tokens":
        notes.append("the answer hit the length limit and may be cut off. Run this phase again.")
    elif stop_reason == "pause_turn":
        notes.append("the research paused too many times and may be incomplete. Run this phase again.")
    return {
        "body": "".join(_field(b, "text", "") for b in blocks if _field(b, "type") == "text").strip(),
        "sources": collect_sources(blocks),
        "searches": searches,
        "stop_reason": stop_reason,
        "notes": notes,
        "label": model,
    }


# ─── engine: Claude Code CLI (your subscription) ─────────────────────────────


def claude_code_command(agent: Agent, model: str) -> list[str]:
    return [
        "claude",
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--system-prompt", agent.prompt + CLAUDE_CODE_NOTE.format(max_searches=agent.max_searches),
        "--tools", "WebSearch,WebFetch",
        "--allowedTools", "WebSearch", "WebFetch",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]


def _spawn_claude(cmd: list[str], prompt: str, env: dict, cwd: str) -> Iterator[str]:
    """Run the CLI with the prompt on stdin and yield its stdout lines."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError as exc:
        raise RunnerError("The `claude` command wasn't found. Install Claude Code and sign in first.") from exc
    assert proc.stdin and proc.stdout and proc.stderr
    proc.stdin.write(prompt)
    proc.stdin.close()
    yield from proc.stdout
    stderr = proc.stderr.read()
    if proc.wait() != 0:
        detail = stderr.strip().splitlines()[-3:] if stderr.strip() else []
        hint = " Try updating Claude Code (`claude update`)." if "unknown option" in stderr else ""
        raise RunnerError(f"Claude Code exited with an error: {' '.join(detail) or 'no details'}.{hint}")


def _run_claude_code(venture: Venture, agent: Agent, model: str, out: TextIO) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # use the subscription login
    texts: list[str] = []
    searches = 0
    streamed = False
    result: dict | None = None
    # Run outside this repo so Claude Code doesn't load the developer CLAUDE.md.
    with tempfile.TemporaryDirectory(prefix="paid-or-pivot-") as cwd:
        for line in _spawn_claude(claude_code_command(agent, model), venture.context_for(agent), env, cwd):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta":
                    streamed = True
                    out.write(delta.get("text", ""))
                    out.flush()
            elif kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                        if not streamed:
                            out.write(block.get("text", ""))
                            out.flush()
                    elif block.get("type") == "tool_use" and block.get("name") == "WebSearch":
                        searches += 1
                        query = (block.get("input") or {}).get("query", "")
                        out.write(f"\n  [searching: {query}]\n")
                        out.flush()
            elif kind == "result":
                result = event

    if result is None:
        raise RunnerError("Claude Code finished without a result. Run the phase again.")
    if result.get("is_error") or result.get("subtype") != "success":
        message = result.get("result") or result.get("subtype") or "unknown error"
        raise RunnerError(f"Claude Code couldn't finish: {message}")
    body = "".join(texts).strip() or (result.get("result") or "").strip()
    if not body:
        raise RunnerError("Claude Code returned an empty report. Run the phase again.")
    return {
        "body": body,
        "sources": links_in(body),
        "searches": searches,
        "stop_reason": result.get("stop_reason") or "end_turn",
        "notes": [],
        "label": f"{model} via Claude Code (subscription)",
    }


# ─── shared ──────────────────────────────────────────────────────────────────


def run_agent(
    venture: Venture,
    agent: Agent,
    client=None,
    model: str | None = None,
    out: TextIO = sys.stdout,
    engine: str | None = None,
) -> tuple[str, dict]:
    """Run one phase. Returns (saved markdown, info) and writes the file."""
    model = model or os.environ.get("PAID_OR_PIVOT_MODEL") or DEFAULT_MODEL
    engine = choose_engine(engine, client)
    if engine == "api":
        run = _run_api(venture, agent, client or make_client(), model, out)
    else:
        run = _run_claude_code(venture, agent, model, out)
    out.write("\n")

    body, sources = run["body"], run["sources"]
    header = f"<!-- {agent.name} agent · {run['label']} · {date.today().isoformat()} -->\n\n"
    footer = ""
    if sources:
        footer += "\n\n## Sources (from web search)\n\n" + "\n".join(f"- [{t}]({u})" for t, u in sources)
    for note in run["notes"]:
        footer += f"\n\n> Note: {note}"
    markdown = header + body + footer + "\n"
    venture.write_output(agent, markdown)
    return markdown, {
        "decision": parse_decision(body),
        "sources": len(sources),
        "searches": run["searches"],
        "stop_reason": run["stop_reason"],
        "engine": engine,
    }
