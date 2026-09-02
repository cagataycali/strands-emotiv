# Three speeds

Three loops at three speeds, kept separate:

<div class="tiers" aria-label="three loops at three speeds">
<div class="tier t-reflex">
  <span class="tier-speed">≤100 ms</span>
  <span class="tier-name">REFLEX</span>
  <span class="tier-src">fac+mot</span>
  <span class="tier-desc">blink · wink · turn · clench · no LLM · clench = veto</span>
</div>
<div class="tier t-ambient">
  <span class="tier-speed">~10 s</span>
  <span class="tier-name">AMBIENT</span>
  <span class="tier-src">met+pow</span>
  <span class="tier-desc">the line: context, never a command</span>
</div>
<div class="tier t-intent">
  <span class="tier-speed">opt-in</span>
  <span class="tier-name">INTENT</span>
  <span class="tier-src">com</span>
  <span class="tier-desc">one trained "push" = yes</span>
</div>
</div>

## Reflex: `events.py`

| event | trigger |
|---|---|
| `blink` / `double_blink` | eye edge · 0.4 s debounce · 0.8 s window |
| `wink_left` / `right` | 0.6 s |
| `clench` | `lPow` · 0.8 s |
| `head_turn_left` / `right` | yaw Δ ≥ 20° |
| `focus_high` / `low` | hysteresis 0.70/0.60 · 0.30/0.40 |
| `stress_high` | 0.70/0.60 |
| `command:<act>` | pow ≥ 0.30 · 1 s |

Fires once per gesture or it's a bug.

## Ambient: `tools.py::ambient_line`
One line per turn. Rendered verbatim on the dashboard so it can't get creepy.

## Intent: `mental.py`
Train `push` from the Consent card. Nothing steers with the mind.
