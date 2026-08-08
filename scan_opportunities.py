"""
scan_opportunities.py — a rules-based screener over the Nifty 200 universe.

IMPORTANT — what this is and isn't:
This is NOT investment advice and NOT a predictive model. It applies fixed,
transparent rules to price/volume history and ranks stocks that match each
rule today. "Score" = percentile rank within the category, not a probability
of anything. Always read the "why" field, not just the score.

Categories (technical, from free OHLCV data only):
  - momentum_leaders     : strong 3-month return, trading above 50 & 200 day averages
  - breakout_candidates  : within 3% of 52-week high, on above-average volume
  - turnaround_stories   : up 20%+ from its own 6-month low, but still well off its high
  - emerging_leaders     : Nifty 200 stocks (excluding Nifty 50) with top-quartile 1-month return

Categories requiring fundamentals (fetched only for a shortlist, to stay light):
  - undervalued_quality  : below-median P/E + above-median ROE among the shortlist
  - earnings_winners     : positive, above-median earnings growth among the shortlist

Universe list is fetched live from NSE each run (not hardcoded), so it stays current
as NSE rebalances the index.

Output: opportunities.json
"""

import json
import time
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import yfinance as yf

NSE_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv"
NSE_50_LIST_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
OUTPUT_FILE = "opportunities.json"
TOP_N = 10
FUNDAMENTALS_SHORTLIST_SIZE = 40  # cap on how many tickers get an .info call


