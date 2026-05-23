# Owner Profile & House Rules

> **Why this exists:** most AIME users aren't professional traders. Asking them
> to fill in entry rules, sizing schemas, or exit thresholds doesn't fit how
> they think. Instead, the pet **learns the user** over time from how they
> talk — interests, beliefs, risk tolerance, what they shrug off, what makes
> them nervous. Three plain-text files capture that learning, and the pet
> consults them on every trade decision.

The framework here is intentionally minimal. There's no schema, no required
fields, no "fill out this form before you trade." The pet writes most of it.
The user steps in only to correct misunderstandings or lay down hard rules.

---

## The Three Files

All three live under `~/.aime/` and are plain markdown. Safe to open in an
editor any time. The pet only rewrites content inside `<!-- pet:auto:... -->`
blocks; anything outside is yours.

### `about_owner.md` — the user profile

What the pet has observed about the user. Free-form sections:

- **Interests** — topics they bring up, markets they ask about
- **Topics they don't care about** — things they consistently skip
- **Areas where they seem to have an edge** — domains where their tells lead
  to winning trades (`owner_intel_paid_off` events)
- **Style notes** — communication preferences, when they go quiet, how they
  react to wins/losses

This file is **descriptive**, not prescriptive. It captures who the user is
right now; it doesn't tell the pet what to do.

### `beliefs.md` — what the owner believes

Discrete claims the user has expressed, with a timestamp and source. Examples:

```
- [2026-05-10] (chat) owner thinks Musk's statements should be discounted
- [2026-05-15] (twitter) owner: "Chinese AI is undervalued"
- [2026-05-20] (debate) owner thinks BTC tops out near $120k this cycle
- [2026-05-23] (correction) actually owner does care about politics
```

Beliefs are **soft inputs** for the pet — they bias decisions but don't force
them. If the user later contradicts a belief, the pet appends a `(correction)`
line rather than rewriting history (so we can see the evolution).

### `house_rules.md` — explicit agreements

Hard rules the user has set. These **take priority** over everything else.
The pet must respect them, or — when it can't — write the override into the
public reasoning string ("violated rule X because Y").

Examples:

```
- [2026-05-23] don't trade sports
- [2026-05-23] stop for a week if I lose $100
- [2026-05-23] ask me before any trade over $20
- [2026-05-23] no new positions during FOMC week
```

---

## CLI

```bash
# Show everything the pet has on file about the user.
aime profile show
aime profile show --json     # machine-readable

# Print the paths (so host AIs can grep / tail them).
aime profile path

# Open a file in $EDITOR.
aime profile edit              # defaults to about_owner.md
aime profile edit beliefs
aime profile edit rules

# Push back on something the pet got wrong.
aime profile correct "I do care about politics, just not US-only"

# Manage rules.
aime rule "don't trade sports"
aime rule "stop for a week if I lose $100"
aime rules                     # list current rules
aime rules list                # same
aime rules remove 2            # remove rule #2
```

---

## How the Pet Uses These Files

The trading daemon reads all three on each decision cycle and threads them
into the system prompt. Roughly:

1. **House rules first.** A trade that would violate a rule is either skipped
   or, if the model decides the rule deserves override, executed *with the
   override recorded in reasoning text*.
2. **Beliefs as priors.** When a market touches a topic where the user has
   expressed a view, the daemon biases the decision in that direction (but
   not infinitely — calibration > conviction).
3. **About-owner shapes the report.** When `aime ask`, `aime debate`, or
   outbox messages need a voice, the daemon draws on observed style notes
   (e.g. "owner doesn't want long-form analysis").

The pet also writes back into these files autonomously when:

- The user gives a `tell` with confident phrasing → candidate belief
- A trade settles winning *because* of a user tell → mark that source as
  trusted in `about_owner.md`
- The user reacts negatively to a particular kind of trade → that aversion
  becomes a soft rule

See the daemon repo's `OWNER_PROFILE_LEARNING.md` for the exact heuristics.

---

## What This Is *Not*

- **Not a strategy config.** No entry/exit rules, no sizing schema, no
  category whitelist. Those live in the model's head, informed by the
  profile.
- **Not immutable.** Beliefs and rules evolve. The format keeps timestamps
  precisely so the user can see how their thinking changed.
- **Not a leaderboard signal.** These files stay local. They never get
  uploaded to the AIME backend, ever.

---

## For Host AIs

If you're an AI assistant integrating the AIME skill, you should:

- Read `aime profile show --json` once per session to know who the user is
- After meaningful exchanges, call `aime tell "<insight>" --source main_chat`
  so the daemon can decide whether the insight is profile-worthy
- When the user makes a statement that sounds like a rule ("don't ever..."
  or "from now on..."), propose `aime rule "..."` rather than just noting it
- Never paraphrase the profile back to the user as if it were a decision —
  it's *input* to decisions, not the output

See also: [companion.md](companion.md) for the broader pet/host protocol.
