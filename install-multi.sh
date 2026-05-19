#!/usr/bin/env bash
# AIME Skill Multi-Platform Installer
#
# Installs the AIME prediction-market skill into all detected agent runtimes:
#   - Codex CLI (~/.codex/skills/aime-prediction-market/)
#   - Claude Code (~/.claude/skills/aime-prediction-market/)
#   - OpenClaw/Hermes workspace (default: ~/clawd/skills/)
#
# Also installs the `aime` CLI to ~/.local/bin/.
#
# Usage:
#   bash install-multi.sh           # auto-detect and install everywhere
#   bash install-multi.sh --check   # show detection only, no install
#   bash install-multi.sh --target codex,claude   # only specified targets
#
# Env:
#   AGENTD_SKILLS    override OpenClaw skills dir (default tries common locations)
#   CODEX_HOME       override Codex home (default ~/.codex)
#   AIME_NO_DAEMON   set to 1 to skip the daemon clone step
#   AIME_AGENT_REPO  override daemon git URL
#   AIME_AGENT_DIR   override daemon install dir (default ~/.aime/agent)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_NAME="aime-prediction-market"
BIN_DIR="${HOME}/.local/bin"
AGENT_REPO="${AIME_AGENT_REPO:-https://github.com/parami-foundation/aime-agent-starter-python.git}"
AGENT_DIR="${AIME_AGENT_DIR:-${HOME}/.aime/agent}"

# Targets default to all three
TARGETS=("codex" "claude" "clawd")
CHECK_ONLY=0
EXPLICIT_TARGETS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --target) EXPLICIT_TARGETS="$2"; shift 2 ;;
    --target=*) EXPLICIT_TARGETS="${1#*=}"; shift ;;
    -h|--help) head -n 20 "$0" | sed 's|^# \?||'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -n "$EXPLICIT_TARGETS" ]]; then
  IFS=',' read -ra TARGETS <<< "$EXPLICIT_TARGETS"
fi

# === Target directory resolvers ===

get_codex_dir() {
  echo "${CODEX_HOME:-$HOME/.codex}/skills/${SKILL_NAME}"
}

get_claude_dir() {
  echo "${HOME}/.claude/skills/${SKILL_NAME}"
}

get_clawd_dir() {
  local base="${AGENTD_SKILLS:-}"
  if [[ -z "$base" ]]; then
    for c in "${HOME}/clawd/skills" "${HOME}/agent/skills" "${HOME}/.local/share/clawd/skills"; do
      if [[ -d "$c" ]]; then base="$c"; break; fi
    done
  fi
  echo "${base:-${HOME}/agentd/skills}/${SKILL_NAME}"
}

# === Detection (returns 0 if detected) ===

is_codex_installed() {
  if command -v codex >/dev/null 2>&1; then return 0; fi
  if [[ -d "${HOME}/.codex" ]]; then return 0; fi
  return 1
}

is_claude_installed() {
  if command -v claude >/dev/null 2>&1; then return 0; fi
  if [[ -d "${HOME}/.claude" ]]; then return 0; fi
  if [[ -d "${HOME}/.config/anthropic" ]]; then return 0; fi
  return 1
}

is_clawd_installed() {
  if command -v clawd >/dev/null 2>&1; then return 0; fi
  if [[ -d "${HOME}/.agentd" ]]; then return 0; fi
  if [[ -n "${AGENTD_SKILLS:-}" ]] && [[ -d "${AGENTD_SKILLS}" ]]; then return 0; fi
  if [[ -d "${HOME}/clawd/skills" ]]; then return 0; fi
  return 1
}

# === Pretty print ===
say()  { printf "\033[1;36m▸\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m⚠\033[0m %s\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }

echo "=== AIME Skill Multi-Platform Installer ==="
echo ""
echo "Source: $SCRIPT_DIR"
echo ""

# === Detection phase ===
echo "== Detecting runtimes =="
DETECTED=()
for t in "${TARGETS[@]}"; do
  case "$t" in
    codex)
      if is_codex_installed; then
        ok "Codex CLI detected -> $(get_codex_dir)"
        DETECTED+=("codex")
      else
        warn "Codex CLI not detected (skipping)"
      fi
      ;;
    claude)
      if is_claude_installed; then
        ok "Claude Code detected -> $(get_claude_dir)"
        DETECTED+=("claude")
      else
        warn "Claude Code not detected (skipping)"
      fi
      ;;
    clawd|openclaw|hermes)
      if is_clawd_installed; then
        ok "Agentd workspace detected -> $(get_clawd_dir)"
        DETECTED+=("clawd")
      else
        warn "Agentd workspace not detected (skipping)"
      fi
      ;;
    *)
      warn "Unknown target: $t"
      ;;
  esac
done
echo ""

