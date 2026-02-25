# Jobber Integrator

Sync wholesaler CSV pricing to Jobber Products & Services via the GraphQL API.

- **CLI** (single account): run `sync_prices_to_jobber.py` with a token in `.env`.
- **Web app** (marketplace shell): multi-tenant app for the Jobber App Marketplace (Step 1 in progress).

## CLI

```bash
pip install -r requirements.txt
# Add JOBBER_ACCESS_TOKEN to .env
python sync_prices_to_jobber.py --dry-run   # preview
python sync_prices_to_jobber.py              # sync
```

## Web app (Step 1)

Local run:

```bash
pip install -r requirements.txt
# Optional: JOBBER_CLIENT_ID, JOBBER_CLIENT_SECRET, BASE_URL in .env (needed for OAuth in Step 2)
uvicorn app.main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) → redirects to `/dashboard`. Health: [http://localhost:8000/health](http://localhost:8000/health).

See [MARKETPLACE_ROADMAP.md](MARKETPLACE_ROADMAP.md) for the full path to the marketplace.
