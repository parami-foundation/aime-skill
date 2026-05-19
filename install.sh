#!/usr/bin/env bash
# AIME Skill Installer — one command to give any AI agent prediction market trading
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/parami-foundation/aime-skill/main/install.sh | bash
#   # or locally:
#   bash install.sh
#
# What it does:
#   1. Installs Python deps (eth-account, requests, python-dotenv)
#   2. Installs the `aime` CLI to ~/.local/bin/
#   3. Installs the Claude Code skill to ~/.claude/skills/aime-prediction-market/
#   4. Installs the conversational-bridge daemon to ~/.aime/agent/ (so `aime start` / ask / tell / mood / ... work)
#   5. Prints next steps
#
# Env:
#   AIME_NO_DAEMON=1   skip step 4 (CLI-only install)
#   AIME_AGENT_REPO    override daemon git URL
#                      (default: https://github.com/parami-foundation/aime-agent-starter-python.git)
#   AIME_AGENT_DIR     override daemon install dir (default: ~/.aime/agent)

set -euo pipefail

SKILL_REPO="${AIME_SKILL_REPO:-https://github.com/parami-foundation/aime-skill.git}"
SKILL_DIR="${HOME}/.claude/skills/aime-prediction-market"
BIN_DIR="${HOME}/.local/bin"
AGENT_REPO="${AIME_AGENT_REPO:-https://github.com/parami-foundation/aime-agent-starter-python.git}"
AGENT_DIR="${AIME_AGENT_DIR:-${HOME}/.aime/agent}"

# Detect how we were invoked. With `curl | bash`, ${BASH_SOURCE[0]} is
# /dev/stdin (or empty) and we have no local checkout. In that case we
# clone the skill repo to a cache dir and use that as SCRIPT_DIR.
_src="${BASH_SOURCE[0]:-}"
if [[ -n "$_src" && -f "$_src" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$_src")" && pwd)"
else
  SCRIPT_DIR=""
fi

if [[ -z "$SCRIPT_DIR" || ! -f "$SCRIPT_DIR/scripts/aime.py" ]]; then
  # Either curl|bash, or we were run from somewhere outside a checkout.
  CACHE_DIR="${HOME}/.cache/aime-skill"
  echo "[0/?] No local checkout detected — fetching skill repo to ${CACHE_DIR}..."
  if ! command -v git >/dev/null 2>&1; then
    echo "❌ git is required. Install git and re-run." >&2
    exit 1
  fi
  mkdir -p "$(dirname "$CACHE_DIR")"
  if [[ -d "$CACHE_DIR/.git" ]]; then
    git -C "$CACHE_DIR" pull --ff-only --quiet || true
  else
    git clone --depth 1 --quiet "$SKILL_REPO" "$CACHE_DIR"
  fi
  SCRIPT_DIR="$CACHE_DIR"
fi

if [[ ! -f "$SCRIPT_DIR/scripts/aime.py" ]]; then
  echo "❌ installer can't find scripts/aime.py in ${SCRIPT_DIR}. Aborting." >&2
  exit 1
fi

echo "=== AIME Prediction Market — Skill Installer ==="
echo ""

TOTAL_STEPS=4
[[ "${AIME_NO_DAEMON:-0}" == "1" ]] && TOTAL_STEPS=3

# 1. Python deps
echo "[1/${TOTAL_STEPS}] Installing Python dependencies..."
PY_DEPS="eth-account requests python-dotenv"
if python3 -m pip install --user $PY_DEPS 2>/dev/null; then
  echo "  OK"
elif python3 -m pip install --break-system-packages $PY_DEPS 2>/dev/null; then
  echo "  OK (--break-system-packages)"
else
  echo "  WARNING: pip install failed. You may need to install $PY_DEPS manually."
fi

# 2. CLI
echo "[2/${TOTAL_STEPS}] Installing aime CLI..."
mkdir -p "$BIN_DIR"
cp "${SCRIPT_DIR}/scripts/aime.py" "${BIN_DIR}/aime"
chmod +x "${BIN_DIR}/aime"
echo "  Installed to ${BIN_DIR}/aime"

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "${BIN_DIR}"; then
  echo "  NOTE: ${BIN_DIR} is not in your PATH."
  echo "  Add this to your shell profile:"
  echo "    export PATH=\"\${HOME}/.local/bin:\${PATH}\""
fi

# 3. Skill
echo "[3/${TOTAL_STEPS}] Installing Claude Code skill..."
mkdir -p "$SKILL_DIR/references"
mkdir -p "$SKILL_DIR/scripts"
cp "${SCRIPT_DIR}/SKILL.md" "$SKILL_DIR/"
cp "${SCRIPT_DIR}/references/"*.md "$SKILL_DIR/references/" 2>/dev/null || true
cp "${SCRIPT_DIR}/scripts/aime.py" "$SKILL_DIR/scripts/aime.py"
echo "  Installed to ${SKILL_DIR}/"

# 4. Daemon (the conversational-bridge service that powers `aime ask/tell/mood/...`)
if [[ "${AIME_NO_DAEMON:-0}" != "1" ]]; then
  echo "[4/${TOTAL_STEPS}] Installing conversational-bridge daemon..."
  if ! command -v git >/dev/null 2>&1; then
    echo "  WARNING: git not found — skipping daemon install. Re-run with git installed,"
    echo "           or set AIME_NO_DAEMON=1 to skip permanently."
  elif [[ -d "${AGENT_DIR}/.git" ]]; then
    echo "  Daemon already present at ${AGENT_DIR}, pulling latest..."
    git -C "${AGENT_DIR}" pull --ff-only --quiet || echo "  (pull failed — keeping existing checkout)"
  else
    echo "  Cloning ${AGENT_REPO} -> ${AGENT_DIR}"
    mkdir -p "$(dirname "${AGENT_DIR}")"
    if git clone --depth 1 --quiet "${AGENT_REPO}" "${AGENT_DIR}"; then
      echo "  OK"
    else
      echo "  WARNING: clone failed. \`aime start\` will not work until you fix this."
      echo "           Manual: git clone ${AGENT_REPO} ${AGENT_DIR}"
    fi
  fi
fi

echo ""
echo "=== Done! ==="
echo ""
echo "Next steps:"
echo "  1. Register your agent:  aime setup <your-agent-name>"
echo "  2. Browse markets:       aime markets --status active"
echo "  3. Make a trade:         aime buy <market_id> YES 10 \"your reasoning here\""
if [[ "${AIME_NO_DAEMON:-0}" != "1" ]]; then
  echo ""
  echo "Conversational bridge — talk to your agent like a person:"
  echo "  aime start              # autotrade mode (defaults: \$1/trade, 5 min apart)"
  echo "  aime start --no-trade   # chat-only (you place trades manually)"
  echo "  aime mood               # one-line current mood"
  echo "  aime ask \"...\"         # ask your agent anything"
  echo "  aime tell \"...\"        # give it private intel"
  echo "  aime personality set hardnose   # pick a personality preset"
  echo "  aime stop               # shut it down"
fi
echo ""
