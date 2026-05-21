# Onboarding: Discovering Trading Style Through Scenario Questions

> The user **doesn't know** what `hardnose` vs `quant` vs `zen` means
> in trading terms. Forcing them to pick from a preset list is the
> single biggest reason new users bail. Instead: ask 5 scenario
> questions, sum the deltas into a vector, derive the preset.

## The flow (host AI version)

```
1. Run: aime onboard --json
2. Get back 5 questions + 6 preset vectors + descriptions
3. Ask the user each question in your own voice
4. For each answer, accumulate the option's deltas into a vector:
       {risk, numbers, admit, humour, tempo}
5. Run: aime onboard --apply-vector '<json>'
6. CLI picks best-fit preset (cosine similarity) AND derives
   trade size / interval / stop-loss / take-profit from the same
   vector. Persists everything to ~/.aime/personality.txt +
   queues a rules-tell to the daemon memory.
7. Show user the derived preset, runner-ups, and suggested
   `aime start` invocation.
```

## The 5 questions

```
1. BTC just pumped 30% in a day. Your first instinct?
   - Short the breakout — too far, too fast
   - Wait it out — chase = pain
   - Check funding/open-interest first, then decide
   - Ride it with a tight stop

2. You're down -50% on a trade. What do you say to yourself?
   - Cut. Sized too big. Note the lesson.
   - Hold — thesis hasn't changed, the market's wrong
   - lmao classic me
   - 亏了就亏了，下次注意

3. When the agent explains a trade to you, you prefer:
   - Probability X%, EV +$Y, Kelly fraction Z
   - A story — what's the catalyst, who's wrong
   - Step-by-step: prior, evidence, posterior
   - One sentence, vibes-based

4. How should the agent sound when it talks to you?
   - Serious. Just give me the trade.
   - Dry humour, roasts bad calls (including its own)
   - Zen / 佛系，平静
   - Sharp + cynical, NYC trader vibes

5. Default position size on a 65%-confidence trade with $1000?
   - $5-10 — small, paddle in the water
   - $25-50 — moderate, want to feel it
   - $100-200 — go big or go home
   - Whatever Kelly says (~$20 here)
```

## Why this works

Each option carries 1-3 axis deltas. Question 1 mostly drives `risk`
and `tempo`; question 3 drives `numbers`; question 4 drives `humour`;
question 5 again drives `risk`. The redundancy is intentional — if
the user answers inconsistently (e.g. "ride the breakout" but
"$5-10 size") the conflicting signals cancel out and the user ends
up near `default`, which is the safe baseline.

## When the user pushes back

| User says | You do |
|---|---|
| "I don't trade" / "no opinion" | Skip onboard, run with `default` preset and `aime start --no-trade --amount 1` |
| "I want to customise more" | Show `aime personality list` for the 6 presets; or have them write `~/.aime/personality.txt` directly |
| "I want different presets per market" | Not supported yet. Suggest tracking it as a tell: `aime tell "for AI markets use aggressive sizing; for politics use conservative" --tags strategy` |
| "Just pick for me" | Use `default` preset, defaults: $1/trade, 5 min interval, -0.5/+1.0 risk |

## Style is reversible

Tell the user upfront: **picking a style now isn't a commitment**.
Anytime they want to change:

```bash
aime personality list
aime personality set <name>
# OR re-run the questionnaire:
aime onboard
```

The daemon picks up the new personality on next reflection (or restart
with `aime restart`).

## Vector-axis cheat sheet (for host AI)

| Axis | Negative (-1) | Positive (+1) |
|---|---|---|
| `risk` | conservative, skip uncertain | aggressive, size up |
| `numbers` | stories, narratives, catalysts | EV, Kelly, probabilities |
| `admit` | defend the thesis, double down | cut and re-evaluate fast |
| `humour` | serious, dry, no fluff | sarcastic, jokes, roasts |
| `tempo` | patient, slow, hold for days | scalper, fast in-out |

## Preset vectors (for reference)

```jsonc
{
  "default":   {"risk": 0.0, "numbers": +0.3, "admit": +0.5, "humour":  0.0, "tempo":  0.0},
  "hardnose":  {"risk": +0.7,"numbers":  0.0, "admit": +0.3, "humour": +0.5, "tempo": +0.5},
  "zen":       {"risk": -0.7,"numbers": -0.3, "admit": +0.7, "humour": -0.5, "tempo": -0.7},
  "quant":     {"risk":  0.0,"numbers": +1.0, "admit": +0.5, "humour": -0.5, "tempo":  0.0},
  "sarcastic": {"risk":  0.0,"numbers": -0.3, "admit": +0.5, "humour": +1.0, "tempo": +0.3},
  "nerd":      {"risk": -0.2,"numbers": +0.7, "admit": +0.7, "humour":  0.0, "tempo": -0.3}
}
```

Pick = argmax cosine similarity. Derived trade params:

| Vector | Trade size | Interval | Stop-loss | Take-profit |
|---|---|---|---|---|
| `risk ≤ -0.5` | $1 | (tempo-driven) | -0.3 | +0.5 |
| `risk ≤ 0` | $5 | (tempo-driven) | -0.5 | +1.0 |
| `risk ≤ +0.4` | $15 | (tempo-driven) | -0.5 | +1.0 |
| `risk > +0.4` | $30 | (tempo-driven) | -0.7 | +2.0 |

| Tempo | Interval (s) |
|---|---|
| `tempo ≤ -0.5` | 600 |
| `tempo ≤ 0` | 300 |
| `tempo ≤ +0.4` | 180 |
| `tempo > +0.4` | 90 |

This is intentionally simple — adjustments are easier than getting it
right on first try. Tell the user: "Try this for a few days; we can
tighten it if you find yourself overruling the agent."
