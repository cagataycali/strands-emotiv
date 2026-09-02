<p align="center"><img src="https://cagataycali.github.io/strands-emotiv/img/logo.svg" alt="strands-emotiv" width="110"></p>
<h1 align="center">strands-emotiv</h1>
<p align="center"><b>An agent that can feel the person it's talking to.</b><br>
EPOC X → Cortex → <a href="https://github.com/strands-agents">Strands</a> tools · live brain dashboard · LeRobot v3 datasets.<br>
<a href="https://cagataycali.github.io/strands-emotiv/">docs</a> · <a href="https://github.com/cagataycali/strands-emotiv/blob/main/SOUL.md">soul</a> · <a href="https://huggingface.co/datasets/cagataydev/emotiv-ecot">dataset</a></p>

<p align="center"><a href="https://www.youtube.com/watch?v=eGYnLK_RREM"><img src="https://cagataycali.github.io/strands-emotiv/img/video-thumb.jpg" width="100%" alt="▶ I put my brain on a dashboard and gave my AI agent a nervous system"></a><br>
<sub>▶ <a href="https://www.youtube.com/watch?v=eGYnLK_RREM">how it works</a></sub></p>

> 2019: *how do I control X with my mind?* Blink = click. A slower keyboard.
> 2026: **what does an agent become when it can feel you?**

It notices when you tense up, go quiet, or light up, and changes how it talks.

## Run

```bash
pip install strands-emotiv            # or: uv tool install strands-emotiv
export EMOTIV_CLIENT_ID=… EMOTIV_CLIENT_SECRET=…   # free Basic license
strands-emotiv doctor                 # Cortex ✓ headset ✓ streams ✓
strands-emotiv dashboard              # → localhost:8765
```

Use the **USB dongle**; the cable only charges. No headset: `--fake` replays 865 real samples.

## What the agent sees

One line per turn. The dashboard shows the exact same line.

```
[brain: focus 0.71↑ · stress 0.22↓ · alpha dominant O1/O2 · still 38s · CQ 13/14 good]
```

| you | it |
|---|---|
| stress ↑, build failed | 3 lines, 1 next step, no questions |
| alpha up, still 40 s | stays quiet |
| CQ < 10/14 | *"I can't see your brain right now"* |

<p align="center"><img src="https://cagataycali.github.io/strands-emotiv/img/anim/cq-gate.svg" width="100%" alt="14 electrode head map: all green says CQ 14/14, T7 dries and the line keeps talking at 13/14, six dry crosses the 10/14 gate and the line becomes the refusal. Absent is not zero."></p>

**Three speeds, never mixed:** reflex ≤100 ms (blink · clench = veto) · ambient ~10 s (the line) · intent opt-in (one trained *yes*).

<p align="center"><img src="https://cagataycali.github.io/strands-emotiv/img/anim/three-speeds.svg" width="100%" alt="Three speeds replayed from 10.53 s of real EPOC X signal: reflex blinks at 0.03 s, 1.78 s and a held wink at 5.25 s; the ambient line emitted at the 9.96 s metrics tick; the intent lane stays dormant"></p>

## Dashboard

<p align="center"><img src="https://cagataycali.github.io/strands-emotiv/img/dashboard.jpg" width="100%"></p>

**Left = you**: skull with 14 live electrodes, 5 band topomaps, 10-min waterfall, head trail.
**Right = the agent**: the line it reads, event river, metric radar, sonification, consent.
**Bottom**: streaming chat. Under each message, the line it saw when you sent it.

## The dataset

<p align="center"><a href="https://www.youtube.com/watch?v=X7RvAoHTpAo"><img src="https://cagataycali.github.io/strands-emotiv/img/topomaps-waterfall.jpg" width="100%" alt="▶ watch an ECoT episode get recorded live"></a><br>
<sub>▶ <a href="https://www.youtube.com/watch?v=X7RvAoHTpAo">1 min: recording an episode on the dashboard</a></sub></p>

[Embodied chain-of-thought](https://arxiv.org/abs/2407.08693), brain edition: **observation** = band power · CQ · motion · metrics · facial · events @ 8 Hz. **action** = what the agent said. **reward** = your brain after the answer (Δstress, Δengagement across the turn boundary). **LeRobot v3.0**, trains with stock tooling.

<p align="center"><img src="https://cagataycali.github.io/strands-emotiv/img/anim/one-turn.svg" width="100%" alt="One recorded turn replayed verbatim: AF3 band power flows in (theta dominant), the ambient line assembles, the agent answers, the action is stamped into the episode as an amber frame, the post-roll waits for the next met tick, and REWARD backfills as nan because none arrived in time. Absent is not zero"></p>

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset
ds = LeRobotDataset("cagataydev/emotiv-ecot")        # private
ds[80]["observation.state"].shape                    # [70]  14 ch × θ α βL βH γ
ds[80]["task"]
# TASK: One sentence: how does my brain look right now? | AMBIENT: [brain: theta dominant · CQ 13/14 good]
# | PLAN: Calm and settled… | TOOL: none | ACT: Calm and settled… | REWARD: Δstress=…
```

That's a real frame from the first live episode. **● REC** in the header → talk → **Publish**.

## Tools

```python
from strands_emotiv.tools import brain_snapshot, brain_bands, head_pose, contact_quality, wait_for_brain_event
agent("wait until I blink twice, then tell me what my alpha did")
```

Every agent action stamps the EEG record (`injectMarker`). The loop closes both ways.

## MCP

Any MCP client can feel the person wearing the headset. Claude Desktop, Cursor, Kiro, another agent:

```json
{ "mcpServers": { "brain": { "command": "uvx", "args": ["strands-emotiv", "mcp"] } } }
```

13 tools: `brain_line` (the one line), `brain_snapshot`, `brain_bands`, `head_pose`, `contact_quality`, `wait_for_brain_event`, `recent_brain_events`, `brain_status`, `mental_approval`, and the four `record_*` tools. Add `--http 8000` for streamable HTTP at `/mcp`, `--fake` to try it without a headset. Built on [strands-mcp-server](https://github.com/cagataycali/strands-mcp-server).

<p align="center"><sub>MIT · Cagatay Cali · 2019 → 2026</sub></p>
