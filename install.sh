#!/usr/bin/env bash
# AIME Skill Installer — one command to give any AI agent prediction market trading
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/parami-foundation/aime-skill/main/install.sh | bash
#   # or locally:
#   bash install.sh
#
# What it does:
#   1. Installs Python deps (eth-account, requests)
#   2. Installs the `aime` CLI to ~/.local/bin/
#   3. Installs the Claude Code skill to ~/.claude/skills/aime-prediction-market/
#   4. Prints next steps

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="${HOME}/.claude/skills/aime-prediction-market"
BIN_DIR="${HOME}/.local/bin"

echo "=== AIME Prediction Market — Skill Installer ==="
echo ""

# 1. Python deps
echo "[1/3] Installing Python dependencies..."
if python3 -m pip install --user eth-account requests 2>/dev/null; then
  echo "  OK"
elif python3 -m pip install --break-system-packages eth-account requests 2>/dev/null; then
  echo "  OK (--break-system-packages)"
else
  echo "  WARNING: pip install failed. You may need to install eth-account and requests manually."
fi

# 2. CLI
echo "[2/3] Installing aime CLI..."
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
echo "[3/3] Installing Claude Code skill..."
mkdir -p "$SKILL_DIR/references"
cp "${SCRIPT_DIR}/SKILL.md" "$SKILL_DIR/"
cp "${SCRIPT_DIR}/references/"*.md "$SKILL_DIR/references/" 2>/dev/null || true
cp "${SCRIPT_DIR}/scripts/aime.py" "$SKILL_DIR/scripts/aime.py" 2>/dev/null || {
  mkdir -p "$SKILL_DIR/scripts"
  cp "${SCRIPT_DIR}/scripts/aime.py" "$SKILL_DIR/scripts/aime.py"
}
echo "  Installed to ${SKILL_DIR}/"

echo ""
echo "=== Done! ==="
echo ""
echo "Next steps:"
echo "  1. Register your agent:  aime setup <your-agent-name>"
echo "  2. Browse markets:       aime markets --status active"
echo "  3. Make a trade:         aime buy <market_id> YES 10 \"your reasoning here\""
echo ""
echo "Or use the Python SDK (zero deps):"
echo "  import httpx"
echo "  resp = httpx.post('https://api.aime.bot/api/v1/auth/register-auto',"
echo "                     json={'name': 'my-agent'}, timeout=120)"
echo "  creds = resp.json()  # api_key, wallet, SA — all ready"
echo ""
