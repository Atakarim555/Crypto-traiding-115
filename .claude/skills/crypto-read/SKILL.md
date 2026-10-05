---
name: crypto-read
description: Live MEXC crypto read with a top-down gate cascade, native MEXC execution/cost data, empirical forecasts with replay, and a single signal journal for grading. Use when the user asks for a read/prediction on a coin, wants to scan for setups, or wants signals graded or reviewed.
---

# Crypto read - merged desk (v3)

Intikhab scalps crypto on **MEXC perpetuals at high leverage (45-60x)**, position size $20-50,
mostly the **15-minute** timeframe. Claude analyzes; **Intikhab places and sizes every trade.**
This skill never places orders.

**This is the merge of the old `crypto-read` (gate discipline, journal, loss-derived rules)
and Codex's `crypto-evidence` (MEXC-native data, cost realism, forecast replay, evidence
hygiene). Where they disagreed, the resolution is recorded below with the reason.
There is one crypto skill and one journal. `crypto-evidence` is retired.**

## OPERATING MODE: PAPER (set 2026-09-07, by his decision)

**Log reads and grade them; do not size real positions off them.** This is not caution for its
own sake - the live sample is 6 graded trades, which cannot distinguish a system from a coin
flip, so every real loss right now buys almost no information at full price. The goal is a
sample of **30-40 graded trades**, then look again.

- Every new signal carries `"execution_mode": "paper"`. Ids 1-21 predate the field and score
  as `unmarked`.
- `review_signals.py` splits by mode and takes `--mode live|paper|unmarked`. **Paper results
  are never a track record** and must never be quoted as one, or mixed with the real ids.
- Reads are produced exactly as before - same gates, same journal, same grading. The only
  difference is that nothing is sized.
- If he says he took a trade anyway, log it `live` and say plainly that it sits outside the
  agreed mode. Do not argue past that once.
- **Exit criterion for paper mode is his call, not mine.** Report the count as it grows; do
  not lobby for going live.

## The two things that matter

1. **Mechanical signals from price history have NO backtested edge.** Proven locally
   (`backtest.py`, and the ML matrix: 5 model families, BiLSTM included, DSR 0.000). Never
   re-pitch a backtested auto-signal. Don't re-litigate ML for 15m.
2. **Being better-reasoned is not the same as being profitable.** The gates below are
   **experimental, user-selected rules**, not proven alpha. The live journal is the only
   edge evidence, and it currently reads 6 graded trades, 16.7% win, -0.76R. Say that
   plainly rather than presenting discretion as the proven alternative to a failed model.

## Core files

