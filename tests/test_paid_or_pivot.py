import io
import os
import json

import pytest
from conftest import citation, message, text_block

from paid_or_pivot import cli, runner
from paid_or_pivot.agents import AGENTS, BY_KEY
from paid_or_pivot.venture import Venture, VentureError, idea_from_bbuilder, parse_decision, slugify

REPORT = "## Verdict\nLooks promising.\n\nDECISION: GO — venues already pay for slower tools"

BRIEF = {
    "name": "Venue Inquiry Desk",
    "description": "Answers event-venue inquiries within minutes and follows up on stalled proposals.",
    "target_market": "Owner-run U.S. wedding and event venues",
    "value_prop": "Book more tours without hiring",
    "pricing_model": {"plans": [{"name": "Standard", "price": 450}]},
    "goals": {"first_30_days": ["Launch landing page", "Get 3 paying venues"]},
    "fit": {"score": 81},
    "source": "bbuilder-saved-idea",
}


# ─── agents and prompts ──────────────────────────────────────────────────────


def test_four_agents_in_order_with_prompts():
    assert [a.key for a in AGENTS] == ["research", "launch", "marketing", "scaling"]
    for agent in AGENTS:
        assert "first paying customer" in agent.prompt
        assert "DECISION:" in agent.prompt
        assert agent.name.split()[0] in agent.prompt


# ─── venture files ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DECISION: GO — strong pain", ("GO", "strong pain")),
        ("blah\n**DECISION:** no-go - nobody pays", ("NO-GO", "nobody pays")),
        ("DECISION: PIVOT — sell to agencies\nmore\nDECISION: GO — later line wins", ("GO", "later line wins")),
        ("no decision here", None),
    ],
)
def test_parse_decision(text, expected):
    assert parse_decision(text) == expected


def test_slugify():
    assert slugify("AI Review Replies for Salons!") == "ai-review-replies-for-salons"
    assert slugify("???") == "venture"


def test_create_from_text_and_context(tmp_path):
    venture = Venture.from_text(tmp_path, "Weekly grant matches for small nonprofits, $99/mo")
    assert venture.slug == "weekly-grant-matches-for-small-nonprofits-99-mo"
    assert "No notes yet" in venture.context_for(BY_KEY["research"])
    with pytest.raises(VentureError):
        Venture.from_text(tmp_path, "Weekly grant matches for small nonprofits, $99/mo")
    with pytest.raises(VentureError):
        Venture.from_text(tmp_path, "   ")


def test_bbuilder_import(tmp_path):
    file = tmp_path / "brief.json"
    file.write_text(json.dumps(BRIEF))
    venture = Venture.from_bbuilder_file(tmp_path / "ventures", file)
    assert venture.slug == "venue-inquiry-desk"
    idea = venture.read("idea.md")
    assert "Owner-run U.S. wedding" in idea
    assert "Standard $450/mo" in idea
    assert "Get 3 paying venues" in idea
    assert json.loads(venture.read("bbuilder.json"))["fit"]["score"] == 81


@pytest.mark.parametrize("brief", [{"description": "x"}, {"name": "Empty"}, ["not", "a", "dict"]])
def test_bbuilder_rejects_unusable_briefs(brief):
    with pytest.raises(VentureError):
        idea_from_bbuilder(brief)


def test_context_includes_notes_and_earlier_phases(tmp_path):
    venture = Venture.from_text(tmp_path, "Podcast repurposing desk")
    (venture.path / "notes.md").write_text(venture.read("notes.md") + "Sent 40 emails, 2 paid $249.\n")
    venture.write_output(BY_KEY["research"], REPORT)
    context = venture.context_for(BY_KEY["launch"])
    assert "Sent 40 emails, 2 paid $249." in context
    assert "Earlier phase: Research & Validation" in context
    assert "Earlier phase: Launch" not in context


def test_rerun_keeps_previous_output_in_history(tmp_path):
    venture = Venture.from_text(tmp_path, "Etsy listing optimizer")
    venture.write_output(BY_KEY["research"], "first")
    venture.write_output(BY_KEY["research"], "second")
    assert venture.output(BY_KEY["research"]) == "second"
    assert (venture.path / "history" / "01-research-1.md").read_text() == "first"


