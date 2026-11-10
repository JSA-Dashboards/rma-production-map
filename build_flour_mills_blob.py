"""
Encrypt the private flour mill list (private/flour_mills_gma2027.csv, git-ignored) into data/wheat/flour_mills.enc, which is safe to commit: the app decrypts it with the MILLS_KEY secret.

    python build_flour_mills_blob.py            # key from MILLS_KEY in the environment or .streamlit/secrets.toml; with none, makes one, appends it to secrets.toml and says so

Re-run after any change to the CSV. The key is the same one the Streamlit Cloud app needs as MILLS_KEY in Settings > Secrets.
"""
import os
import re
import sys

from cryptography.fernet import Fernet

import flour_mills as fm

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, ".streamlit", "secrets.toml")


def get_key() -> str:
    k = os.environ.get("MILLS_KEY", "")
    if not k and os.path.exists(SECRETS):
        m = re.search(r'^MILLS_KEY\s*=\s*"([^"]+)"', open(SECRETS, encoding="utf-8", errors="ignore").read(), re.M)
        k = m.group(1) if m else ""
    if not k:
        k = Fernet.generate_key().decode()
        with open(SECRETS, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f'\nMILLS_KEY = "{k}"\n')
        print("made a new MILLS_KEY and appended it to .streamlit/secrets.toml (add the same value to the Cloud app's Secrets)")
    return k


def main():
    key = get_key()
    for plain, blob in ((fm.PLAIN, fm.BLOB), (fm.DURUM_PLAIN, fm.DURUM_BLOB)):
        if not os.path.exists(plain):
            print(f"{plain} is missing, skipped")
            continue
        text = open(plain, encoding="utf-8", newline="").read()
        data = fm.encrypt(text, key)
        with open(blob, "wb") as fh:
            fh.write(data)
        print(f"wrote {blob}: {len(data):,} bytes, {text.count(chr(10))} lines")


if __name__ == "__main__":
    main()
