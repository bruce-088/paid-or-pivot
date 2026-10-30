"""A fake Claude client that streams canned answers, so tests never call the API."""
from types import SimpleNamespace

import pytest


def text_block(text, citations=None):
    return SimpleNamespace(type="text", text=text, citations=citations or [])


def citation(url, title):
    return SimpleNamespace(type="web_search_result_location", url=url, title=title, cited_text="...")


def message(blocks, stop_reason="end_turn", searches=0):
    usage = SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=searches))
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage)


class _Stream:
    def __init__(self, msg):
        self._msg = msg
        self.text_stream = iter([b.text for b in msg.content if b.type == "text"])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class FakeClient:
    """Returns the queued messages in order and records every request."""

    def __init__(self, *messages):
        self._queue = list(messages)
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        return _Stream(self._queue.pop(0))


@pytest.fixture
def fake():
    return FakeClient


@pytest.fixture(autouse=True)
def no_real_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PAID_OR_PIVOT_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's own .env is never read
