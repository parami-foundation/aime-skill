# Onboarding: Diagnose Then Let The User Pick A Pet

> The user doesn't know what `hardnose` vs `quant` vs `zen` means in
> trading terms. **But they also don't trust a black-box that picks for
> them.** So we do both: ask 5 scenario questions to diagnose their
> trading vector, then show them 4 fleshed-out pets ranked by fit —
> with names, backstories, voice samples — and let them choose.

## The flow (host AI version)

```
1. Run: aime onboard --json
2. Get back:
   - 5 questions, each with 3-4 options carrying axis deltas
   - 4 pet profiles (Tao, Akira, Jing, Dr. Petrov), full details
3. Ask the user each question in your own voice
4. For each answer, accumulate the option's deltas into a 4-axis vector:
       {risk, numbers, admit, tempo}
5. Run: aime onboard --rank-vector '<json>'
   → returns 4 pets RANKED by score, plus derived trade params
6. Show all 4 ranked pets to the user, in your own words. Top match
   gets ⭐. Include each pet's backstory + voice samples so the user
   can FEEL who they're picking, not just match a label.
7. Let the user pick — they may go for runner-up #2 because they
   resonate with the personality, even if the vector says #1 is closer.
   Don't argue.
8. Run: aime onboard --pick <pet_name>
   → applies personality.txt + queues rules tell to daemon
9. Show suggested `aime start ...` line with derived params.
```

## Why this design

| Approach | Pros | Cons |
|---|---|---|
| **Old (preset list)** — user picks from {default, hardnose, zen, quant, sarcastic, nerd} | Simple | Users don't know what those mean |
| **v2.5-v2.7 (auto-apply vector)** — answer 5 questions, CLI picks best-fit, applies | No decision fatigue | Black-box feel, no agency, no runner-up signal |
| **v2.8 (rank + pick)** — same questions, but user sees ranked pets and picks | Honors both diagnosis and agency. User sees runner-ups, can choose by vibe. | One extra step |

Owner instinct: "tone shouldn't be a question, but you should still ask
trading questions". Translation: keep the diagnosis, but make the
output a menu of fleshed-out characters, not a single verdict.

## The 4 pets

```
🧠 Tao (default preset)
   Mid-career prop trader, 35. Mixes Chinese and English. Hedges
   everything, will admit when wrong, pushes back when you're wrong.
   Voice sample: "Yo 老大, I'd skip this one — yes_price 0.62 isn't
   crazy mispriced enough for me to chase."
   Trading: moderate size, 5-10 min cycles, stop -50% take +100%

🐺 Akira (hardnose preset)
   NYC, 30, ex-Citadel. Hates momentum chasers, hates news traders.
   Voice sample: "Fuck this. BTC at 12 manda? Everyone's a genius
   until they aren't. Shorting."
   Trading: aggressive sizing, fast cycles (1-3 min), looser stops
   (-70%) but takes profit at +200%

🧘 Jing (zen preset)
   Former Shanghai HF quant, trades on her own. 佛系. Won't FOMO.
   Voice sample: "这个不碰，价格太混乱。再等等。"
   Trading: small size, slow cycles (10-20 min), tight stops (-30%)
   take profit early (+50%). Skips more often than she trades.

🧮 Dr. Petrov (quant preset)
   PhD in probability theory, ex-Renaissance. Only EV / Kelly / Bayes.
   Voice sample: "YES at 0.42, my posterior is 0.58 given the latest
   poll data. Kelly says 4.2% of bankroll. Buy $42."
   Trading: Kelly-sized positions, stop tied to posterior drift.
```

## The 5 questions

Each question's option carries axis deltas; sum them across all 5
answers to get the user's vector.

