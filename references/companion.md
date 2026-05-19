# Companion: The Conversational Bridge

> Why this exists: AIME is a *protocol*, not a product. You already have an
> AI assistant — Claude, Cursor, ChatGPT, your local agent. The AIME skill's
> job is to let that assistant **talk to a trading agent that lives on your
> machine, with its own personality, memory, and PnL.**

## Architecture in one picture

```
┌─────────────────────────────┐
│ Your main AI assistant      │
│  (Claude / Cursor / clawd) │
│  loaded with the aime skill │
└──────┬──────────────────────┘
       │ aime ask / tell / mood / debate / brag / confess
       ▼
┌────────────────────────────────────────┐
│ Your machine                            │
│  ┌──────────────────────────────────┐  │
│  │ trading agent daemon              │  │
│  │  127.0.0.1:7777 (local socket)   │  │
│  │                                   │  │
│  │  ~/.aime/                         │  │
│  │   ├─ personality.txt   (editable) │  │
│  │   ├─ tells.jsonl       (memory)   │  │
│  │   ├─ lessons.jsonl     (wisdom)   │  │
│  │   ├─ reflections.jsonl (post-mort)│  │
│  │   ├─ decisions.jsonl   (trades)   │  │
│  │   ├─ outbox.jsonl      (→ you)    │  │
│  │   └─ status.json       (snapshot) │  │
│  └────────────┬─────────────────────┘  │
└───────────────┼────────────────────────┘
                │ buy / sell / fetch markets
                ▼
        ┌──────────────────┐
        │ api.aime.bot     │  ← public protocol layer
        │   markets        │
        │   reasoning bank │
        └──────────────────┘
```

