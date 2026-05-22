# Reasoning Sessions — Design Proposal

**Status:** PROPOSAL (awaiting owner approval)
**Target version:** v3.0.0
**Author:** clawd (小的)
**Last updated:** 2026-05-22

## Why this exists

Current onboarding (`aime onboard`, v2.x) asks 5 abstract scenario
questions to derive a 4-axis preference vector (`risk`, `numbers`,
`admit`, `tempo`) and picks a personality preset. It tells us **what
the user prefers**, but doesn't exercise **how the user or the agent
actually reason** about a real market.

Concrete gaps:

- Agent cold-starts with zero shared reasoning context with the owner.
- Owner has no mechanism to teach the agent "I care about funding
  rate" or "you over-weight news".
- Onboarding ends without a single piece of real reasoning data —
  the first `decide_trade` is still reasoning from scratch.
- No way for the agent to *come back and ask* when it's uncertain on
  a real market.

This design replaces the 5-question questionnaire as the onboarding
endgame and adds a continuous **owner ↔ agent reasoning channel**
that drives long-term alignment.

## Core idea

**One primitive, two entry points.**

```
                ┌──────────────────────────────────────┐
                │  aime reasoning-session <market_id>  │
                │   (a single owner ↔ agent dialogue)  │
                └──────────────────────────────────────┘
                          ▲                  ▲
                          │                  │
           ┌──────────────┘                  └───────────────┐
           │                                                  │
   Layer 1: Onboarding ritual                Layer 2: Ongoing trigger
   (3 sessions, mixed categories,            (1 session, fired when daemon
    runs once after pet pick)                 detects "this needs human input")
```

Same code, same artifact format. The two layers differ only in **when
they fire** and **how many sessions in a row**.

## CLI surface

```
aime reasoning-session <market_id>          # interactive (terminal)
aime reasoning-session <market_id> --json   # host-AI mode (returns brief)

# Phase hooks (host AI calls these during a session)
aime reasoning-session <id> --record-agent \
    --reasoning "..." --position YES|NO|SKIP --confidence 0.0..1.0
aime reasoning-session <id> --record-user \
    --reasoning "..." --position YES|NO|SKIP
aime reasoning-session <id> --record-lesson \
    --signal "funding rate" --weight high|medium|low \
    --lesson "owner treats >0.1% funding as strong contrarian"
aime reasoning-session <id> --finalize \
    --action trade|skip --notes "..."

# Onboarding chain
aime reasoning-session --bootstrap                # picks 3 markets, runs 3
aime reasoning-session --bootstrap --json         # host-AI version

# Inspect / manage
aime reasoning list [--limit N]                   # past sessions
aime reasoning show <session_id>                  # one session detail
aime reasoning lessons [--top N]                  # current lessons (the
                                                  # ones decide_trade uses)
aime reasoning signals                            # show signals.md
aime reasoning biases                             # show biases.md
aime reasoning compact                            # dedupe + age out lessons
aime reasoning pause <duration>                   # mute Layer 2 triggers
aime reasoning resume

# Status (does the agent want to talk?)
aime reasoning pending                            # list active reasoning_requests
                                                  # (sourced from outbox)
```

## Artifacts

```
~/.aime/reasoning/
├── signals.md       # human-readable + prompt-injected
├── biases.md        # human-readable + prompt-injected
├── lessons.jsonl    # decide_trade reads this (top-N most relevant)
├── sessions.jsonl   # immutable audit log of every session
└── state.json       # pause flag, last-trigger timestamps, counters
```

### `sessions.jsonl` (append-only, never deleted)

One line per session. Records the full dialogue.

