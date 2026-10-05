#!/usr/bin/env python3
"""
ml_study.py - the standard ML metrics, training curves and detection power
for the models in ml_pipeline.py and ml_seq.py.

Those two scripts answer the question that matters (does it make money after
fees?). A paper also has to report what the classifiers learned in ordinary
ML terms, and how sensitive the harness is. This script adds exactly that and
nothing else: it reuses the SAME features, labels, purged folds, uniqueness
weights and hyperparameters, imported from those modules.

  1. Out-of-fold classification metrics for LightGBM, BiLSTM and LSTM:
     accuracy, balanced accuracy, macro-F1, multiclass log-loss and Brier,
     confusion matrix. Each against two baselines fitted on the training fold
     only: majority class and class prior.
  2. In-sample (training fold) versus out-of-fold, i.e. the overfitting gap.
  3. Per-epoch training loss for the recurrent models.
  4. Detection power: plant a leaked label at strengths 0.02 .. 0.55 and record
     the deflated Sharpe and profit factor the full PnL harness reports. This
     measures the weakest edge the harness can see, which the single
     positive control in ml_sanity_check.py does not.

Set CRYPTO_ASOF=<epoch ms> so every run reads identical data (see backtest.py).

Usage:
    CRYPTO_ASOF=1790611199999 python ml_study.py --symbol ETHUSDT --fee 0.0003
    python ml_study.py --symbol BTCUSDT --skip-seq          # LightGBM + power only
"""
import os
import json
import time
import argparse
from datetime import datetime, timezone

import numpy as np
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, f1_score,
                             log_loss, confusion_matrix)

import ml_pipeline as mp
import ml_seq as ms
from backtest import load_klines
from fetch_micro import load_micro

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "ml_reports")
CLASSES = np.array([-1, 0, 1])
YMAP = {-1: 0, 0: 1, 1: 2}
LEAK_STRENGTHS = [0.0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.55]


def prepare(symbol, a, log):
    """Features, labels and samples exactly as ml_pipeline.run_symbol builds them."""
    candles = load_klines(symbol, a.interval, total=a.candles, log=log)
    X, names, px = mp.build_features(candles)
    if a.micro:
        micro = load_micro(symbol, candles[0]["open_time"], candles[-1]["close_time"],
                           workers=8, log=log)
        if micro:
            Xm, nm, _ = mp.build_micro_features(candles, micro, px["close"])
            X, names = np.column_stack([X, Xm]), names + nm
    label, t1, ret, valid, n_amb = mp.triple_barrier(
        px["close"], px["high"], px["low"], px["atr"], a.pt, a.sl, a.vert, "drop")
    finite = np.isfinite(X).all(axis=1)
    return dict(candles=candles, X=X, names=names, px=px, label=label, t1=t1,
                ret=ret, valid=valid, finite=finite, n_amb=n_amb)


def brier(y_idx, proba):
    onehot = np.eye(3)[y_idx]
    return float(np.mean(np.sum((proba - onehot) ** 2, axis=1)))


def cls_metrics(y_idx, proba):
    pred = proba.argmax(axis=1)
    return dict(
        n=int(len(y_idx)),
        accuracy=float(accuracy_score(y_idx, pred)),
        balanced_accuracy=float(balanced_accuracy_score(y_idx, pred)),
        macro_f1=float(f1_score(y_idx, pred, average="macro", zero_division=0)),
        log_loss=float(log_loss(y_idx, np.clip(proba, 1e-15, 1), labels=[0, 1, 2])),
        brier=brier(y_idx, proba),
        confusion=confusion_matrix(y_idx, pred, labels=[0, 1, 2]).tolist(),
    )


def fold_baselines(y_tr, te_len):
    """Majority-class and class-prior predictions, fitted on the training fold."""
    prior = np.bincount(y_tr, minlength=3) / len(y_tr)
    majority = np.zeros((te_len, 3))
    majority[:, int(prior.argmax())] = 1.0
    return majority, np.tile(prior, (te_len, 1))


def summarize(tag, y_idx, oof, base_major, base_prior, train_rows, log, extra=None):
    ok = np.isfinite(oof).all(axis=1)
    m = cls_metrics(y_idx[ok], oof[ok])
    bm = cls_metrics(y_idx[ok], base_major[ok])
    bp = cls_metrics(y_idx[ok], base_prior[ok])
    tr = {k: float(np.mean([r[k] for r in train_rows])) for k in
          ("accuracy", "balanced_accuracy", "macro_f1", "log_loss", "brier")}
    log("\n  %s  out-of-fold n=%d" % (tag, m["n"]))
    log("    %-18s %9s %9s %9s %9s %9s" % ("", "acc", "bal_acc", "macroF1", "logloss", "brier"))
    for name, r in (("model (test)", m), ("model (train)", tr),
                    ("majority class", bm), ("class prior", bp)):
        log("    %-18s %9.4f %9.4f %9.4f %9.4f %9.4f"
            % (name, r["accuracy"], r["balanced_accuracy"], r["macro_f1"],
               r["log_loss"], r["brier"]))
    log("    confusion (rows true -1/0/+1, cols predicted): %s" % m["confusion"])
    out = dict(test=m, train_mean=tr, baseline_majority=bm, baseline_prior=bp)
    if extra:
        out.update(extra)
    return out


