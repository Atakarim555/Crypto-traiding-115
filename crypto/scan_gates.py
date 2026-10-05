#!/usr/bin/env python3
"""
scan_gates.py - run the crypto-read gate cascade across many symbols at once.

Same logic as the skill's Workflow A, applied as a screen so we stop staring at
one chart waiting for it to become a trade:

  GATE 1 (4h)  DIRECTION  EMAs stacked + CVD agreeing -> long-only / short-only.
                          Anything else = stand down, symbol is out.
  GATE 2 (1h)  STRUCTURE  Nearest level in the allowed direction must sit within
                          1.5x the 15m ATR, or it will never fill in a 15m hold.
  GATE 3 (15m) REGIME     Last CLOSED bar volume >= 0.8x its 20-bar average.
                          Absolute veto - low volume is the chop regime where
                          the intraday edge inverts.

Only symbols passing ALL THREE are candidates, and a candidate is still just a
starting point for a real read - not a signal. Nothing here is logged to the
journal; it only narrows where to look.

Uses the last CLOSED 15m bar throughout (hard rule 7 - never read the forming
bar). Binance data; verified to match MEXC to within ~0.05% (hard rule 9).

Usage:
    python scan_gates.py
    python scan_gates.py --symbols BTCUSDT ETHUSDT --verbose
    python scan_gates.py --min-vol 1.0
"""

import math
import argparse
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

BASE = "https://api.binance.com/api/v3/klines"
H = {"User-Agent": "Mozilla/5.0 (gate-scan)"}
TIMEOUT = 20

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
            "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT", "TONUSDT", "NEARUSDT",
            "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT"]