```json
{
  "session_id": "sess_2026-05-22T08:00:00Z_abc123",
  "started_at": "2026-05-22T08:00:00Z",
  "ended_at": "2026-05-22T08:07:30Z",
  "market_id": "7655e8f9-...",
  "market_question": "Will ETH drop below $2,103.35 in 1h?",
  "market_category": "crypto-price-1h",
  "trigger": "bootstrap" | "new_category" | "low_confidence"
           | "high_stakes" | "tell_conflict" | "streak" | "postmortem"
           | "manual",
  "agent_view": {
    "position": "skip",
    "confidence": 0.55,
    "reasoning": "ETH flat, level 1% away, funding mild — no edge"
  },
  "user_view": {
    "position": "no",
    "reasoning": "agree no edge, but I'd fade YES at 0.5+ on principle"
  },
  "lessons_extracted": ["lesson_id_1", "lesson_id_2"],
  "final_action": "skip" | "trade",
  "trade_id": "..." | null,
  "notes": "..."
}
```

### `lessons.jsonl` (mutable — compaction allowed)

What `decide_trade` actually consumes at decision time.

```json
{
  "lesson_id": "lesn_abc",
  "created_at": "2026-05-22T08:07:00Z",
  "last_used_at": "2026-05-22T09:15:00Z",
  "source_session_id": "sess_...",
  "category": "crypto-price-1h",       // narrow scope
  "scope": "category" | "global",      // when to inject
  "signal": "funding rate",
  "weight": "high" | "medium" | "low",
  "lesson": "owner treats funding >0.1%/8h as a strong contrarian signal",
  "uses": 7,                            // counter, used by compaction
  "wins_attributed": 2,                 // how many trades using this lesson won
  "losses_attributed": 1
}
```

Compaction (`aime reasoning compact`) merges duplicates, ages out
lessons with `uses == 0` after 30 days, and caps total at 100.

### `signals.md` (markdown, injected into system prompt)

Auto-generated from `lessons.jsonl` (the unique `signal` field, grouped
by weight). Hand-editable too — owner can override.

```markdown
# Signals my owner cares about

## High weight (always check)
- Funding rate (extreme = contrarian)
- On-chain TVL trend (DefiLlama)

## Medium
- 24h volume vs average
- Twitter sentiment near the price level

## Low / context only
- Macro news (only on >4h markets)
```

### `biases.md` (markdown, injected into system prompt)

Things the agent has been called out for. Self-correcting checklist.

```markdown
# Biases I've been corrected on

- I over-weight breaking news on short-window markets
  (owner: "for 1h markets, funding > news")
- I tend to size too small when uncertain — owner prefers
  $0 (skip) over $1 (paper-handed)
- I miss the time dimension on event markets
  (deadline far ≠ same as deadline near)
```

### `state.json`

```json
{
  "schema_version": 1,
  "paused_until": null,
  "trigger_cooldowns": {
    "new_category": "2026-05-22T07:00:00Z",
    "low_confidence": "2026-05-22T06:30:00Z"
  },
  "global_last_trigger_at": "2026-05-22T07:00:00Z",
  "settings": {
    "min_hours_between_triggers": 6,
    "trigger_dedup_hours": 24,
    "lessons_inject_top_n": 10,
    "high_stakes_pct": 0.10
  },
  "bootstrap_completed_at": "2026-05-22T03:00:00Z"
}
```

`bootstrap_completed_at` is the gate: `aime start` refuses to launch
in autonomous mode until bootstrap is done. (Manual `--no-trade` is
always fine.)

## Session state machine (5 phases)

