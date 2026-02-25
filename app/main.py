"""
FastAPI app for Price Sync (marketplace). Step 2: OAuth implemented.
- GET /connect → redirect to Jobber authorize URL (state in cookie)
- GET /oauth/callback → exchange code, store tokens, set account cookie, redirect dashboard
- GET /dashboard → Manage App; shows connected state or Connect button
- GET /disconnect → clear account cookie, redirect dashboard
"""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_URL, JOBBER_CLIENT_ID
from app.cookies import (
    COOKIE_ACCOUNT,
    COOKIE_OAUTH_STATE,
    COOKIE_MAX_AGE,
    make_account_cookie_value,
    get_account_id_from_cookie,
    generate_state,
)
from app.database import init_db, get_connection_by_account_id, save_connection, delete_connection
from app.jobber_oauth import (
    build_authorize_url,
    exchange_code_for_tokens,
    get_account_info,
)

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


def _callback_uri() -> str:
    return f"{BASE_URL.rstrip('/')}/oauth/callback"


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/connect/", response_class=RedirectResponse)
async def connect_trailing_slash(request: Request):
    """Redirect /connect/ to /connect so both work."""
    return RedirectResponse(url="/connect", status_code=302)


@app.get("/connect", response_class=RedirectResponse)
async def connect(request: Request):
    """Redirect to Jobber OAuth authorize URL. State stored in cookie."""
    if not JOBBER_CLIENT_ID:
        return RedirectResponse(url="/dashboard?error=missing_client_id", status_code=302)
    state = generate_state()
    redirect_uri = _callback_uri()
    url = build_authorize_url(redirect_uri, state)
    response = RedirectResponse(url=url, status_code=302)
    _secure = BASE_URL.strip().lower().startswith("https")
    response.set_cookie(
        COOKIE_OAUTH_STATE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    return response


@app.get("/oauth/callback/", response_class=RedirectResponse)
async def oauth_callback_trailing_slash(request: Request):
    """Redirect trailing-slash callback to canonical path (with query string) so Jobber redirects don't 404."""
    path = "/oauth/callback"
    if request.url.query:
        path = path + "?" + request.url.query
    return RedirectResponse(url=path, status_code=302)


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Exchange code for tokens, fetch account, store connection, set session cookie."""
    code = request.query_params.get("code")
    state_param = request.query_params.get("state")
    state_cookie = request.cookies.get(COOKIE_OAUTH_STATE)

    if not code:
        return RedirectResponse(url="/dashboard?error=no_code", status_code=302)
    # Require both state cookie and param to match (CSRF protection)
    if not state_cookie or not state_param or state_cookie != state_param:
        return RedirectResponse(url="/dashboard?error=invalid_state", status_code=302)

    redirect_uri = _callback_uri()
    try:
        tokens = exchange_code_for_tokens(code, redirect_uri)
    except Exception as e:
        msg = quote(str(e)[:80], safe="")
        return RedirectResponse(
            url=f"/dashboard?error=token_exchange&message={msg}",
            status_code=302,
        )

    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    try:
        account = get_account_info(access_token)
    except Exception as e:
        msg = quote(str(e)[:80], safe="")
        return RedirectResponse(
            url=f"/dashboard?error=account_query&message={msg}",
            status_code=302,
        )

    account_id = (account.get("id") or "").strip()
    account_name = (account.get("name") or "").strip()
    if not account_id:
        return RedirectResponse(
            url="/dashboard?error=account_query&message=empty_account_id",
            status_code=302,
        )

    save_connection(
        jobber_account_id=account_id,
        jobber_account_name=account_name,
        access_token=access_token,
        refresh_token=refresh_token,
    )

    response = RedirectResponse(url="/dashboard", status_code=302)
    _secure = BASE_URL.strip().lower().startswith("https")
    response.set_cookie(
        COOKIE_OAUTH_STATE,
        "",
        max_age=0,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    response.set_cookie(
        COOKIE_ACCOUNT,
        make_account_cookie_value(account_id),
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_secure,
    )
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Manage App URL: show connected state or Connect to Jobber."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    connection = get_connection_by_account_id(account_id) if account_id else None

    connected = connection is not None
    jobber_account_name = connection.get("jobber_account_name") if connection else None

    error = request.query_params.get("error")
    message = request.query_params.get("message", "")

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "base_url": BASE_URL,
            "connected": connected,
            "jobber_account_name": jobber_account_name,
            "error": error,
            "error_message": message,
        },
    )


@app.get("/disconnect", response_class=RedirectResponse)
async def disconnect(request: Request):
    """Clear account session cookie and remove connection from DB; user sees Connect again."""
    account_cookie = request.cookies.get(COOKIE_ACCOUNT)
    account_id = get_account_id_from_cookie(account_cookie)
    if account_id:
        delete_connection(account_id)
    response = RedirectResponse(url="/dashboard", status_code=302)
    response.delete_cookie(COOKIE_ACCOUNT)
    return response


@app.get("/health")
async def health():
    """For deploy health checks (e.g. Render, Railway)."""
    return {"status": "ok"}
