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

DATA SOURCE: uses the `nse` PyPI package (pip install nse), a maintained
wrapper around NSE's own unofficial JSON endpoints. Critically, it's run
with server=True, which switches its HTTP client to one built specifically
to work from cloud/datacenter environments like GitHub Actions -- NSE
otherwise blocks requests from these IP ranges even with correct headers,
which is what caused earlier runs of this script to fail with 404s.

Output: fno_data.json
"""

import json
import time
from datetime import date, datetime, timedelta, timezone

from nse import NSE

OUTPUT_FILE = "fno_data.json"
DOWNLOAD_FOLDER = "."
INDEX_SYMBOLS = ["nifty", "banknifty"]
# fnoLots() sometimes includes index tokens alongside stocks -- exclude them
# from the stock-futures scan since they're handled separately as indices.
KNOWN_INDEX_TOKENS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYIT", "MIDCPNIFTY", "NIFTYNXT50"}


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


def fetch_stock_buildup(nse_client, symbols):
    today = date.today()
    # NSE's historical F&O endpoint can lag -- "today" may not be populated yet,
    # especially early in the session. Pull a short window and use each
    # symbol's most recent available row instead of requiring today exactly.
    window_start = today - timedelta(days=5)
    results = []
    for sym in symbols:
        try:
            rows = nse_client.fetch_historical_fno_data(
                symbol=sym, instrument="FUTSTK", from_date=window_start, to_date=today
            )
            if not rows:
                continue
            # multiple expiries/days can come back -- take the latest trading
            # day, then the nearest expiry within that day
            latest_date = max(r["FH_TIMESTAMP"] for r in rows)
            same_day_rows = [r for r in rows if r["FH_TIMESTAMP"] == latest_date]
            row = min(same_day_rows, key=lambda r: datetime.strptime(r["FH_EXPIRY_DT"], "%d-%b-%Y"))

            close = row.get("FH_CLOSING_PRICE") or row.get("FH_LAST_TRADED_PRICE")
            prev_close = row.get("FH_PREV_CLS")
            price_change_pct = (
                round((close - prev_close) / prev_close * 100, 2)
                if close is not None and prev_close else None
            )

            oi = row.get("FH_OPEN_INT")
            oi_change = row.get("FH_CHANGE_IN_OI")
            prev_oi = (oi - oi_change) if (oi is not None and oi_change is not None) else None
            oi_change_pct = round(oi_change / prev_oi * 100, 2) if prev_oi else None

            results.append({
                "symbol": sym,
                "price": close,
                "price_change_pct": price_change_pct,
                "open_interest": oi,
                "oi_change": oi_change,
                "oi_change_pct": oi_change_pct,
                "buildup": classify_buildup(price_change_pct, oi_change_pct),
                "as_of": row.get("FH_TIMESTAMP"),
            })
        except Exception:
            continue
        time.sleep(0.35)  # stay well under NSE's informal rate limits
    return results


def fetch_index_option_summary(nse_client, index_symbol):
    expiries = nse_client.getFuturesExpiry(index=index_symbol)
    nearest_expiry_str = expiries[0]
    nearest_expiry_dt = datetime.strptime(nearest_expiry_str, "%d-%b-%Y")

    compiled = nse_client.compileOptionChain(index_symbol, nearest_expiry_dt)

    chain = compiled.get("chain", {})
    call_oi_by_strike = sorted(
        ((strike, data.get("ce", {}).get("oi", 0) or 0) for strike, data in chain.items()),
        key=lambda kv: kv[1], reverse=True,
    )[:5]
    put_oi_by_strike = sorted(
        ((strike, data.get("pe", {}).get("oi", 0) or 0) for strike, data in chain.items()),
        key=lambda kv: kv[1], reverse=True,
    )[:5]

    return {
        "underlying_value": compiled.get("underlying"),
        "expiry": compiled.get("expiry"),
        "atm_strike": compiled.get("atm"),
        "max_pain": compiled.get("maxpain"),
        "total_call_oi": compiled.get("coiTotal"),
        "total_put_oi": compiled.get("poiTotal"),
        "pcr": compiled.get("pcr"),
        "top_call_oi_strikes": [{"strike": s, "oi": oi} for s, oi in call_oi_by_strike],
        "top_put_oi_strikes": [{"strike": s, "oi": oi} for s, oi in put_oi_by_strike],
    }


def main():
    with NSE(download_folder=DOWNLOAD_FOLDER, server=True) as nse_client:
        lots = nse_client.fnoLots()
        symbols = [s for s in lots.keys() if s not in KNOWN_INDEX_TOKENS]

        buildup = fetch_stock_buildup(nse_client, symbols)

        index_summary = {}
        for idx in INDEX_SYMBOLS:
            try:
                index_summary[idx.upper()] = fetch_index_option_summary(nse_client, idx)
            except Exception as e:
                index_summary[idx.upper()] = {"error": str(e)}

    buildup_grouped = {
        "long_buildup": [b for b in buildup if b["buildup"] == "Long Buildup"],
        "short_buildup": [b for b in buildup if b["buildup"] == "Short Buildup"],
        "short_covering": [b for b in buildup if b["buildup"] == "Short Covering"],
        "long_unwinding": [b for b in buildup if b["buildup"] == "Long Unwinding"],
    }
    for group in buildup_grouped.values():
        group.sort(key=lambda b: abs(b["oi_change_pct"] or 0), reverse=True)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Descriptive classification of today's futures/options activity, not a "
                       "prediction or a trade recommendation. Does not suggest any option, "
                       "strike, or strategy to trade.",
        "stock_futures_scanned": len(buildup),
        "buildup": buildup_grouped,
        "index_options": index_summary,
    }

    total_buildup_entries = sum(len(v) for v in buildup_grouped.values())
    index_ok = any("error" not in v for v in index_summary.values())

    if total_buildup_entries == 0 and not index_ok:
        # This run got essentially nothing from NSE (likely blocked, rate
        # limited, or hit a day with no data yet). Don't let a bad run
        # overwrite a previous good file -- leave it untouched instead.
        print("This run returned no usable data (0 buildup entries, no working "
              "index data). Leaving the existing fno_data.json untouched rather "
              "than overwriting it with an empty result.")
        return

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Scanned {len(buildup)} F&O stocks, saved {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
