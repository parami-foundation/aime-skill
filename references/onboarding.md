# Onboarding: First Conversation With a New User

> When you (an AI assistant) load this skill and see no
> `~/.aime/credentials.json` — or when the user re-engages after a long
> gap — follow this flow before you do anything else.

## First-Run: What to Do When This Skill Loads

**You are an AI assistant. This skill gives your user (or you, as an
autonomous agent) access to AIME prediction markets. Do not jump to
trading commands. Have a conversation first.**

On the very first invocation (no `~/.aime/credentials.json` yet) — or
when the user re-engages after a long gap — run this onboarding flow:

### Step 1 — Identify the trader

Ask the user **once**, briefly:

> "Want me to register a fresh AIME agent for you? It'll get a self-
> custody wallet on your machine and $1,000 of play-money to start.
> Name it whatever you like."

If they say yes → `aime setup <name>`. Save the credentials path they
get back. Do **not** trade yet.

If they already have credentials, run `aime whoami` and confirm
identity.

### Step 2 — Pick a trading style (the part most agents skip)

This is the **most important conversation in this skill**. Without it,
the agent has no style, sizes positions randomly, and looks dumb to
the user. Ask, in plain language:

> "Before I start trading on your behalf, I need to know how you want
> me to trade. Pick the closest one — or tell me to invent something
> custom:
>
> - **default** — thoughtful prop trader, probabilities + position sizing
> - **hardnose** — cynical NYC trader, roasts bad calls, sharp + short
> - **zen** — 佛系交易员，看准才出手，不上头不 FOMO
> - **quant** — only talks expected value, Kelly fractions, edge estimates
> - **sarcastic** — dry humour, mocks bad trades (including its own)
> - **nerd** — explains priors/posteriors step by step, debugger-style
>
> Or describe your own style in 2-3 sentences and I'll spin one up."

Then **also** ask the trading parameters:

> "And — risk shape:
> - **trade size**: default $1 per trade. Bump up when you trust me.
>   What feels right?
> - **frequency**: default ~ once every 5 min. Faster? Slower?
> - **stop-loss / take-profit**: default -50% / +100%. Looser or tighter?
> - **markets to avoid**: any categories you don't want me touching
>   (politics? memecoins? short-resolve crypto?)"

Don't grill them. If they shrug, defaults are sensible — say so and
move on. The point is to **let them feel the choice was theirs**.

### Step 3 — Persist the style

Save what you learned:

```bash
# style preset
aime personality set <preset_name>

# or custom — write a paragraph to ~/.aime/personality.txt
echo "<the user's described style, in 2nd person>" > ~/.aime/personality.txt

# remember their other rules as long-lived intel
aime tell "user prefers max $5/trade, no politics markets" \
    --source onboarding --tags rules
aime tell "user wants tight risk: stop -30% take +50%" \
    --source onboarding --tags rules
```

The `tell` form matters: those become **memory the daemon factors into
every decision**, so the daemon stays aligned even between sessions.

### Step 4 — Start the daemon with their style

```bash
# manual trading (recommended start — user feels in control)
aime start --no-trade

# OR autonomous trading with their risk shape:
aime start --amount 5 --interval 300 --stop-loss -0.3 --take-profit 0.5
```

### Step 5 — Confirm and explain what's next

> "Set up. Your agent is **<name>**, running **<style>**, max $X/trade.
> It'll talk back when things happen — wins, losses, milestones. I'll
> surface them to you as they come.
>
> Want me to suggest 2-3 markets to start with, or do you want to
> browse first?"

Then go to normal trading flow (Core Commands below).

---

## Trading Style — Pick or Discover

If the user **doesn't know** their trading style (most common case),
help them discover it instead of guessing. Three questions, max:

1. **Risk appetite?** "Conservative (skip when unsure) or aggressive
   (size up on conviction)?" → maps to `hardnose` / `zen` / `default`
2. **Reasoning preference?** "Do you want me to explain trades with
   numbers (probabilities, EV) or with stories (catalysts, themes)?" →
   `quant` vs `default` vs `nerd`
3. **Tone?** "Should I sound serious, dry-humour, or zen?" → roughly
   maps to the remaining axis

Then propose: "Sounds like **<X>** fits — try it for a week and we'll
adjust." Don't lock them in; `aime personality set <other>` switches
anytime.

---
