---
hide: [toc]
---
<div class="hero" markdown>
<canvas id="eeg-hero" aria-hidden="true"></canvas>
<div class="hero-inner" markdown>
<p align="center"><img src="img/logo.svg" width="110" alt="strands-emotiv"></p>

# An agent that can feel you

<p class="hero-sub">EPOC X → Cortex → Strands tools · live dashboard · LeRobot v3 datasets.</p>
</div>
</div>

<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/eGYnLK_RREM" title="I put my brain on a dashboard and gave my AI agent a nervous system" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>
<p align="center"><sub>1:40: how it works</sub></p>

The agent gets one line per turn. You see the same line:

<span class="ambient">[brain: stress 0.45↑ · theta dominant · still 12s · CQ 13/14 good]</span>

<p class="signal-path" aria-label="signal path">EPOC&nbsp;X <span>▸</span> dongle <span>▸</span> Cortex <code>wss:6868</code> <span>▸</span> relay <code>:8765</code> <span>▸</span> dashboard&nbsp;+&nbsp;agent</p>

<p class="band-legend">
<span class="chip theta">θ theta</span>
<span class="chip alpha">α alpha</span>
<span class="chip betal">β low</span>
<span class="chip betah">β high</span>
<span class="chip gamma">γ gamma</span>
<sub>The five bands the dashboard streams at 8&nbsp;Hz. This site uses their colors.</sub>
</p>

<div class="grid cards" markdown>

- :material-rocket-launch: **[Quickstart](quickstart.md)**: headset on, dongle in, four commands.
- :material-mirror: **[Dashboard](dashboard.md)**: left is you, right is the agent, bottom is chat.
- :material-brain: **[Dataset](ecot.md)**: brain as observation and reward.
- :material-scale-balance: **[Design rules](soul.md)**

</div>

