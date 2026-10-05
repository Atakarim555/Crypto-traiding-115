# Crypto skill audit - 2026-09-06

The existing skill has an unproven strategy, inconsistent data calculations, and an unreliable measurement contract. These defects can produce poor decisions and misleading scores. The available records cannot establish how much each caused the user's real losses, nor prove a replacement will predict better.

## What the current journal actually reports

Ran `python crypto/review_signals.py` against the existing 20-row journal without modifying it:

| Existing reviewer measure | Result |
| --- | ---: |
| Resolved / pending | 18 / 2 |
| Classified tradeable | 15 |
| Triggered / not triggered | 7 / 8 |
| Graded / triggered but missing exit | 6 / 1 |
| Winners / graded | 1 / 6 (16.7%) |
| Mean gross R / total gross R | -0.13 / -0.76 |
| Gross profit factor | 0.82 |
| Separately scored regime calls | 3 / 3 called correct |

These are legacy-reported figures, not independently verified fills, net account returns, or a reliable forecast accuracy estimate. Its 53% non-entry classification includes id 3, a 4h record with null hit and triggered=false. Do not describe all eight as cleanly verified missed executions. Id 14 lacks an exit. The old skill's 62% figure is stale relative to this journal. Three narrative regime wins do not establish 100% forecasting ability. The current 4h/1h/15m cascade was introduced after some of these signals, so the full journal is not a clean test of that frozen strategy.

## Findings

1. **Closed-bar instruction is violated by the snapshot.** `analyze_candles` computes EMA, ATR, swings, and close from the forming bar, while volume and order flow strip it. Decisions can change before close despite the skill claiming closed-bar discipline.
2. **Scanner and reader disagree by construction.** Snapshot CVD change omits the first delta in its window; scanner sums the full window. Snapshot ATR is Wilder-smoothed; scanner uses a simple mean. EMA seeds/history also differ. The monitor mismatch recorded for ADA id 20 adds another execution inconsistency.
3. **Wrong market for precise execution.** Snapshot candles, quotes, and book are Binance spot; derivatives context is Binance futures; the user executes MEXC perpetuals. OI/price joins use row count instead of timestamps. Top-100 depth may not cover the advertised band. A single matching quote cannot prove venues always match.
4. **Evidence is overstated.** The old skill claims 4h direction has academic support while citing momentum at weeks/months, and makes 0.8x volume an absolute gate based partly on a few losses. These exact gates are unvalidated. Price/OI quadrants cannot uniquely identify new longs or liquidations. Failed mechanical models do not establish discretionary edge.
5. **Historical-data doctrine is outdated.** The skill says OI/book evidence cannot be backtested; the user's own ML memory describes historical microstructure tests. Those reports were inspected as reported findings, not rerun or independently validated here.
6. **Grading can change the question after the outcome.** Entries are zones, exits have multiple targets without fixed allocation, horizons/expiry are not explicit fields, and manual resolution lacks a mandatory chronology/ambiguity check. Id 17 is a hypothetical +3.47R at target2, not the user's executed return. Id 20 notes an actual 0.2200 fill, but `entry_ref` always chooses zone midpoint 0.21995. Both stop exits can still score -1R, concealing the mismatch in cash risk.
7. **Costs are absent from R.** `r_multiple` computes price move / midpoint stop distance without fees, funding, spread, or slippage. No-fill and missing-data classifications are not robust, and unknown directions can fall through to long arithmetic. Fifteen observations are not a statistical proof threshold.
8. **Liquidation approximation is too weak for safety claims.** `1/leverage` ignores maintenance margin and account state. The stop and liquidation may use different reference prices. MEXC documents fair-price liquidation. A close-based thesis invalidation is also different from a live stop trigger, a distinction blurred in id 20.
9. **Evidence preservation is incomplete.** Snapshot filenames use symbol plus candle-close timestamp, so repeat snapshots in one bar can overwrite prior evidence. The skill calls the journal append-only while instructing edits to prior outcomes. The advertised immutable candle cache is not implemented by `market_snapshot.py`.

## Replacement delivered

`C:/Users/intikhab azam/.codex/skills/crypto-evidence/` contains a new skill, data contract, evaluation protocol, and tested linear-contract risk helper. It requires consistent closed-bar data, execution-venue checks, explicit entry/exit rules, actual-fill grading, and costs. It distinguishes uncalibrated scenarios from verified forecasting skill and preserves strategy versions for fair comparisons.

The original skill, collectors, journal, and historical snapshots remain intact. This is a replacement analysis workflow, not a repaired live feed or automatic trading system. Future reads must correct/recompute legacy features or disclose missing data. No live orders, journal resolutions, or expensive model retraining were performed. Better predictive performance remains unproven until forward comparison.

## Primary references checked

- [MEXC liquidation FAQ](https://www.mexc.com/support/article/faq-on-liquidation-for-futures-trading-8123281969561): fair price and maintenance margin matter.
- [AQR time-series momentum methodology](https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Factors-Monthly?aqrPDF=1): 12-month formation and 1-month holding periods do not validate the skill's 4h gate.
- [Binance public datasets](https://github.com/binance/binance-public-data) and [historical futures depth](https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2FbookDepth%2F): historical data availability contradicts the blanket live-only claim.
- [Bailey et al., Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf): repeated strategy selection needs explicit controls.
