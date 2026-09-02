"""Generate every data figure for the arXiv paper — ONLY from real repo data.

Sources (all in-repo, nothing invented):
  tests/fixtures/epocx_live_10s.jsonl        865 raw Cortex samples, 10 s live capture
  datasets/live-accept-1                      LeRobot v3.0, 1 ep / 99 frames
  datasets/session-20260902-0101              LeRobot v3.0, 2 eps / 196 frames
  datasets/tool-accept-2                      LeRobot v3.0, 2 eps / 94 frames

Run: cd paper && make figures   (uv run --with matplotlib/pandas/pyarrow/scipy)
Outputs land in paper/common/figures/*.pdf
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "paper" / "common" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

# ---- paper palette (matches main.tex definitions) ----
BAND_COLORS = {
    "theta": "#4ee1c2",
    "alpha": "#7aa2ff",
    "betaL": "#ffb454",
    "betaH": "#ff7a7a",
    "gamma": "#c98bff",
}
AGENT = "#00b060"
INK = "#1a1a2e"
GRID = "#d8d8e4"

CHANNELS = ["AF3", "F7", "F3", "FC5", "T7", "P7", "O1", "O2", "P8", "T8", "FC6", "F4", "F8", "AF4"]
BANDS = list(BAND_COLORS)

# Approximate 10-20 scalp coordinates (top view, nose up) for the EPOC X montage.
POS = {
    "AF3": (-0.30, 0.78), "AF4": (0.30, 0.78),
    "F7": (-0.72, 0.42), "F3": (-0.38, 0.44), "F4": (0.38, 0.44), "F8": (0.72, 0.42),
    "FC5": (-0.62, 0.14), "FC6": (0.62, 0.14),
    "T7": (-0.85, -0.10), "T8": (0.85, -0.10),
    "P7": (-0.62, -0.55), "P8": (0.62, -0.55),
    "O1": (-0.28, -0.83), "O2": (0.28, -0.83),
}

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 9,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": INK,
    "ytick.color": INK,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 150,
    "savefig.bbox": "tight",
})


def load_ds(name: str) -> pd.DataFrame:
    return pd.read_parquet(ROOT / "datasets" / name / "data" / "chunk-000" / "file-000.parquet")


def col(df: pd.DataFrame, name: str) -> np.ndarray:
    return np.stack(df[name].values)


def save(fig, name: str) -> None:
    fig.savefig(OUT / f"{name}.pdf")
    plt.close(fig)
    print(f"  wrote {name}.pdf")


# ------------------------------------------------------------------
# FIG — what Cortex actually sends: stream sample counts in 10 s.
# (Part I §3) source: tests/fixtures/epocx_live_10s.jsonl
# ------------------------------------------------------------------
def fig_stream_rates() -> None:
    counts: dict[str, int] = {}
    with open(ROOT / "tests" / "fixtures" / "epocx_live_10s.jsonl") as f:
        for line in f:
            s = json.loads(line)["stream"]
            counts[s] = counts.get(s, 0) + 1
    order = ["mot", "fac", "pow", "com", "dev", "met"]
    labels = {
        "mot": "mot\nhead pose", "fac": "fac\nface", "pow": "pow\nband power",
        "com": "com\nmental cmd", "dev": "dev\ncontact", "met": "met\nmetrics",
    }
    vals = [counts[k] for k in order]
    colors = ["#7aa2ff", "#ffb454", "#4ee1c2", "#c98bff", "#ff7a7a", AGENT]
    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    bars = ax.bar([labels[k] for k in order], vals, color=colors, width=0.62)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 8, f"{v}\n≈{v/10:.1f} Hz",
                ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("samples in 10 s (measured)")
    ax.set_ylim(0, 440)
    ax.set_axisbelow(True)
    ax.grid(axis="x", visible=False)
    save(fig, "stream_rates")


# ------------------------------------------------------------------
# FIG — band power across one live episode, with the moment the
# agent spoke marked. (Part II §5) source: datasets/live-accept-1
# ------------------------------------------------------------------
def fig_bandpower_episode() -> None:
    df = load_ds("live-accept-1")
    state = col(df, "observation.state").reshape(len(df), 14, 5)  # ch × band
    t = col(df, "timestamp").ravel()
    act = col(df, "action")
    spoke = np.where(act[:, 0] > 0)[0]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    band_tex = {"theta": r"$\theta$", "alpha": r"$\alpha$", "betaL": r"$\beta_L$",
                "betaH": r"$\beta_H$", "gamma": r"$\gamma$"}
    for bi, band in enumerate(BANDS):
        ax.plot(t, np.nanmean(state[:, :, bi], axis=1), color=BAND_COLORS[band],
                lw=1.4, label=band_tex[band])
    top = ax.get_ylim()[1]
    for si in spoke:
        ax.axvline(t[si], color=AGENT, lw=1.6, ls="--")
        ax.text(t[si] + 0.15, top * 0.93, "agent spoke", color=AGENT, fontsize=8, va="top")
    ax.set_xlabel("episode time (s) — 99 frames @ 8 Hz")
    ax.set_ylabel("band power, mean of 14 ch")
    ax.legend(ncol=5, fontsize=9, framealpha=0.9, loc="upper left")
    save(fig, "bandpower_episode")


# ------------------------------------------------------------------
# FIG — contact quality scalp map, mean over the same episode.
# (Part I §5) source: datasets/live-accept-1
# ------------------------------------------------------------------
def fig_cq_map() -> None:
    df = load_ds("live-accept-1")
    cq = np.nanmean(col(df, "observation.contact_quality"), axis=0)  # per channel, 0..4
    fig, ax = plt.subplots(figsize=(3.6, 3.6))
    head = plt.Circle((0, 0), 1.0, fill=False, color=INK, lw=1.5)
    ax.add_patch(head)
    ax.plot([-0.08, 0, 0.08], [0.99, 1.09, 0.99], color=INK, lw=1.5)  # nose
    cmap = matplotlib.colormaps["RdYlGn"]
    for ch, q in zip(CHANNELS, cq):
        x, y = POS[ch]
        ax.scatter(x, y, s=560, color=cmap(q / 4.0), edgecolor=INK, lw=0.8, zorder=3)
        ax.text(x, y, f"{ch}\n{q:.1f}", ha="center", va="center", fontsize=6.5, zorder=4)
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-1.25, 1.3)
    ax.set_aspect("equal")
    ax.axis("off")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(0, 4))
    cb = fig.colorbar(sm, ax=ax, shrink=0.7, pad=0.02)
    cb.set_label("contact quality 0–4 (episode mean)", fontsize=8)
    save(fig, "cq_map")


# ------------------------------------------------------------------
# FIG — the events channel: every derived event across the three
# recorded sessions, as a raster. (Part I §4, Part II §5)
# ------------------------------------------------------------------
def fig_event_raster() -> None:
    names = ["blink", "wink_left", "wink_right", "head_turn_left", "head_turn_right",
             "nod", "clench", "smile", "look_up", "look_down", "double_blink", "eyes_closed"]
    fig, axes = plt.subplots(3, 1, figsize=(6.4, 4.8))
    for ax, ds in zip(axes, ["live-accept-1", "session-20260902-0101", "tool-accept-2"]):
        df = load_ds(ds)
        ev = np.nan_to_num(col(df, "observation.events"))
        t = col(df, "timestamp").ravel() + col(df, "episode_index").ravel() * 16.0
        for k in range(min(ev.shape[1], len(names))):
            idx = np.where(ev[:, k] > 0)[0]
            if len(idx):
                ax.scatter(t[idx], np.full(len(idx), k), s=16,
                           color=list(BAND_COLORS.values())[k % 5], zorder=3)
        act = col(df, "action")
        for si in np.where(act[:, 0] > 0)[0]:
            ax.axvline(t[si], color=AGENT, lw=1.0, ls="--", alpha=0.7)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=5.5)
        ax.set_ylim(-0.8, len(names) - 0.2)
        n_ep = df.episode_index.nunique()
        ax.set_title(f"{ds} — {n_ep} ep / {len(df)} frames", fontsize=8, loc="left")
    axes[-1].set_xlabel("time (s; episodes offset for display) — green dashes: agent spoke")
    fig.tight_layout()
    save(fig, "event_raster")


# ------------------------------------------------------------------
# FIG — head pose from the raw fixture: quaternion → yaw/pitch,
# 10 s of live mot samples, with the turn threshold shaded. (Part I §4)
# ------------------------------------------------------------------
def fig_headpose() -> None:
    ts, yaws, pitches = [], [], []
    with open(ROOT / "tests" / "fixtures" / "epocx_live_10s.jsonl") as f:
        for line in f:
            j = json.loads(line)
            if j["stream"] != "mot":
                continue
            d = j["data"]
            q0, q1, q2, q3 = d["Q0"], d["Q1"], d["Q2"], d["Q3"]
            yaw = np.degrees(np.arctan2(2 * (q0 * q3 + q1 * q2), 1 - 2 * (q2**2 + q3**2)))
            pitch = np.degrees(np.arcsin(np.clip(2 * (q0 * q2 - q3 * q1), -1, 1)))
            ts.append(j["time"])
            yaws.append(yaw)
            pitches.append(pitch)
    t = np.array(ts) - ts[0]
    yy = np.array(yaws) - yaws[0]
    pp = np.array(pitches) - pitches[0]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    ax.plot(t, yy, color="#7aa2ff", lw=1.2, label="yaw (turn)")
    ax.plot(t, pp, color="#ffb454", lw=1.2, label="pitch (nod)")
    lo = min(-25, float(min(yy.min(), pp.min())) - 2)
    hi = max(25, float(max(yy.max(), pp.max())) + 2)
    ax.axhspan(20, hi, color="#7aa2ff", alpha=0.10)
    ax.axhspan(lo, -20, color="#7aa2ff", alpha=0.10)
    ax.axhline(20, color="#7aa2ff", lw=0.8, ls=":")
    ax.axhline(-20, color="#7aa2ff", lw=0.8, ls=":")
    ax.set_ylim(lo, hi)
    ax.text(0.1, 21, "TURN_DEG = 20° within 1 s → head_turn event (events.py)",
            fontsize=7.5, color="#33507a")
    ax.set_xlabel(f"time (s) — {len(t)} mot samples in 10 s (≈{len(t)/10:.0f} Hz)")
    ax.set_ylabel("degrees from start")
    ax.legend(fontsize=8, loc="lower left")
    save(fig, "headpose")


# ------------------------------------------------------------------
# FIG — reward honesty: metrics_valid coverage per dataset.
# met arrives at 0.1 Hz; a 12 s episode sees it once or never.
# (Part II §6)
# ------------------------------------------------------------------
def fig_metrics_coverage() -> None:
    names = ["attention", "engagement", "excitement", "longExcitement",
             "stress", "relaxation", "interest"]
    mvs = []
    total = 0
    for ds in ["live-accept-1", "session-20260902-0101", "tool-accept-2"]:
        df = load_ds(ds)
        mvs.append(np.nan_to_num(col(df, "observation.metrics_valid")))
        total += len(df)
    mv = np.concatenate(mvs)  # [N, 7]
    vals = mv.mean(axis=0) * 100
    reward_axes = {"stress", "engagement"}
    fig, ax = plt.subplots(figsize=(5.8, 2.6))
    y = np.arange(len(names))
    colors = ["#ff7a7a" if n in reward_axes else "#7aa2ff" for n in names]
    ax.barh(y, vals, color=colors, height=0.55)
    ax.barh(y, 100 - vals, left=vals, color="#e8e8f0", height=0.55)
    for i, v in enumerate(vals):
        label = f"{v:.0f}%" if v > 0 else "0% — never valid"
        ax.text(max(v, 0) + 1.5, i, label, va="center", fontsize=8,
                color="#c04040" if names[i] in reward_axes else "#333333")
    ax.set_yticks(y)
    ax.set_yticklabels([n + (" ◀ reward axis" if n in reward_axes else "")
                        for n in names], fontsize=8)
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xlabel(f"frames with this metric axis valid — all {total} frames, met streams at 0.1 Hz")
    ax.grid(axis="y", visible=False)
    save(fig, "metrics_coverage")


# ------------------------------------------------------------------
# FIG — band-power fingerprint: mean power per channel × band over
# all recorded frames — theta dominance visible. (Part II §5)
# ------------------------------------------------------------------
def fig_band_heatmap() -> None:
    frames = []
    for ds in ["live-accept-1", "session-20260902-0101", "tool-accept-2"]:
        df = load_ds(ds)
        frames.append(col(df, "observation.state").reshape(len(df), 14, 5))
    allf = np.concatenate(frames)  # N × 14 × 5
    mean = np.nanmean(allf, axis=0)  # 14 × 5
    fig, ax = plt.subplots(figsize=(4.6, 3.4))
    im = ax.imshow(mean, aspect="auto", cmap="viridis")
    ax.set_xticks(range(5))
    ax.set_xticklabels([r"$\theta$", r"$\alpha$", r"$\beta_L$", r"$\beta_H$", r"$\gamma$"])
    ax.set_yticks(range(14))
    ax.set_yticklabels(CHANNELS, fontsize=7)
    ax.grid(visible=False)
    cb = fig.colorbar(im, ax=ax, shrink=0.85)
    cb.set_label(f"mean band power over {len(allf)} frames", fontsize=8)
    save(fig, "band_heatmap")


# ------------------------------------------------------------------
# FIG — one conversational turn end-to-end (session ep 1):
# bands + action channels. (Part II §4)
# ------------------------------------------------------------------
def fig_turn_anatomy() -> None:
    df = load_ds("session-20260902-0101")
    df = df[df.episode_index == 1].reset_index(drop=True)
    state = col(df, "observation.state").reshape(len(df), 14, 5)
    t = col(df, "timestamp").ravel()
    act = col(df, "action")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(6.4, 3.8), sharex=True,
                                   height_ratios=[2, 1])
    for band in ["theta", "alpha", "gamma"]:
        k = BANDS.index(band)
        ax1.plot(t, np.nanmean(state[:, :, k], axis=1), color=BAND_COLORS[band],
                 lw=1.3, label=band)
    pre = min(24, len(t) - 1)
    ax1.axvspan(t[0], t[pre], color="#e8e8f0", alpha=0.6)
    ax1.text(t[1], float(np.nanmax(np.nanmean(state[:, :, 0], axis=1))) * 0.97,
             "3 s pre-roll", fontsize=7.5, color="#666")
    ax1.legend(fontsize=8, ncol=3)
    ax1.set_ylabel("band power (14-ch mean)")
    labels = ["spoke", "tool_called", "marker_injected"]
    for i, (lab, c) in enumerate(zip(labels, [AGENT, "#c98bff", "#ff7a7a"])):
        idx = np.where(act[:, i] > 0)[0]
        if len(idx):
            ax2.scatter(t[idx], np.full(len(idx), i), s=60, marker="|",
                        linewidths=2.5, color=c)
    ax2.set_yticks(range(3))
    ax2.set_yticklabels(labels, fontsize=7.5)
    ax2.set_ylim(-0.6, 2.6)
    ax2.set_xlabel(f"episode time (s) — session-20260902-0101 ep 1, {len(df)} frames @ 8 Hz")
    fig.tight_layout()
    save(fig, "turn_anatomy")


if __name__ == "__main__":
    print("figures →", OUT)
    fig_stream_rates()
    fig_bandpower_episode()
    fig_cq_map()
    fig_event_raster()
    fig_headpose()
    fig_metrics_coverage()
    fig_band_heatmap()
    fig_turn_anatomy()
    print("done.")
