#!/bin/sh
# Review-day one-liner: preflight checks, then an unattended factory run.
#   sh review.sh "<brief text or brief.txt>" <docs_folder> [run_name]
set -e
BRIEF="$1"; DOCS="$2"; NAME="${3:-review_$(date +%H%M)}"
[ -z "$BRIEF" ] || [ -z "$DOCS" ] && { echo "usage: sh review.sh <brief|brief.txt> <docs_folder> [run_name]"; exit 1; }
cd "$(dirname "$0")"
PY=.venv/bin/python
[ -x "$PY" ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
curl -s "${OLLAMA_URL:-http://localhost:11434}/api/tags" >/dev/null || { echo "Ollama not running: start it with 'ollama serve'"; exit 1; }
for m in "${FORGE_GEN_MODEL:-qwen2.5:3b}" "${FORGE_JUDGE_MODEL:-llama3.2:3b}"; do
  ollama list | grep -q "^$m" || { echo "missing model $m -> ollama pull $m"; exit 1; }
done
$PY tests/test_deterministic.py >/dev/null && echo "deterministic checks: ok"
echo "documents:"; ls -1 "$DOCS"
$PY forge.py --brief "$BRIEF" --docs "$DOCS" --out "runs/$NAME" --auto
echo; sed -n '1,12p' "runs/$NAME/report.md"
echo; echo "chat:  $PY chat.py runs/$NAME/agent.json"
