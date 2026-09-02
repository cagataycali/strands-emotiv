# The Brain Is Another Embodiment

Two-part arXiv paper. Every number is measured from this repo's code, datasets, and fixtures — no invented data.

| | title | pdf |
|---|---|---|
| **Part I** | A Consumer EEG Headset as a First-Class Robot Body for AI Agents | [`brain-is-another-embodiment-part1.pdf`](brain-is-another-embodiment-part1.pdf) |
| **Part II** | Embodied Chain-of-Thought for a Cortex — the Brain as Observation and Reward | [`brain-is-another-embodiment-part2.pdf`](brain-is-another-embodiment-part2.pdf) |

## Build

```
make figures   # regenerate all 8 figures from datasets/ + tests/fixtures/ (measured, not drawn)
make           # figures + both PDFs
make arxiv     # dist/arxiv-part{1,2}.tar.gz — flat, self-contained, cold-compile verified
make review    # PNG render of every page for visual inspection
```

Layout: `common/` (arxiv.sty, references.bib, figure script, figures), `part1/`, `part2/` (one `main.tex` each).

## Submission notes

- **Categories:** cs.HC (primary), cs.RO + cs.LG (cross-list).
- `make arxiv` produces the upload artifacts; each tarball carries `main.tex`, `main.bbl`, `arxiv.sty`, and its figures — nothing else needed. The script refuses to package a bundle that doesn't compile clean (0 errors, 0 undefined refs) with plain `pdflatex`.
- Part II cites the dataset `cagataydev/emotiv-ecot` (HuggingFace, **private by design** — see Part II §9). The papers stand without access: all corpus numbers are printed in the text.
- Figures regenerate deterministically from the recorded parquet; if datasets are re-recorded, re-run `make figures` and re-check the numbers quoted in prose (§5–§6 of Part II carry the corpus census).

## Ground rules (how these papers were written)

1. Every quantitative claim traced to a measurement run in this repo at writing time.
2. Absence reported, never imputed (all 20 REWARD clauses are `nan`, and §6 of Part II is about why).
3. The subject's brain data stays private; the machinery is public.
