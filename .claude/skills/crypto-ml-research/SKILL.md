---
name: crypto-ml-research
description: Run, extend and audit the leakage-controlled ML evaluation harness for 15-minute crypto prediction (LightGBM, BiLSTM/LSTM, quantile forecasting, triple-barrier labels, purged CV, deflated Sharpe, positive controls). Use when the user wants to test whether a model or signal has a real edge after costs, add a new model to the harness, reproduce or audit a published crypto ML result for leakage, run the ml_study metrics/power experiment, or write up results for the research report.
---

# Crypto ML research harness

This skill drives the evaluation harness in `crypto/`. The harness is a **falsification
tool**: its job is to say honestly whether a model makes money after costs, and to prove it
could have said yes. It is not a signal generator and must never be presented as one.

The companion skill `crypto-read` is the original operator's live-trading desk. It is
specific to his account and his 45-60x leverage. Read it for context; do not use it for
research runs.

## What already exists (read before building anything)

| file | what it does |
|---|---|
| `crypto/backtest.py` | candle loader (Binance spot, cached in `crypto/data/`) + walk-forward rule backtester |
| `crypto/fetch_micro.py` | Binance public futures dumps: OI, long/short ratios, book depth (cached per day) |
| `crypto/ml_pipeline.py` | features, triple-barrier labels, purged k-fold, uniqueness weights, PnL simulation, deflated Sharpe, LightGBM classifier; `--model xgb` for XGBoost (capacity-matched, pinned to 1 thread) |
| `crypto/ml_seq.py` | BiLSTM + LSTM under the identical harness; `--control` plants a leak |
| `crypto/ml_forecast.py` | LightGBM quantile regression for return/excursion ranges; `--live` |
| `crypto/ml_sanity_check.py` | positive control for LightGBM |
| `crypto/ml_study.py` | standard ML metrics, train/test gap, loss curves, detection-power sweep |
| `crypto/ml_reports/` | every past run, `.txt` + `.json` |
| `.claude/skills/crypto-read/scripts/ablation.py` | rule-gate ablation on MEXC data |

The research report (`paper/*.pdf`, editable `paper/*.docx`) records every result to date,
and `crypto/ml_reports/study-2026-09-28/` holds the exact run logs behind it (`build_data.py`
re-extracts every Section 7 number into `paper_data.json` from the saved reports). Check them before re-running
anything, and cite the saved report file for any number you quote.

## Rules (each one exists because breaking it produced a false result once)

1. **Run the positive control first, every session.** `python ml_sanity_check.py SYMBOL`
   must print `HARNESS SOUND`. A "no edge" result from a harness that cannot detect a
   planted edge means nothing. After any change to features, labels, folds or scoring, run
   the control again before running anything else.
2. **Freeze the data for any study.** Set `CRYPTO_ASOF=<epoch ms>` so every run reads the
   identical cached candles and touches no network. Report the value. Without it, each run
   appends new candles and results stop being comparable.
3. **Use the real cost.** The harness default is 0.04% per side. Pass `--fee` as the
   exchange's taker fee plus about 2 bp slippage. Report the cost used next to every result.
4. **Every variant tried is a trial.** Thresholds, feature sets, hyperparameters, horizons,
   architectures: if you looked at the outcome and then chose, it counts toward the deflated
   Sharpe. Never tune on the evaluation data and then report the tuned result as if it were
   the only one. Log every variant you run, including the ones that failed.
5. **An edge claim needs all of:** DSR >= 0.95, profit factor > 1.15x the random-entry
   baseline, at least 15 trades, the same result on a second symbol, and a forward test on
   data collected after the model was frozen. Anything less is "no edge found" or
   "candidate, needs forward test". Say which.
6. **Report both kinds of metric.** Classification metrics (accuracy, balanced accuracy,
   macro-F1, log-loss, Brier, each against majority-class and class-prior baselines) come
   from `ml_study.py`. Trading metrics (PF, DSR, random baseline, buy-and-hold) come from
   `ml_pipeline.py` / `ml_seq.py`. Accuracy alone is never evidence: a model can beat the
   majority class on accuracy while its probabilities are worse than the class prior.
