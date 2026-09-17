"""
FinMan authentication and session access control.

This module provides the authentication helpers used by FinMan's browser-based login system.

Authentication design
---------------------
FinMan is a single-user application, so authentication uses one assword rather than username/password credentials.

The password and session secret are read from environment variables:
    FINMAN_PASSWORD
        Password required to log in to FinMan.
    FINMAN_SESSION_SECRET
        Secret used by Starlette's SessionMiddleware to sign the browser session cookie.

The session secret is deliberately separate from the login password. It is not a second password for the user.
It is a cryptographic secret used to protect the integrity of the authenticated session.

Functions
---------
get_auth_config()
    Read and validate authentication configuration.

authenticate(password)
    Check whether the supplied password is correct.

require_login(request)
    FastAPI dependency that protects application routes and redirects unauthenticated users to the login page.
"""
import hmac
import os

from urllib.parse import quote

from fastapi import HTTPException, Request

def get_auth_config():
    """
    Read FinMan authentication settings from environment variables.

    Returns
    -------
    tuple
        A tuple containing:
        password:  The configured FinMan login password.
        session_secret: The secret used by SessionMiddleware to sign session cookies.
    Raises
    ------
    RuntimeError: If either required authentication setting is missing.
    Notes
    -----
    Authentication secrets are intentionally kept outside config.json so that passwords and cryptographic secrets 
    are not stored in the application's configuration files or source code.
    """
    password = os.environ.get("FINMAN_PASSWORD")
    session_secret = os.environ.get("FINMAN_SESSION_SECRET")

    if not password or not session_secret:
        raise RuntimeError("FinMan authentication is not configured. " "Set FINMAN_PASSWORD and FINMAN_SESSION_SECRET.")
    return password, session_secret


def authenticate(password: str) -> bool:
    """
    Validate the supplied FinMan login password.
    Parameters
    ----------
    password : str
        Password entered by the user on the login page.
    Returns
    -------
    bool
        True if the supplied password matches the configured password; otherwise False.

    Notes
    -----
    hmac.compare_digest() is used instead of a normal string comparison to avoid timing-based comparison differences.
    """    
    expected_password, _ = get_auth_config()

    return hmac.compare_digest(password, expected_password)


def require_login(request: Request):
    """
    FastAPI dependency for protected browser routes.

    If the current browser session is already authenticated, the dependency returns normally and the requested route continues.

    If the user is not authenticated, the request is redirected to /login. The original URL is preserved in the "next"
    parameter so that, after successful login, the user can be returned to the page they originally requested.

    Parameters
    ----------
    request : Request
        The current FastAPI/Starlette request.
    Raises
    ------
    HTTPException
        A 303 redirect to the login page when the session is not authenticated.
    """    
    if request.session.get("authenticated"):
        return

    next_url = request.url.path

    if request.url.query:
        next_url += "?" + request.url.query
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/dashboard"

    login_url = f"/login?next={quote(next_url)}"

    raise HTTPException(status_code=303, headers={"Location": login_url})