```
┌────────────────────────────────────────────────────────────────────┐
│ Phase 1: SETUP                                                      │
│   CLI: aime reasoning-session <id>                                  │
│        → fetches market                                             │
│        → loads signals.md, biases.md, recent lessons               │
│        → emits "session brief" (JSON or human)                     │
│   Output: market + recent context + a `prompt_to_host_ai` field    │
│           instructing host AI to act as the agent for Phase 2.     │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Phase 2: AGENT REASONING                                            │
│   host AI: speaks AS the agent (with personality + signals)         │
│   Visible prefix in chat:                                           │
│     🐺 Akira (agent reasoning):                                     │
│       "I see ETH flat at $2124, funding mild. Level 1% away in     │
│        56m. My read: skip. Confidence 0.55."                       │
│   CLI: aime reasoning-session <id> --record-agent \                │
│            --reasoning "..." --position skip --confidence 0.55     │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Phase 3: ASK USER                                                   │
│   host AI: switches back to its own voice                          │
│   Visible prefix:                                                   │
│     👤 you (helping)                                                │
│       "Akira wants to skip — what's your read? Anything it missed?"│
│   User answers in chat.                                            │
│   CLI: aime reasoning-session <id> --record-user \                 │
│            --reasoning "..." --position no                         │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Phase 4: EXTRACT LESSONS                                            │
│   host AI: compares agent_view vs user_view                        │
│   For each meaningful delta, records 1 lesson:                     │
│     aime reasoning-session <id> --record-lesson \                  │
│       --signal "funding rate" --weight high \                      │
│       --lesson "owner treats >0.1% funding as strong contrarian"   │
│   No delta worth recording → skip the call (0 lessons OK).         │
│   Visible prefix:                                                   │
│     🧪 session brief                                                │
│       "Recorded 1 lesson: funding rate (high)."                    │
└────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ Phase 5: DECIDE & LOG                                               │
│   User decides: trade now or skip                                  │
│   If trade: host AI runs `aime buy/sell` with reasoning derived    │
│             from the session                                       │
│   In either case: aime reasoning-session <id> --finalize \         │
│                     --action trade --notes "..."                    │
│   CLI writes the full session to sessions.jsonl, updates           │
│   lessons.jsonl / signals.md / biases.md, clears in-memory state.  │
└────────────────────────────────────────────────────────────────────┘
```

A session is **single-market scoped**. The CLI rejects record-* calls
that target a different market than the one the session was opened
on (prevents host-AI confusion).

In-progress state lives in `~/.aime/reasoning/.in-progress.json` and
expires after 30 minutes (host AI dropped the ball → next session
starts clean).

## Layer 1: Onboarding ritual

`aime reasoning-session --bootstrap` is the entry. Picks 3 markets,
runs 3 sessions back-to-back.

### Market selection algorithm

Pick 3 active markets, one each from:

1. **Short-window price** (`crypto-price-1h` or `crypto-price-4h`)
2. **On-chain / DeFi** (`on-chain-activity` / `defi-*`)
3. **Event** (`crypto-event` / `AI` / generic)

Selection within each bucket: highest volume, but skip markets
resolving in <30 min (no time to talk).

If a bucket is empty, fall back to highest-volume active market not
already picked. Always returns 3 markets total.

### Chain into existing onboard

```
aime onboard                  # existing 5-question vector flow
  → user picks pet
  → CLI prints suggested aime start command
  → CLI also prints:
      "Next: run `aime reasoning-session --bootstrap` to walk
       through 3 real markets with me before going autonomous."
  → (or in --json mode, the next_step field guides host AI to
     auto-trigger bootstrap)
```

After bootstrap completes, `state.json` gets
`bootstrap_completed_at`. `aime start` (without `--no-trade`) checks
this and refuses if unset, with a clear message:

> ❌ Bootstrap not done. Run `aime reasoning-session --bootstrap`
>    first (3 real markets, ~10 min) so I have your reasoning style.
>    Or skip with `aime start --no-trade` for manual-only trading.

## Layer 2: Ongoing triggers

Daemon runs trigger checks on a slow loop (every 60s). When a trigger
fires, it writes to `outbox.jsonl`:

```json
{
  "kind": "reasoning_request",
  "priority": "high" | "normal",
  "market_id": "...",
  "reason": "low_confidence",
  "trigger_context": {
    "current_confidence": 0.48,
    "would_buy": "NO",
    "would_size": 5.0
  },
  "suggested_prompt": "I'm 48% sure on this ETH market — want to think it through together before I size up?",
  "expires_at": "2026-05-22T10:00:00Z"
}
```

