# DATASET: ECoT episodes in LeRobot v3.0

The analogy of Embodied Chain-of-Thought reasoning (ECoT, Zawalski et al. 2024) with the robot body replaced by a human cortex in an EPOC X: observation = the brain (band power, metrics, gestures, CQ); action = the agent's speech; reasoning = the per-frame ECoT string; reward = the brain's response (Δstress, Δengagement) after the answer.

## On-disk layout

```
datasets/<name>/
├── meta/
│   ├── info.json                 # codebase_version "v3.0", fps, features, paths
│   ├── stats.json                # global per-feature stats
│   ├── tasks.parquet             # task string → task_index (the ECoT text)
│   └── episodes/chunk-000/file-000.parquet   # per-episode metadata + stats
└── data/chunk-000/file-000.parquet           # frames, all episodes concatenated
```

`save_episode()` dedupes `task` strings into `meta/tasks.parquet` and writes a per-frame `task_index`. Writer contract: `LeRobotDataset.create(repo_id, fps, features, root, robot_type="epoc-x", use_videos=False)`; `add_frame` per tick; `save_episode` per episode; `finalize()` once.

Dependencies: `pyarrow>=15`, `pandas>=2` (`uv sync --extra dataset`); `dataset_v3.py` writes the v3 layout directly, mirroring lerobot's `utils.py`; lerobot is only needed to load. The dataset is valid on disk after every episode.

## Clock

fps = 8, the native rate of `pow`: one frame per 125 ms tick. Slower streams resample last-known-value (`met` slow keys ~10 s, `dev` 2 Hz), faster ones latest-sample (`fac` and `mot`, 32 Hz), events OR-accumulate. `timestamp` = `frame_index / fps`.

## Feature schema

| key | dtype | shape | semantics |
|---|---|---|---|
| `observation.state` | float32 | [70] | band power, channel-major `AF3.theta … AF4.gamma` (14 ch × 5 bands, `pow` order) |
| `observation.contact_quality` | float32 | [14] | per-channel CQ 0 to 4 |
| `observation.motion` | float32 | [10] | `Q0 Q1 Q2 Q3 ACCX ACCY ACCZ MAGX MAGY MAGZ`; zeros when absent |
| `observation.motion_valid` | float32 | [1] | 1.0 if a `mot` sample landed within 0.5 s |
| `observation.metrics` | float32 | [7] | `attention engagement excitement longExcitement stress relaxation interest`; absent → -1 |
| `observation.metrics_valid` | float32 | [7] | per-axis 1/0 validity mask |
| `observation.facial` | float32 | [6] | eye one-hot + `upper_pow` + `lower_pow` |
| `observation.facial_label` | float32 | [2] | vocab indices `[eyeAct, lowerAct]` |
| `observation.events` | float32 | [12] | multi-hot events this tick |
| `action` | float32 | [4] | `[spoke, tool_called, marker_injected, turn_length_tokens_norm]` |
| `task` (string) | | | the per-frame ECoT text |
| `timestamp, frame_index, episode_index, index, task_index` | std | [1] | v3 bookkeeping |

No video keys. Channel order: `AF3 F7 F3 FC5 T7 P7 O1 O2 P8 T8 FC6 F4 F8 AF4`. Bands: `theta alpha betaL betaH gamma` (raw eeg is license-gated on Basic; `pow` is the source). Event slots: `blink wink_left wink_right head_turn_left head_turn_right nod clench smile focus_high focus_low stress_high command`; `double_blink` also sets `blink`; `command:<act>` sets `command`; `look_up`/`look_down` set no bits. Facial vocab (in `info.json` `names`): `eyeAct ∈ {neutral, blink, winkL, winkR}`, `lowerAct ∈ {neutral, smile, clench, frown, laugh, smirkLeft, smirkRight}`; unknown → -1.

## The `task` string

```
TASK: <user question or 'ambient'> | AMBIENT: <ambient line verbatim> | PLAN: <first sentence of reasoning> | TOOL: <name or none> | ACT: <first 200 chars said so far> | REWARD: <Δstress,Δengagement after the turn>
```

Each tick of a streaming turn carries the reply so far, interleaving reasoning with the brain at 8 Hz. `REWARD` is back-filled at `save_episode()`: the first metric sample after the turn minus the last one before it (`met` ticks every ~10 s, so windowed means would see nothing). Idle ticks: `TASK: idle | AMBIENT: <line>`.

## Episode semantics

One episode is one agent turn: a 3 s ring buffer puts the start 3 s before the user message, the end 12 s after the agent finishes, long enough to catch the next `met` sample so REWARD is computable. `strands-emotiv record ambient --minutes N` records idle 30 s baseline episodes (`TASK: idle`).

## Components

`recorder.py` (`on_sample`/`on_event`/`on_agent`) taps the server's Cortex fan-out; `bus.py` feeds it agent turns from `/ask` and `/stream`. `dataset_api.py` exposes `record/start`, `record/stop`, `status`, `episodes`, `export` and `publish` under `/api/dataset/`, passkey-gated; the dashboard REC panel drives these routes. Datasets live under `./datasets/<name>/`, gitignored.

## Loading and pushing

```python
from lerobot.datasets.lerobot_dataset import LeRobotDataset
ds = LeRobotDataset("cagataydev/emotiv-ecot", root="datasets/<name>")  # local
ds = LeRobotDataset("cagataydev/emotiv-ecot")                          # Hub
frame = ds[0]                      # tensors + ECoT string
```

Publish uploads the root to hf.co/datasets/cagataydev/emotiv-ecot (private) and moves the `v3.0` tag to the new head; lerobot refuses Hub datasets without that tag.

## Validation

The full loop ran against a real EPOC X: record, one agent turn, stop, publish, load back from the Hub. One episode: 99 frames @ 8 fps (12.4 s), a real `head_turn_left` in `observation.events[3]` at frame 49, ambient `CQ 13/14` matching one dead channel in `observation.contact_quality` (P7 = 0).

That episode carries `Δstress=nan`, correctly: `met` ticks at 0.1 Hz on the Basic license, so an episode whose post-roll ends before the next sample stays honestly unmeasured. Episodes that catch it get real deltas.
