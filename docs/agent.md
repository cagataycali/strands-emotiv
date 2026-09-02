# What the agent sees

<span class="ambient">[brain: focus 0.71↑ · stress 0.22↓ · alpha dominant O1/O2 · still 38s · CQ 13/14 good]</span>

`focus` attention (+ delta arrow) · `stress` · dominant band + top 2 channels · seconds still · electrodes at CQ 4.

It never mentions the line unless it matters. It changes behavior:

| you | it |
|---|---|
| stress ↑, task failed | 3 lines, 1 next step, no questions |
| focus dropped mid-answer | one-line summary |
| alpha up, still 40 s | shuts up |
| engagement rising | pulls the thread |
| CQ < 10/14 | normal assistant, says so |

## One turn

```mermaid
sequenceDiagram
  You->>server: POST /api/agent/stream
  server-->>UI: ambient (the line)
  server->>agent: turn
  agent->>agent: brain_bands()
  server-->>UI: tool · delta × N
  Cortex-->>server: blink (mid-turn)
  server-->>UI: event
  agent->>Cortex: injectMarker
  server-->>UI: done
```

Every action stamps the EEG record → [the dataset](ecot.md).

## Consent, not steering

<div class="consent-hud" aria-label="mental consent verdicts">
<span class="verdict v-yes">PUSH → YES</span>
<span class="verdict v-no">PULL → NO</span>
<span class="verdict v-veto">CLENCH → VETO · ALWAYS</span>
</div>

![the consent rail: 3 pushes at pow 0.25+ inside a rolling 2.5 s bracket earn a YES; a jaw clench vetoes instantly](img/anim/consent-rail.svg)
