"""
FastAPI app shell for Price Sync (marketplace). Step 1: hosting and structure.
- GET / → redirect to dashboard
- GET /dashboard → Manage App placeholder (where users land after connecting)
- GET /oauth/callback → placeholder for step 2 (OAuth)
- GET /health → for deployment checks
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import BASE_URL
from app.database import init_db

templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Price Sync",
    description="Sync wholesaler CSV pricing to Jobber Products & Services",
    lifespan=lifespan,
)


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Manage App URL: where users go after connecting from Jobber marketplace."""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "base_url": BASE_URL, "connected": False},
    )


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Placeholder. Step 2: exchange code for tokens and store by account."""
    return HTMLResponse(
        "<h1>OAuth callback</h1><p>Step 2: implement code exchange and token storage here.</p>",
        status_code=501,
    )


@app.get("/health")
async def health():
    """For deploy health checks (e.g. Render, Railway)."""
    return {"status": "ok"}
