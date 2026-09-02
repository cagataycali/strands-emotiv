#!/bin/sh
# Assemble flat, self-contained arXiv source tarballs for both parts.
# arXiv has no TEXINPUTS tricks: everything lands in one directory,
# the .bbl ships instead of the .bib, and paths are rewritten flat.
set -e
cd "$(dirname "$0")/.."
mkdir -p dist
P1_FIGS="stream_rates headpose cq_map"
P2_FIGS="band_heatmap turn_anatomy bandpower_episode event_raster metrics_coverage"
for p in part1 part2; do
  stage=$(mktemp -d)
  cp "$p/main.tex" "$p/main.bbl" common/arxiv.sty "$stage/"
  if [ "$p" = part1 ]; then
    for f in $P1_FIGS; do cp "common/figures/$f.pdf" "$stage/"; done
    cp ../docs/img/dashboard.jpg ../docs/img/topomaps-waterfall.jpg "$stage/"
  else
    for f in $P2_FIGS; do cp "common/figures/$f.pdf" "$stage/"; done
  fi
  # flatten paths: graphicspath → ./ ; bibliography name is irrelevant once .bbl ships
  perl -pi -e 's/\\graphicspath\{.*$/\\graphicspath{{.\/}}/; s/\\bibliography\{\.\.\/common\/references\}/\\bibliography{references}/' "$stage/main.tex"
  # prove it compiles cold, twice, with plain pdflatex
  (cd "$stage" && pdflatex -interaction=nonstopmode main.tex >/dev/null 2>&1 \
               && pdflatex -interaction=nonstopmode main.tex >/dev/null 2>&1)
  errs=$(grep -c '^!' "$stage/main.log" || true)
  undef=$(grep -c 'undefined' "$stage/main.log" || true)
  [ "$errs" = 0 ] && [ "$undef" = 0 ] || { echo "$p: NOT CLEAN (errors=$errs undefined=$undef)"; exit 1; }
  tar -czf "dist/arxiv-$p.tar.gz" -C "$stage" $(cd "$stage" && ls | grep -v -E '\.(pdf|log|aux|out)$' ) \
      $(cd "$stage" && ls *.pdf | grep -v '^main.pdf$')
  rm -rf "$stage"
  echo "dist/arxiv-$p.tar.gz: $(tar -tzf dist/arxiv-$p.tar.gz | wc -l | tr -d ' ') files, $(du -h dist/arxiv-$p.tar.gz | cut -f1)"
done
