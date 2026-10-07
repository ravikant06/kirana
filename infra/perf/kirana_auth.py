"""
Signing in for the scripts (AI Phase 5). Kirana no longer accepts X-User-Id: every call that
needs a user carries "Authorization: Bearer <token>" from POST /auth/login.

    signup(api, name, email)   creates a shopper with SCRIPT_PASSWORD, signs in, returns the user
    headers(api, user)         the bearer header for a user id from signup(); None = the admin
                               (products, inventory, system endpoints need admin permissions)
    login(api, email, pw)      any account, e.g. a seeded one with the demo password

The admin is user 1 with the demo password unless KIRANA_ADMIN_EMAIL / KIRANA_ADMIN_PASSWORD say otherwise.
"""
import json
import os
import urllib.request

SCRIPT_PASSWORD = "script-pass-123"
DEMO_PASSWORD = "kirana123"
ADMIN_EMAIL = os.getenv("KIRANA_ADMIN_EMAIL", "kumar.ravee101@gmail.com")
ADMIN_PASSWORD = os.getenv("KIRANA_ADMIN_PASSWORD", DEMO_PASSWORD)

_tokens: dict[int | None, str] = {}


def _post(api, path, body):
    req = urllib.request.Request(api + path, method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def login(api, email, password):
    r = _post(api, "/auth/login", {"email": email, "password": password})
    _tokens[r["user"]["id"]] = r["accessToken"]
    return r["user"], r["accessToken"]


def signup(api, name, email):
    _post(api, "/users", {"name": name, "email": email, "password": SCRIPT_PASSWORD})
    user, _ = login(api, email, SCRIPT_PASSWORD)
    return user


def headers(api, user=None):
    if user is None:
        if None not in _tokens:
            _tokens[None] = login(api, ADMIN_EMAIL, ADMIN_PASSWORD)[1]
        return {"Authorization": f"Bearer {_tokens[None]}"}
    return {"Authorization": f"Bearer {_tokens[int(user)]}"}
