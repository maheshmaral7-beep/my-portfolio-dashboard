# Portfolio dashboard — setup

This folder is a ready-to-use GitHub repo. It contains:

- `poll.py` — fetches Nifty/Sensex/Bank Nifty prices (Yahoo Finance) and headlines (RSS) every time it runs, and saves them to `latest_data.json`.
- `.github/workflows/poll.yml` — tells GitHub to run `poll.py` automatically every 5 minutes and save the result.
- `dashboard.html` — the actual dashboard. Open it in any browser. It fetches `latest_data.json` on load and every 5 minutes.
- `sample_latest_data.json` — fake data so you can preview the dashboard before GitHub is set up.

## Try it right now, with no setup

1. Open `dashboard.html` directly in your browser (double-click it).
2. It will try to fetch from a placeholder GitHub URL and fail (that's expected — nothing is set up yet), and show empty states.
3. To preview it with sample data instead, run a local server in this folder and open:
   `dashboard.html?data=sample_latest_data.json`
   (a plain double-click won't work for this step, browsers block local file-to-file fetches — any simple local server works, e.g. `python3 -m http.server` then visit `http://localhost:8000/dashboard.html?data=sample_latest_data.json`)

## Set up the real live version (10 minutes, free)

1. **Create a GitHub account** if you don't have one (github.com — free).
2. **Create a new repository** (e.g. `my-portfolio-dashboard`). Public repo is fine and free.
3. **Upload every file in this folder** to that repo (drag and drop on the GitHub website works, or use `git push` if you're comfortable with git).
4. On GitHub, go to your repo → **Settings → Actions → General** → under "Workflow permissions" choose **"Read and write permissions"**, then save. (This lets the scheduled job save `latest_data.json` back to your repo.)
5. Go to the **Actions** tab of your repo → you should see "Refresh dashboard data" → click **Run workflow** once manually to test it.
6. After it runs successfully, `latest_data.json` in your repo will have real numbers. Its public URL will be:
   `https://raw.githubusercontent.com/<your-username>/<your-repo>/main/latest_data.json`
7. Open `dashboard.html`, find this line near the bottom:
   ```
   DATA_URL: "https://raw.githubusercontent.com/YOUR-USERNAME/YOUR-REPO/main/latest_data.json",
   ```
   Replace `YOUR-USERNAME/YOUR-REPO` with your actual repo path, save, and re-upload it (or just edit it directly on GitHub).
8. Open `dashboard.html` in your browser — it now shows live prices and news, refreshing every 5 minutes.

## Still not connected (on purpose, for now)

- **Zerodha holdings** — needs the Kite Connect Personal login flow (we'll add this when you're ready).
- **Groww holdings/prices** — needs the paid Groww API (₹499/month) or manual CSV import.
- **Opportunity Radar / Top Picks / Sector Engine** — these need a stock-scoring model that doesn't exist yet; the dashboard currently shows an honest "not built yet" placeholder instead of making up numbers.
