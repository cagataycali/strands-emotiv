"""agent.py + cli.py: prompt traceability, turn composition, parser wiring."""

from __future__ import annotations

import pytest

import strands_emotiv.agent as ag
import strands_emotiv.tools as bt
from strands_emotiv.cli import _parser
from strands_emotiv.events import EventEngine
from strands_emotiv.state import BrainState
from strands_emotiv.types import Sample


@pytest.fixture(autouse=True)
def fresh_singleton(monkeypatch):
    monkeypatch.setattr(bt, "STATE", BrainState())
    monkeypatch.setattr(bt, "ENGINE", EventEngine())
    monkeypatch.setattr(bt, "_stream_task", None)
    monkeypatch.setattr(bt, "_source_obj", None)
    yield


# ------------------------------------------------------------------ prompt
def test_system_prompt_content():
    p = ag.SYSTEM_PROMPT
    # ambient behaviors
    assert "Never mention the brain data unless" in p
    assert "one next step" in p and "focus dropped" in p.lower()
    # refusals
    assert "your focus metric fell" in p and "you're bored" in p
    assert "clench" in p
    # L1/L3 tool guidance
    assert "wait_for_brain_event" in p and "mental_approval" in p


def test_compose_turn_prepends_ambient():
    turn = ag.compose_turn("hello")
    assert turn.startswith("[brain: ") and turn.endswith("\n\nhello")


def test_compose_turn_with_live_state():
    bt.feed(Sample(stream="dev", time=1.0,
                   data={"Battery": 80, **{ch: 4 for ch in ("AF3", "AF4", "O1", "O2")}}))
    bt.feed(Sample(stream="met", time=2.0, data={"foc": 0.8, "str": 0.1}))
    turn = ag.compose_turn("hi")
    assert "focus 0.80" in turn and "stress 0.10" in turn


# ------------------------------------------------------------------ marker
class SpySource:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.marks: list[tuple[str, object]] = []

    async def inject_marker(self, label, value):
        if self.fail:
            raise RuntimeError("no session")
        self.marks.append((label, value))


async def test_mark_lands_on_live_source(monkeypatch):
    spy = SpySource()
    monkeypatch.setattr(bt, "_source_obj", spy)
    assert await ag.mark("agent_turn_start") is True
    assert spy.marks == [("agent_turn_start", 1)]


async def test_mark_is_best_effort(monkeypatch):
    assert await ag.mark("x") is False  # no source at all
    monkeypatch.setattr(bt, "_source_obj", SpySource(fail=True))
    assert await ag.mark("x") is False  # source raises → swallowed


async def test_ask_closes_the_loop(monkeypatch):
    spy = SpySource()
    monkeypatch.setattr(bt, "_source_obj", spy)

    class FakeAgent:
        async def invoke_async(self, msg):
            assert msg.startswith("[brain: ")
            return "short answer"

    out = await ag.ask(FakeAgent(), "what now?")
    assert out == "short answer"
    assert [m[0] for m in spy.marks] == ["agent_turn_start", "agent_turn_end"]
    assert spy.marks[1][1] == len("short answer")


def test_build_agent_wears_the_brain_tools():
    agent = ag.build_agent(model="test-model")
    names = set(agent.tool_names)
    assert {"brain_snapshot", "wait_for_brain_event", "mental_approval"} <= names


# --------------------------------------------------------------------- cli
def test_parser_subcommands():
    p = _parser()
    a = p.parse_args(["doctor", "--json"])
    assert a.cmd == "doctor" and a.json is True
    a = p.parse_args(["dashboard", "--port", "9000"])
    assert a.port == 9000
    a = p.parse_args(["agent", "--fake"])
    assert a.fake is True
    a = p.parse_args(["capture", "-s", "5", "-o", "x.jsonl"])
    assert a.seconds == 5.0 and a.out == "x.jsonl"


def test_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        _parser().parse_args([])


def test_record_parser_and_no_relay(capsys):
    """`record ambient` exists (the docs promise it) and fails honestly without a relay."""
    from strands_emotiv.cli import _parser, main

    args = _parser().parse_args(["record", "ambient", "--minutes", "5", "--name", "n"])
    assert (args.cmd, args.kind, args.minutes, args.name) == ("record", "ambient", 5.0, "n")
    rc = main(["record", "ambient", "--minutes", "0.01", "--relay", "http://127.0.0.1:9"])
    assert rc == 1
    assert "run `strands-emotiv dashboard` first" in capsys.readouterr().err
