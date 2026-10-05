"""Collect every number the paper shows from the saved run reports of the frozen study.

Only reports generated after STUDY_START are used, so older runs can't leak in.
Prints the files it used, so the paper's numbers trace back to named files.
"""
import glob, json, os, re, sys

STUDY = os.path.dirname(os.path.abspath(__file__))      # crypto/ml_reports/study-2026-09-28 (run logs)
REPORTS = os.path.dirname(STUDY)                        # crypto/ml_reports
ABL = os.path.join(os.path.dirname(REPORTS), "evidence-v2", "ablation")
STUDY_START = "20260928T1520"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
used = []


def latest(pattern, pred):
    best = None
    for p in glob.glob(os.path.join(REPORTS, pattern)):
        d = json.load(open(p, encoding="utf-8"))
        stamp = d.get("generated_utc") or re.search(r"(\d{8}T\d{6}Z)", p).group(1)
        if stamp < STUDY_START or not pred(d):
            continue
        if best is None or stamp > best[0]:
            best = (stamp, p, d)
    if best is None:
        sys.exit("missing report for %s" % pattern)
    used.append(os.path.basename(best[1]))
    return best[2]


data = {"asof": 1790608499999, "study": {}, "pnl": {}, "forecast": {}, "ablation": {}}

for s in SYMS:
    st = latest("study_%s_15m_*.json" % s, lambda d: d.get("crypto_asof") == "1790608499999" and "sequence" in d)
    xg = latest("study_%s_15m_*.json" % s, lambda d: d.get("crypto_asof") == "1790608499999" and "xgboost" in d)
    st["xgboost"] = xg["xgboost"]
    for m in ("BiLSTM", "LSTM"):
        st["sequence"][m].pop("loss_curves", None)          # per-fold curves: keep the mean only
    data["study"][s] = st

    pnl = {}
    for key, micro, model in (("lgbm", False, "lgbm"), ("lgbm_micro", True, "lgbm"), ("xgb", False, "xgb")):
        d = latest("ml_15m_*.json", lambda d, micro=micro, model=model: d["args"]["symbols"] == [s]
                   and d["args"]["micro"] == micro and d["args"].get("model", "lgbm") == model)
        r = d["reports"][0]
        pnl[key] = dict(trades=r.get("trades"), threshold=r.get("best_threshold"), pf=r.get("profit_factor"),
                        random_pf=r.get("random_pf"), dsr=r.get("deflated_sharpe"), total=r.get("total_return"),
                        verdict=r.get("verdict"), n_samples=r.get("n_samples"))
        if not micro:
            pnl["fee"], pnl["buy_hold"] = d["args"]["fee"], r.get("buy_hold")
    d = latest("seq_15m_L24_*.json", lambda d: d["args"]["symbols"] == [s] and not d["args"]["control"])
    for m, r in d["reports"][0]["results"].items():
        pnl[m] = dict(trades=r.get("trades"), threshold=r.get("threshold"), pf=r.get("profit_factor"),
                      random_pf=r.get("random_pf"), dsr=r.get("deflated_sharpe"), total=r.get("total_return"),
                      verdict=r.get("verdict"))
    for ckey, cfile in (("control", "%s_control.txt"), ("control_xgb", "%s_control_xgb.txt")):
        ctl = open(os.path.join(STUDY, cfile % s), encoding="utf-8").read()
        used.append(cfile % s)
        g = lambda pat: float(re.search(pat, ctl).group(1))
        pnl[ckey] = dict(trades=int(g(r"trades\s+(\d+)")), threshold=g(r"BEST \(thr=([\d.]+)"),
                           pf=g(r"profit factor\s+([\d.]+)"), random_pf=g(r"random baseline ([\d.]+)"),
                           dsr=g(r"control DSR = ([\d.]+)"), total=g(r"total return\s+([+-][\d.]+)%") / 100,
                           sound="HARNESS SOUND" in ctl)
    data["pnl"][s] = pnl

    fc = {}
    for h in (1, 4, 16, 96):
        d = latest("forecast_15m_h%d_*.json" % h, lambda d, h=h: d["args"]["symbols"] == [s] and d["args"]["horizon"] == h and not d["args"].get("live"))
        res = d["reports"][0]["results"]
        row = {k: dict(calibration_error=v["calibration_error"], mean_skill_vs_vol=v["mean_skill_vs_vol"],
                       mean_skill_vs_uncond=v["mean_skill_vs_uncond"], n=v["n"]) for k, v in res.items()}
        txt = open(os.path.join(STUDY, "%s_fc_h%d.txt" % (s, h)), encoding="utf-8").read()
        used.append("%s_fc_h%d.txt" % (s, h))
        m = re.search(r"60x \(liq 1\.67%\): breached ([\d.]+)% of bars \| warned p05 ([\d.]+)% p01 ([\d.]+)% \| caught (\w+)% / (\w+)% of breaches", txt)
        if m:
            row["liq60"] = dict(breached=float(m.group(1)) / 100, recall05=float(m.group(4)) / 100 if m.group(4) != "nan" else None,
                                recall01=float(m.group(5)) / 100 if m.group(5) != "nan" else None)
        fc["h%d" % h] = row
    data["forecast"][s] = fc


def ablation(path):
    txt = open(path, encoding="utf-8").read()
    used.append(os.path.basename(path))
    # old header "POOLED ACROSS SYMBOLS", new header "POOLED ACROSS 6 OF 6 SYMBOLS"
    block = txt.split("POOLED ACROSS", 1)[1]
    out = {}
    for m in re.finditer(r"^\s{2}(.+?)\s+2nd half n=(\d+)\s+win%=\s*([\d.]+)\s+meanR=([+-][\d.]+) \[([+-][\d.]+),([+-][\d.]+)\]", block, re.M):
        out[m.group(1).strip()] = dict(n=int(m.group(2)), win=float(m.group(3)), mean=float(m.group(4)),
                                       lo=float(m.group(5)), hi=float(m.group(6)))
    assert len(out) == 6, (path, out.keys())
    # a dropped symbol: old format "X: fetch failed (...)", new format names it after MISSING:
    # (new-format retries print "fetch failed, attempt n/3" and may still have succeeded)
    assert not re.search(r"fetch failed \(|MISSING:|REFUSING TO POOL", txt), "a symbol failed to load in " + path
    return out


data["ablation"]["A"] = ablation(os.path.join(ABL, "ablation-2026-09-07.txt"))
data["ablation"]["B"] = ablation(os.path.join(ABL, "ablation-2026-09-28.txt"))

json.dump(data, open(os.path.join(STUDY, "paper_data.json"), "w"), indent=1)
print("wrote paper_data.json (every number in the report's Section 7) from %d files:" % len(used))
print("\n".join("  " + u for u in used))
