"""
Password gate for the RMA dashboard (the whole app, called at the top of main()) and the Draw Territory tab (the same login, so one entry covers both):
one shared password, kept out of the code.

The password is read from APP_PASSWORD, or DRAW_PASSWORD if that is not set (the name the Draw Territory gate started with), first from Streamlit secrets
(Streamlit Cloud: Settings > Secrets; locally .streamlit/secrets.toml, which is gitignored), then from the environment (the droplet: /opt/jpsi/rma-county-map/.env).
With no password configured the page stays LOCKED, never open.

Inside the JSA admin portal the sync sets st.session_state["_authed"] (preauth in sync/apps.json), so the portal's own login is the only gate there,
the same way the Risk Analyzer's is.

What it does and does not do: it keeps visitors out of the page. It is not per-user security, the session is the only memory (closing the tab means entering
it again), and it does not hide the repository: this repo and the portal's mirror of it are public, so the model code and the data files stay readable on
GitHub.
"""
import hmac
import os
import time

import streamlit as st

MAX_TRIES = 5            # wrong passwords in a row before a pause
PAUSE_SECONDS = 60


NAMES = ("APP_PASSWORD", "DRAW_PASSWORD")             # first one set wins


def configured_password() -> str:
    for name in NAMES:
        try:
            v = st.secrets.get(name, "")
        except Exception:                                    # no secrets file at all
            v = ""
        v = str(v or os.environ.get(name, "") or "").strip()
        if v:
            return v
    return ""


def require_password(title: str = "Draw Territory") -> bool:
    """True when the visitor may see the page. Otherwise draws the password form (or the locked notice) and returns False; the caller returns."""
    if st.session_state.get("_authed"):
        return True
    expected = configured_password()
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown(f"##### {title}")
        if not expected:
            st.warning("This page is locked: no password has been set for it (APP_PASSWORD or DRAW_PASSWORD).")
            return False
        st.caption("Enter the password to continue.")
        wait = st.session_state.get("_draw_locked_until", 0.0) - time.time()
        if wait > 0:
            st.error(f"Too many wrong passwords. Try again in {int(wait) + 1} seconds.")
            return False
        with st.form("draw_login"):                          # a form, so Enter submits as well as the button
            pwd = st.text_input("Password", type="password", key="draw_password_input")
            entered = st.form_submit_button("Enter", type="primary")
        if entered:
            if hmac.compare_digest(pwd.encode(), expected.encode()):
                st.session_state["_authed"] = True
                st.session_state["_draw_tries"] = 0
                st.rerun()
            tries = st.session_state.get("_draw_tries", 0) + 1
            st.session_state["_draw_tries"] = tries
            if tries >= MAX_TRIES:
                st.session_state["_draw_locked_until"] = time.time() + PAUSE_SECONDS
                st.session_state["_draw_tries"] = 0
            time.sleep(1.0)                                  # slows guessing a little
            st.error("Incorrect password.")
    return False