# ─── runner ──────────────────────────────────────────────────────────────────


def test_run_agent_saves_answer_with_sources(tmp_path, fake):
    venture = Venture.from_text(tmp_path, "Venue inquiry desk")
    client = fake(
        message(
            [
                text_block("## Verdict\nVenues pay $99–299/mo already.", [citation("https://capterra.com/pv", "Perfect Venue")]),
                text_block("\n\nDECISION: GO — buyers already pay", [citation("https://capterra.com/pv", "Perfect Venue")]),
            ],
            searches=4,
        )
    )
    out = io.StringIO()
    markdown, info = runner.run_agent(venture, BY_KEY["research"], client=client, out=out)

    request = client.requests[0]
    assert request["model"] == runner.DEFAULT_MODEL
    assert request["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 12}]
    assert "Research & Validation agent" in request["system"]
    assert "Venue inquiry desk" in request["messages"][0]["content"]
    assert "temperature" not in request
    assert "Venues pay" in out.getvalue()  # streamed live
    assert markdown == venture.output(BY_KEY["research"])
    assert markdown.count("https://capterra.com/pv") == 1
    assert info == {"decision": ("GO", "buyers already pay"), "sources": 1, "searches": 4, "stop_reason": "end_turn", "engine": "api"}


def test_run_agent_continues_after_pause_turn(tmp_path, fake):
    venture = Venture.from_text(tmp_path, "Grant match weekly")
    first = text_block("Searching grant databases...")
    client = fake(
        message([first], stop_reason="pause_turn", searches=5),
        message([text_block(" Done.\n\nDECISION: PIVOT — sell to grant consultants")], searches=3),
    )
    markdown, info = runner.run_agent(venture, BY_KEY["research"], client=client, out=io.StringIO())
    assert len(client.requests) == 2
    resumed = client.requests[1]["messages"]
    assert resumed[1] == {"role": "assistant", "content": [first]}
    assert client.requests[1]["tools"] == client.requests[0]["tools"]
    assert "Searching grant databases... Done." in markdown
    assert info["decision"][0] == "PIVOT"
    assert info["searches"] == 8


def test_run_agent_flags_truncated_answers(tmp_path, fake):
    venture = Venture.from_text(tmp_path, "Bid digest")
    client = fake(message([text_block("## Verdict\nPartial")], stop_reason="max_tokens"))
    markdown, info = runner.run_agent(venture, BY_KEY["research"], client=client, out=io.StringIO())
    assert "hit the length limit" in markdown
    assert info["decision"] is None


def test_model_override_from_env(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("PAID_OR_PIVOT_MODEL", "claude-opus-5-5")
    venture = Venture.from_text(tmp_path, "Review reply desk")
    client = fake(message([text_block(REPORT)]))
    runner.run_agent(venture, BY_KEY["research"], client=client, out=io.StringIO())
    assert client.requests[0]["model"] == "claude-opus-5-5"


def test_missing_api_key_gives_a_clear_message():
    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        runner.make_client()


# ─── command line ────────────────────────────────────────────────────────────


def run_cli(args, client=None):
    out = io.StringIO()
    code = cli.main(args, client=client, out=out)
    return code, out.getvalue()


def test_cli_full_path_research_to_launch(fake):
    code, output = run_cli(["research", "Venue inquiry desk for event venues"], fake(message([text_block(REPORT)])))
    assert code == 0
    assert "Decision: GO" in output
    assert "paid-or-pivot launch venue-inquiry-desk-for-event-venues" in output

    launch = "## The offer\n...\n\nDECISION: GO — ready to sell"
    code, output = run_cli(["launch", "venue-inquiry-desk-for-event-venues"], fake(message([text_block(launch)])))
    assert code == 0
    assert "Next:" in output and "paid-or-pivot marketing" in output

    code, output = run_cli(["status"])
    assert "Research & Validation  GO — venues already pay for slower tools" in output
    assert "Launch                 GO — ready to sell" in output
    assert "Marketing              not run" in output


def test_cli_blocks_next_phase_unless_previous_said_go(fake):
    run_cli(["research", "Job tailor"], fake(message([text_block("DECISION: NO-GO — crowded")])))
    code, output = run_cli(["launch", "job-tailor"], fake())
    assert code == 1
    assert "said NO-GO" in output and "--force" in output

    code, output = run_cli(["launch", "job-tailor", "--force"], fake(message([text_block("DECISION: GO — ok")])))
    assert code == 0


def test_cli_requires_previous_phase_and_known_venture(fake):
    Venture.from_text(cli.Path("ventures"), "Ad intel brief")
    code, output = run_cli(["marketing", "ad-intel-brief"], fake())
    assert code == 1 and "Run the Launch phase first" in output
    code, output = run_cli(["launch", "nope"], fake())
    assert code == 1 and "No venture 'nope'" in output


def test_cli_research_from_bbuilder_and_rerun(fake, tmp_path):
    file = tmp_path / "brief.json"
    file.write_text(json.dumps(BRIEF))
    code, _ = run_cli(["research", "--bbuilder", str(file)], fake(message([text_block(REPORT)])))
    assert code == 0
    code, _ = run_cli(["research", "venue-inquiry-desk"], fake(message([text_block("DECISION: NO-GO — changed")])))
    venture = Venture.load(cli.Path("ventures"), "venue-inquiry-desk")
    assert venture.decision(BY_KEY["research"])[0] == "NO-GO"
    assert (venture.path / "history" / "01-research-1.md").exists()


def test_dotenv_does_not_override_shell(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY='from-file'\n# comment\nPAID_OR_PIVOT_MODEL=claude-haiku-5-5\n")
    monkeypatch.setenv("PAID_OR_PIVOT_MODEL", "from-shell")
    cli.load_dotenv(tmp_path / ".env")
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "from-file"
    assert os.environ["PAID_OR_PIVOT_MODEL"] == "from-shell"


# ─── Claude Code engine (subscription) ───────────────────────────────────────


def cc_events(*texts, searches=(), result="ok", subtype="success", is_error=False, deltas=True):
    """Canned `claude -p --output-format stream-json` lines."""
    lines = [{"type": "system", "subtype": "init"}]
    for query in searches:
        lines.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "WebSearch", "input": {"query": query}}]}})
        lines.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "Links: ..."}]}})
    for text in texts:
        if deltas:
            lines.append({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}})
        lines.append({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    lines.append({"type": "result", "subtype": subtype, "is_error": is_error, "result": result, "stop_reason": "end_turn"})
    return [json.dumps(line) + "\n" for line in lines] + ["not json\n"]


@pytest.fixture
def spawn(monkeypatch):
    calls = []

    def install(lines):
        def fake(cmd, prompt, env, cwd):
            calls.append({"cmd": cmd, "prompt": prompt, "env": env, "cwd": cwd})
            yield from lines

        monkeypatch.setattr(runner, "_spawn_claude", fake)
        return calls

    return install


def test_auto_engine_picks_api_key_then_claude_code(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert runner.choose_engine() == "claude-code"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert runner.choose_engine() == "api"
    assert runner.choose_engine("claude-code") == "claude-code"
    monkeypatch.setenv("PAID_OR_PIVOT_ENGINE", "claude-code")
    assert runner.choose_engine() == "claude-code"


def test_no_engine_available_explains_both_options(monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)
    with pytest.raises(VentureError, match="Claude Code.*ANTHROPIC_API_KEY"):
        runner.choose_engine()
    with pytest.raises(VentureError, match="Unknown engine"):
        runner.choose_engine("gpt")


def test_claude_code_engine_runs_report_through_subscription(tmp_path, spawn, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    venture = Venture.from_text(tmp_path, "Venue inquiry desk")
    calls = spawn(cc_events(
        "## Verdict\nVenues pay [Perfect Venue](https://capterra.com/pv) $99–299/mo.",
        " Also [Perfect Venue](https://capterra.com/pv) and [Hyperleap](https://hyperleap.ai/x).\n\nDECISION: GO — buyers already pay",
        searches=["venue booking software pricing", "venue inquiry response time"],
    ))
    out = io.StringIO()
    markdown, info = runner.run_agent(venture, BY_KEY["research"], out=out, engine="claude-code")

    call = calls[0]
    cmd = call["cmd"]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--model") + 1] == runner.DEFAULT_MODEL
    system = cmd[cmd.index("--system-prompt") + 1]
    assert "Research & Validation agent" in system and "about 12 web searches" in system
    assert "Bash" not in " ".join(cmd)
    assert "ANTHROPIC_API_KEY" not in call["env"]  # forces the subscription login
    assert "Venue inquiry desk" in call["prompt"]
    assert not call["cwd"].startswith(str(tmp_path))

    shown = out.getvalue()
    assert "[searching: venue booking software pricing]" in shown
    assert shown.count("## Verdict") == 1  # streamed once, not doubled
    assert "via Claude Code (subscription)" in markdown
    assert markdown.count("- [Perfect Venue](https://capterra.com/pv)") == 1
    assert "- [Hyperleap](https://hyperleap.ai/x)" in markdown
    assert info == {"decision": ("GO", "buyers already pay"), "sources": 2, "searches": 2, "stop_reason": "end_turn", "engine": "claude-code"}


def test_claude_code_engine_without_partial_messages_still_prints(tmp_path, spawn):
    venture = Venture.from_text(tmp_path, "Grant match")
    spawn(cc_events("## Verdict\nOK\n\nDECISION: NO-GO — tiny budgets", deltas=False))
    out = io.StringIO()
    _, info = runner.run_agent(venture, BY_KEY["research"], out=out, engine="claude-code")
    assert "## Verdict" in out.getvalue()
    assert info["decision"] == ("NO-GO", "tiny budgets")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"subtype": "error_max_turns", "is_error": True, "result": None}, "couldn't finish: error_max_turns"),
        ({"is_error": True, "result": "Not logged in · Please run /login"}, "Please run /login"),
    ],
)
def test_claude_code_engine_reports_errors(tmp_path, spawn, kwargs, message):
    venture = Venture.from_text(tmp_path, "Bid digest")
    spawn(cc_events("partial", **kwargs))
    with pytest.raises(VentureError, match=message):
        runner.run_agent(venture, BY_KEY["research"], out=io.StringIO(), engine="claude-code")
    assert not venture.output(BY_KEY["research"])  # nothing half-written


