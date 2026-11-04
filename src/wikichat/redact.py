"""Keep secrets and excluded pages out of the search index.

Internal wikis often hold Wi-Fi passwords, door codes, API keys or server logins.
The chatbot should never repeat them, so they are removed before indexing:
the model cannot leak what it never sees.
"""
from __future__ import annotations

import re

from .config import Settings

HIDDEN = "[hidden]"

# "Password: abc", "Passwort = abc", "| Kennwort || abc", "the PIN is 1234"
_LABELS = (
    r"pass(?:word|wort|wd|phrase)?s?|kennwort|pwd|pin|secret|geheimnis|token|"
    r"api[ _-]?key|access[ _-]?key|zugangs?code|access[ _-]?code|t(?:ü|ue)rcode|door[ _-]?code"
)
KEY_VALUE = re.compile(
    rf"(?i)\b({_LABELS})\b(\s*(?:[:=]|\|{{1,2}})\s*|\s+(?:is|ist|lautet)\s+)([^\s|<>]{{3,}})"
)
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")
TOKENS = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{20,}"          # OpenAI / Anthropic style keys
    r"|gh[pousr]_[A-Za-z0-9]{30,}"         # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"           # GitLab
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"       # Slack
    r"|AKIA[0-9A-Z]{16}"                   # AWS access key id
    r"|AIza[0-9A-Za-z_-]{35}"              # Google API key
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"  # JWT
)


def redact(text: str) -> tuple[str, int]:
    """Return (clean_text, number_of_hidden_values)."""
    count = 0

    def sub(pattern: re.Pattern, repl, s: str) -> str:
        nonlocal count
        s, n = pattern.subn(repl, s)
        count += n
        return s

    text = sub(PRIVATE_KEY, HIDDEN, text)
    text = sub(URL_CREDENTIALS, lambda m: f"{m.group(1)}{HIDDEN}@", text)
    text = sub(TOKENS, HIDDEN, text)
    text = sub(KEY_VALUE, lambda m: f"{m.group(1)}{m.group(2)}{HIDDEN}" if m.group(3) != HIDDEN else m.group(0), text)
    return text, count


def is_excluded(title: str, categories: list[str], settings: Settings) -> bool:
    """True when a page must not be indexed (title pattern or category)."""
    if settings.exclude_titles and re.search(settings.exclude_titles, title, flags=re.I):
        return True
    banned = settings.excluded_categories
    return bool(banned and any(c.strip().lower() in banned for c in categories))