def klines(symbol, interval, limit=120):
    r = requests.get(BASE, params={"symbol": symbol, "interval": interval,
                                   "limit": limit}, headers=H, timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    # drop the still-forming bar: every feature built on it is partial
    return [c for c in rows if c[6] / 1000 < now]


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    out = vals[0]
    for v in vals[1:]:
        out = v * k + out * (1 - k)
    return out


def atr_pct(rows, period=14):
    trs = []
    for i in range(1, len(rows)):
        h, l = float(rows[i][2]), float(rows[i][3])
        pc = float(rows[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period / float(rows[-1][4]) * 100.0


def cvd_dir(rows, window=20):
    """Sign of cumulative signed volume over the window, from taker_buy_base."""
    seg = rows[-window:]
    delta = sum((2 * float(c[9]) / float(c[5]) - 1) * float(c[5])
                for c in seg if float(c[5]) > 0)
    return delta


def analyse(symbol):
    try:
        k4 = klines(symbol, "4h", 120)
        k1 = klines(symbol, "1h", 120)
        k15 = klines(symbol, "15m", 120)
    except Exception as e:
        return dict(symbol=symbol, error="%s" % type(e).__name__)
    if min(len(k4), len(k1), len(k15)) < 60:
        return dict(symbol=symbol, error="short history")

    price = float(k15[-1][4])
    c4 = [float(c[4]) for c in k4]
    c1 = [float(c[4]) for c in k1]
    c15 = [float(c[4]) for c in k15]

    # ---- GATE 1: 4h direction ----
    e9, e21, e50 = ema(c4, 9), ema(c4, 21), ema(c4, 50)
    cvd4 = cvd_dir(k4)
    if e9 > e21 > e50 and cvd4 > 0:
        direction = "long"
    elif e9 < e21 < e50 and cvd4 < 0:
        direction = "short"
    else:
        return dict(symbol=symbol, price=price, gate1="STAND DOWN",
                    reason="4h EMAs unstacked or CVD disagrees", passed=False)

    # ---- GATE 2: nearest level in the allowed direction, within 1.5x ATR15m ----
    a15 = atr_pct(k15)
    if not a15:
        return dict(symbol=symbol, price=price, error="no ATR")
    levels = {
        "15m EMA9": ema(c15, 9), "15m EMA21": ema(c15, 21),
        "15m EMA50": ema(c15, 50), "1h EMA9": ema(c1, 9), "1h EMA21": ema(c1, 21),
    }
    reach = 1.5 * a15
    best = None
    for name, lv in levels.items():
        if direction == "long" and lv >= price:
            continue
        if direction == "short" and lv <= price:
            continue
        dist = abs(price - lv) / price * 100.0
        if dist <= reach and (best is None or dist < best[2]):
            best = (name, lv, dist)
    if best is None:
        return dict(symbol=symbol, price=price, gate1=direction,
                    gate2="NO LEVEL", reason="no level within %.2f%% (1.5x ATR)" % reach,
                    passed=False, atr=a15)

    # ---- GATE 3: 15m regime on the last CLOSED bar ----
    vols = [float(c[5]) for c in k15]
    avg20 = sum(vols[-21:-1]) / 20.0
    vratio = vols[-1] / avg20 if avg20 else 0.0
    buy = float(k15[-1][9]) / vols[-1] * 100 if vols[-1] else 0.0

    return dict(symbol=symbol, price=price, gate1=direction, atr=a15,
                level_name=best[0], level=best[1], level_dist=best[2],
                vratio=vratio, buy=buy,
                passed=None, reason=None)   # gate 3 judged by caller vs --min-vol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=UNIVERSE)
    ap.add_argument("--min-vol", type=float, default=0.8,
                    help="gate 3 volume threshold (default 0.8)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--verbose", action="store_true", help="show rejected symbols too")
    args = ap.parse_args()

    print("GATE CASCADE SCAN  %s UTC" % dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("gate1 4h direction -> gate2 level within 1.5x ATR15m -> gate3 vol >= %.1fx"
          % args.min_vol)
    print("=" * 92)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        res = list(ex.map(analyse, args.symbols))

    passed, rejected = [], []
    for r in res:
        if r.get("error"):
            rejected.append((r["symbol"], "ERROR " + r["error"])); continue
        if r.get("passed") is False:
            rejected.append((r["symbol"], "%s - %s" % (r.get("gate1", "?"), r["reason"]))); continue
        # GATE 3 has TWO halves. Volume alone is not enough: a 1.79x bar at a
        # 24% buy ratio is heavy SELLING, which passes a naive volume check
        # while flatly contradicting a long. Participation must lean the way
        # the trade does, or the "regime is alive" reading is backwards.
        aligned = (r["buy"] >= 50.0) if r["gate1"] == "long" else (r["buy"] <= 50.0)
        r["aligned"] = aligned
        if r["vratio"] < args.min_vol:
            rejected.append((r["symbol"], "%s, level ok, GATE3 vol %.2fx" % (r["gate1"], r["vratio"])))
        elif not aligned:
            rejected.append((r["symbol"], "%s, vol %.2fx OK but FLOW AGAINST (buy %.0f%%)"
                             % (r["gate1"], r["vratio"], r["buy"])))
        else:
            passed.append(r)

    if passed:
        passed.sort(key=lambda r: -r["vratio"])
        print("%-10s %-6s %10s %-11s %9s %8s %7s %6s"
              % ("SYMBOL", "DIR", "PRICE", "LEVEL", "LEVEL@", "DIST%", "VOL", "BUY%"))
        print("-" * 92)
        for r in passed:
            print("%-10s %-6s %10.4f %-11s %9.4f %7.2f%% %6.2fx %5.0f%%"
                  % (r["symbol"], r["gate1"].upper(), r["price"], r["level_name"],
                     r["level"], r["level_dist"], r["vratio"], r["buy"]))
    else:
        print("NO SYMBOL PASSED ALL THREE GATES.")
        print("That is a market-wide answer, not a scanning failure - sit out.")

    print("-" * 92)
    print("rejected: %d" % len(rejected))
    if args.verbose:
        for s, why in sorted(rejected):
            print("  %-10s %s" % (s, why))
    print("\nA pass is a CANDIDATE, not a signal. Run the full read before trading it,")
    print("and check that the stop implied by its level fits inside the liq buffer.")


if __name__ == "__main__":
    main()
