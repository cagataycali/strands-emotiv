# SOUL.md

[mind-controlled-x](https://github.com/cagataycali/mind-controlled-x) (2019) asked how to control a machine with a mind. This project asks what an agent becomes when it can sense the person it talks to.

**The agent has a sense of you the way a friend across the table does.**

## What the headset gives

The EPOC X delivers five bandwidths, not thoughts:

| stream | rate | good for | lies about |
|---|---|---|---|
| `eeg` | 256 Hz | rhythms, blink artifacts (AF3/AF4) | anything semantic |
| `mot` | 32 Hz | deliberate gesture (turn, nod, tilt), ~50 ms, no training | intent |
| `fac` | 32 Hz | buttons: fast, voluntary | involuntary blinks |
| `met` | 2 Hz / ~10 s | engagement, excitement, stress, relaxation, interest, focus | anything instantaneous |
| `com` | ~8 Hz | one trained "go ahead" | steering |
| `dev`/`eq` | 1 to 2 Hz | contact quality, battery | nothing |

The agent never sees a stream without its contact quality: a dry electrode is noise.

## Three layers, three speeds

One loop is too slow for buttons and too twitchy for mood, so there are three.

**Reflex** (`fac` + `mot`, ≤100 ms, no LLM). Double blink confirms, wink says yes, head turn steps previous/next, nod acknowledges, clench always cancels. Debounced, hysteresis on every threshold, one fire per gesture. Lives in `events.py`; the only layer acting without a model.

**Ambient** (`met` + `pow`, ~10 s). Each agent turn gets one line:

```
[brain: focus 0.71↑ · stress 0.22↓ · alpha dominant O1/O2 · still 38s · CQ 13/14 good]
```

The agent mentions it only when relevant, but it shapes tone and length: stress gets shorter answers, a thinking pause gets silence.

**Intent** (`com`, opt-in). One trained command, `push`, meaning "go ahead". Nothing steers with the mind.

## The loop closes both ways

Each agent action stamps the EEG record (`injectMarker`), so the record shows what the brain did in the 2 s after each action. The agent's style gets fitted to one nervous system, measured, not assumed.

## Design rules

- Say "your focus metric fell", not "you're bored".
- Nothing acts on a single sample: a deliberate gesture or 10 s of accumulation.
- The dashboard shows the exact line the agent reads.
- Clench cancels everything at every layer.
- Recordings are local `jsonl`; nothing leaves this machine unless deliberately pushed.
- Below the contact-quality threshold the agent says so and acts as a plain assistant.

## The dashboard

Left: you (scope, band radar, CQ-colored 10-20 head map, head pose, gauges, event ticker). Right: the agent (ambient line, last event, last marker, chat). You watch the agent watching you.
