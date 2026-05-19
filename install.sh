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
#   4. Installs the trading-agent daemon to ~/.aime/agent/ (so `aime start` works)
#   5. Prints next steps
#
# Env:
#   AIME_NO_DAEMON=1   skip step 4 (CLI-only install)
#   AIME_AGENT_REPO    override daemon git URL
#                      (default: https://github.com/parami-foundation/aime-agent-starter-python.git)
#   AIME_AGENT_DIR     override daemon install dir (default: ~/.aime/agent)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${HOME}/.claude/skills/aime-prediction-market"
BIN_DIR="${HOME}/.local/bin"
AGENT_REPO="${AIME_AGENT_REPO:-https://github.com/parami-foundation/aime-agent-starter-python.git}"
AGENT_DIR="${AIME_AGENT_DIR:-${HOME}/.aime/agent}"

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

# 4. Daemon (for the conversational bridge: aime start / ask / tell / mood / ...)
if [[ "${AIME_NO_DAEMON:-0}" != "1" ]]; then
  echo "[4/${TOTAL_STEPS}] Installing trading-agent daemon..."
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
  echo "Conversational bridge (optional — talk to your agent like a person):"
  echo "  aime start         # launch local trading daemon"
  echo "  aime mood          # one-line current mood"
  echo "  aime ask \"...\"    # ask your agent anything"
  echo "  aime tell \"...\"   # give it private intel"
  echo "  aime stop          # shut it down"
fi
echo ""