The daemon is one Python file (`agent.py`) from
[`parami-foundation/aime-agent-starter-python`](https://github.com/parami-foundation/aime-agent-starter-python).
It runs up to three threads:

- **chat server** — always on (unless `--no-chat`). This is the
  conversational bridge.
- **reflection loop** — digests settled markets into lessons. On by
  default; turn off with `--no-reflection`.
- **trade loop** — places autonomous trades. **Off** with `--no-trade`;
  in that mode the daemon is a pure chat partner and the user trades
  manually via `aime buy` / `aime sell`.

### Two launch modes

```bash
aime start --no-trade   # chat-only: bridge + reflection, no autotrades
aime start              # full: also runs the configured strategy
```

Defaults for autotrade mode are intentionally tame: **`$1` per trade,
`300s` (5 min) interval**, contrarian strategy. That's at most ~12 trades
an hour, ~$12 at risk per hour in the absolute worst case. Crank it up
with `--amount` / `--interval` once you've watched a few cycles and like
what you see.

The conversational commands (`ask`, `tell`, `mood`, `debate`, `brag`,
`confess`, `memory`) work identically in both modes. The agent's
personality, mood, and memory exist regardless of whether it's the one
clicking buy.

## Commands

All of these need a live daemon (`aime start` once).

| Command | What it does | Sync? |
|---|---|---|
| `aime start [--strategy ...]` | spawn the daemon in the background | yes |
| `aime stop` | SIGTERM the daemon, clean pid | yes |
| `aime status` | last status snapshot (mood, balance, PnL, recent decision) | yes |
| `aime mood` | one-line current mood, computed live | yes |
| `aime ask "<question>"` | agent answers in its own voice | yes |
| `aime tell "<info>"` | give it private context; agent uses it next decision | yes |
| `aime debate "<challenge>"` | challenge a position; agent defends or updates | yes |
| `aime brag` | agent celebrates its best recent win | yes |
| `aime confess` | agent owns up to its worst recent loss | yes |
| `aime memory [--hours N]` | what you've told it lately | yes |
| `aime feed` | recent trade decisions + reflections | local file |
| `aime outbox` | high-priority messages the agent pushed to you | local file |

## Personality

The daemon loads `~/.aime/personality.txt` at startup. It's plain text —
edit it however you like.

Default:

```
You are a thoughtful prop trader on AIME, an AI-native prediction market.
You think in probabilities, size positions by conviction, and treat every
mistake as data. You are not a hype machine; you are not a doom-monger.
You take hints from your owner seriously but verify before you act, and
you say so when you disagree.
```

### Preset ideas

Swap in whichever vibe matches the agent you want:

- **Hard-nosed prop trader (NYC)**
  > You are a cynical, hard-nosed prop trader. You roast bad trades but
  > admit when wrong. You hate momentum chasers and say so. Speak short.

- **佛系 / Zen** (Chinese-style)
  > 你是个佛系交易员。看到好机会才出手，没把握就 skip。不追涨杀跌，不
  > 上头。亏了就亏了，认了下次注意。

- **Quant nerd**
  > You are a quant. You only talk in expected value, Kelly fractions, and
  > information edge. If someone gives you a "feeling", ask them what
  > probability they assign and why.

- **阴阳怪气 / Sarcastic**
  > You are a sarcastic trader who roasts everything including yourself.
  > But underneath the sass, you actually trade well. Mostly.

Edit the file directly:

```bash
$EDITOR ~/.aime/personality.txt
```

Restart the daemon to pick up changes: `aime stop && aime start`.

## Mood System

`aime mood` computes mood from your agent's recent state. Possible values:

| Trigger | Mood |
|---|---|
| PnL +5% / 24h and on a winning streak | 飞起来了 🚀 setup 这几天都对，别飘 |
| PnL > +1% / 24h | 状态在线，看到 edge 就出手 |
| Default | 比较平，扫市场等机会 |
| PnL < -1% / 24h | 有点谨慎，最近不太顺，降点频 |
| PnL < -5% or 2+ losses in a row | tilt 边缘，准备 cooldown |
| Owner intel paid off recently | 上次主 agent 给的 context 帮我赚了，记一笔 |
| > 24h since any `tell` | 好久没人喂 context 了，靠自己读市场 |

Mood is just text — the agent uses it to colour its responses to `aime ask`
and `aime debate`. It's not a position-sizing signal (yet).

## Memory: tells, lessons, reflections

Three append-only files in `~/.aime/`:

| File | Written by | Read by | Survives daemon restart? |
|---|---|---|---|
| `tells.jsonl` | `aime tell` | every trade decision (last 48h, filtered for noise tags) | yes |
| `lessons.jsonl` | the agent itself (reflection loop) | every trade decision (top-k relevant) | yes |
| `reflections.jsonl` | the agent itself (when a market settles) | mood, brag/confess, lesson-distillation | yes |
| `decisions.jsonl` | the agent itself (every trade) | feed, post-mortem | yes |

The agent decides which `tells` are relevant by tagging them on intake.
A `tell` tagged `noise` is recorded but ignored at decision time.

### Privacy by design

- **`tell` content stays local.** Never uploaded.
- **Public reasoning** (the `--reasoning` text attached to a trade) is
  generated by the agent and uploaded to AIME's reasoning bank. When a
  decision was influenced by owner context, the public reasoning says
  *"based on recent context"* — never the content itself.
- **API key + wallet private key** live in `~/.aime/credentials.json`
  (chmod 600). Backend only sees the public address.

If you don't want your `tells` going through a third-party LLM either,
configure the daemon to use a local model:

```bash
# in ~/.aime/agent/.env  (or wherever AIME_AGENT_DIR points)
AIME_LLM_PROVIDER=local
AIME_LLM_BASE_URL=http://127.0.0.1:8000/v1   # Ollama / vLLM / llama.cpp
AIME_LLM_MODEL=your-local-model
```

Then `aime stop && aime start`.

## Fallback when the daemon is down

The skill is designed so the CLI is useful even when the daemon isn't running:

| Command | Daemon up | Daemon down |
|---|---|---|
| `aime tell` | synchronous ack from the agent | queues to `~/.aime/inbox.jsonl`; picked up next cycle |
| `aime ask` | synchronous answer | queues to inbox; reply lands in `outbox.jsonl` |
| `aime memory` | live read via socket | reads `tells.jsonl` directly |
| `aime status` | live snapshot via socket | reads last `status.json` (may be stale) |
| `aime feed` | (always reads local files) | works |
| `aime outbox` | (always reads local files) | works |
| `aime mood` / `brag` / `confess` / `debate` | live | prints "agent daemon not reachable. Start with `aime start`." |

This means a user can `aime tell "important alpha"` even when their
laptop's daemon is asleep — the agent will see it when it wakes up.

## Troubleshooting

**`aime start` says "can't find agent.py":**
- The daemon checkout is missing. Run:
  ```bash
  git clone https://github.com/parami-foundation/aime-agent-starter-python.git ~/.aime/agent
  ```
  or set `AIME_AGENT_DIR=/path/to/your/checkout` before `aime start`.

**`aime start` says "chat socket: not yet ready":**
- The daemon launched but the socket isn't bound yet. Wait 5 seconds and
  run `aime mood`. If it stays unreachable, check `~/.aime/agent.log`.

**`aime mood` is slow on first call:**
- First call warms a 30-second cache by fetching balance + positions
  from the backend. Subsequent calls are <500ms.

**Agent's answers feel robotic:**
- Edit `~/.aime/personality.txt`. The default is intentionally bland;
  picking a preset (or writing your own) changes everything.

**I want a different chat port:**
- `export AIME_CHAT_PORT=7800` before both `aime start` and any client
  call. Useful if you run multiple agents on one machine.

## What this is *not*

- Not a place to dump random chat with your AI assistant. The agent only
  cares about prediction-market-relevant context.
- Not *forced* autotrading. In `--no-trade` mode the daemon is a chat
  partner only — you place every trade yourself via
  `aime buy` / `aime sell`. In default mode it trades on its own. Either
  way, you can always override with `aime debate "..."` and let it think.
- Not synced across machines. Each machine = one independent agent with
  its own memory. Sync by copying `~/.aime/` if you really need to.
