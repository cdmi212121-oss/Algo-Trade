# Strategy rules — extracted from course transcripts

Source: YouTube caption transcripts (via tactiq.io) for Topics 2–5. These are
genuine, coherent transcripts (unlike the earlier ASR attempts) — this is the
real content. Topics 6–29 (scalping setup, trade examples, SL/target
specifics, trend identification, risk management update, etc.) have not been
transcribed yet; sections below note exactly what's still missing.

## 1. Support & Resistance + Swings (Topic 2)

- No trend lines — only horizontal Support/Resistance, because trend lines
  have no fixed drawing rule (subjective, low accuracy). Support/Resistance
  are objective, rule-based levels.
- **Concept**: a level where price reverses (U-turn/V-turn) from down to up
  = Support; from up to down = Resistance. These are levels where
  institutional buyers/sellers act.
- **Zones, not lines**: mark Support/Resistance as a *box* (zone), not a
  single price — typically 60–100 points wide (index points) — because
  reversal can start anywhere inside the zone. Built from where sustained
  buying/selling previously occurred; support-becomes-resistance and
  resistance-becomes-support after a breakout is used to build/confirm zones.
- Draw S/R zones on the **15-minute chart**.
- **Swings**: intraday price moves between Support and Resistance in a
  zigzag of alternating swing highs/lows (labelled sequentially: 1, 1.5, 2,
  2.5, 3, ... as price makes a new high/low each leg).
- **Core entry rule**: *to go up, the LAST swing (high) must break; to come
  down, the LAST swing (low) must break.* i.e. trade the breakout/breakdown
  of the most recently formed swing point, in the direction of the break.
  Each swing that breaks flips the immediate bias (a swing that was acting
  as support, once broken, triggers a downside trade, and vice versa).
- Trading timeframe for spotting these swings: 1-minute chart early in the
  day, 3-minute later (see Topic 3 timing below).

## 2. Rules & Money Management (Topic 3)

### Candle timeframe rule
- 9:15–10:30: use the **1-minute** chart (too few candles have formed yet
  for 3-min to be useful).
- 10:30–3:15/3:30 (rest of day): use the **3-minute** chart (5-minute is
  also acceptable per the instructor — personal preference, not a hard rule).

### Session behavior rule (time-of-day market character)
| Window | Character | What to do |
|---|---|---|
| 9:15–10:00 | Volatile, **non-directional** | Book profit fast — don't hold, market can reverse anytime. |
| 10:00–11:30 | **Directional momentum** | Best trending window — ride the direction, can plan bigger targets. |
| 11:30–1:30 | Mostly **sideways** | Avoid trading. Exception: near expiry, if a clear swing break/breakout is seen, trade with **small quantity** only. |
| 1:30–3:30 | **Slow but directional** | Small quantity, proper SL at the swing point (no tiny SL), can hold for a bigger target or wait until close if target isn't hit quickly. |

### 15-minute directional bias check
Check the 15-min chart's overall structure **4 times a day**, at 11:30,
12:30, 1:30, and 2:30, to set directional bias (call-side lean vs put-side
lean) for that period. Actual entries still come from the swing-break rule
above (on 1-min/3-min) — this is a higher-timeframe context filter, not an
entry trigger by itself.

