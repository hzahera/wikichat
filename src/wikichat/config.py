"""Settings, read from environment variables or a .env file (prefix WIKICHAT_).

Every option is documented in .env.example and in the README.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Settings that hold secrets. They can also live in the OS keychain (see `wikichat login`).
SECRET_FIELDS = ("wiki_username", "wiki_password", "wiki_cookie", "llm_api_key", "access_password")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WIKICHAT_", extra="ignore")

    # --- Wiki ---------------------------------------------------------------
    # Any page of the wiki, for example https://wiki.example.org/Main_Page
    wiki_url: str = ""
    # Leave empty to auto-discover api.php from the wiki_url page.
    wiki_api_url: str = ""
    # Page links shown to users. {title} is replaced with the page title.
    # Empty = use the link the wiki API returns.
    wiki_page_url: str = ""
    # Bot password login (Special:BotPasswords), e.g. "yourname@wikichat".
    wiki_username: str = ""
    wiki_password: str = ""
    # Alternative for SSO wikis: the Cookie header from a logged-in browser.
    wiki_cookie: str = ""
    # Comma-separated namespace ids. 0 = main, 4 = project, 14 = categories.
    namespaces: str = "0"
    request_delay: float = 0.3
    user_agent: str = "wikichat/0.2 (MediaWiki question answering bot)"

    # --- Privacy: what never reaches the index --------------------------------
    # Hide passwords, API keys, tokens and private keys found in wiki text.
    redact_secrets: bool = True
    # Regular expression on page titles to skip, e.g. "^(Passwords|Admin/.*)$"
    exclude_titles: str = ""
    # Comma-separated category names to skip, e.g. "Intern,Vertraulich"
    exclude_categories: str = ""

    # --- Storage & index ------------------------------------------------------
    data_dir: Path = Path("data")
    chunk_size: int = 900
    chunk_overlap: int = 150
    use_embeddings: bool = False
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    top_k: int = 6

    # --- LLM ------------------------------------------------------------------
    # openai (any OpenAI-compatible API: OpenAI, Ollama, vLLM, ...), anthropic, none
    llm_provider: str = "none"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1024
    # Extra JSON fields for every request to an OpenAI-compatible server, e.g. {"think": false}
    llm_extra_body: str = ""

    # --- Server & security ----------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    # Password for the web UI (HTTP Basic auth). Empty = no password.
    access_password: str = ""
    # Allow listening on a public address without a password (only behind a proxy with its own login).
    allow_open: bool = False
    # Chat and search requests per minute per visitor. 0 = no limit.
    rate_limit_per_minute: int = 20

    # --- Branding ---------------------------------------------------------------
    bot_name: str = "Wiki Assistant"
    org_name: str = ""                 # e.g. "DICE"
    org_url: str = ""                  # e.g. "https://dice-research.org"
    org_logo_file: str = ""            # local .svg/.png/.jpg/.webp file, e.g. "branding/org-logo.svg"
    accent_color: str = "#14b8a6"
    welcome_title: str = "Hi, what would you like to know?"
    welcome_text: str = "I search every page of the wiki and answer with links to the sources."
    # Example questions on the start screen, separated by "|".
    suggestions: str = ""

    def secret(self, name: str) -> str:
        """Environment / .env value first, then the OS keychain (see `wikichat login`)."""
        value = getattr(self, name, "") or ""
        if value:
            return value
        from . import vault

        return vault.get(name)

    def secret_values(self) -> list[str]:
        """All secret values in use, for masking them in logs."""
        out = []
        for name in SECRET_FIELDS:
            v = self.secret(name)
            if name == "wiki_cookie" and v:
                out += [p.split("=", 1)[1] for p in v.split(";") if "=" in p]
            elif v:
                out.append(v)
        return [v.strip() for v in out if len(v.strip()) >= 6]

    @property
    def namespace_ids(self) -> list[int]:
        return [int(n) for n in self.namespaces.split(",") if n.strip()]

    @property
    def suggestion_list(self) -> list[str]:
        return [s.strip() for s in self.suggestions.split("|") if s.strip()][:6]

    @property
    def excluded_categories(self) -> set[str]:
        return {c.strip().lower() for c in self.exclude_categories.split(",") if c.strip()}

    @property
    def pages_file(self) -> Path:
        return self.data_dir / "pages.jsonl"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"


@lru_cache
def get_settings() -> Settings:
    return Settings()