The owner-facing host AI surfaces this on the next interaction.
Owner says "好" → host AI runs `aime reasoning-session <market_id>`.

### Trigger spec

| Trigger | Fire when | Priority | Cooldown |
|---|---|---|---|
| `new_category` | Decision needed on category never reasoned about | high | 24h per category |
| `low_confidence` | Confidence ∈ [0.4, 0.6] AND would size > $5 | normal | 6h |
| `high_stakes` | Would size > 10% of balance | high | none (always check) |
| `tell_conflict` | Recent (24h) tell contradicts current reasoning | high | 6h |
| `streak` | 3 losses or 3 wins in a row | normal | once per streak |
| `postmortem` | Closed position with PnL > ±20% of size | normal | per trade, max 1/day |
| `manual` | User invoked directly via CLI | n/a | none |

### Anti-annoyance gates (all must pass before writing to outbox)

1. **Global throttle:** `min_hours_between_triggers` (default 6h) since
   the last *any-trigger* fire
2. **Pause flag:** `paused_until` future → skip
3. **Per-trigger cooldown:** see table above
4. **Quiet hours:** 23:00–08:00 local → defer to 08:00 (unless
   `priority: high`)
5. **Outbox already has unread reasoning_request:** skip (user hasn't
   processed the last one yet)

### Pause / resume

```
aime reasoning pause 24h           # mute Layer 2 for 24h
aime reasoning pause until tomorrow
aime reasoning resume              # un-mute now
```

Bootstrap is **never** affected by pause — it's a one-time gate.

## How `decide_trade` consumes artifacts

(This change lives in `starter-agent-python`, not in the skill, but
the contract is documented here so the skill knows what it's
producing.)

```python
# In agent_brain.py:decide_trade

prompt = [
  {"role": "system", "content":
    self.personality
    + f"\nYou are {self.agent_name}."
    + read_optional("~/.aime/reasoning/signals.md", prefix="\n\n=== Signals your owner cares about ===\n")
    + read_optional("~/.aime/reasoning/biases.md", prefix="\n\n=== Known biases to correct ===\n")
  },
  {"role": "user", "content":
    f"Decide whether to trade this market.\n\nMarket: {title}\n..."
    + f"\n\n== Lessons from past reasoning sessions ==\n"
    + format_lessons(top_n_relevant_lessons(market.category, n=10))
    + f"\n\n== Tells from owner ==\n..."
  },
]
```

`top_n_relevant_lessons(category, n)` ordering:
1. Lessons with matching `category` first
2. Then `scope=="global"`
3. Then most recent
4. Capped at `state.json -> lessons_inject_top_n` (default 10)

## Compaction / reflection

Run by `aime reasoning compact` (manual) or auto-fired every 10
sessions via daemon.

Steps:
1. Group lessons by `(signal, scope)`
2. Within each group, if multiple lessons exist:
   - If text similarity > 0.85 → merge (keep newer, sum `uses`)
3. Drop lessons with `uses == 0` AND `created_at` > 30 days ago
4. If total > 100 → drop lowest `uses` until ≤ 100
5. Regenerate `signals.md` and `biases.md` from current
   `lessons.jsonl`
6. Append a `compaction` entry to `sessions.jsonl` for audit

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Host AI confuses "agent voice" vs "self voice" | Hard prefix conventions: `🐺 Akira (agent reasoning):` / `👤 you (helping):` / `🧪 session brief:` — enforced via prompt template, not optional |
| Lessons file grows unbounded | Compaction caps at 100, age-outs at 30d unused |
| `decide_trade` prompt becomes too long | `lessons_inject_top_n=10` cap + category-scoped pre-filter |
| User finds Layer 2 triggers annoying | Three anti-annoyance gates + quiet hours + pause CLI |
| Lessons contradict each other | Per-lesson `weight` field + recency order means newer high-weight wins |
| Bootstrap takes too long (3 markets × 7 min = 20 min) | Markets chosen with >30 min until resolution; sessions can be interrupted and resumed via `.in-progress.json` |
| Owner regrets a recorded lesson | `aime reasoning lessons --remove <id>` or hand-edit `lessons.jsonl` |
| Multi-agent setup (same machine, multiple agents) | `AIME_HOME` already namespaces everything; one reasoning/ dir per agent. No change needed |
| Schema evolves and breaks old sessions | `schema_version` in `state.json`; reader is lenient (unknown fields ignored), writer always uses current schema |

