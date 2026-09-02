#!/bin/sh
# Owner style gate for the public surface.
#   1. no em or en dashes
#   2. no coordination-journal references (LANES.md, "Lane A", sprints, SOUL sections)
#   3. warn when a docs page runs past 230 words
PATHS="README.md SOUL.md docs strands_emotiv tests dashboard/frontend/src"
SKIP='__pycache__|node_modules'
fail=0

hits=$(grep -rn -e "—" -e "–" $PATHS 2>/dev/null | grep -vE "$SKIP")
if [ -n "$hits" ]; then
  echo "style gate: em or en dash found. Use a period, comma, colon or parentheses."
  echo "$hits"; fail=1
fi

# "LANES" alone is a legitimate chart term (swimlanes), so match the journal forms only.
refs=$(grep -rnE "LANES\.md|DASHBOARD\.md|goal\.md|[Ll]ane [A-Z]\b|\b[Ss]print [0-9A-Z]|SOUL §" $PATHS 2>/dev/null | grep -vE "$SKIP")
if [ -n "$refs" ]; then
  echo "style gate: coordination-journal reference found. Keep it in .steward/."
  echo "$refs"; fail=1
fi

# DATASET.md and DATASET_CARD.md carry the schema and are exempt from the word budget.
over=$(for f in docs/*.md; do
  if [ "$f" = docs/DATASET.md ] || [ "$f" = docs/DATASET_CARD.md ]; then continue; fi
  w=$(wc -w < "$f"); [ "$w" -gt 230 ] && echo "  $w words  $f"
done)
[ -n "$over" ] && { echo "style gate WARNING: docs page over 230 words:"; echo "$over"; }

exit $fail