### Money management
- Only deploy **50% of total capital**; keep 50% in reserve (so 3–4
  consecutive loss days don't force reducing position size).
- Max daily loss: **10–12% of the deployed (50%) capital**, i.e. roughly
  **5–6% of total capital per day**. Stop trading for the day if hit.
- Position size example given was in lots (index-specific, e.g. "5–10 lots
  of Bank Nifty") — the transferable rule is the **5–6%-of-capital daily
  cap** and **reserve half your capital**; exact lot counts are account-size
  specific and not transferable as-is.

## 3. Strike Price Selection (Topic 4)

- **Always trade In-The-Money (ITM) options, never OTM.** OTM premium
  doesn't move with the underlying ("no momentum") — trading OTM increases
  losses. ATM has the highest premium decay/theta sensitivity generally
  reserved for special cases below.
- **Minimum ITM depth by index**:
  - NIFTY: ≥ 100 points ITM
  - BANKNIFTY: ≥ 200 points ITM
  - FINNIFTY: ≥ 150 points ITM
- **Expiry day exception**: trade ITM (per above) **before 12:00 PM**; after
  12:00 PM you may switch to **ATM**.
- **"Hero Zero" strategy** (separate bonus strategy, not the main system):
  very cheap far/ATM options bought late on expiry day (after ~1:30–2:00 PM)
  for a lottery-style payoff. Minimum premium floors given (index/VIX
  dependent, see caveat below): roughly ₹30–45 minimum — going cheaper
  measurably hurts accuracy. This is a distinct, higher-risk sub-strategy,
  not the default entry method.
- **If a trade's SL is getting too large in premium terms**, you can select
  a strike with *less* ITM depth (cheaper, closer to spot) to reduce it —
  but never drop to OTM to "solve" a large SL; that increases losses instead.
- **Caveat on absolute premium/rupee figures**: the transcript also gives
  day-of-week premium bands (e.g. "₹320–400 the day after expiry, decaying
  down to ₹60–70 on expiry morning" for Bank Nifty). These are **specific to
  Bank Nifty trading around the 44,000 level with India VIX ~12–20 in the
  original recording** — Bank Nifty is around 56,000+ now (per our live feed),
  so the absolute rupee numbers are stale and **not implemented as hardcoded
  thresholds**. The transferable rule implemented in code is the **points-ITM
  depth** (100/200/150 above), which holds regardless of index level.

## 4. Importance of the Option's Own Premium Chart (Topic 5)

This is the critical second half of the entry rule that Topic 2 alone
doesn't cover:

- A swing break on the **underlying (index/futures) chart is not sufficient
  on its own** to enter a trade.
- You must **also** open the chart of the *specific option premium* you'd
  actually trade (the ITM strike from Topic 4) and confirm that **it** has
  also broken its own recent swing high (for a call-side entry) or swing low
  (for a put-side entry) — using a nearby round-figure level for the trigger
  (e.g. an actual high of 373 gets treated as a 375, sometimes rounded
  further to 380 for a safer/cleaner trigger).
- **Rationale**: the underlying can make a new swing high/low while the
  option premium fails to follow (due to IV/theta effects), producing a fake
  breakout if traded on the underlying alone. Requiring the premium to
  independently confirm the same swing break avoids these fake entries.
- Only track the **most recent relevant swing** on the premium chart too —
  ignore older/smaller swings once price has moved away from them (same
  "last swing" principle as Topic 2, applied to the premium series as well).
- Illustrative SL example given: ~20 points on the premium for a scalp
  (illustrative only — Topic 9, "SL AND TARGET", not yet transcribed, should
  give the actual precise SL/target rule; treat the current implementation's
  SL/target as provisional until that's available).

## Additional topics (batch 2)

### 5. Scalping Setup (Topic 6) — resolves the swing-confirmation rule

- **Swing confirmation rule (the missing zigzag threshold)**: a swing point
  is confirmed by *candle count*, not a point threshold. A high is confirmed
  as a swing high once **3 (sometimes 3–4) consecutive candles** move away
  from it in the opposite direction (3+ straight red candles after a high;
  3+ straight green candles after a low confirms a swing low). Small
  "hold"/doji-like candles against the move don't necessarily break the
  count as long as the overall directional structure still resolves clearly
  — if the structure looks ambiguous, skip that swing rather than force it.
- **Range/box concept**: price often gets stuck consolidating between a
  local high and low (a "box"). While stuck in that box, *both* call and put
  premium just decay — even a small move in the underlying doesn't move
  premium meaningfully while the range holds. Momentum (and thus scalping
  profit) is only generated when that box **breaks**.
- **Gamma move**: right at a range breakout, premium can spike sharply
  within a single candle (tens of points in one candle), disproportionate to
  normal delta-implied movement — this is the move scalping is actually
  trying to capture.
- **No retest required**: enter at the breakout candle itself; waiting for a
  retest (a common idea in other trading styles) is explicitly rejected here
  — by the time a retest happens the gamma move is often already gone.
- Reconfirms Topic 5's dual-confirmation rule (underlying swing break AND
  matching premium break) and Topic 2's "pure price action, no indicators"
  positioning.

### 6. First Trade Setup (Topic 7) — the opening-15-minutes procedure

Special-cased logic for handling the market's open, based on *where* price
opens relative to the marked Support/Resistance zones:

- **Gap-up open, at/above the Resistance zone**: don't take a call-side
  trade off the gap alone. Wait for a candle with a wick that holds above
  the zone for ~1–2 minutes, then enter **call-side** only once that
  candle's high breaks. (Rationale: holding above a zone that previously
  attracted sellers is a real strength signal.) If instead a decisive red
  candle breaks the zone back down, enter **put-side** once that candle's
  low breaks, provided there's some room below (not immediate support).
- **Gap-down open, at/below the Support zone**: mirror logic — a green
  candle holding above support that then breaks its own high → **call-side**
  entry; a decisive red candle breaking support down → **put-side** entry.
- **Open somewhere between Support and Resistance (neither zone)**: no
  special opening rule applies — just wait for the normal swing-formation
  rule (§1/§5 above) on the 1-minute chart and trade whichever swing breaks
  first.
- In every opening scenario, the **option premium chart must also confirm**
  (its own high/low from the relevant candle must break too) — same
  requirement as Topic 5, applied to the open specifically.
- Building S/R zones from **2–3 prior days** of price action (not just one)
  gives more robust zones than a single day.

### 7. Second Trade Setup (Topic 8) — reinforces swing rule, adds quantity-by-risk

- Reconfirms the 3–4 consecutive-candle swing rule from Topic 6 with many
  worked examples; the practical procedure for "the second trade of the
  day" (or any trade really) is: **identify two opposing swings first** (one
  up-swing, one down-swing candidate), then trade whichever one actually
  breaks.
- **Risk-scaled quantity**: if today's open is "hovering" in the same zone
  where price closed the previous day, wait for a *larger, clearer* swing
  break before sizing up — a break of a small/tight swing near that
  hovering level is explicitly called a "risky trade" and should be sized
  down (small quantity), while a clear, larger swing break can be sized up
  normally.
- Breaks after ~10:00 AM (once past the "second trade" opening window) are
  still tradeable but are framed as lower-priority/smaller-size than the
  first two setups of the day.

### 8. SL and Target (Topic 9) — resolves the SL/target placeholder

**Stop loss** — two explicit methods, either is valid:
- **Variable/identified SL**: place the SL at the actual invalidation point
  of the trade structure (e.g., the swing/level whose break would mean the
  setup failed). More accurate to market structure; can improve win rate by
  avoiding premature stop-outs, but makes risk (and therefore risk:reward
  statistics across many trades) inconsistent trade-to-trade.
- **Fixed SL**: a constant point value regardless of structure — the
  instructor's personal default is **30 points** (on the premium) for Bank
  Nifty, reasoned from that instrument's normal intraday noise band, never
  larger. Simpler/faster to execute and to compute expectancy over many
  trades, but sometimes pays for more risk than the structure needed, and
  sometimes still gets stopped out by noise that the structure-based SL
  would have survived.
- **Hybrid** (explicitly endorsed as valid too): use the smaller of the two
  — a tight structural SL when the structure calls for one, capped at the
  fixed value (e.g. 30 pts) when the structure would otherwise call for
  something larger.
- Sensex uses a wider fixed SL (40 points) — see §10 below; the fixed value
  is instrument-specific, not universal.

**Target** — momentum/candle-based scaling is the primary method, with a
simpler fixed-target variant offered as an explicit alternative:
- Watch the 1-minute candle in the trade's direction. While each new candle
  keeps closing in your favor, don't book profit — let it run. A minimum
  floor applies: never book below **15–20 points**; the instructor's own
  personal rule is that once ~20 points of unrealized profit appears, he
  mentally commits to not letting the trade go into a loss from there
  (moving toward breakeven/trailing, not necessarily booking immediately at
  exactly 20).
- **Exit signal for the bulk of the position**: when price gets "stuck" —
  a doji-like or range-bound candle appears after a run — that's the cue to
  book the **majority of the quantity** (the instructor's own examples used
  roughly 70–80% of the position) at that point.
- **Scale-out mechanics**: after booking the bulk on the first
  consolidation signal, move the SL on the *remaining* small quantity to
  breakeven (cost) or trail it via the same structural-SL logic, and let
  that remainder ride for a bigger target with reduced/near-zero risk. This
  can repeat if momentum resumes and another consolidation signal appears.
- Illustrative personal target tiers mentioned (Bank Nifty): roughly 20,
  40, and 75 points as rough experience-based checkpoints — not a hard law.
- **Fixed-target variant** (explicit alternative): a constant point target
  (e.g. 30 points) is simpler and fine for a beginner still learning to
  hold trades, at the cost of lower average profit than the momentum-based
  scale-out method.
- Framing note directly from the instructor: these are *his* variants —
  pick whichever combination of the above suits you; there's no single
  mandated SL/target pairing.

### 9. Strike Price Selection in Nifty Options (Topic 18) — **revises §3 for Nifty**

Based on further observation, this **updates/replaces** Topic 4's
"NIFTY ≥100 points ITM" rule:

- For Nifty, trade **near At-The-Money**, not deep ITM: select from a window
  of about **4–5 strikes centered on ATM** (a mix of slightly ITM and
  slightly OTM), rather than 100+ points ITM.
- Because call/put premiums at the same "moneyness" don't match in absolute
  rupees (put-call skew), prefer whichever strike within that window gives
  **roughly matched CE/PE premiums**, rather than insisting on strict ATM
  for both sides.
- If India VIX rises toward ~20 (from a calm ~15), you may shift slightly
  further OTM within reason — but only on the 3 days right after expiry
  (the transcript's example: Fri/Mon/Tue); do **not** extend this allowance
  to the day before expiry or expiry day itself.
- Day before expiry: use the **first ITM strike** (minimal ITM depth, not
  ATM/OTM), with a premium floor of **₹80** (never trade a premium below
  that, call or put).
- Expiry day, before 12:00 PM: first ITM strike again, premium floor
  **₹60**. After 12:00 PM: ATM or a discretionary "Hero Zero" trade is fine.
- General principle re-stated: never go 200–300 points ITM *or* OTM for
  Nifty — stay in that moderate near-the-money window.

### 10. How to Trade in Sensex (Topic 19) — new (not covered in batch 1)

- Sensex has weekly expiry (day of week has changed over time; treat "day
  relative to expiry" as what matters, not the literal weekday).
- **Liquidity caveat**: Sensex's 1-minute premium charts can show gaps that
  cause bad/unexpected order fills. The instructor's recommendation: don't
  trade Sensex every day — limit it to **expiry day and the day before**,
  when liquidity/OI concentrates near the money and the setup works
  reliably; avoid extending further back (explicitly: Nifty is the better
  daily instrument otherwise).
- **Strike window**: stay within a narrow band — one strike OTM to one
  strike ITM per side; never deep ITM (order execution/fill issues observed
  on large Sensex premium spikes) and never far OTM.
- **SL/Target ladder (differs from Bank Nifty)**: SL = **40 points**;
  targets are tiered at **30 / 60 / 90 points** (first/second/third target),
  a more fixed structure than Bank Nifty's momentum/candle-based scaling.
- **Premium floors**: before 12:00 PM on expiry day, minimum **₹200**
  premium; after 12:00 PM (including any Hero-Zero-style trade), minimum
  **₹100**. Never far OTM or deep ITM regardless of time of day.

### 11. How to Trade in Banknifty, Parts 1 & 2 (Topics 20–21) — **revises §3 and the SL for Bank Nifty**

- **Strike selection update**: same shift as Nifty (§9 above) — trade from
  a narrow ~4-strike near-ATM window, picking whichever strike's premium
  chart shows *no gaps* (for reliable order fills), rather than the older
  "≥200 points ITM" rule from Topics 3/4.
- **Structural note — Bank Nifty moved to monthly-only expiry**: with only
  a monthly expiry, ATM premium early in the cycle can be very large (the
  transcript's example: ATM around ₹1,000), which would make a fixed
  10%/30-point-style SL blow out to ~100 points — not acceptable for a tight
  scalp. **Consequence: the instructor's primary daily-scalping instrument
  shifted to Nifty** (which kept weekly expiry and correspondingly smaller,
  scalp-appropriate premiums); Bank Nifty is only traded like the old
  weekly-style setup (30-point SL, 20-point first target, trailing) during
  its **last week before monthly expiry**, when premiums shrink back to the
  old ~₹300–420 weekly-like range.
- Outside that last week, if trading Bank Nifty anyway, widen the fixed SL
  to **40–45 points** (matching target sized proportionally) — the
  instructor's own stated compromise, not a preferred setup.

### Trade-example topics skimmed, no new mechanical rules found

Topics 10, 11, 16, 23, 24 (Friday trade examples, Nifty back-testing/live
trade walkthroughs) and Topic 12 (Sensex trade example) were skimmed rather
than fully transcribed-and-parsed here — they apply the rules above with
real numbers but didn't surface additional mechanical rules beyond what's
already captured in sections 1–11. Worth a closer pass later if specific
edge cases come up that these rules don't cover.

## Full entry pipeline (as implemented)

1. Maintain a swing tracker on the underlying index's tick series: a swing
   high/low is confirmed once 3+ consecutive candles move away from it in
   the opposite direction (§5/§7 above) — not a point threshold.
2. Maintain a separate swing tracker, same rule, on the *currently selected*
   option's premium tick series.
3. Pick the option to watch via the strike-selection rule: near-ATM window
   (4–5 strikes) for Nifty and Bank Nifty (§9/§11 — this **supersedes** the
   older flat "≥100/200/150 points ITM" framing in §3), a 1-ITM-to-1-OTM
   window for Sensex (§10), switching to ATM/Hero-Zero after 12:00 PM on
   expiry day for all three. Prefer whichever candidate strike's premium
   history has no data gaps / roughly matches its opposite-side premium.
4. **Entry trigger** = underlying breaks its last confirmed swing point
   **AND** the option premium independently breaks its own last confirmed
   swing point, in the same direction (§4) **AND** the time-of-day gate (§2)
   allows trading right now **AND**, at the open specifically, the
   opening-scenario logic (§6) is satisfied if applicable.
5. **Stop loss**: fixed value by instrument (30 pts Bank Nifty weekly-style,
   40–45 pts Bank Nifty monthly/other, 40 pts Sensex — §8/§11) **or** the
   structural invalidation point, whichever the implementation chooses (both
   are explicitly valid per §8); cap structural SL at the fixed value.
6. **Target**: momentum-based scale-out (book ~70–80% on the first
   consolidation-candle signal after ≥15–20 points, trail the remainder to
   breakeven/structural-SL) is the primary method, with a simple fixed
   target (e.g. 30/60/90 points, Sensex-style) as a valid simpler
   alternative (§8/§10).

## Implementation status update

Since the sections above were written, the engine was completed further:

- **Momentum-based scale-out (§8) is now implemented** (`strategy_engine.
  manage_position`, wired into `engine.py`'s tick loop): books ~75% of an
  open position once unrealized profit clears 15 points and the premium
  prints a consolidation candle, moves the remainder's stop to breakeven,
  and lets it ride to the fixed target as a backstop. Needed a new
  `PaperBroker.partial_close` (books a partial quantity, keeps the rest
  open) — added.
- **Sensex option-chain data is now wired in** (`angel_data.py` — Sensex
  options trade on Angel's BFO segment, not NFO; confirmed working against
  the live feed). §10's rules (narrow 1-strike window, expiry-day-or-day-
  before only, 40pt SL) are implemented in `strike_selection.py` /
  `strategy_engine.py`.

## Still explicitly not implemented / pending more transcripts

- Trailing SL specifics beyond what §8 already covers (Topic 26, "Stoploss
  Trail" — likely adds nuance, not yet transcribed). Not implemented
  because the content isn't available to implement from, not because it
  was skipped.
- Risk management updates (Topic 27), sideways-market handling detail
  (Topic 28), trend identification detail (Topic 29) — separate topics,
  not yet transcribed; same reason.
- The trade-example topics (10, 11, 12, 16, 23, 24) were only skimmed, not
  fully parsed — revisit if an edge case shows up that sections 1–11 don't
  clearly resolve.
- Never validated against a real live trading session end-to-end (only
  smoke-tested against live snapshots) — needs the engine running during
  actual market hours on a real trading day.
- The "prefer the strike with no premium data gaps / roughly matched CE-PE
  premium" refinement from §9/§11 is simplified to picking by strike
  distance alone — not implemented, since it needs persisted premium
  history to detect gaps, which is a bigger change than its expected payoff
  right now.
