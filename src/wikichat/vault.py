"""Store secrets in the operating system keychain instead of a text file.

macOS: Keychain. Windows: Credential Manager. Linux: Secret Service (GNOME Keyring, KWallet).
Values set as environment variables (or in .env) still win, which is handy for servers and CI.

Set WIKICHAT_KEYRING_SERVICE to keep the secrets of several wikis apart on one machine.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)
SERVICE = os.environ.get("WIKICHAT_KEYRING_SERVICE", "wikichat")
LEGACY_SERVICES = ("dice-wiki-chat",)  # read-only fallback for logins saved by version 0.1
NAMES = ("wiki_username", "wiki_password", "wiki_cookie", "llm_api_key", "access_password")


def _keyring():
    try:
        import keyring

        return keyring
    except Exception:  # noqa: BLE001
        return None


def _services() -> list[str]:
    return [SERVICE, *(s for s in LEGACY_SERVICES if s != SERVICE)]


def get(name: str) -> str:
    kr = _keyring()
    if not kr:
        return ""
    for service in _services():
        try:
            value = kr.get_password(service, name)
        except Exception as e:  # noqa: BLE001  (no keychain available, locked, ...)
            log.debug("Keychain read failed for %s: %s", name, e)
            return ""
        if value:
            return value
    return ""


def set(name: str, value: str) -> None:  # noqa: A001
    kr = _keyring()
    if not kr:
        raise RuntimeError("The 'keyring' package is not installed. Run: pip install keyring")
    kr.set_password(SERVICE, name, value)


def delete(name: str) -> bool:
    kr = _keyring()
    if not kr:
        return False
    removed = False
    for service in _services():
        try:
            kr.delete_password(service, name)
            removed = True
        except Exception:  # noqa: BLE001  (not stored)
            pass
    return removed


def clean_cookie(raw: str) -> str:
    """Accept 'Cookie: a=1; b=2' or 'a=1; b=2', and reject anything that is not a cookie."""
    value = raw.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    value = " ".join(value.split())  # remove line breaks from copying
    parts = [p.strip() for p in value.split(";") if p.strip()]
    if not parts or not all("=" in p and " " not in p.split("=", 1)[0] for p in parts):
        raise ValueError("This does not look like a cookie (expected name=value; name2=value2).")
    return "; ".join(parts)


def cookie_names(cookie: str) -> list[str]:
    return [p.split("=", 1)[0].strip() for p in cookie.split(";") if "=" in p]


def read_clipboard() -> str:
    import platform
    import subprocess

    system = platform.system()
    if system == "Darwin":
        cmd = ["pbpaste"]
    elif system == "Windows":
        cmd = ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def clear_clipboard() -> None:
    import platform
    import subprocess

    try:
        if platform.system() == "Darwin":
            subprocess.run(["pbcopy"], input="", text=True, check=True)
        elif platform.system() == "Windows":
            subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ' '"], check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input="", text=True, check=True)
    except Exception:  # noqa: BLE001
        pass


def stored() -> list[str]:
    return [n for n in NAMES if get(n)]
