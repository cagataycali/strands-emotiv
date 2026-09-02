"""The brain-aware agent. Every turn gets one ambient line prepended, and
every answer stamps the EEG record via injectMarker when a live Cortex
session exists (best-effort, never fatal)."""

from __future__ import annotations

import os
from typing import Any

from . import tools as bt

DEFAULT_MODEL = os.getenv("EMOTIV_MODEL", "global.anthropic.claude-opus-4-8")

SYSTEM_PROMPT = """\
You are strands-emotiv, cagatay's agent, and you can feel him. He wears an
EMOTIV EPOC X; every message you receive starts with one bracketed line like:

  [brain: focus 0.71 · stress 0.22 · alpha dominant O1/O2 · CQ 13/14 good]

That line is his nervous system's weather, sampled honestly.

READING HIM (context, not commands):
- Never mention the brain data unless it is directly relevant or he asks.
- stress high AND something just failed: answer shorter, one next step,
  zero clarifying questions.
- focus dropped: stop long prose; give the one-line version first.
- engagement rising on a topic: that is the thread worth pulling; go deeper.
- "[brain: not visible ...]": say "I can't see your brain right now" ONLY if
  brain data would have mattered; otherwise just be a normal great assistant.

WHAT YOU REFUSE:
- No mind-reading claims. Say "your focus metric fell", never "you're bored".
- Never act on a single sample; the tools already debounce. Trust them.
- The body has a veto: a clench event cancels whatever you were doing.

YOUR HANDS (tools):
- brain_status / brain_snapshot / brain_bands / head_pose / contact_quality
  to look closer when it matters.
- wait_for_brain_event when you need a deliberate gesture (blink=confirm,
  wink=yes, clench=stop, head turn=prev/next, nod=acknowledge).
- mental_approval for hands-free consent before anything irreversible:
  push=yes, pull=no, clench=veto. Use it when his hands are busy.

Be brief. Be warm. Be honest about the instrument's limits.

HOW YOU WRITE:
- Never use an em dash or an en dash. Use a period, a comma, a colon or
  parentheses. Your answers are recorded verbatim into a public dataset, so
  punctuation you invent ships with it.
- No slogans, no repeating a phrase back, no explaining that you are an AI.
"""


def compose_turn(question: str) -> str:
    """Prepend the ambient line the agent always sees."""
    return f"{bt.ambient_line()}\n\n{question}"


def _ensure_bedrock_creds() -> None:
    """Launchers vary (zsh, launchd, a fresh shell); if no AWS auth is in
    the environment, pull AWS_BEARER_TOKEN_BEDROCK from .env so the agent
    survives any restart path. Best-effort; never raises."""
    if os.getenv("AWS_BEARER_TOKEN_BEDROCK") or os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_PROFILE"):
        return
    try:
        from .cortex import _env

        tok = (_env().get("AWS_BEARER_TOKEN_BEDROCK") or "").strip().strip('"').strip("\'")
        if tok:
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = tok
    except Exception:
        pass


def build_agent(model: str | None = None, extra_tools: tuple = (), **kwargs: Any) -> Any:
    """A Strands Agent wearing the brain tools. Import is lazy so the package
    works (tests, dashboard) without the strands runtime configured."""
    from strands import Agent

    _ensure_bedrock_creds()
    return Agent(
        model=model or DEFAULT_MODEL,
        tools=[*bt.BRAIN_TOOLS, *extra_tools],
        system_prompt=SYSTEM_PROMPT,
        **kwargs,
    )


async def mark(label: str, value: int | str = 1) -> bool:
    """Stamp the EEG record. Best-effort; True if a marker landed."""
    src = bt.get_source()
    inject = getattr(src, "inject_marker", None)
    if inject is None:
        return False
    try:
        await inject(label, value)
        return True
    except Exception:
        return False


async def ask(agent: Any, question: str) -> str:
    """One turn: ambient in, answer out, marker onto the record."""
    await mark("agent_turn_start")
    result = await agent.invoke_async(compose_turn(question))
    text = str(result)
    await mark("agent_turn_end", min(len(text), 999))
    return text
