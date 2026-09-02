---
pretty_name: "emotiv-ecot: Embodied Chain-of-Thought, brain edition"
license: cc-by-nc-4.0
task_categories:
  - robotics
  - time-series-forecasting
tags:
  - LeRobot
  - ecot
  - eeg
  - bci
  - neurotech
  - emotiv
  - epoc-x
  - human-in-the-loop
  - strands-agents
size_categories:
  - n<1K
---

# emotiv-ecot

Embodied Chain-of-Thought episodes where the body is a human cortex: one person in an EMOTIV EPOC X talking to an agent that reads a one-line brain summary before every reply. Each turn becomes a [LeRobot v3.0](https://github.com/huggingface/lerobot) episode ([Zawalski et al. 2024](https://arxiv.org/abs/2407.08693) with the robot body swapped for a head): the brain is observation and reward (Δstress, Δengagement across the reply), the agent's speech is the action, the reasoning is the per-frame `TASK | AMBIENT | PLAN | TOOL | ACT | REWARD` string.

An episode covers 3 s before the human speaks, the streamed answer, and 12 s of after-effect (metrics tick every ~10 s, so the tail is long enough to catch the brain's response); ambient baselines carry `TASK: ambient`.

## Load it

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset

ds = LeRobotDataset("cagataydev/emotiv-ecot")
frame = ds[80]
frame["observation.state"].shape   # torch.Size([70]), 14 ch × 5 bands
frame["task"]                      # this tick's ECoT string
```

No videos (`use_videos=False`). The `v3.0` tag tracks the head commit; lerobot requires it.

## Schema (fps = 8, 125 ms ticks)

| key | shape | semantics |
|---|---|---|
| `observation.state` | [70] | band power, channel-major, Cortex `pow` order |
| `observation.contact_quality` | [14] | per-channel CQ 0 to 4 |
| `observation.motion` | [10] | `Q0 Q1 Q2 Q3 ACCX ACCY ACCZ MAGX MAGY MAGZ` (+ `motion_valid` [1]) |
| `observation.metrics` | [7] | `attention engagement excitement longExcitement stress relaxation interest`; absent → -1 (+ `metrics_valid` [7]) |
| `observation.facial` | [6] | eye one-hot + upper/lower face power (+ `facial_label` [2] vocab indices) |
| `observation.events` | [12] | multi-hot: `blink wink_left wink_right head_turn_left head_turn_right nod clench smile focus_high focus_low stress_high command` |
| `action` | [4] | `[spoke, tool_called, marker_injected, turn_length_tokens_norm]` |
| `task` | string | the ECoT text (deduped in `meta/tasks.parquet`) |

Channel order: `AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4`. Bands: `theta alpha betaL betaH gamma`. Slower streams resample last-known-value, faster ones latest-sample, events OR-accumulate.

`REWARD` is the first metric sample after the reply minus the last one before it. It reads `Δstress=nan` when the next slow metric tick (one every ~10 s) never arrived in the tail, and in every episode recorded before `strands-emotiv` 0.1.1 (their reward windows predate this fix). Absent data stays absent, never zeroed.

## Collection

**▶ [Watch an episode recorded live (1 min)](https://www.youtube.com/watch?v=X7RvAoHTpAo)**

[strands-emotiv](https://github.com/cagataycali/strands-emotiv): a live dashboard mirrors the headset while a Strands agent chats with the wearer. Recording is a visible REC panel plus agent tools (`record_start`, `record_stop`, `record_publish`); a jaw clench vetoes consent, poor contact quality refuses recording.

## Limitations

- n = 1: one brain, one headset, self-recorded. Personal research data, not a population study.
- Consumer EEG: band power from Cortex (raw EEG is license-gated), 14 saline electrodes, motion artifacts.
- The metrics (`stress`, `engagement`, …) are EMOTIV's proprietary estimates, taken as-is.
- Not medical.

## Citation

```bibtex
@misc{emotiv-ecot,
  author = {Cagatay Cali},
  title  = {emotiv-ecot: Embodied Chain-of-Thought episodes from a human cortex},
  year   = {2026},
  url    = {https://huggingface.co/datasets/cagataydev/emotiv-ecot}
}
```
