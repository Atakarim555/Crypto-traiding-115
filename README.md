# Crypto research desk - handoff package

Start with the research report in `paper/` (PDF to read, Word .docx to edit; the equations in
the .docx are native Word equations). It explains the whole system, every model, every result
and how to reproduce it. This file is only the quick start.

## Layout (keep it - scripts locate each other by relative path)

    paper/                            the research report: .pdf and .docx
    .claude/skills/crypto-ml-research/ skill for YOUR agent: rules for running/extending the harness
    .claude/skills/crypto-read/        the original trader's live-read skill + scripts + tests
    crypto/                            research harness, ML models, journal, evidence
      backtest.py        candle loader (CRYPTO_ASOF freeze) + walk-forward rule backtester
      fetch_micro.py     Binance public futures dumps (OI, positioning, book depth)
      ml_pipeline.py     harness core + LightGBM or XGBoost (--model xgb)
      ml_seq.py          BiLSTM / LSTM under the same harness
      ml_forecast.py     LightGBM quantile range forecaster (+ --live)
      ml_study.py        accuracy/log-loss/Brier vs baselines, loss curves, detection power
      ml_sanity_check.py positive control (planted leak must be caught)
      review_signals.py  scorecard for the live signal journal
      signals/           the live journal (signal_log.jsonl)
      ml_reports/        every run (.txt + .json); study-2026-09-28/ = the report's exact runs
      evidence-v2/       desk reports + gate ablation (two windows)
      market_snapshot.py scan_gates.py   LEGACY, known defects - read, don't use

The candle cache (crypto/data) is NOT included; it rebuilds from public APIs on first run.
No API keys are needed anywhere. Binance is geo-blocked in some countries (the ML harness
needs it; the live MEXC desk does not).

## Quick start

    pip install -r requirements.txt
    cd .claude/skills/crypto-read/scripts
    python -m unittest test_gate_scan test_market_engine test_trade_math    # 39 tests
    cd ../../../../crypto
    python -c "import backtest as b; [b.load_klines(s,'15m',5000) for s in ('BTCUSDT','ETHUSDT','SOLUSDT')]"
    export CRYPTO_ASOF=<epoch ms of the last closed bar>    # freeze the data for a study
    python ml_sanity_check.py ETHUSDT                      # must print HARNESS SOUND
    python ml_study.py --symbol ETHUSDT --fee 0.0003
    python ml_pipeline.py --symbols ETHUSDT --fee 0.0003
    python ml_pipeline.py --symbols ETHUSDT --fee 0.0003 --model xgb
    python ml_seq.py --symbols ETHUSDT --fee 0.0003

Your numbers will differ from the report's because your data window ends later; the
report's exact runs are in crypto/ml_reports/study-2026-09-28/.
