"""
poll.py — fetches free, public market + news data and saves it to a
single JSON file (latest_data.json) that a dashboard can read.

Currently wired up (no login required):
  - Nifty 50, Sensex, Bank Nifty prices        (Yahoo Finance, via yfinance)
  - Latest headlines from Economic Times,
    Moneycontrol, and Livemint                  (public RSS feeds)

NOT wired up yet (needs your input / a decision later):
  - Zerodha holdings & positions                (needs Kite Connect + daily token)
  - Groww holdings & live prices                 (needs paid Groww API, ₹499/mo)
  - NSE/BSE corporate announcements              (needs a scraper, breaks often)
  - "Impact / Beneficiaries / Confidence" scoring on news (needs an LLM call per headline)

Run this manually with: python3 poll.py
Later this can be put on a schedule (e.g. GitHub Actions, cron) to run every 60s.
"""

import json
import time
from datetime import datetime, timezone

import yfinance as yf
import feedparser

OUTPUT_FILE = "latest_data.json"

# Yahoo Finance tickers for Indian indices
INDEX_TICKERS = {
    "Nifty 50": "^NSEI",
    "Sensex": "^BSESN",
    "Bank Nifty": "^NSEBANK",
}

# Free public RSS feeds
NEWS_FEEDS = {
    "Economic Times": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Livemint": "https://www.livemint.com/rss/markets",
}


def fetch_index_prices():
    prices = {}
    for name, ticker in INDEX_TICKERS.items():
        try:
            data = yf.Ticker(ticker).history(period="1d", interval="1m")
            if not data.empty:
                last_row = data.iloc[-1]
                prices[name] = {
                    "price": round(float(last_row["Close"]), 2),
                    "as_of": str(data.index[-1]),
                }
            else:
                prices[name] = {"error": "no data returned"}
        except Exception as e:
            prices[name] = {"error": str(e)}
    return prices


def fetch_news(max_per_source=5):
    news = {}
    for source, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            articles = []
            for entry in feed.entries[:max_per_source]:
                articles.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
            news[source] = articles
        except Exception as e:
            news[source] = [{"error": str(e)}]
    return news


def main():
    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "market_prices": fetch_index_prices(),
        "news": fetch_news(),
        "portfolio": {
            "status": "not_connected",
            "note": "Zerodha Kite Connect Personal not yet linked. See step 2/3 of setup."
        },
        "groww": {
            "status": "not_connected",
            "note": "Groww API requires paid plan (₹499/month) or manual CSV import."
        },
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Saved fresh data to {OUTPUT_FILE} at {result['fetched_at']}")
    return result


if __name__ == "__main__":
    main()
