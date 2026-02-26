# Jobber Integrator

Sync wholesaler CSV pricing to Jobber Products & Services via the GraphQL API.

- **CLI** (single account): run `sync_prices_to_jobber.py` with a token in `.env`.
- **Web app** (marketplace): multi-tenant app with OAuth and token refresh (Steps 2–3). Connect from dashboard, then sync CSV (Step 4+).

## CLI

```bash
pip install -r requirements.txt
# Add JOBBER_ACCESS_TOKEN to .env
python sync_prices_to_jobber.py --dry-run   # preview
python sync_prices_to_jobber.py              # sync
```

## Web app (OAuth + token refresh)

1. Copy `.env.example` to `.env` and fill in your values.
2. In [Jobber Developer Center](https://developer.getjobber.com/apps), open your app and set **OAuth Callback URL** to `http://localhost:8000/oauth/callback` (local) or `https://your-domain.com/oauth/callback` (production). Must match `BASE_URL` + `/oauth/callback`.
3. In `.env` set `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, and `BASE_URL=http://localhost:8000` (or your public URL).

### Run the web app locally (PowerShell)

From your project folder, run:

```powershell
cd "c:\Users\reggin\Random Cursor Shit\Jobber Integrator"
.\run_webapp.ps1
```

Or without the script (same port):

```powershell
cd "c:\Users\reggin\Random Cursor Shit\Jobber Integrator"
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

Then open [http://localhost:8000](http://localhost:8000) → **Connect to Jobber** → authorize → dashboard shows connected. Health: [http://localhost:8000/health](http://localhost:8000/health).

**Test sync (verify cost + selling price mutation):** After connecting, run `python run_sync_check.py` (uses `wholesaler_prices.csv` with 25% markup), or open [http://localhost:8000/test-sync](http://localhost:8000/test-sync) and click **Run test sync**.

## Tests

Run the test suite before merging to `master` or deploying:

```bash
pytest
```

With the project venv: `.venv\Scripts\pytest` (Windows) or `.venv/bin/pytest` (Unix).

See [MARKETPLACE_ROADMAP.md](MARKETPLACE_ROADMAP.md) for the full path to the marketplace.