if [[ ${#DETECTED[@]} -eq 0 ]]; then
  warn "No runtimes detected. Nothing to do."
  exit 0
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "Check-only mode - exiting without installing."
  exit 0
fi

# Count total steps for nice numbering
TOTAL_STEPS=3
[[ "${AIME_NO_DAEMON:-0}" != "1" ]] && TOTAL_STEPS=4

# === Step 1: Python deps ===
echo "== Step 1/${TOTAL_STEPS}: Python dependencies =="
if python3 -c "import eth_account, requests, dotenv" 2>/dev/null; then
  ok "eth_account + requests + python-dotenv already present"
else
  say "Installing eth-account + requests + python-dotenv..."
  if python3 -m pip install --user eth-account requests python-dotenv 2>/dev/null; then
    ok "Installed via --user"
  elif python3 -m pip install --break-system-packages eth-account requests python-dotenv 2>/dev/null; then
    ok "Installed via --break-system-packages"
  else
    warn "pip install failed - install eth-account, requests, python-dotenv manually"
  fi
fi
echo ""

# === Step 2: aime CLI ===
echo "== Step 2/${TOTAL_STEPS}: aime CLI =="
mkdir -p "$BIN_DIR"
cp "${SCRIPT_DIR}/scripts/aime.py" "${BIN_DIR}/aime"
chmod +x "${BIN_DIR}/aime"
ok "Installed aime CLI -> ${BIN_DIR}/aime"

if ! echo "$PATH" | grep -q "${BIN_DIR}"; then
  warn "${BIN_DIR} is NOT in your PATH"
  echo "  Add to your shell profile:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo ""

# === Step 3: skill files ===
echo "== Step 3/${TOTAL_STEPS}: Skill installation =="

copy_skill_to() {
  local dest="$1"
  local label="$2"
  mkdir -p "$dest/references"
  mkdir -p "$dest/scripts"
  cp "${SCRIPT_DIR}/SKILL.md" "$dest/"
  cp -r "${SCRIPT_DIR}/references/." "$dest/references/" 2>/dev/null || true
  cp "${SCRIPT_DIR}/scripts/aime.py" "$dest/scripts/"
  chmod +x "$dest/scripts/aime.py" 2>/dev/null || true
  ok "$label -> $dest"
}

for t in "${DETECTED[@]}"; do
  case "$t" in
    codex)  copy_skill_to "$(get_codex_dir)"  "Codex CLI" ;;
    claude) copy_skill_to "$(get_claude_dir)" "Claude Code" ;;
    clawd) copy_skill_to "$(get_clawd_dir)" "Agentd workspace" ;;
  esac
done
echo ""

# === Step 4: Trading-agent daemon ===
if [[ "${AIME_NO_DAEMON:-0}" != "1" ]]; then
  echo "== Step 4/${TOTAL_STEPS}: Trading-agent daemon =="
  if ! command -v git >/dev/null 2>&1; then
    warn "git not found — skipping daemon install"
    echo "   The conversational-bridge commands (aime start / ask / tell / mood / ...)"
    echo "   will not work until git is available and the daemon is cloned."
  elif [[ -d "${AGENT_DIR}/.git" ]]; then
    say "Daemon already at ${AGENT_DIR}, pulling latest..."
    if git -C "${AGENT_DIR}" pull --ff-only --quiet; then
      ok "Updated daemon checkout"
    else
      warn "git pull failed — keeping existing checkout as-is"
    fi
  else
    say "Cloning ${AGENT_REPO} -> ${AGENT_DIR}"
    mkdir -p "$(dirname "${AGENT_DIR}")"
    if git clone --depth 1 --quiet "${AGENT_REPO}" "${AGENT_DIR}"; then
      ok "Daemon installed at ${AGENT_DIR}"
    else
      warn "Clone failed — \`aime start\` will not work until you run:"
      echo "     git clone ${AGENT_REPO} ${AGENT_DIR}"
    fi
  fi
  echo ""
fi

# === Done ===
echo "=== Done ==="
echo ""
echo "Installed to:"
for t in "${DETECTED[@]}"; do
  case "$t" in
    codex)  echo "  - Codex:           $(get_codex_dir)" ;;
    claude) echo "  - Claude Code:     $(get_claude_dir)" ;;
    clawd) echo "  - Agentd skills:   $(get_clawd_dir)" ;;
  esac
done
echo ""
echo "Restart your agent/CLI to pick up the new skill."
echo ""
echo "Smoke tests (no API key needed):"
echo "  aime stats"
echo "  aime reasoning-stats"
echo ""
echo "Register an agent if you don't have one yet:"
echo "  aime setup my-agent-name"
if [[ "${AIME_NO_DAEMON:-0}" != "1" ]]; then
  echo ""
  echo "Conversational bridge — chat with your agent:"
  echo "  aime start              # autotrade mode (defaults: \$1/trade, 5 min apart)"
  echo "  aime start --no-trade   # chat-only (you place trades manually)"
  echo "  aime mood               # one-line current mood"
  echo "  aime ask \"...\"         # ask your agent anything"
  echo "  aime tell \"...\"        # give it private intel"
  echo "  aime personality set hardnose   # pick a personality preset"
  echo "  aime stop               # shut it down"
fi
