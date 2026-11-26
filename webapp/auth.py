"""Optional login (one user from the settings).

    AUTH_USER            user name; without it Photon Ledger stays open as before
    AUTH_PASSWORD_HASH   password hash, made with:  python -m webapp.auth
    AUTH_PUBLIC_READ     true = everyone may look, only changes need the login

With a login the SECRET_KEY must be set (it signs the session cookie).
"""
from __future__ import annotations

import getpass
import hmac
import sys
import time
from datetime import timedelta
from urllib.parse import urlsplit

from flask import (Blueprint, current_app, flash, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

auth = Blueprint("auth", __name__)

OPEN_ENDPOINTS = {"auth.login", "auth.logout", "main.healthz", "static"}
DEFAULT_SECRET = "change-me"


def enabled() -> bool:
    return bool(current_app.config.get("AUTH_USER"))


def logged_in() -> bool:
    return enabled() and session.get("user") == current_app.config["AUTH_USER"]


def public_read() -> bool:
    v = current_app.config.get("AUTH_PUBLIC_READ")
    return v is True or str(v).lower() in ("1", "true", "yes", "ja")


def may_change() -> bool:
    """True if the current visitor may change something (always without login)."""
    return not enabled() or logged_in()


def init_app(app) -> None:
    if app.config.get("AUTH_USER"):
        if not app.config.get("AUTH_PASSWORD_HASH"):
            raise RuntimeError("AUTH_USER ist gesetzt, aber AUTH_PASSWORD_HASH fehlt "
                               "(erzeugen mit: python -m webapp.auth)")
        if (app.config.get("SECRET_KEY") or DEFAULT_SECRET) == DEFAULT_SECRET:
            raise RuntimeError("Mit Login muss SECRET_KEY gesetzt sein (nicht „change-me“), "
                               "z. B.: python -c 'import secrets; print(secrets.token_hex(32))'")
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")       # no form posts from other sites
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.permanent_session_lifetime = timedelta(days=30)
    app.register_blueprint(auth)
    app.before_request(_guard)
    app.context_processor(lambda: {"auth_enabled": enabled(), "auth_user": session.get("user") if enabled() else None,
                                   "may_change": may_change()})


def _guard():
    if not enabled() or request.endpoint in OPEN_ENDPOINTS or logged_in():
        return None
    if public_read() and request.method in ("GET", "HEAD"):
        return None
    if request.method not in ("GET", "HEAD"):
        flash("Zum Ändern bitte anmelden.", "warning")
        back = request.referrer if _same_site(request.referrer) else request.full_path.rstrip("?")
    else:
        back = request.full_path.rstrip("?")
    target = url_for("auth.login", next=back)
    if request.headers.get("HX-Request"):
        return "", 401, {"HX-Redirect": target}
    if request.accept_mimetypes.best == "application/json" or request.endpoint == "main.status_version":
        return {"error": "login required"}, 401
    return redirect(target)


def _same_site(url: str | None) -> bool:
    """Only redirect to paths of this site after the login (no open redirect)."""
    if not url:
        return False
    parts = urlsplit(url)
    return (not parts.netloc or parts.netloc == request.host) and not url.startswith("//")


def _local(url: str | None) -> str:
    if not _same_site(url):
        return url_for("main.index")
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "") if parts.netloc else url


@auth.route("/login", methods=["GET", "POST"])
def login():
    nxt = request.values.get("next")
    if not enabled():
        return redirect(_local(nxt))
    if request.method == "POST":
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        ok_user = hmac.compare_digest(user.strip(), current_app.config["AUTH_USER"])
        ok_pw = check_password_hash(current_app.config["AUTH_PASSWORD_HASH"], pw)
        if ok_user and ok_pw:
            session.clear()
            session["user"] = current_app.config["AUTH_USER"]
            session.permanent = bool(request.form.get("remember"))
            return redirect(_local(nxt))
        time.sleep(1)                                     # slows down guessing
        flash("Benutzer oder Passwort falsch.", "danger")
    return render_template("login.html", next=nxt)


@auth.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login") if not public_read() else url_for("main.index"))


def main() -> None:
    """Print a password hash for AUTH_PASSWORD_HASH (asks for the password)."""
    pw = getpass.getpass("Passwort: ")
    if pw != getpass.getpass("Wiederholen: "):
        sys.exit("Die Passwörter stimmen nicht überein.")
    if len(pw) < 8:
        sys.exit("Bitte mindestens 8 Zeichen.")
    h = generate_password_hash(pw)
    print("In .env eintragen (einfache Anführungszeichen behalten, das $ darf nicht ersetzt werden):")
    print(f"FLASK_AUTH_PASSWORD_HASH='{h}'")


if __name__ == "__main__":
    main()
