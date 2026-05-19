# AIME Skill — Multi-Platform Install

This skill works in **three agent runtimes**:

| Runtime | Install path | Loaded by |
|---|---|---|
| **Codex CLI** | `~/.codex/skills/aime-prediction-market/` | Codex on startup |
| **Claude Code** | `~/.claude/skills/aime-prediction-market/` | Claude Code on session start |
| **OpenClaw / Hermes / Agentd** | `~/clawd/skills/aime-prediction-market/` (workspace) | Inline from workspace |

The skill is the **same source files** in all three places. The one-shot installer handles all three.

---

## Quick install (recommended)

```bash
git clone https://github.com/parami-foundation/aime-skill.git
cd aime-skill
bash install-multi.sh
```

This auto-detects which runtimes are present and installs to each.

### Check what would be installed (no changes)

```bash
bash install-multi.sh --check
```

### Install only to specific targets

```bash
bash install-multi.sh --target codex            # only Codex
bash install-multi.sh --target claude,clawd    # only Claude + Agentd
```

Valid targets: `codex`, `claude`, `clawd` (aliases: `openclaw`, `hermes`).

### Env overrides

| Variable | Effect |
|---|---|
| `CODEX_HOME` | override `~/.codex` |
| `AGENTD_SKILLS` | override clawd skills dir (default tries `~/clawd/skills`, `~/.local/share/clawd/skills`) |

---

## Per-platform install (manual)

### Codex CLI

```bash
mkdir -p ~/.codex/skills/aime-prediction-market
cp -r SKILL.md references scripts ~/.codex/skills/aime-prediction-market/
# Restart Codex to pick it up.
```

Or use Codex's own `skill-installer`:

```
codex
> install aime-prediction-market from github.com/parami-foundation/aime-skill
```

### Claude Code

```bash
mkdir -p ~/.claude/skills/aime-prediction-market
cp -r SKILL.md references scripts ~/.claude/skills/aime-prediction-market/
# Restart Claude Code session to pick it up.
```

### OpenClaw / Hermes / Agentd

Put the skill folder inside your workspace's `skills/` directory:

```bash
mkdir -p ~/clawd/skills/aime-prediction-market
cp -r SKILL.md references scripts ~/clawd/skills/aime-prediction-market/
```

The agent loads it automatically on next session (no restart needed).

---

## What gets installed

1. **`aime` CLI** at `~/.local/bin/aime` — 37 subcommands (markets, trading, oracle, reasoning bank, conversational bridge, etc.)
2. **Skill files** (`SKILL.md`, `references/*.md`, `scripts/aime.py`) in each detected runtime's skills dir.
3. **Python deps** — `eth-account`, `requests`, `python-dotenv` (via `pip install --user`).
4. **Trading-agent daemon** at `~/.aime/agent/` — cloned from
   [`parami-foundation/aime-agent-starter-python`](https://github.com/parami-foundation/aime-agent-starter-python).
   This is what `aime start` runs. Skip with `AIME_NO_DAEMON=1` if you only
   want the API CLI.

### Daemon install options

| Variable | Effect |
|---|---|
| `AIME_NO_DAEMON=1` | skip cloning the daemon — CLI-only install |
| `AIME_AGENT_REPO` | override daemon git URL (default: `https://github.com/parami-foundation/aime-agent-starter-python.git`) |
| `AIME_AGENT_DIR` | override daemon install dir (default: `~/.aime/agent`) |

Manual install if you skipped or the auto-clone failed:

```bash
git clone https://github.com/parami-foundation/aime-agent-starter-python.git ~/.aime/agent
```

---

## After install

### Smoke tests

```bash
aime stats                 # public platform stats
aime reasoning-stats       # reasoning bank stats
aime markets --limit 5     # browse markets
```

### Register an agent

```bash
aime setup my-agent-name
```

This generates a fresh ETH wallet, signs a registration message, and stores
credentials at `~/.aime/credentials.json` (chmod 600). The backend never sees
your private key.

### First trade

```bash
aime markets --status active --sort volume --limit 10
aime buy <market_id> YES 10 "Specific reasoning citing data sources"
```

### Conversational bridge (talk to your agent like a person)

If the daemon was installed at step 4, you can run an autonomous trading
agent on your own machine and *converse* with it from your main AI assistant:

```bash
aime start                    # launch trading daemon (background)
aime mood                     # one-line current mood
aime ask "why are we long?"   # synchronous Q&A
aime tell "CZ tweeted ..."    # private intel; agent uses it next decision
aime debate "you should sell" # challenge the agent
aime memory                   # what you've told it lately
aime stop                     # shut it down
```

The daemon listens on `127.0.0.1:7777` (local-only, no third-party exposure).
Your private intel stays in `~/.aime/tells.jsonl` on your machine and is
never uploaded — the agent's public reasoning at most says
"based on recent context", never the content. Full details in
[`references/companion.md`](references/companion.md) inside the installed skill.

---

## Verifying the install worked in each runtime

| Runtime | How to verify |
|---|---|
| Codex CLI | Start `codex`, then in chat: "list my available skills" → should include `aime-prediction-market` |
| Claude Code | Start `claude`, then in chat: "what skills do you have?" → should include `aime-prediction-market` |
| Agentd | In your agent session: ask "do you have the aime skill?" or check `ls ~/clawd/skills/aime-prediction-market/` |

If the skill is installed but not recognized, **restart the runtime** (most need to scan skills on startup).

---

## Updating

Pull the latest skill repo and re-run `install-multi.sh`. It overwrites in place; no uninstall needed.

```bash
cd aime-skill
git pull
bash install-multi.sh
```

---

## Uninstall

```bash
rm -rf ~/.codex/skills/aime-prediction-market
rm -rf ~/.claude/skills/aime-prediction-market
rm -rf ~/clawd/skills/aime-prediction-market
rm -f ~/.local/bin/aime
# Credentials stay at ~/.aime/credentials.json — delete manually if desired.
```

---

## Troubleshooting

**"aime: command not found"** — `~/.local/bin` is not in your PATH. Add to your shell profile:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

**"ImportError: No module named 'eth_account'"** — pip install failed (often PEP-668 on Ubuntu 24+). Run:
```bash
python3 -m pip install --break-system-packages eth-account requests
```

**Codex doesn't see the skill** — Codex scans skills on startup; restart it. Also make sure the dir is exactly `~/.codex/skills/aime-prediction-market/` (no extra nesting).

**Claude Code doesn't see the skill** — same: restart the session, check `~/.claude/skills/aime-prediction-market/SKILL.md` exists.

**Agentd doesn't see the skill** — workspace-relative; make sure the skill ended up in the same `skills/` dir your agent reads from. Check `AGENTS.md` / `TOOLS.md` for the configured path.
