# Flask → Quart Migration Design

**Date:** 2026-05-21
**Branch:** separate PR before penguin-aaa wiring
**Status:** Design approved, pending implementation plan

---

## Problem

Elder uses Flask + `asgiref.wsgi.WsgiToAsgi` to run under uvicorn. This means every request goes through a WSGI→ASGI translation layer, which is synchronous under the hood and prevents native ASGI middleware (like `penguin-aaa`'s `TenantMiddleware`) from working correctly. Quart is Flask's ASGI-native sibling with a nearly identical API, making it the right target.

---

## Architecture

### What Changes

| Layer | Before | After |
|-------|--------|-------|
| Framework | `Flask` + `WsgiToAsgi` adapter | `Quart` (native ASGI) |
| CORS | `flask_cors.CORS` / `cross_origin` | `quart_cors.cors()` / `route_cors()` |
| CSRF | `flask_wtf.csrf.CSRFProtect` | `quart_wtf.CSRFProtect` |
| Web auth | `flask_login.LoginManager` + session | JWT cookie (HttpOnly, Secure) |
| Metrics | `prometheus_flask_exporter` | manual `/metrics` route via `prometheus_client` |
| ASGI adapter | `asgiref` | removed (Quart IS the ASGI app) |

### What Does NOT Change

- All 30+ blueprint registrations — Quart uses identical `Blueprint` API
- All `@app.route` / `@bp.route` decorators
- All `jsonify()`, `request`, `g`, `redirect`, `url_for`, `render_template` calls
- `apps/api/auth/jwt_handler.py` — unchanged
- `apps/api/auth/decorators.py` — unchanged (already async-native)
- SQLAlchemy / PyDAL setup
- All `apps/api/api/v1/*.py` blueprint files (except `tenants.py` stray import fix and `sync.py` CORS)

---

## Dependency Changes

### Remove from `requirements.in`

```
Flask-Login==0.6.3
asgiref==3.8.1
prometheus-flask-exporter==0.23.1
# Dead deps (in requirements.in but not imported anywhere):
Flask-RESTful==0.3.10
flask-restx==1.3.0
Flask-Limiter==3.8.0
Flask-SocketIO==5.4.1
python-socketio==5.14.0
flask-caching==2.3.0
flasgger==0.9.7.1
apispec==6.8.0
apispec-webframeworks==1.2.0
uvloop==0.22.1  # quart/uvicorn handles this
```

### Add to `requirements.in`

```
quart>=0.19.0           # Flask-compatible ASGI framework
quart-cors>=0.7.0       # CORS for Quart (replaces flask-cors)
quart-wtf>=1.0.0        # WTF/CSRF for Quart (replaces flask-wtf)
```

### Keep (already present)

```
uvicorn[standard]       # unchanged — still the ASGI server
Flask[async]            # REMOVE — no longer needed
Flask-CORS              # REMOVE — replaced by quart-cors
Flask-WTF               # REMOVE — replaced by quart-wtf
```

---

## Key File Changes

### `apps/api/main.py`

```python
# Before
from flask import Flask, jsonify
from flask_cors import CORS
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from asgiref.wsgi import WsgiToAsgi
from prometheus_flask_exporter import PrometheusMetrics

def create_app(...) -> Flask:
    app = Flask(__name__)
    ...
    return WsgiToAsgi(app)

# After
from quart import Quart, jsonify
from quart_cors import cors
from quart_wtf.csrf import CSRFProtect

def create_app(...) -> Quart:
    app = Quart(__name__)
    ...
    return app   # Quart IS the ASGI callable
```

`_init_extensions`: Remove `LoginManager` block. Replace `CORS(app, ...)` with `cors(app, ...)`. Replace `PrometheusMetrics` with a `/metrics` route that calls `prometheus_client.generate_latest()`.

### `apps/api/web/routes.py`

Replace Flask-Login with a JWT cookie helper.

```python
# Remove
from flask_login import current_user, login_required, logout_user

# Add (new module apps/api/auth/web_auth.py)
from apps.api.auth.web_auth import get_web_user, web_login_required, web_logout_response
```

`current_user.is_authenticated` → `get_web_user() is not None`
`@login_required` → `@web_login_required`
`logout_user()` + redirect → `web_logout_response(redirect_url)`

### `apps/api/auth/web_auth.py` (new file)

```python
from functools import wraps
from quart import g, redirect, request, url_for
from apps.api.auth.jwt_handler import verify_token, get_token_from_header

COOKIE_NAME = "elder_session"

def get_web_user():
    """Read JWT from cookie; returns identity dict or None."""
    if hasattr(g, "web_user"):
        return g.web_user
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = verify_token(token)
    g.web_user = payload
    return payload

def web_login_required(f):
    @wraps(f)
    async def decorated(*args, **kwargs):
        if get_web_user() is None:
            return redirect(url_for("web.login"))
        return await f(*args, **kwargs)
    return decorated

def make_logout_response(redirect_url):
    response = redirect(redirect_url)
    response.delete_cookie(COOKIE_NAME)
    return response
```

The login API endpoint (in `apps/api/api/v1/auth.py`) is updated to **also set** the `elder_session` cookie (HttpOnly, Secure, SameSite=Strict) when authenticating for the web UI, in addition to returning the Bearer token in JSON for API clients.

### `apps/api/api/v1/tenants.py`

Remove stray `from flask_login import login_required` (line 14) and `@login_required` on `create_tenant` (line 183). Replace with the custom decorator: `from apps.api.auth.decorators import login_required`.

### `apps/api/api/v1/sync.py`

Replace `from flask_cors import cross_origin` with `from quart_cors import route_cors`.
Replace `@cross_origin()` decorators with `@route_cors()`.

---

## Auth Flow for Web UI (Post-Migration)

```
Browser GET /login  →  render login.html (no auth required)
Browser POST /api/v1/auth/login  →  returns JSON {token: "..."} + sets elder_session cookie
Browser GET /dashboard  →  web_login_required reads cookie → validates JWT → renders template
Browser GET /logout  →  clears cookie → redirect /login
```

API clients are unaffected: they continue sending `Authorization: Bearer <token>` headers. The cookie is an additional parallel delivery mechanism for the web UI only.

---

## Error Handling

- `@web_login_required` redirects to `/login` (preserves SSR UX)
- `@login_required` (custom, for API) still returns `401 JSON` (unchanged)
- No behavioral change for API consumers

---

## Testing

- Update `conftest.py` fixtures to use `Quart` test client (`app.test_client()` — same API)
- Add test: `GET /dashboard` without cookie → redirects to `/login`
- Add test: `GET /dashboard` with valid JWT cookie → renders template
- Add test: `GET /logout` → response clears `elder_session` cookie
- Existing API endpoint tests unchanged (they use Bearer tokens, not cookies)

---

## Migration Steps (implementation order)

1. Update `requirements.in` / `requirements.txt` — add quart/quart-cors/quart-wtf, remove Flask-Login/asgiref/dead deps
2. `apps/api/main.py` — Quart app factory, drop WsgiToAsgi, update extensions
3. `apps/api/auth/web_auth.py` — new JWT cookie helper
4. `apps/api/web/routes.py` — replace Flask-Login usage
5. `apps/api/api/v1/auth.py` — set `elder_session` cookie on login response
6. `apps/api/api/v1/sync.py` — replace cross_origin
7. `apps/api/api/v1/tenants.py` — fix stray import
8. Update `apps/worker/` health endpoint (Flask → Quart or plain http.server)
9. Run full test suite; fix any `flask`-specific import failures
10. Update `requirements.txt` (pip-compile)

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Quart async context differs from Flask's | Quart's `g`, `request`, `current_app` work identically; test suite catches regressions |
| `quart-wtf` CSRF tokens in templates | Audit templates for `{{ csrf_token() }}` usage before cutting over |
| Cookie domain/path on login endpoint | Set `domain` and `path=/` explicitly; test in staging before prod |
| Missed Flask-specific imports in 30+ blueprints | `grep -r "from flask import" apps/api` — audit all, Quart re-exports same symbols |