def gbdt_block(kind, d, pos, y_idx, w, a, log):
    """Gradient-boosted classifier (kind 'lgbm' or 'xgb'), same folds and weights."""
    Xs, t1 = d["X"][pos], d["t1"]
    embargo = max(a.vert, int(0.01 * len(pos)))
    oof = np.full((len(pos), 3), np.nan)
    bmaj, bpri = np.full_like(oof, np.nan), np.full_like(oof, np.nan)
    train_rows, iters = [], []
    for tr, te in mp.purged_folds(pos, t1, a.folds, embargo):
        if len(np.unique(y_idx[tr])) < 3:
            continue
        model = mp.make_classifier(kind, a.trees, a.seed)
        model.fit(Xs[tr], y_idx[tr], sample_weight=w[tr])
        cols = [list(model.classes_).index(c) for c in (0, 1, 2)]
        oof[te] = model.predict_proba(Xs[te])[:, cols]
        train_rows.append(cls_metrics(y_idx[tr], model.predict_proba(Xs[tr])[:, cols]))
        bmaj[te], bpri[te] = fold_baselines(y_idx[tr], len(te))
        iters.append(len(tr))
    params = mp.XGB_PARAMS if kind == "xgb" else mp.LGBM_PARAMS
    return summarize("XGBoost" if kind == "xgb" else "LightGBM", y_idx, oof, bmaj, bpri, train_rows, log,
                     extra=dict(train_sizes=iters, embargo_bars=embargo,
                                params=dict(n_estimators=a.trees, **params)))


