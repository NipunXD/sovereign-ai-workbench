#!/usr/bin/env bash
# =============================================================================
# Verify (and where possible fetch) every model named in config/models.yaml.
#
# The manifest spans providers, so this checks each one against its own
# registry: LM Studio via its OpenAI-compatible /v1/models, Ollama via /api/show.
# Model names drift between releases; rather than letting a demo fail on stage,
# this reports exactly which manifest entries do not resolve.
#
#   scripts/pull_models.sh            fetch anything missing
#   scripts/pull_models.sh --all      include models marked optional
#   scripts/pull_models.sh --check    report only, download nothing
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO_ROOT/config/models.yaml"
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
LMSTUDIO_URL="${LMSTUDIO_BASE_URL:-http://localhost:1234/v1}"
LMS_BIN="${LMS_BIN:-$HOME/.lmstudio/bin/lms}"

INCLUDE_OPTIONAL=""
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --all)   INCLUDE_OPTIONAL="--all" ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
if ! "$PY" -c "import yaml" >/dev/null 2>&1; then
  # The repo venv always has pyyaml even when the system interpreter does not.
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; fi
fi

# --- provider reachability ---------------------------------------------------
lmstudio_up=0; ollama_up=0
curl -sf --max-time 5 "$LMSTUDIO_URL/models" >/dev/null 2>&1 && lmstudio_up=1
curl -sf --max-time 5 "$OLLAMA_URL/api/version" >/dev/null 2>&1 && ollama_up=1

LMS_CACHE=""
if [ "$lmstudio_up" -eq 1 ]; then
  LMS_CACHE="$(curl -sf --max-time 10 "$LMSTUDIO_URL/models" 2>/dev/null)"
fi

present() {  # present <provider> <model>
  case "$1" in
    lmstudio)
      [ "$lmstudio_up" -eq 1 ] || return 1
      printf '%s' "$LMS_CACHE" | grep -Fq "\"$2\"" ;;
    ollama)
      [ "$ollama_up" -eq 1 ] || return 1
      curl -sf --max-time 10 -X POST "$OLLAMA_URL/api/show" -d "{\"model\":\"$2\"}" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

fetch() {  # fetch <provider> <model>
  case "$1" in
    lmstudio)
      [ -x "$LMS_BIN" ] || { echo "      lms CLI not found at $LMS_BIN" >&2; return 1; }
      "$LMS_BIN" get "$2" --mlx -y >/dev/null 2>&1
      LMS_CACHE="$(curl -sf --max-time 10 "$LMSTUDIO_URL/models" 2>/dev/null)" ;;
    ollama)
      command -v ollama >/dev/null 2>&1 || { echo "      ollama CLI not found" >&2; return 1; }
      ollama pull "$2" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
}

# --- walk the manifest -------------------------------------------------------
total=0; ok=0; missing=0; failed=0
missing_list=""; failed_list=""

while IFS=$'\t' read -r provider model; do
  [ -n "${provider:-}" ] || continue
  total=$((total + 1))

  if present "$provider" "$model"; then
    printf '  \033[32m*\033[0m %-10s %-42s present\n' "$provider" "$model"
    ok=$((ok + 1)); continue
  fi

  if [ "$CHECK_ONLY" -eq 1 ]; then
    printf '  \033[33m-\033[0m %-10s %-42s NOT AVAILABLE\n' "$provider" "$model"
    missing=$((missing + 1)); missing_list="$missing_list $provider/$model"; continue
  fi

  printf '  \033[36m>\033[0m %-10s %-42s fetching...\n' "$provider" "$model"
  if fetch "$provider" "$model" && present "$provider" "$model"; then
    printf '  \033[32m*\033[0m %-10s %-42s done\n' "$provider" "$model"
    ok=$((ok + 1))
  else
    printf '  \033[31mx\033[0m %-10s %-42s FAILED\n' "$provider" "$model"
    failed=$((failed + 1)); failed_list="$failed_list $provider/$model"
  fi
done <<EOF
$("$PY" "$REPO_ROOT/scripts/manifest_tags.py" "$MANIFEST" $INCLUDE_OPTIONAL)
EOF

echo
[ "$lmstudio_up" -eq 1 ] || echo "  note: LM Studio unreachable at $LMSTUDIO_URL — run 'lms server start'"
[ "$ollama_up"   -eq 1 ] || echo "  note: Ollama unreachable at $OLLAMA_URL — run 'ollama serve'"
echo "available: $ok/$total"

if [ "$failed" -gt 0 ]; then
  echo "failed: $failed_list" >&2
  echo "The model name may have changed upstream. Update config/models.yaml." >&2
  exit 1
fi
if [ "$missing" -gt 0 ]; then
  echo "missing: $missing_list  (run without --check to fetch)" >&2
  exit 1
fi
echo "Every manifest model is available."