```
Q1. BTC just pumped 30% in a day. Your first instinct?
   - Short the breakout                                  risk +0.7, admit +0.2
   - Wait it out — chase = pain                          risk -0.7, tempo -0.5, admit +0.3
   - Check funding/OI first                              numbers +0.7, admit +0.3
   - Ride it with a tight stop                           risk +0.3, tempo +0.5

Q2. You're down -50% on a trade. What's your move?
   - Cut. Sized too big. Note the lesson.                admit +1.0, numbers +0.3
   - Hold — thesis hasn't changed                        admit -0.7, risk +0.3
   - Average down if thesis holds                        admit +0.3, risk +0.3, numbers +0.3
   - Take the L quietly                                  admit +0.7, risk -0.3, tempo -0.3

Q3. When the agent explains a trade to you, you prefer:
   - EV / Kelly / probabilities                          numbers +1.0
   - A story — catalyst, who's wrong                     numbers -0.7
   - Step-by-step: prior, evidence, posterior            numbers +0.7, tempo -0.3
   - One sentence — call + confidence                    numbers -0.3, tempo +0.3

Q4. What kind of trade pisses you off most when others do it?
   - Blind FOMO, no thesis                               admit +0.5, numbers +0.5
   - Holding losers and praying                          admit +1.0
   - Too small to matter                                 risk +0.7
   - Reckless oversizing                                 risk -0.7, admit +0.3

Q5. Default position size on a 65%-confidence trade with $1000?
   - $5-10 (small)                                       risk -0.7
   - $25-50 (moderate)                                   risk 0.0
   - $100-200 (big)                                      risk +0.8, tempo +0.3
   - Kelly says (~$20)                                   numbers +0.8, risk 0.0
```

**Voice/tone is NOT asked.** Tone comes from the pet's personality
prompt, not from a separate question. Two pets can have similar trading
behavior but very different voices (e.g. Tao vs Akira both moderate
risk-tolerance, but Tao is bilingual hedging, Akira is short-sharp
NYC). The vector matches trading style; the user matches voice by
reading the profiles.

## Vector-axis cheat sheet

| Axis | Negative (-1) | Positive (+1) |
|---|---|---|
| `risk` | conservative, skip uncertain | aggressive, size up |
| `numbers` | stories, narratives, catalysts | EV, Kelly, probabilities |
| `admit` | defend the thesis, double down | cut and re-evaluate fast |
| `tempo` | patient, slow, hold for days | scalper, fast in-out |

## Pet ideal vectors (for reference)

```jsonc
{
  "Tao (default)":      {"risk": 0.0,  "numbers": +0.3, "admit": +0.5, "tempo":  0.0},
  "Akira (hardnose)":   {"risk": +0.7, "numbers":  0.0, "admit": +0.3, "tempo": +0.5},
  "Jing (zen)":         {"risk": -0.7, "numbers": -0.3, "admit": +0.7, "tempo": -0.7},
  "Dr. Petrov (quant)": {"risk":  0.0, "numbers": +1.0, "admit": +0.5, "tempo":  0.0}
}
```

Pick = sorted by cosine similarity, shown to user, user picks.

## Derived trade params (from same vector)

| Vector axis | Effect |
|---|---|
| `risk ≤ -0.5` | trade size $1, stop -0.3, take +0.5 |
| `risk ≤ 0` | trade size $5, stop -0.5, take +1.0 |
| `risk ≤ +0.4` | trade size $15, stop -0.5, take +1.0 |
| `risk > +0.4` | trade size $30, stop -0.7, take +2.0 |
| `tempo ≤ -0.5` | interval 600s (10 min) |
| `tempo ≤ 0` | interval 300s (5 min) |
| `tempo ≤ +0.4` | interval 180s (3 min) |
| `tempo > +0.4` | interval 90s (1.5 min) |

These get passed to `aime tell --source onboarding --tags rules` so
the daemon's brain factors them into every trade decision.

## Voice variants (not in onboard)

`sarcastic` and `nerd` are voice variants that don't match cleanly to
trading behavior. They're not in PET_PROFILES so onboard won't show
them. If the user picks (say) Tao but wants a different voice:

```bash
aime personality set sarcastic   # keeps Tao's trading rules, swaps voice
aime personality set nerd
```

## When the user pushes back

| User says | You do |
|---|---|
| "I don't trade, just pick for me" | Default to `aime onboard --pick Tao` (default preset is the safe middle ground) |
| "These pets don't fit me" | Offer custom: edit `~/.aime/personality.txt` directly. Rules can be set via `aime tell "<rules>" --source onboarding --tags rules` |
| "I want pet X's trading style but pet Y's voice" | Set X via `--pick X`, then `aime personality set <Y's preset key>` to swap voice only |
| "Just pick the best one" | Use `aime onboard --apply-vector <json>` (one-shot, no ranking display) |

## Style is reversible

Anytime they want to switch pets:

```bash
aime onboard                    # re-run the diagnosis interactively
aime onboard --pick <pet>       # directly switch
aime personality set <preset>   # voice variant
```

The daemon picks up the new personality on next reflection (or
`aime restart` for immediate effect).
