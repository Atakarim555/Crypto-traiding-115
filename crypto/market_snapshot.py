#!/usr/bin/env python3
"""
market_snapshot.py — Claude's "eyes on the crypto market".

Pulls live data from free, no-key public APIs, computes indicators in pure
Python, and prints ONE structured JSON snapshot to stdout (and saves a copy).

Usage:
    python market_snapshot.py            # defaults to BTCUSDT
    python market_snapshot.py ETHUSDT
    python market_snapshot.py SOLUSDT 1h # symbol + primary timeframe

Data is the same across venues, so this is NOT tied to MEXC. It uses Binance
public endpoints (deepest free source) + alternative.me Fear & Greed. Prices
track MEXC closely. Analysis is produced by Claude reading this JSON; the human
places the trades.
"""

import sys
import os
import json
import time
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------- #
# Config (swap base URLs here if Binance is ever geo-blocked on your network)
# --------------------------------------------------------------------------- #
SPOT_BASE = "https://api.binance.com"
FUT_BASE = "https://fapi.binance.com"
FNG_URL = "https://api.alternative.me/fng/?limit=2"

HEADERS = {"User-Agent": "Mozilla/5.0 (market-snapshot)"}
TIMEOUT = 15
SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")

# Timeframes to pull candles for (primary is index 0)
TIMEFRAMES = ["15m", "1h", "4h"]


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _get(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# --------------------------------------------------------------------------- #
# Fetchers — each returns (data, error_string_or_None)
# --------------------------------------------------------------------------- #
def fetch_klines(symbol, interval, limit=300):
    """Return list of candle dicts (oldest->newest)."""
    try:
        raw = _get(f"{SPOT_BASE}/api/v3/klines",
                   {"symbol": symbol, "interval": interval, "limit": limit})
        candles = [{
            "open_time": c[0],
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "close_time": c[6],
            "trades": c[8],
            "taker_buy_base": float(c[9]),
        } for c in raw]
        return candles, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_ticker(symbol):
    try:
        t = _get(f"{SPOT_BASE}/api/v3/ticker/24hr", {"symbol": symbol})
        return {
            "last_price": float(t["lastPrice"]),
            "price_change_pct_24h": float(t["priceChangePercent"]),
            "high_24h": float(t["highPrice"]),
            "low_24h": float(t["lowPrice"]),
            "volume_24h_base": float(t["volume"]),
            "volume_24h_quote": float(t["quoteVolume"]),
            "weighted_avg_price": float(t["weightedAvgPrice"]),
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_depth(symbol, limit=100):
    """Order book imbalance within +/-0.5% of mid + top-of-book spread."""
    try:
        d = _get(f"{SPOT_BASE}/api/v3/depth", {"symbol": symbol, "limit": limit})
        bids = [(float(p), float(q)) for p, q in d["bids"]]
        asks = [(float(p), float(q)) for p, q in d["asks"]]
        if not bids or not asks:
            return None, "empty book"
        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        band_lo, band_hi = mid * 0.995, mid * 1.005
        bid_vol = sum(q for p, q in bids if p >= band_lo)
        ask_vol = sum(q for p, q in asks if p <= band_hi)
        total = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total if total else 0.0
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_pct": round((best_ask - best_bid) / mid * 100, 4),
            "bid_vol_within_0.5pct": round(bid_vol, 4),
            "ask_vol_within_0.5pct": round(ask_vol, 4),
            "imbalance": round(imbalance, 4),  # +ve = buy-heavy, -ve = sell-heavy
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_funding(symbol):
    try:
        p = _get(f"{FUT_BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
        return {
            "mark_price": float(p["markPrice"]),
            "index_price": float(p["indexPrice"]),
            "last_funding_rate": float(p["lastFundingRate"]),
            "funding_rate_pct": round(float(p["lastFundingRate"]) * 100, 5),
            "next_funding_time": p["nextFundingTime"],
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# Binance futures-data endpoints only accept these period buckets.
OI_PERIODS = {"5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}


def fetch_open_interest(symbol, period="15m", limit=48):
    # Scale the OI window to the primary timeframe so OI-vs-price compares the
    # same span. Fall back to 15m (and flag it) for unsupported periods.
    used_period = period if period in OI_PERIODS else "15m"
    fallback = used_period if used_period != period else None
    try:
        oi = _get(f"{FUT_BASE}/futures/data/openInterestHist",
                  {"symbol": symbol, "period": used_period, "limit": limit})
        if not oi:
            return None, "empty"
        latest = float(oi[-1]["sumOpenInterest"])
        first = float(oi[0]["sumOpenInterest"])
        change_pct = (latest - first) / first * 100 if first else 0.0
        return {
            "latest_open_interest": latest,
            "latest_oi_value_usd": float(oi[-1]["sumOpenInterestValue"]),
            "oi_change_pct_over_window": round(change_pct, 3),
            "window": f"{limit}x{used_period}",
            "period_fallback_from": period if fallback else None,
            "_raw": oi,  # kept for OI-vs-price signal; stripped before output
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_long_short(symbol, period="15m", limit=4):
    used_period = period if period in OI_PERIODS else "15m"
    try:
        ls = _get(f"{FUT_BASE}/futures/data/globalLongShortAccountRatio",
                  {"symbol": symbol, "period": used_period, "limit": limit})
        if not ls:
            return None, "empty"
        latest = ls[-1]
        return {
            "long_account": float(latest["longAccount"]),
            "short_account": float(latest["shortAccount"]),
            "long_short_ratio": float(latest["longShortRatio"]),
        }, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_fear_greed():
    try:
        d = _get(FNG_URL)["data"]
        cur = d[0]
        out = {
            "value": int(cur["value"]),
            "classification": cur["value_classification"],
        }
        if len(d) > 1:
            out["previous_value"] = int(d[1]["value"])
        return out, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# Indicators — pure Python (no numpy/pandas)
# --------------------------------------------------------------------------- #
def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period  # seed with SMA
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _ema_series(values, period):
    """Full EMA series (for MACD)."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    e = sum(values[:period]) / period
    series = [e]
    for v in values[period:]:
        e = v * k + e * (1 - k)
        series.append(e)
    return series


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    # Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None
    fast_s = _ema_series(closes, fast)
    slow_s = _ema_series(closes, slow)
    # align tails (slow series is shorter/offset)
    n = min(len(fast_s), len(slow_s))
    macd_line = [fast_s[-n + i] - slow_s[-n + i] for i in range(n)]
    signal_s = _ema_series(macd_line, signal)
    if not signal_s:
        return None
    macd_val = macd_line[-1]
    sig_val = signal_s[-1]
    return {
        "macd": round(macd_val, 4),
        "signal": round(sig_val, 4),
        "histogram": round(macd_val - sig_val, 4),
    }


def bollinger(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    upper = mid + mult * sd
    lower = mid - mult * sd
    price = closes[-1]
    pct_b = (price - lower) / (upper - lower) if upper != lower else 0.5
    bandwidth = (upper - lower) / mid * 100 if mid else 0.0
    return {
        "upper": round(upper, 4),
        "mid": round(mid, 4),
        "lower": round(lower, 4),
        "percent_b": round(pct_b, 4),
        "bandwidth_pct": round(bandwidth, 4),  # low = squeeze
    }


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def swings(candles, lookback=50):
    """Nearest recent swing high/low over lookback candles."""
    window = candles[-lookback:] if len(candles) >= lookback else candles
    hi = max(c["high"] for c in window)
    lo = min(c["low"] for c in window)
    return {"swing_high": hi, "swing_low": lo, "lookback": len(window)}


def order_flow(candles, window=20):
    """LEADING signal. Aggressive buy vs sell pressure from taker volume.

    Each candle: taker_buy_base = market-buy volume; the rest = market-sell.
    delta = buys - sells. CVD = cumulative delta. Uses CLOSED candles only.
    """
    closed = candles[:-1] if len(candles) > 1 else candles
    if len(closed) < 2:
        return None
    deltas = []
    for c in closed:
        buys = c["taker_buy_base"]
        sells = c["volume"] - buys
        deltas.append(buys - sells)

    cvd = []
    running = 0.0
    for d in deltas:
        running += d
        cvd.append(running)

    win = deltas[-window:]
    buy_vol = sum(c["taker_buy_base"] for c in closed[-window:])
    total_vol = sum(c["volume"] for c in closed[-window:])
    buy_ratio = buy_vol / total_vol if total_vol else 0.5

    # Is CVD rising or falling over the window? (slope sign via first vs last)
    cvd_window = cvd[-window:]
    cvd_change = cvd_window[-1] - cvd_window[0] if len(cvd_window) > 1 else 0.0

    last = deltas[-1]
    return {
        "last_candle_delta": round(last, 4),
        "last_candle_pressure": "buy" if last > 0 else "sell",
        "cvd_change_over_window": round(cvd_change, 4),
        "cvd_trend": "rising" if cvd_change > 0 else "falling",
        "buy_volume_ratio": round(buy_ratio, 4),  # >0.5 = buyers aggressive
        "window": window,
    }


def oi_price_signal(oi_hist, primary_closes):
    """LEADING signal. Classify Open-Interest-vs-Price over the same window.

    OI up + price up   = new longs      (bullish continuation)
    OI up + price down = new shorts      (bearish continuation)
    OI down + price up = short covering  (weak rally, fading)
    OI down + price down = long liquidation (weak selloff, fading)

    OI history is now fetched at the PRIMARY timeframe (see fetch_open_interest),
    so the OI window and the price window below span the same real time — valid
    on 15m/1h/4h alike.
    """
    if not oi_hist or len(oi_hist) < 2 or not primary_closes or len(primary_closes) < 2:
        return None
    oi_first = float(oi_hist[0]["sumOpenInterest"])
    oi_last = float(oi_hist[-1]["sumOpenInterest"])
    n = min(len(oi_hist), len(primary_closes))
    price_first = primary_closes[-n]
    price_last = primary_closes[-1]
    oi_up = oi_last >= oi_first
    price_up = price_last >= price_first
    if oi_up and price_up:
        label, bias = "new_longs", "bullish"
    elif oi_up and not price_up:
        label, bias = "new_shorts", "bearish"
    elif not oi_up and price_up:
        label, bias = "short_covering", "weak_up"
    else:
        label, bias = "long_liquidation", "weak_down"
    return {
        "oi_change_pct": round((oi_last - oi_first) / oi_first * 100, 3) if oi_first else None,
        "price_change_pct": round((price_last - price_first) / price_first * 100, 3) if price_first else None,
        "interpretation": label,
        "bias": bias,
    }


def analyze_candles(candles):
    """Compute the full indicator block for one timeframe."""
    closes = [c["close"] for c in candles]
    price = closes[-1]

    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    # Last candle is still forming (partial volume). Compare the last CLOSED
    # candle to the average of the 20 closed candles before it.
    closed = candles[:-1] if len(candles) > 1 else candles
    vols = [c["volume"] for c in closed]
    last_vol = vols[-1] if vols else None
    prior = vols[-21:-1] if len(vols) > 21 else vols[:-1]
    vol_avg20 = sum(prior) / len(prior) if prior else None

    a = atr(candles, 14)

    rsi_val = rsi(closes, 14)
    block = {
        "close": price,
        # --- LEADING (real-time pressure; weight these) ---
        "order_flow": order_flow(candles),
        "volume_last": last_vol,
        "volume_avg20": round(vol_avg20, 4) if vol_avg20 else None,
        "volume_spike_x": round(last_vol / vol_avg20, 2) if (vol_avg20 and last_vol) else None,
        # --- STRUCTURE (price levels; objective) ---
        "swings": swings(candles),
        "atr14": round(a, 4) if a else None,
        "atr_pct": round(a / price * 100, 3) if a else None,
        # --- LAGGING (context only; do NOT trigger on these alone) ---
        "_lagging": {
            "ema9": round(e9, 4) if e9 else None,
            "ema21": round(e21, 4) if e21 else None,
            "ema50": round(e50, 4) if e50 else None,
            "ema200": round(e200, 4) if e200 else None,
            "price_vs_ema50": _rel(price, e50),
            "price_vs_ema200": _rel(price, e200),
            "ema9_vs_ema21": _rel(e9, e21),
            "rsi14": round(rsi_val, 2) if rsi_val else None,
            "macd": macd(closes),
            "bollinger": bollinger(closes),
        },
    }
    return block


def _rel(a, b):
    """Signed % difference of a vs b, or None."""
    if a is None or b is None or b == 0:
        return None
    return round((a - b) / b * 100, 3)


# --------------------------------------------------------------------------- #
# Assemble snapshot
# --------------------------------------------------------------------------- #
def build_snapshot(symbol, primary_tf):
    tfs = [primary_tf] + [t for t in TIMEFRAMES if t != primary_tf]
    snap = {
        "symbol": symbol,
        "primary_timeframe": primary_tf,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sources": {"spot": SPOT_BASE, "futures": FUT_BASE, "fear_greed": "alternative.me"},
        "errors": {},
    }

    # Candles + indicators per timeframe
    indicators = {}
    last_close_time = None
    primary_closes = None
    for tf in tfs:
        candles, err = fetch_klines(symbol, tf)
        if err:
            snap["errors"][f"klines_{tf}"] = err
            continue
        indicators[tf] = analyze_candles(candles)
        if tf == primary_tf:
            last_close_time = candles[-1]["close_time"]
            primary_closes = [c["close"] for c in candles]
    snap["indicators"] = indicators

    # Ticker
    ticker, err = fetch_ticker(symbol)
    if err:
        snap["errors"]["ticker"] = err
    snap["ticker_24h"] = ticker
    if ticker:
        snap["last_price"] = ticker["last_price"]
    elif primary_tf in indicators:
        snap["last_price"] = indicators[primary_tf]["close"]

    # Order book
    depth, err = fetch_depth(symbol)
    if err:
        snap["errors"]["depth"] = err
    snap["order_book"] = depth

    # Derivatives
    funding, err = fetch_funding(symbol)
    if err:
        snap["errors"]["funding"] = err
    snap["funding"] = funding

    # OI window now scaled to the primary timeframe so oi_vs_price is valid on
    # all TFs (fixes the fixed-12h-window bug).
    oi, err = fetch_open_interest(symbol, period=primary_tf)
    if err:
        snap["errors"]["open_interest"] = err
    if oi:
        oi_raw = oi.pop("_raw", None)
        # LEADING: classify OI move against primary-timeframe price move
        oi["oi_vs_price"] = oi_price_signal(oi_raw, primary_closes)
    snap["open_interest"] = oi

    ls, err = fetch_long_short(symbol, period=primary_tf)
    if err:
        snap["errors"]["long_short"] = err
    snap["long_short"] = ls

    # Sentiment
    fng, err = fetch_fear_greed()
    if err:
        snap["errors"]["fear_greed"] = err
    snap["fear_greed"] = fng

    snap["_last_close_time"] = last_close_time
    return snap


def save_snapshot(snap):
    try:
        os.makedirs(SNAP_DIR, exist_ok=True)
        stamp = snap.get("_last_close_time") or int(time.time() * 1000)
        path = os.path.join(SNAP_DIR, f"{snap['symbol']}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2)
        return path
    except Exception as e:
        return f"(save failed: {e})"


def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    primary_tf = sys.argv[2] if len(sys.argv) > 2 else "15m"
    if primary_tf not in TIMEFRAMES:
        TIMEFRAMES.insert(0, primary_tf)

    snap = build_snapshot(symbol, primary_tf)
    saved = save_snapshot(snap)
    snap["_saved_to"] = saved
    print(json.dumps(snap, indent=2))


if __name__ == "__main__":
    main()