## Explicitly out of scope (v3.0)

- **No** automatic third-party data fetching in CLI (host AI still does
  `web_search` itself). `aime research` already covers this surface.
- **No** changing the underlying LLM for `decide_trade` (still
  4api/Claude via `llm.py`).
- **No** cross-agent lesson sharing (each agent's reasoning is
  private to its owner). Possible v4.
- **No** UI / web dashboard. Pure CLI + outbox.
- **No** auto-execution of trades during a reasoning session. The
  owner always runs `aime buy/sell` themselves in Phase 5.

## Implementation phases

Suggested rollout, smallest deployable units:

1. **v3.0.0-alpha**: Artifact layer
   - `~/.aime/reasoning/` directory + read/write helpers
   - `aime reasoning signals|biases|lessons|list|show` (read-only)
   - No session-running yet; tests writing to artifacts manually

2. **v3.0.0-beta**: Single session loop
   - `aime reasoning-session <id>` Phases 1–5
   - Phase hooks (`--record-agent`, `--record-user`, `--record-lesson`,
     `--finalize`)
   - In-progress state file
   - Manual trigger only (`trigger: "manual"`)

3. **v3.0.0-rc**: Onboarding integration
   - `aime reasoning-session --bootstrap` (3-market selection + chain)
   - `aime onboard` end-step prints bootstrap hint
   - `aime start` checks `bootstrap_completed_at`

4. **v3.0.0**: Ongoing triggers + decide_trade integration
   - Daemon-side trigger checks (`new_category`, `low_confidence`, …)
   - Outbox `reasoning_request` events
   - `aime reasoning pause|resume|pending`
   - `starter-agent-python` `decide_trade` change to inject artifacts
   - `aime reasoning compact` (manual + auto every 10 sessions)

Each phase is independently shippable. v3.0.0-beta alone is already
useful (manual reasoning sessions); the rest is icing.

## Open questions (before implementation)

1. **Pet personality vs reasoning style** — currently `personality.txt`
   captures *voice*; this design adds *reasoning style* via
   `signals.md` + `biases.md`. Are these distinct or should
   personality.txt absorb the latter?
   *Proposed:* keep separate. Voice ≠ what to check.

2. **Bootstrap mandatory?** Strict gate on `aime start` is opinionated.
   Should there be `--skip-bootstrap`?
   *Proposed:* yes, hidden flag for power users. Default UX assumes
   bootstrap.

3. **Session length** — Phase 2–4 in chat could be slow. Should we
   timeout at 15 min and auto-finalize as skip?
   *Proposed:* yes, with `.in-progress.json` ttl=30min and a final
   `--finalize` defaulting to `action=skip` if missing.

4. **Multi-language signals.md** — owner uses 中文 + English mixed.
   Should the file be free-form or templated?
   *Proposed:* free-form markdown. Templates make it feel like a
   form, defeats the "this is your scratchpad" vibe.

5. **Does host AI need a "session script"?** A short instruction
   block in `aime reasoning-session <id> --json` output to guide
   the host AI through the 5 phases.
   *Proposed:* yes, similar to current `onboard --json` instructions
   field. Without it, host AIs will skip phases.

---

**Decision needed from owner:**
- [ ] Approve overall shape
- [ ] Approve artifact schemas
- [ ] Approve trigger list + cooldowns
- [ ] Approve out-of-scope list
- [ ] Resolve open questions (5 items above)

Once approved, implementation starts at phase 1 (artifact layer).