7. **Keep every feature causal.** A value at bar i may use only bars <= i. Check any new
   feature by shifting the input series by one bar and confirming the value at i does not
   change for bars before the shift. Watch `np.roll`: it wraps the end of the array to the
   start.
8. **Period-aggregate data is stamped at the period start.** Binance `metrics` rows cover
   the 5 minutes after their timestamp. Use only buckets that close before the bar closes.
   Apply the same rule to any new aggregate feed.
9. **Sequence models:** one window, one prediction after its last bar. Never label every
   timestep of a bidirectional model (the backward pass reads the future of every interior
   step). Purge on the left by `seq_len` as well as on the right (`purged_folds_seq`). Fit
   scalers on training folds only. Always train a unidirectional LSTM alongside a BiLSTM:
   if the BiLSTM clearly beats it, suspect a leak before believing a discovery.
10. **Ambiguous bars.** If one candle crosses both barriers, the order is unknown. Drop the
    sample (default) or count it as a loss (`--ambiguous conservative`). Never assume the
    favourable order.
11. **Baselines must have the target's shape.** A symmetric Gaussian band is the wrong
    baseline for a one-sided target such as maximum drawdown; it is trivially beaten and
    once produced a fake +36% skill.
12. **Calibration in the middle says nothing about the tails.** Report tail recall
    separately whenever a range forecast is used for risk.
13. **Check that every asset loaded before reading a pooled number.** `ablation.py` prints
    `fetch failed` for an asset whose download failed and then pools the rest without
    warning; one such run silently dropped ETH and reversed the sign of the headline result.
    Grep the saved output for `fetch failed` and rerun if it appears.
14. **Pin threads for any multi-threaded model.** XGBoost's result changed with the thread
    count alone (ETH PF 0.83 / 0.96 / 1.08 at 2 / 4 / 8 threads) because parallel float sums
    round differently. It is pinned to one thread; do the same for any new library before
    comparing runs, and record the setting.
15. **Do not invent a root cause from one bad result**, and do not retune a threshold
    because one run looked bad. A changed rule is a new version with its own test.

## Workflow: test an existing model on a coin

```
cd crypto
export CRYPTO_ASOF=<epoch ms of the last closed bar you want>
python ml_sanity_check.py ETHUSDT                    # must say HARNESS SOUND
python ml_pipeline.py --symbols ETHUSDT --fee 0.0003
python ml_seq.py --symbols ETHUSDT --fee 0.0003
python ml_study.py --symbol ETHUSDT --fee 0.0003      # metrics, curves, power
```

Read the `.txt` report, not only the console tail. Report: data span, samples, label
counts, mean uniqueness, cost, PF vs random PF, DSR, classification metrics vs baselines,
train-vs-test gap, and the verdict line.

## Workflow: add a new model

1. Write a function that takes training rows (or windows) plus labels and uniqueness
   weights, and returns class probabilities for the test rows in the column order
   `[-1, 0, +1]`.
2. Call it inside `mp.purged_folds` (tabular) or `ms.purged_folds_seq` (sequences), filling
   an out-of-fold matrix exactly as `ml_pipeline.run_symbol` does. Do not write a new
   splitter.
3. Score with the existing `mp.simulate`, `mp.trade_stats`, `mp.deflated_sharpe` and the
   random-entry baseline loop. Do not write new scoring.
4. Add a planted-leak control for the new model and confirm it reaches DSR ~1.0.
5. Run the power sweep (`ml_study.power_block` pattern) so the result states the weakest
   edge the setup can detect.
6. Fix hyperparameters before looking at results. If you must tune, use nested CV inside
   the training folds only, and count the grid size as trials.

## Workflow: audit a published model for leakage

1. Reproduce the published pipeline as written, including its splitter and metric, and
   confirm you get close to the published number.
2. Close one leak at a time and re-measure after each: chronological split, purge and
   embargo, scaler fit on train only, causal features, single-output sequence labelling,
   costs, trial deflation. Record how much of the reported performance each step removes.
3. Finish with the full harness verdict. The table of "reported vs after each fix" is the
   result.

## Reporting

Every result written into the report states: data source and span, `CRYPTO_ASOF`, symbol,
cost per side, number of variants tried, the saved report path, and the verdict wording
from rule 5. Use "no edge found", never "proved impossible": a null on three weeks to two
months of data does not prove that no model can work.
