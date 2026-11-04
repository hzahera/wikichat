# Security policy

## Reporting a problem

Please do **not** open a public issue for security problems.
Use GitHub's private reporting instead: **Security** tab, then **Report a vulnerability**.
We aim to answer within 7 days.

Please include what you found, how to reproduce it, and which version you used.

## What counts

- A way to read wiki content without the UI password, or from outside `127.0.0.1` by default
- Secrets (wiki login, API key, UI password) showing up in logs, the browser, or files outside the keychain
- Passwords or keys from the wiki showing up in answers even though `WIKICHAT_REDACT_SECRETS=true`
- Script injection (XSS) through wiki content or model answers

## If you run WikiChat

- Run `wikichat doctor` after every setup change.
- Use a bot password with **read rights only**, never your personal wiki password.
- Prefer a local model (Ollama) or a server inside your network for internal wikis.
- If a secret was ever committed to git, change it. Removing the commit is not enough.
- Redaction is a safety net, not a guarantee. Keep real secrets out of the wiki, or exclude those pages.
