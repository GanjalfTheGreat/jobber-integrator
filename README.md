# Jobber Integrator

Sync wholesaler CSV pricing to Jobber Products & Services via the GraphQL API.

- **CLI** (single account): run `sync_prices_to_jobber.py` with a token in `.env`.
- **Web app** (marketplace): multi-tenant app with OAuth (Step 2 done). Connect from dashboard, then sync CSV (Step 3+).

## CLI

```bash
pip install -r requirements.txt
# Add JOBBER_ACCESS_TOKEN to .env
python sync_prices_to_jobber.py --dry-run   # preview
python sync_prices_to_jobber.py              # sync
```

## Web app (Step 2: OAuth)

1. In [Jobber Developer Center](https://developer.getjobber.com/apps), open your app and set **OAuth Callback URL** to `http://localhost:8000/oauth/callback` (local) or `https://your-domain.com/oauth/callback` (production). Must match `BASE_URL` + `/oauth/callback`.
2. In `.env` set `JOBBER_CLIENT_ID`, `JOBBER_CLIENT_SECRET`, and `BASE_URL=http://localhost:8000` (or your public URL).
3. Run the app **from your terminal** (so it always uses port 8000 and you avoid port conflicts):

```powershell
.\run_webapp.ps1
```

Or manually: `uvicorn app.main:app --reload --port 8000`

Open [http://localhost:8000](http://localhost:8000) → **Connect to Jobber** → authorize → dashboard shows connected. Health: [http://localhost:8000/health](http://localhost:8000/health).

See [MARKETPLACE_ROADMAP.md](MARKETPLACE_ROADMAP.md) for the full path to the marketplace.
