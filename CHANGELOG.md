# Changelog

## 0.2.0

- New WikiChat logo, favicon and wordmark; cleaner, lighter UI with system fonts (no Google Fonts)
- Branding settings: organization name, website link, logo, accent color, welcome text, example questions
- Works with any MediaWiki: no DICE defaults left in the code
- Passwords, keys and tokens in wiki text are hidden before indexing; pages can be excluded by title or category
- Optional web UI password, rate limit, request size limits, strict security headers, no API docs page
- `wikichat serve` refuses to open the UI to the network without a password
- Secret values are masked in logs; error details no longer reach the browser
- New commands: `wikichat doctor`, `wikichat set-password`
- Docker image runs as a normal user; CI adds secret scanning, linting and dependency audit
- LICENSE, SECURITY.md, CONTRIBUTING.md, full README

## 0.1.0

- First version: crawler, BM25 and embedding search, streaming chat UI, keychain login