def fetch_universe_simple():
    """Simpler, defensive fetch: returns a DataFrame with Symbol, Company Name, Industry."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    session = requests.Session()
    session.headers.update(headers)
    session.get("https://www.nseindia.com", timeout=10)  # sets cookies NSE expects

    resp200 = session.get(NSE_LIST_URL, timeout=15)
    resp200.raise_for_status()
    from io import StringIO
    nifty200 = pd.read_csv(StringIO(resp200.text))

    resp50 = session.get(NSE_50_LIST_URL, timeout=15)
    resp50.raise_for_status()
    nifty50 = pd.read_csv(StringIO(resp50.text))

    nifty200["is_nifty50"] = nifty200["Symbol"].isin(nifty50["Symbol"])
    return nifty200


def batch_price_history(symbols, period="1y"):
    tickers = [f"{s}.NS" for s in symbols]
    data = yf.download(tickers=tickers, period=period, interval="1d",
                        group_by="ticker", auto_adjust=True, threads=True, progress=False)
    return data


def compute_technicals(data, symbols):
    rows = []
    for s in symbols:
        ticker = f"{s}.NS"
        try:
            df = data[ticker].dropna()
        except Exception:
            continue
        if len(df) < 60:
            continue
        close = df["Close"]
        vol = df["Volume"]

        price = close.iloc[-1]
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else np.nan
        high_52w = close.rolling(min(252, len(close))).max().iloc[-1]
        low_52w = close.rolling(min(252, len(close))).min().iloc[-1]
        low_6m = close.tail(126).min() if len(close) >= 20 else close.min()

        ret_1m = (price / close.iloc[-21] - 1) * 100 if len(close) > 21 else np.nan
        ret_3m = (price / close.iloc[-63] - 1) * 100 if len(close) > 63 else np.nan

        avg_vol_20 = vol.tail(20).mean()
        avg_vol_90 = vol.tail(90).mean() if len(vol) >= 90 else vol.mean()
        vol_surge = (avg_vol_20 / avg_vol_90) if avg_vol_90 else np.nan

        daily_ret = close.pct_change().dropna()
        volatility_ann = daily_ret.std() * (252 ** 0.5) * 100 if len(daily_ret) > 20 else np.nan

        rows.append({
            "symbol": s,
            "price": round(float(price), 2),
            "sma50": round(float(sma50), 2) if pd.notna(sma50) else None,
            "sma200": round(float(sma200), 2) if pd.notna(sma200) else None,
            "high_52w": round(float(high_52w), 2),
            "low_52w": round(float(low_52w), 2),
            "low_6m": round(float(low_6m), 2),
            "ret_1m_pct": round(float(ret_1m), 2) if pd.notna(ret_1m) else None,
            "ret_3m_pct": round(float(ret_3m), 2) if pd.notna(ret_3m) else None,
            "dist_from_52w_high_pct": round(float((price - high_52w) / high_52w * 100), 2),
            "up_from_6m_low_pct": round(float((price - low_6m) / low_6m * 100), 2),
            "vol_surge_ratio": round(float(vol_surge), 2) if pd.notna(vol_surge) else None,
            "volatility_ann_pct": round(float(volatility_ann), 2) if pd.notna(volatility_ann) else None,
        })
    return pd.DataFrame(rows)


def percentile_score(series):
    if series.dropna().empty:
        return pd.Series([50] * len(series), index=series.index)
    return (series.rank(pct=True) * 100).round(0)


def risk_from_volatility(series):
    return percentile_score(series)  # higher volatility -> higher risk percentile


NSE_QUOTE_URL = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"


def add_trade_reference_fields(item, r, signal_label):
    """Adds CMP/Entry/Target/Upside/Signal/News Link -- all derived from observed
    data (today's price, the stock's own 52-week high), never predicted or invented."""
    cmp_price = r.price
    target = r.high_52w
    upside_pct = round((target - cmp_price) / cmp_price * 100, 2) if cmp_price else None
    item.update({
        "cmp": cmp_price,
        "entry": cmp_price,
        "target": round(float(target), 2),
        "upside_pct": upside_pct,
        "signal": signal_label,
        "news_link": NSE_QUOTE_URL.format(symbol=r.symbol),
    })
    return item


def build_categories(tech_df, universe_df):
    tech_df = tech_df.merge(universe_df[["Symbol", "Company Name", "Industry", "is_nifty50"]],
                             left_on="symbol", right_on="Symbol", how="left")
    tech_df = tech_df.rename(columns={"Company Name": "company_name", "Industry": "sector"})

    categories = {}

    # Momentum leaders: uptrend (price > sma50 > sma200) ranked by 3m return
    mom = tech_df[
        (tech_df["sma200"].notna()) &
        (tech_df["price"] > tech_df["sma50"]) &
        (tech_df["sma50"] > tech_df["sma200"])
    ].copy()
    mom["score"] = percentile_score(mom["ret_3m_pct"])
    mom["risk"] = risk_from_volatility(mom["volatility_ann_pct"])
    mom = mom.sort_values("ret_3m_pct", ascending=False).head(TOP_N)
    categories["momentum_leaders"] = []
    for r in mom.itertuples():
        item = {
            "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
            "score": int(r.score), "confidence": 70, "risk": int(r.risk),
            "why": f"Up {r.ret_3m_pct}% over 3 months, trading above both its 50-day and 200-day averages."
        }
        categories["momentum_leaders"].append(add_trade_reference_fields(item, r, "Momentum"))

    # Breakout candidates: within 3% of 52w high, volume surge > 1.5x
    brk = tech_df[
        (tech_df["dist_from_52w_high_pct"] >= -3) &
        (tech_df["vol_surge_ratio"].notna()) &
        (tech_df["vol_surge_ratio"] >= 1.5)
    ].copy()
    brk["score"] = percentile_score(brk["vol_surge_ratio"])
    brk["risk"] = risk_from_volatility(brk["volatility_ann_pct"])
    brk = brk.sort_values("vol_surge_ratio", ascending=False).head(TOP_N)
    categories["breakout_candidates"] = []
    for r in brk.itertuples():
        item = {
            "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
            "score": int(r.score), "confidence": 60, "risk": int(r.risk),
            "why": f"Within {abs(r.dist_from_52w_high_pct)}% of its 52-week high, on {r.vol_surge_ratio}x average volume."
        }
        categories["breakout_candidates"].append(add_trade_reference_fields(item, r, "Breakout"))

    # Turnaround stories: up 20%+ from 6-month low, but still 15%+ below 52w high
    turn = tech_df[
        (tech_df["up_from_6m_low_pct"] >= 20) &
        (tech_df["dist_from_52w_high_pct"] <= -15)
    ].copy()
    turn["score"] = percentile_score(turn["up_from_6m_low_pct"])
    turn["risk"] = risk_from_volatility(turn["volatility_ann_pct"])
    turn = turn.sort_values("up_from_6m_low_pct", ascending=False).head(TOP_N)
    categories["turnaround_stories"] = []
    for r in turn.itertuples():
        item = {
            "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
            "score": int(r.score), "confidence": 55, "risk": int(r.risk),
            "why": f"Up {r.up_from_6m_low_pct}% from its 6-month low, still {abs(r.dist_from_52w_high_pct)}% below its 52-week high."
        }
        categories["turnaround_stories"].append(add_trade_reference_fields(item, r, "Turnaround"))

    # Emerging leaders: not in Nifty 50, top-quartile 1-month return
    emg = tech_df[tech_df["is_nifty50"] == False].copy()
    categories["emerging_leaders"] = []
    if not emg.empty:
        cutoff = emg["ret_1m_pct"].quantile(0.75)
        emg = emg[emg["ret_1m_pct"] >= cutoff]
        emg["score"] = percentile_score(emg["ret_1m_pct"])
        emg["risk"] = risk_from_volatility(emg["volatility_ann_pct"])
        emg = emg.sort_values("ret_1m_pct", ascending=False).head(TOP_N)
        for r in emg.itertuples():
            item = {
                "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
                "score": int(r.score), "confidence": 55, "risk": int(r.risk),
                "why": f"Mid-cap (outside Nifty 50), up {r.ret_1m_pct}% in the past month, top quartile of the universe."
            }
            categories["emerging_leaders"].append(add_trade_reference_fields(item, r, "Emerging"))

    return categories, tech_df


def add_fundamentals_categories(categories, tech_df):
    """Fetch .info for a bounded shortlist only, to avoid heavy/slow API usage."""
    shortlist_symbols = pd.unique(pd.concat([
        tech_df.sort_values("ret_3m_pct", ascending=False).head(FUNDAMENTALS_SHORTLIST_SIZE // 2)["symbol"],
        tech_df.sort_values("ret_1m_pct", ascending=False).head(FUNDAMENTALS_SHORTLIST_SIZE // 2)["symbol"],
    ])).tolist()

    fundamentals = []
    for s in shortlist_symbols:
        try:
            info = yf.Ticker(f"{s}.NS").info
            fundamentals.append({
                "symbol": s,
                "trailingPE": info.get("trailingPE"),
                "returnOnEquity": info.get("returnOnEquity"),
                "earningsQuarterlyGrowth": info.get("earningsQuarterlyGrowth"),
                "revenueGrowth": info.get("revenueGrowth"),
            })
        except Exception:
            continue
        time.sleep(0.3)  # be gentle with the free endpoint

    fdf = pd.DataFrame(fundamentals)
    if fdf.empty:
        return {"undervalued_quality": [], "earnings_winners": []}

    merged = tech_df.merge(fdf, on="symbol", how="inner")

    uq = merged[
        (merged["trailingPE"].notna()) & (merged["trailingPE"] > 0) &
        (merged["returnOnEquity"].notna())
    ].copy()
    if not uq.empty:
        pe_median = uq["trailingPE"].median()
        roe_median = uq["returnOnEquity"].median()
        uq = uq[(uq["trailingPE"] <= pe_median) & (uq["returnOnEquity"] >= roe_median)]
        uq["score"] = percentile_score(-uq["trailingPE"])
        uq["risk"] = risk_from_volatility(uq["volatility_ann_pct"])
        uq = uq.sort_values("trailingPE").head(TOP_N)
        uq_out = []
        for r in uq.itertuples():
            item = {
                "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
                "score": int(r.score), "confidence": 50, "risk": int(r.risk),
                "why": f"P/E of {round(r.trailingPE,1)} (below the shortlist median) with ROE of {round(r.returnOnEquity*100,1)}%."
            }
            uq_out.append(add_trade_reference_fields(item, r, "Value"))
    else:
        uq_out = []

    ew = merged[
        (merged["earningsQuarterlyGrowth"].notna()) & (merged["earningsQuarterlyGrowth"] > 0)
    ].copy()
    if not ew.empty:
        ew["score"] = percentile_score(ew["earningsQuarterlyGrowth"])
        ew["risk"] = risk_from_volatility(ew["volatility_ann_pct"])
        ew = ew.sort_values("earningsQuarterlyGrowth", ascending=False).head(TOP_N)
        ew_out = []
        for r in ew.itertuples():
            item = {
                "symbol": r.symbol, "name": r.company_name, "sector": r.sector,
                "score": int(r.score), "confidence": 50, "risk": int(r.risk),
                "why": f"Latest quarterly earnings growth of {round(r.earningsQuarterlyGrowth*100,1)}%."
            }
            ew_out.append(add_trade_reference_fields(item, r, "Earnings"))
    else:
        ew_out = []

    return {"undervalued_quality": uq_out, "earnings_winners": ew_out}


def consolidate(categories, top_n=10):
    seen = {}
    for cat_name, items in categories.items():
        for item in items:
            sym = item["symbol"]
            if sym not in seen or item["score"] > seen[sym]["score"]:
                entry = dict(item)
                entry["category"] = cat_name
                seen[sym] = entry
    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


def main():
    universe_df = fetch_universe_simple()
    symbols = universe_df["Symbol"].tolist()

    price_data = batch_price_history(symbols)
    tech_df = compute_technicals(price_data, symbols)

    categories, tech_df = build_categories(tech_df, universe_df)
    fundamentals_categories = add_fundamentals_categories(categories, tech_df)
    categories.update(fundamentals_categories)

    consolidated = consolidate(categories)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": "Nifty 200",
        "universe_size_scanned": len(symbols),
        "disclaimer": "Rules-based screener, not investment advice or a prediction. "
                       "Scores are percentile ranks within today's matching stocks, not probabilities.",
        "categories": categories,
        "consolidated_top": consolidated,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Scanned {len(symbols)} stocks, saved {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
