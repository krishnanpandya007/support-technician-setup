"""Secret intake and redaction. Database URLs are read from the environment or via a
no-echo prompt, held in memory for the run only (never written to disk or cache), and
registered with a redactor so they can never appear in printed output or error messages.
"""
from __future__ import annotations

import os
from getpass import getpass
from urllib.parse import urlsplit


class Redactor:
    """Masks any registered secret substring (and the password component of a URL)
    everywhere it might otherwise be printed."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, value: str | None) -> None:
        if not value:
            return
        self._secrets.add(value)
        try:
            pw = urlsplit(value).password
        except ValueError:
            pw = None
        if pw:
            self._secrets.add(pw)

    def mask(self, text: object) -> str:
        out = str(text)
        for secret in sorted(self._secrets, key=len, reverse=True):
            if secret:
                out = out.replace(secret, "***")
        return out


def read_url(env_var: str, label: str, redactor: Redactor, *, env: dict | None = None) -> str:
    """Read a database URL from ``env_var`` or, if absent, a hidden prompt. Registers it
    with the redactor before returning so it cannot leak into logs."""
    environ = env if env is not None else os.environ
    url = (environ.get(env_var) or "").strip()
    if not url:
        url = getpass(f"{label} (input hidden, not stored): ").strip()
    if not url:
        raise SystemExit(f"No URL provided for {label} (set {env_var} or enter it when prompted).")
    # Strip accidental surrounding quotes (a common mistake when setting the env var on
    # Windows, e.g. `set VAR="postgresql://..."`, which would break libpq's URL parsing).
    if len(url) >= 2 and url[0] == url[-1] and url[0] in "\"'":
        url = url[1:-1].strip()
    # Validate the shape before psycopg sees it, so a malformed value gives a clear hint
    # instead of a cryptic, redacted libpq parse error. (The message never echoes the URL.)
    if not (url.startswith(("postgresql://", "postgres://")) or "=" in url):
        raise SystemExit(
            f"{label} is not a valid Postgres connection string. Expected a URL like "
            "postgresql://user:password@host:5432/dbname (no surrounding quotes)."
        )
    redactor.register(url)
    return url
