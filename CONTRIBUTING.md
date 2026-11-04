# Contributing

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Setup

```bash
git clone https://github.com/dice-group/dice-wiki-chat.git
cd dice-wiki-chat
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
pip install pre-commit && pre-commit install   # blocks commits that contain secrets
```

## Before a pull request

```bash
pytest -q
ruff check .
```

- Tests use a fake wiki in `tests/conftest.py`. Never add real wiki content or real credentials to tests.
- Keep the UI free of external resources (no CDN, no web fonts). The Content Security Policy blocks them anyway.
- New settings go in `config.py`, `.env.example` and the settings table in the README.

## Ideas we would like help with

- Support for other wiki engines (Confluence, DokuWiki, Wiki.js)
- An evaluation set and a script to measure answer quality
- Answer feedback (thumbs up/down) stored locally
