#!/bin/sh
# pdflatex ×2 (+bibtex if a .bib is referenced) — halts on the first real error, prints it.
set -e; f=${1%.tex}
run(){ pdflatex -interaction=nonstopmode -halt-on-error "$f.tex" >/tmp/latex_$f.log 2>&1 || { grep -A8 -m1 "^!" /tmp/latex_$f.log; exit 1; }; }
run; if grep -q "\\\\bibdata" "$f.aux" 2>/dev/null; then bibtex "$f" >/dev/null || true; run; fi; run
grep -E "LaTeX Warning: (Reference|Citation).*undefined" /tmp/latex_$f.log | head -5 || true
