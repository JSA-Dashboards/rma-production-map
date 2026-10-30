"""The Draw Territory password gate: locked without a password, wrong password refused, right one lets in, the portal's pre-set key skips it, a pause after
five misses. Run:  python -m pytest test_draw_auth.py   (or python test_draw_auth.py)"""
import os
import sys

from streamlit.testing.v1 import AppTest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = f'''
import sys
sys.path.insert(0, {HERE!r})
import streamlit as st
import draw_auth
if draw_auth.require_password():
    st.write("INSIDE")
'''


def run(env_pw=None, secret_pw="", session=None, typed=None):
    """One run of the gate. The password comes from st.secrets (secret_pw; "" = none, whatever the machine's own secrets file says) or the environment (env_pw)."""
    os.environ.pop("DRAW_PASSWORD", None)
    if env_pw:
        os.environ["DRAW_PASSWORD"] = env_pw
    at = AppTest.from_string(SCRIPT, default_timeout=30)
    at.secrets["DRAW_PASSWORD"] = secret_pw
    for k, v in (session or {}).items():
        at.session_state[k] = v
    at.run()
    if typed is not None:
        at.text_input[0].set_value(typed)
        at.button[0].click()
        at.run()
    return at


def inside(at):
    return any(m.value == "INSIDE" for m in at.markdown)


def test_locked_without_a_password():
    at = run()
    assert not inside(at) and any("locked" in w.value for w in at.warning)


def test_wrong_password_refused():
    at = run(env_pw="right-one", typed="wrong")
    assert not inside(at) and any("Incorrect" in e.value for e in at.error)


def test_right_password_lets_in_from_the_environment():
    assert inside(run(env_pw="right-one", typed="right-one"))


def test_right_password_lets_in_from_secrets():
    assert inside(run(secret_pw="from-secrets", typed="from-secrets"))
    assert not inside(run(secret_pw="from-secrets", typed="right-one"))


def test_portal_preauth_skips_the_gate():
    assert inside(run(env_pw="right-one", session={"_authed": True}))
    assert inside(run(session={"_authed": True}))                  # even before a password is set


def test_pause_after_five_misses():
    at = run(env_pw="right-one", session={"_draw_tries": 4}, typed="wrong")
    assert not inside(at)
    at.run()
    assert any("Too many" in e.value for e in at.error)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    sys.exit(0)