| what | path |
|---|---|
| Gate scanner (**use this**) | `.claude/skills/crypto-read/scripts/gate_scan.py` |
| Research desk (forecasts, replay, costs) | `.claude/skills/crypto-read/scripts/crypto_desk.py` |
| Feature/replay engine | `.claude/skills/crypto-read/scripts/market_engine.py` |
| Deterministic risk arithmetic | `.claude/skills/crypto-read/scripts/trade_math.py` |
| Gate ablation / evidence | `.claude/skills/crypto-read/scripts/ablation.py` |
| **The** signal journal | `C:\Users\intikhab azam\crypto\signals\signal_log.jsonl` |
| Scorecard | `C:\Users\intikhab azam\crypto\review_signals.py` |
| Evidence artifacts | `C:\Users\intikhab azam\crypto\evidence-v2\` |
| Legacy, defective - see data-contract | `crypto\market_snapshot.py`, `crypto\scan_gates.py` |

Run tests after touching anything: `python -m unittest test_gate_scan test_market_engine test_trade_math`
(37 tests: 19 engine - leakage, no-fill-on-signal-bar, stop gaps, ambiguous bars; 18 gate -
closed-bar selection, gaps, stale tails, the 20-bar-vs-last-bar flow split, proxy health.)

## Where the data comes from - non-negotiable

**MEXC is the execution venue, so MEXC supplies price, levels, structure, ATR, contract
specs, fees, funding and quotes.** Binance supplies **one thing only: flow.**

MEXC futures klines return `time/open/close/high/low/vol/amount` and **no taker-buy field**
(verified 2026-09-07), so per-bar CVD and buy ratio are impossible on MEXC. Binance **USDM
futures** klines publish `takerBuyBase`, so flow is computed there and **always labelled a
cross-venue proxy**. Never use the Binance feed for a price, a level, a stop or a cost.

This replaces the old setup, which read Binance **spot** candles for structure while he
traded MEXC perps - an instrument mismatch on top of a venue mismatch.

## Workflow A - give a read

### Step 1 - scan, or go straight to the symbol

```
python scripts/gate_scan.py --verbose
python scripts/gate_scan.py --symbols SOL_USDT ADA_USDT --verbose
```

Symbols are MEXC form (`SOL_USDT`). A pass is a **candidate, not a signal**; nothing is logged.

### Step 2 - run the desk on the symbol you are actually reading

```
python scripts/crypto_desk.py SOL_USDT
```

Native MEXC OHLC 15m/1h/4h, bid/ask, fair/index/last, contract specs, OI level, funding
schedule, depth samples, recent tape; plus empirical 15m/1h/4h forecasts with Brier-skill
replay against a baseline. Read the JSON report it prints the path to, not just the console
line. **If replay skill is negative or ~zero, say the forecast did not beat the baseline.**
Do not replace that result with a confident narrative.

### Step 3 - data contract gate (BEFORE any analysis)

Read `reference/data-contract.md`. If a required execution feed is missing or stale, the
answer is **`DATA LIMITED`** plus exactly what is missing - not a read with a hole in it.
A failed fetch is missing evidence, not a neutral observation.

### Step 4 - TOP-DOWN GATE CASCADE: 4h -> 1h -> 15m

Run IN ORDER. Each gate can veto alone. Name the gate that failed. Never skip a gate because
a later one looks good. Interpret LEADING-first (`reference/reading-guide.md`): flow -> book
-> OI -> funding -> long/short -> F&G. EMAs/RSI/MACD/BB are backdrop, never the trigger.

**GATE 1 - 4h = DIRECTION. What side am I allowed to take?**
- 4h EMAs stacked 9>21>50 -> **long-only**. Stacked down -> **short-only**.
- Unstacked -> **no directional permission -> stand down.** Do not proceed hunting a setup.
- Binding: never pitch a counter-trend 15m entry. (id 16 shorted into a stacked uptrend ->
  stopped. The AVAX long was into a live downtrend -> lost.)
- **Honesty requirement:** an EMA stack is a descriptor, not proof of a mandatory direction.
  Monthly momentum research (AQR, Jegadeesh-Titman) does **not** validate a 4h EMA gate -
  never cite it as if it did. Report when the stack is thin (EMA9 within ~1% of EMA21, price
  sitting on EMA9): that is one candle from unstacking.

**GATE 2 - 1h = STRUCTURE. Where is the transaction level?**
- Find the nearest level in the allowed direction: swing S/R, EMA cluster, band edge.
- Must be **within ~1.5x the 15m ATR**, or it will not fill in a 15m holding window.
- No level within reach -> **no entry exists -> no-trade.** A direction without a level is
  not a trade.
- **Coil check:** if the whole EMA cluster spans less than ~0.5% and price is *inside* it,
  that is a compression, not a transaction level - there is no "come to us" point and no
  natural stop. Downgrade to WATCH and say so.

**GATE 3 - 15m = REGIME + FLOW + TIMING.**
- **3a regime, absolute veto:** last closed 15m volume < 0.8x its 20-bar average -> NO TRADE,
  regardless of how good 4h and 1h look. Low volume is the reversal/chop regime that produced
  the July losses.
- **3b flow - REPORTED, NOT GATING.** Demoted 2026-09-07 by measurement and his decision.
  The **20-bar window** is still used (screen and read must measure the same thing), but the
  50% threshold is gone. Over 1,480 bars x 6 symbols the ratio is **49.2 +- 2.4**, so a 50%
  line splits the middle of the distribution, and it was lopsided by side: **longs cleared it
  36.2% of the time, shorts 65.1%**. The ablation agreed - removing it improved mean R
  (-0.278 vs -0.308). `gate_scan.py` now prints the ratio with its distance from typical in
  sd, and never rejects on it.
  **Never say a trade passed or failed "on flow."** Quote it as "49.4%, -0.1 sd from typical,
  against the trade" or leave it out. Proxy problems (unmatched bars, venue divergence, bar
  skew, fetch failure) are now NOTED, not blocking - but never fabricate a flow block when the
  proxy is missing.
- **3c timing:** is price AT the gate-2 level, or must it come to us? Wait for the 15m
  **CLOSE** - never read the forming bar.

**The anti-confirmation-bias rule.** Lower TFs must be able to KILL, not merely confirm. A 4h
you love plus 15m volume 0.5x is a NO-TRADE, and say so plainly - that is the gate working.
Corollary: 1h and 4h OI windows may legitimately disagree; report both, do not force one story.

### Step 5 - classify, then deliver in his format

Status is one of **`DATA LIMITED` / `NO SETUP` / `WATCH` / `CONDITIONAL SETUP`.** `WATCH` is
for a future trigger. Give an entry only when level, trigger, invalidation, expiry and costs
can all be specified coherently. Abstaining is a real answer and does not predict a flat market.

Deliver:
- **Gate report FIRST** - one line each for 4h/1h/15m, PASS or the reason it vetoed. If a gate
  vetoed, the read STOPS and the answer is no-trade with the failing gate named. Do not
  produce an entry anyway "in case it sets up".
- **Bias** - must match gate 1. Say no-trade openly.
- **Entry zone** - a real level, priced off MEXC, rounded to `priceUnit`.
- **Stop** - from **15m/1h structure**, framed against the liq buffer *and* against a deeper
  MEXC wick. Never import a 4h stop into a 15m trade.
- **Targets**, with reasoning.
- **Invalidation** - what kills or flips it, plus the decider level.
- **Costs** - real `takerFeeRate` from the contract, current spread, adverse slippage.
- **Honest caveats** - mixed signals, spoofable book, proxy flow, data quirks.

No invented confidence percentages. If no calibrated model applies, say directional
probability is uncalibrated. Probabilities may only come from the desk's forecast block,
carrying its replay skill alongside.

### Step 6 - LOG IT (mandatory)

Append one line to `signal_log.jsonl` per `reference/signal-schema.md`. An unlogged read is a
wasted data point. **One journal.** Do not start a second one, and do not rewrite history:
resolve by editing only the `outcome` block of an existing line.

### Step 7 - schedule the grading check

One-shot cron at the horizon (15m -> ~15min, 1h -> ~1h, 4h -> ~4h, 24h -> 24h). Get local
time first. Tell him crons are session-only. **If a gate veto names a specific numeric
re-entry condition, arm a watch on that condition** - id 18 vetoed on volume 0.33x, volume
recovered to 1.67x twenty-seven minutes later, and nobody was watching.

## Workflow B - grade / review

Re-pull data, compare against what was predicted, edit the matching `outcome` block
(status, triggered, hit, exit_price, result_pct, notes). Rules in `reference/signal-schema.md`,
deeper discipline in `reference/evaluation.md`.

```
python "C:\Users\intikhab azam\crypto\review_signals.py" [--symbol SOLUSDT]
```

Keep the two tracks separate: **15m/1h entries** grade as R-multiples; **4h/24h calls** grade
as prediction correct/partial/wrong. Never let regime calls inflate the entry hit-rate.
Grade at the **actual fill**, not the zone midpoint. Report no-fills as their own outcome.
The reviewer refuses an edge verdict under ~15 graded trades - respect it, and note that no
threshold makes a small sample decisive.

## Hard rules (each one is a paid-for regression)

1. **Entries must be realistic - 53% of tradeable signals never filled.** On a
   trend-continuation setup the entry goes AT price or within ~0.3x the 15m ATR. Compute
   `distance_from_price / ATR15m` before logging; over ~1.0 will probably no_fill. Deep
   pullback zones only when the pullback IS the thesis. A no_fill costs the same as being
   wrong and does not enter the win-rate, so it silently flatters the scorecard.
   **Never move an entry toward price merely to improve fill rate.**
2. **Book imbalance may be spoof.** A lopsided wall can be pulled before it fills. Weight it,
   never marry it. Three REST depth samples are not proof of persistence - and not proof of
   spoofing either.
3. **OI is per-window and is an observation, not a verdict.** Join OI to price by timestamp,
   never by array position. "OI up + price up" does not prove new directional longs; every
   contract has both sides. Treat short-covering / liquidation labels as hypotheses.
4. **Two tracks, never mixed.** (See Workflow B.)
5. **Conviction gating.** Low-conviction reads get logged but flagged no-trade, so the
   scorecard measures actionable calls.
6. **Never claim edge from a backtest,** and never infer universal impossibility from one.
7. **Wait for the candle to CLOSE.** The last bar is live; volume uses the last CLOSED candle.
8. **DIRECTION from above, RISK from below.** Never import a 4h stop into a 15m trade. SOL's
   4h ATR (~1.7%) exceeds the 60x buffer. If a thesis genuinely needs 4h room, call it a
   LOW-LEVERAGE (10-15x) swing, not a 45-60x scalp.
9. **MEXC wicks deeper than Binance.** Resting quotes match to ~0.05%, but on the ADA 13:00
   bar the lows differed by **0.36%** - and wicks are what trigger stops and liquidations.
   Structure now comes from MEXC directly, so this is mostly designed out; still allow wick
   room on any stop, and check MEXC's own candles for stop proximity. If he says the MEXC
   price looks wrong, the real causes are contracts-vs-coins sizing, a leveraged token
   (SOL3L/SOL3S), or fair-vs-index-vs-last - not a real divergence.
10. **A watch must apply the SAME filters the read did.** If a condition is good enough to
    screen on, it is good enough to trigger on - and good enough to re-arm on. The ADA entry
    monitor fired on a bar the scan's own flow test would have rejected.
11. **Leverage feasibility is unknown without his account.** `1/leverage` is a rough screen,
    not a liquidation calculator: it ignores maintenance margin, margin mode and the stop's
    trigger reference. Use it to *reject* setups whose stop is clearly outside the buffer;
    never use it to *certify* one as safe. Ask for his MEXC liq price when it matters.
12. **Sizing:** clarify whether a dollar figure is margin, notional, or max loss before sizing.
    Convert to **contracts** using `contractSize` (SOL 0.1, ADA 1, BTC 0.0001) and round to
    `priceUnit`.
13. **Fees are per-symbol and often ZERO.** SOL_USDT and ADA_USDT are currently zero-fee on
    MEXC (`isZeroFeeSymbol`); BTC 2bp, ETH 1bp taker. Read `takerFeeRate` from the contract -
    never assume a flat round-trip. The old flat ~0.17% assumption overstated cost on the two
    symbols he trades most.

## MEASURED: the gates did not beat no gates (2026-09-07)

First ablation of the cascade. `scripts/ablation.py`, ~1,999 MEXC 15m bars per symbol
(~3 weeks), 6 symbols, chronological halves, real per-symbol fees + 2bp adverse slippage per
side, next-open entry, 1x ATR stop / 2x ATR target / 16-bar time exit, ambiguous bars counted
at both bounds. Full report: `crypto/evidence-v2/ablation/ablation-2026-09-07.txt`.

Pooled mean R per trade, **held-out second half**:

| variant | n | win% | mean R | 95% CI |
|---|---|---|---|---|
| all gates on | 215 | 27.9 | **-0.308** | [-0.480, -0.117] |
| minus gate3a (volume) | 270 | 28.9 | -0.297 | [-0.456, -0.138] |
| minus gate3b (flow) | 379 | 30.1 | -0.278 | [-0.405, -0.138] |
| minus gate2 (reach) | 261 | 29.9 | -0.245 | [-0.407, -0.089] |
| gate1 only | 548 | 32.8 | -0.189 | [-0.297, -0.076] |
| **no gates (control)** | 896 | 33.7 | **-0.174** | [-0.262, -0.084] |

**Every variant loses after costs, and mean R gets monotonically WORSE as gates are added.**
The full cascade was the worst of the six. The first half was noisier and mixed (all gates
-0.046 vs control -0.233), so the effect does not even hold its sign across periods - which is
itself the finding: no stable contribution from any gate.

**What this does and does not show.**
- It does **not** prove the gates are useless in his workflow. It tests them as filters on a
  *mechanical* rule; they were designed as vetoes on *discretionary* entries, and the biggest
  claimed benefit - not trading in the chop regime - shows up as trades avoided, not as R.
- It **does** mean no gate has demonstrated contribution, and the burden of proof is not met.
  **Never tell him the gates improve results.** If asked, this table is the answer.
- The result is consistent with the local ML nulls: a ~30-38% win rate at a 2R target is
  roughly break-even before costs, and costs make it negative. That is what a no-edge system
  looks like.
- Three weeks is a smoke test. Bootstrap CIs ignore clustering by symbol and session, so they
  understate uncertainty.

### MEASURED: the cascade has a large, undesigned LONG bias

He asked whether the reads lean long. They do, and it is structural, not a mood. Over 7,104
bars (6 symbols, ~3 weeks):

| gate 1 permits | share of bars |
|---|---|
| long-only | **49.1%** |
| short-only | **14.2%** |
| neither (stand down) | 36.7% |

**On BTC and SOL, short was permitted on 0.0% of bars for the entire sample.** A stacked 4h
in a rising market forbids shorts by construction, so the cascade could not have produced a
short on his two main symbols even once.

Worse, gate 3b pushes the opposite way. Because the buy ratio averages ~49 against a 50
threshold, **longs clear flow 36.2% of the time and shorts 65.1%**. So the machine carries two
uncalibrated directional biases pointing in opposite directions, neither of them intended.
Net candidate mix still lands near **2:1 long** (1,263 vs 656), and the journal's own logged
directions run ~60/40 long - i.e. the reads track the machinery's skew rather than correcting
for it.

**How to handle it.** Say out loud when gate 1 is forbidding one side, and how often it has
been doing so - "this is long-only because the 4h has been stacked up for N bars, and shorts
have been structurally unavailable" is honest; presenting a long as a *choice* is not. When
the market turns, this exact mechanism will make the reads short-biased and it will feel just
as much like conviction. Never let a run of same-side calls stand as evidence of a view.

### The companion finding: one loss explains nothing

The ADA loss (id 20) was written up as a process failure - "the trigger bar failed the flow
test, the watch spec didn't inherit the condition." **That was wrong, and it is corrected in
the journal.** At signal time the ratio was already 48.3%; across the decision window it
crossed 50% four times in seven bars; and half of all bars on every symbol sit in that band.
The trade was simply a **wrong directional call**: long at 0.2200, ADA fell to 0.2178, -1R.

**Rule: do not manufacture a root cause from a single loss.** n=1 carries almost no diagnostic
content, and post-hoc process stories are how thresholds get overfitted. When he loses a trade,
the default answer is "the call was wrong" unless there is a defect that reproduces
independently of the outcome. A defect worth naming is one you can demonstrate without knowing
whether the trade won.

**Do not retune the thresholds to fix this table.** Tuning on these outcomes is exactly the
overfitting the measurement exists to catch. A changed threshold is a new version with a fresh
forward window.

## Watches - what may and may not be claimed

A watch is a **recorded condition, not a running process.** Only say something is being
monitored when a persistent process is actually running and has been checked.

Every watch records: trigger conditions (all of them, in the same form the read used),
invalidation, expiry, and `last_evaluated_utc`. A watch inherits **every** filter that
produced it - including the 20-bar flow test. If a condition was good enough to screen on, it
is good enough to trigger on, and good enough to re-arm on. Restating a bare price level is
what fired the ADA entry on a bar the scan's own flow test would have rejected.

When a gate veto names a specific numeric re-entry condition, arm the watch on that condition
and say when it was last checked. An unchecked watch is stale, not passive.

## Claims discipline

Do not present 0.8x volume, 0.3x ATR entry distance, 4h EMA stacking or the 50% flow floor as
proven alpha. They are frozen experimental parameters. Do not silently retune them after a
loss; a changed rule is a **new version** with a fresh forward window. Only call a change an
improvement after comparing frozen versions on the same unseen opportunities, with costs,
fill rate and abstention coverage reported. Until then, describe improvements as **data,
execution and evaluation controls** - which is exactly what this merge is.