def test_claude_code_engine_missing_result(tmp_path, spawn):
    venture = Venture.from_text(tmp_path, "Etsy optimizer")
    spawn(cc_events("text")[:-2])  # drop the result line
    with pytest.raises(VentureError, match="without a result"):
        runner.run_agent(venture, BY_KEY["research"], out=io.StringIO(), engine="claude-code")


def test_cli_engine_flag_reaches_runner(spawn):
    calls = spawn(cc_events("DECISION: GO — fine"))
    code, output = run_cli(["--engine", "claude-code", "research", "Review reply desk"])
    assert code == 0 and calls
    assert "Decision: GO" in output


def test_cli_shows_friendly_error_when_claude_code_fails(spawn):
    spawn(cc_events("x", is_error=True, result="Not logged in · Please run /login"))
    code, output = run_cli(["--engine", "claude-code", "research", "Podcast desk"])
    assert code == 1
    assert "Error: Claude Code couldn't finish: Not logged in" in output


def test_spawn_reports_missing_binary_and_cli_errors(monkeypatch, tmp_path):
    def missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(runner.subprocess, "Popen", missing)
    with pytest.raises(VentureError, match="wasn't found"):
        list(runner._spawn_claude(["claude"], "hi", {}, str(tmp_path)))

    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\ncat > /dev/null\necho '{\"type\":\"system\"}'\necho \"error: unknown option '--tools'\" >&2\nexit 1\n")
    script.chmod(0o755)
    monkeypatch.undo()
    lines = runner._spawn_claude([str(script)], "hi", dict(os.environ), str(tmp_path))
    assert json.loads(next(lines)) == {"type": "system"}
    with pytest.raises(VentureError, match="unknown option.*claude update"):
        list(lines)


def test_links_keep_most_descriptive_title():
    text = "costs [$299/mo](https://g.com/a) ... see [GrantWatch vs Instrumentl](https://g.com/a) and [x](https://h.com)"
    assert runner.links_in(text) == [("GrantWatch vs Instrumentl", "https://g.com/a"), ("x", "https://h.com")]
