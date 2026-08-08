"""
scan_fno.py — pulls free, public F&O (futures & options) data from NSE and
turns it into two things a trader actually uses:

1. STOCK FUTURES OI BUILDUP -- for every F&O stock, classifies today's
   activity using the standard rule (price direction x open-interest
   direction), the same logic every derivatives desk uses as a first read:
     Price up   + OI up   -> Long Buildup    (new longs being added)
     Price down + OI up   -> Short Buildup   (new shorts being added)
     Price up   + OI down -> Short Covering  (shorts closing out)
     Price down + OI down -> Long Unwinding  (longs closing out)

2. INDEX OPTIONS SNAPSHOT (Nifty & Bank Nifty) -- Put-Call Ratio, Max Pain,
   and the strikes carrying the heaviest open interest on each side.

IMPORTANT: this is descriptive classification of what already happened in
the market, not a prediction and not a trade recommendation. It doesn't
suggest which option/strike/strategy to trade -- that decision, especially
with leverage involved, stays with you.

Data sources (both free, both unofficial NSE endpoints, same session-cookie
pattern used elsewhere in this project):
  https://www.nseindia.com/api/quote-derivative?symbol=<SYMBOL>
  https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY / BANKNIFTY

Output: fno_data.json
"""

import json
import time
from datetime import datetime, timezone
from io import StringIO

import requests
import pandas as pd

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
QUOTE_DERIVATIVE_URL = "https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
OUTPUT_FILE = "fno_data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get("https://www.nseindia.com", timeout=10)  # sets cookies NSE expects
    return session


def fetch_universe(session):
    resp = session.get(NSE_LIST_URL, timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    return df["Symbol"].tolist()


def classify_buildup(price_change_pct, oi_change_pct):
    if price_change_pct is None or oi_change_pct is None:
        return None
    if price_change_pct >= 0 and oi_change_pct >= 0:
        return "Long Buildup"
    if price_change_pct < 0 and oi_change_pct >= 0:
        return "Short Buildup"
    if price_change_pct >= 0 and oi_change_pct < 0:
        return "Short Covering"
    return "Long Unwinding"


def fetch_stock_buildup(session, symbols):
    results = []
    for sym in symbols:
        try:
            resp = session.get(QUOTE_DERIVATIVE_URL.format(symbol=sym), timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            stocks = data.get("stocks", [])
            if not stocks:
                continue  # this symbol has no F&O contracts
            # use the nearest-expiry futures contract (first entry is typically nearest)
            fut = next((s for s in stocks if s.get("metadata", {}).get("instrumentType", "").endswith("Futures")), None)
            if not fut:
                continue
            meta = fut.get("metadata", {})
            price_change_pct = meta.get("pChange")
            oi = meta.get("openInterest")
            oi_change = meta.get("changeInOpenInterest")
            prev_oi = (oi - oi_change) if (oi is not None and oi_change is not None) else None
            oi_change_pct = (oi_change / prev_oi * 100) if prev_oi else None

            results.append({
                "symbol": sym,
                "price": meta.get("lastPrice"),
                "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
                "open_interest": oi,
                "oi_change": oi_change,
                "oi_change_pct": round(oi_change_pct, 2) if oi_change_pct is not None else None,
                "buildup": classify_buildup(price_change_pct, oi_change_pct),
            })
        except Exception:
            continue
        time.sleep(0.35)  # stay well under NSE's informal rate limits
    return results


def compute_max_pain(option_chain_records, expiry):
    strikes = {}
    for row in option_chain_records:
        if row.get("expiryDate") != expiry:
            continue
        strike = row.get("strikePrice")
        ce = row.get("CE", {})
        pe = row.get("PE", {})
        strikes.setdefault(strike, {"call_oi": 0, "put_oi": 0})
        strikes[strike]["call_oi"] += ce.get("openInterest", 0) or 0
        strikes[strike]["put_oi"] += pe.get("openInterest", 0) or 0

    if not strikes:
        return None, {}

    candidate_strikes = sorted(strikes.keys())
    pain_by_strike = {}
    for settle in candidate_strikes:
        total_pain = 0
        for strike, oi in strikes.items():
            total_pain += oi["call_oi"] * max(0, settle - strike)
            total_pain += oi["put_oi"] * max(0, strike - settle)
        pain_by_strike[settle] = total_pain

    max_pain_strike = min(pain_by_strike, key=pain_by_strike.get)
    return max_pain_strike, strikes


def fetch_index_option_summary(session, index_symbol):
    resp = session.get(OPTION_CHAIN_URL.format(symbol=index_symbol), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    records = data.get("records", {})
    all_rows = records.get("data", [])
    underlying_value = records.get("underlyingValue")
    expiries = records.get("expiryDates", [])
    nearest_expiry = expiries[0] if expiries else None

    total_call_oi = sum((r.get("CE", {}).get("openInterest", 0) or 0) for r in all_rows if r.get("expiryDate") == nearest_expiry)
    total_put_oi = sum((r.get("PE", {}).get("openInterest", 0) or 0) for r in all_rows if r.get("expiryDate") == nearest_expiry)
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi else None

    max_pain_strike, strikes = compute_max_pain(all_rows, nearest_expiry)

    top_call_oi = sorted(strikes.items(), key=lambda kv: kv[1]["call_oi"], reverse=True)[:5]
    top_put_oi = sorted(strikes.items(), key=lambda kv: kv[1]["put_oi"], reverse=True)[:5]

    return {
        "underlying_value": underlying_value,
        "expiry": nearest_expiry,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "pcr": pcr,
        "max_pain": max_pain_strike,
        "top_call_oi_strikes": [{"strike": k, "oi": v["call_oi"]} for k, v in top_call_oi],
        "top_put_oi_strikes": [{"strike": k, "oi": v["put_oi"]} for k, v in top_put_oi],
    }


def main():
    session = make_session()

    symbols = fetch_universe(session)
    buildup = fetch_stock_buildup(session, symbols)

    buildup_grouped = {
        "long_buildup": [b for b in buildup if b["buildup"] == "Long Buildup"],
        "short_buildup": [b for b in buildup if b["buildup"] == "Short Buildup"],
        "short_covering": [b for b in buildup if b["buildup"] == "Short Covering"],
        "long_unwinding": [b for b in buildup if b["buildup"] == "Long Unwinding"],
    }
    for group in buildup_grouped.values():
        group.sort(key=lambda b: abs(b["oi_change_pct"] or 0), reverse=True)

    index_summary = {}
    for idx in ["NIFTY", "BANKNIFTY"]:
        try:
            index_summary[idx] = fetch_index_option_summary(session, idx)
        except Exception as e:
            index_summary[idx] = {"error": str(e)}

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Descriptive classification of today's futures/options activity, not a "
                       "prediction or a trade recommendation. Does not suggest any option, "
                       "strike, or strategy to trade.",
        "stock_futures_scanned": len(buildup),
        "buildup": buildup_grouped,
        "index_options": index_summary,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Scanned {len(buildup)} F&O stocks, saved {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