def seq_block(d, a, log):
    X, t1, finite = d["X"], d["t1"], d["finite"]
    L = a.seq_len
    ok = d["valid"] & finite
    ok[-a.vert:] = False
    ok[:L - 1] = False
    hist_ok = np.concatenate([np.zeros(L - 1, bool),
                              np.array([finite[i - L + 1:i + 1].all()
                                        for i in range(L - 1, len(finite))])])
    pos = np.where(ok & hist_ok)[0]
    Xw, pos = ms.make_windows(X, pos, L)
    y_idx = np.array([YMAP[int(v)] for v in d["label"][pos]])
    w = mp.uniqueness_weights(pos, t1)
    embargo = max(a.vert, int(0.01 * len(pos)))

    class SeqArgs:
        hidden, layers, dropout = a.hidden, 1, 0.3
        lr, batch, epochs = 1e-3, 128, a.epochs

    out = {}
    for tag, bidir in (("BiLSTM", True), ("LSTM", False)):
        t0 = time.time()
        oof = np.full((len(pos), 3), np.nan)
        bmaj, bpri = np.full_like(oof, np.nan), np.full_like(oof, np.nan)
        train_rows, curves = [], []
        for tr, te in ms.purged_folds_seq(pos, t1, a.seq_folds, embargo, L):
            if len(np.unique(y_idx[tr])) < 3:
                continue
            hist = []
            both = ms.train_fold(Xw[tr], y_idx[tr], w[tr], np.concatenate([Xw[tr], Xw[te]]),
                                 SeqArgs, bidir, a.seed, history=hist)
            ptr, pte = both[:len(tr)], both[len(tr):]
            oof[te] = pte
            train_rows.append(cls_metrics(y_idx[tr], ptr))
            bmaj[te], bpri[te] = fold_baselines(y_idx[tr], len(te))
            curves.append(hist)
        net = ms.RNNClassifier(X.shape[1], hidden=a.hidden, bidirectional=bidir)
        n_params = int(sum(p.numel() for p in net.parameters()))
        mean_curve = np.mean(np.array(curves), axis=0).tolist() if curves else []
        log("\n  %s: %d trainable parameters, %.0fs" % (tag, n_params, time.time() - t0))
        log("    mean train loss by epoch: " +
            " ".join("%.4f" % v for v in mean_curve[::max(1, len(mean_curve) // 8)]) +
            (" ... %.4f" % mean_curve[-1] if mean_curve else ""))
        out[tag] = summarize(tag, y_idx, oof, bmaj, bpri, train_rows, log,
                             extra=dict(n_params=n_params, loss_curve_mean=mean_curve,
                                        loss_curves=curves, n_windows=int(len(pos))))
    return out


def power_block(symbol, a, log):
    """Plant a leak of strength s and run the full PnL harness unchanged."""
    orig = mp.build_features
    rows = []

    class Args:
        interval, candles, pt, sl, vert = a.interval, a.candles, a.pt, a.sl, a.vert
        ambiguous, folds, embargo, trees = "drop", a.folds, 0.01, a.trees
        fee, seed, micro, workers = a.fee, a.seed, False, 8

    for s in LEAK_STRENGTHS:
        def leaky(candles, s=s):
            X, names, px = orig(candles)
            lab, _, _, _, _ = mp.triple_barrier(px["close"], px["high"], px["low"],
                                                px["atr"], a.pt, a.sl, a.vert, "drop")
            noise = np.random.default_rng(0).normal(0, 1.0, len(lab))
            return np.column_stack([X, s * lab + (1 - s) * noise]), names + ["LEAK"], px
        mp.build_features = leaky
        try:
            r = mp.run_symbol(symbol, Args(), lambda *_: None) or {}
        finally:
            mp.build_features = orig
        row = dict(strength=s, dsr=r.get("deflated_sharpe"), pf=r.get("profit_factor"),
                   random_pf=r.get("random_pf"), trades=r.get("trades"),
                   verdict=r.get("verdict"))
        rows.append(row)
        log("    leak %.2f  DSR %s  PF %s  random PF %s  trades %s  %s"
            % (s, _f(row["dsr"]), _f(row["pf"]), _f(row["random_pf"]),
               row["trades"], row["verdict"]))
    return rows


def _f(x):
    return "%.3f" % x if isinstance(x, (int, float)) else str(x)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", default="15m")
    ap.add_argument("--candles", type=int, default=5000)
    ap.add_argument("--pt", type=float, default=1.5)
    ap.add_argument("--sl", type=float, default=1.5)
    ap.add_argument("--vert", type=int, default=8)
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--seq-folds", type=int, default=5, dest="seq_folds")
    ap.add_argument("--trees", type=int, default=300)
    ap.add_argument("--seq-len", type=int, default=24, dest="seq_len")
    ap.add_argument("--hidden", type=int, default=48)
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--fee", type=float, default=mp.TAKER_FEE)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--micro", action="store_true")
    ap.add_argument("--gbdt", default="lgbm", help="comma list of boosters to score: lgbm, xgb")
    ap.add_argument("--skip-seq", action="store_true", dest="skip_seq")
    ap.add_argument("--skip-power", action="store_true", dest="skip_power")
    a = ap.parse_args()

    lines = []

    def log(msg=""):
        print(msg, flush=True)
        lines.append(msg)

    log("ml_study  %s  %s  CRYPTO_ASOF=%s  fee %.4f/side"
        % (a.symbol, a.interval, os.environ.get("CRYPTO_ASOF"), a.fee))
    d = prepare(a.symbol, a, log)
    c = d["candles"]
    span = [datetime.fromtimestamp(c[0]["open_time"] / 1000, timezone.utc).isoformat(),
            datetime.fromtimestamp(c[-1]["close_time"] / 1000, timezone.utc).isoformat()]
    ok = d["valid"] & d["finite"]
    ok[-a.vert:] = False
    pos = np.where(ok)[0]
    y_idx = np.array([YMAP[int(v)] for v in d["label"][pos]])
    w = mp.uniqueness_weights(pos, d["t1"])
    dist = np.bincount(y_idx, minlength=3).tolist()
    log("  data %s .. %s  (%d candles)" % (span[0], span[1], len(c)))
    log("  features %d  samples %d  labels -1/0/+1 %s  ambiguous dropped %d  "
        "mean uniqueness %.3f" % (len(d["names"]), len(pos), dist, d["n_amb"], w.mean()))

    report = dict(symbol=a.symbol, interval=a.interval, data_span_utc=span,
                  crypto_asof=os.environ.get("CRYPTO_ASOF"), fee_per_side=a.fee,
                  n_features=len(d["names"]), feature_names=d["names"],
                  n_samples=int(len(pos)), label_counts=dist,
                  ambiguous_dropped=int(d["n_amb"]), mean_uniqueness=float(w.mean()),
                  barriers=dict(pt_atr=a.pt, sl_atr=a.sl, vertical_bars=a.vert))
    for kind in a.gbdt.split(","):
        report["xgboost" if kind == "xgb" else "lightgbm"] = gbdt_block(kind, d, pos, y_idx, w, a, log)
    if not a.skip_seq:
        report["sequence"] = seq_block(d, a, log)
    if not a.skip_power:
        log("\n  detection power (planted leak -> full PnL harness):")
        report["power"] = power_block(a.symbol, a, log)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(OUT_DIR, "study_%s_%s_%s" % (a.symbol, a.interval, stamp))
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    with open(path + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("\nSaved: %s.json" % path)


if __name__ == "__main__":
    main()
