"""Command line: wikichat check | login | crawl | index | search | ask | serve | doctor."""
from __future__ import annotations

import logging
import os
import stat
import subprocess
from pathlib import Path

import typer

from .config import get_settings

app = typer.Typer(add_completion=False, help="Chat with any MediaWiki.", no_args_is_help=True)


def _setup_logging(verbose: bool) -> None:
    from .security import install_secret_filter

    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    install_secret_filter(get_settings().secret_values())


@app.command()
def check(verbose: bool = False):
    """Test the connection and login to the wiki."""
    _setup_logging(verbose)
    from . import vault
    from .crawler import WikiClient

    s = get_settings()
    c = WikiClient(s)
    where = "from .env" if (s.wiki_password or s.wiki_cookie) else (
        "from keychain" if vault.stored() else "none (anonymous)")
    typer.echo(f"Login: {where}")
    typer.echo(f"API: {c.api}")
    c.login()
    info = c.siteinfo()
    g, st = info.get("general", {}), info.get("statistics", {})
    typer.echo(f"Wiki: {g.get('sitename')}  ({g.get('generator')})")
    typer.echo(f"Content pages: {st.get('articles')}  All pages: {st.get('pages')}")
    ns = {v["id"]: v.get("name") or "(main)" for v in info.get("namespaces", {}).values()} \
        if isinstance(info.get("namespaces"), dict) else {}
    if ns:
        typer.echo("Namespaces: " + ", ".join(f"{k}={v}" for k, v in sorted(ns.items()) if k >= 0))


@app.command()
def login(method: str = typer.Option(None, help="bot or cookie"),
          clipboard: bool = typer.Option(False, "--clipboard",
                                         help="Read the cookie from the clipboard (copy it in the browser first)."),
          test: bool = True):
    """Save your wiki login in the system keychain (typing is hidden)."""
    from . import vault

    if clipboard:
        try:
            cookie = vault.clean_cookie(vault.read_clipboard())
        except Exception as e:  # noqa: BLE001
            typer.secho(f"Clipboard: {e}\nCopy the Cookie value in the browser, then run this command again "
                        "(press the Up arrow to rerun it, so the clipboard is not replaced).", fg="red")
            raise typer.Exit(1) from None
        vault.set("wiki_cookie", cookie)
        vault.delete("wiki_username")
        vault.delete("wiki_password")
        vault.clear_clipboard()
        names = vault.cookie_names(cookie)
        typer.secho(f"Saved cookie in the system keychain ({len(names)} parts: {', '.join(names)}). "
                    "Clipboard cleared.", fg="green")
        if test:
            get_settings.cache_clear()
            check()
        return

    if method not in ("bot", "cookie"):
        typer.echo("How do you log in to the wiki?\n  1) Bot password (Special:BotPasswords)\n  2) Browser cookie (single sign-on)")
        method = "cookie" if typer.prompt("Choose 1 or 2", default="1").strip() == "2" else "bot"

    if method == "bot":
        user = typer.prompt("Bot user name (like yourname@wikichat)")
        password = typer.prompt("Bot password", hide_input=True)
        vault.set("wiki_username", user.strip())
        vault.set("wiki_password", password.strip())
        vault.delete("wiki_cookie")
    else:
        typer.echo("Tip: long cookies may not paste here. Use `wikichat login --clipboard` instead.")
        try:
            cookie = vault.clean_cookie(typer.prompt("Paste the Cookie header", hide_input=True))
        except ValueError as e:
            typer.secho(str(e), fg="red")
            raise typer.Exit(1) from None
        vault.set("wiki_cookie", cookie)
        vault.delete("wiki_username")
        vault.delete("wiki_password")
    typer.secho("Saved in the system keychain.", fg="green")
    if test:
        get_settings.cache_clear()
        check()


@app.command("set-key")
def set_key():
    """Save the LLM API key in the system keychain (typing is hidden)."""
    from . import vault

    vault.set("llm_api_key", typer.prompt("LLM API key", hide_input=True).strip())
    typer.secho("Saved in the system keychain.", fg="green")


@app.command("set-password")
def set_password():
    """Protect the web UI with a password (saved in the system keychain)."""
    from . import vault

    pw = typer.prompt("New web UI password (12+ characters)", hide_input=True, confirmation_prompt=True).strip()
    if len(pw) < 12:
        typer.secho("Too short. Use at least 12 characters.", fg="red")
        raise typer.Exit(1)
    vault.set("access_password", pw)
    typer.secho("Saved. The browser will ask for it (any user name works).", fg="green")


@app.command()
def logout():
    """Remove every saved secret from the system keychain."""
    from . import vault

    removed = [n for n in vault.NAMES if vault.delete(n)]
    typer.echo(f"Removed: {', '.join(removed)}" if removed else "Nothing was saved.")


@app.command()
def crawl(full: bool = typer.Option(False, help="Re-download every page."),
          verbose: bool = False, index: bool = typer.Option(True, help="Rebuild the index afterwards.")):
    """Download all wiki pages (only changed pages, unless --full)."""
    _setup_logging(verbose)
    from .crawler import crawl as run

    stats = run(get_settings(), full=full)
    typer.echo(f"Pages: {stats['pages']}  updated: {stats['updated']}  removed: {stats['removed']}"
               f"  excluded: {stats['excluded']}")
    if index:
        build_index(verbose)


@app.command("index")
def build_index(verbose: bool = False):
    """Build the search index from the crawled pages."""
    _setup_logging(verbose)
    from .index import SearchIndex

    idx = SearchIndex.build(get_settings())
    m = idx.meta
    typer.echo(f"Indexed {m['pages']} pages into {m['chunks']} chunks "
               f"({m['hidden_values']} secret-looking values hidden, {m['excluded_pages']} pages excluded).")


@app.command()
def search(query: str, k: int = 5):
    """Show the best matching wiki passages."""
    from .index import SearchIndex

    for n, h in enumerate(SearchIndex.load(get_settings()).search(query, k=k), 1):
        typer.secho(f"[{n}] {h.chunk.header}  ({h.score:.3f})", bold=True)
        typer.echo(f"    {h.chunk.url}\n    {h.chunk.text[:200].replace(chr(10), ' ')}\n")


@app.command()
def ask(question: str):
    """Ask one question in the terminal."""
    from .index import SearchIndex
    from .llm import LLM

    s = get_settings()
    hits = SearchIndex.load(s).search(question, k=s.top_k)
    for token in LLM(s).stream(question, hits):
        typer.echo(token, nl=False)
    typer.echo("\n\nSources:")
    for n, h in enumerate(hits, 1):
        typer.echo(f"  [{n}] {h.chunk.header}  {h.chunk.url}")


def open_bind_problem(host: str) -> str | None:
    """Why the server must not start on this address, or None when it is fine."""
    from .security import is_loopback

    s = get_settings()
    if is_loopback(host) or s.secret("access_password") or s.allow_open:
        return None
    return (f"Refusing to listen on {host} without a password: anyone on the network could read the wiki.\n"
            "Fix one of these:\n"
            "  - run `wikichat set-password` (or set WIKICHAT_ACCESS_PASSWORD on servers), or\n"
            "  - set WIKICHAT_HOST=127.0.0.1, or\n"
            "  - set WIKICHAT_ALLOW_OPEN=true if a reverse proxy already asks for a login.")


@app.command()
def serve(host: str = None, port: int = None, reload: bool = False):
    """Start the web UI."""
    import uvicorn

    s = get_settings()
    host = host or s.host
    problem = open_bind_problem(host)
    if problem:
        typer.secho(problem, fg="red")
        raise typer.Exit(2)
    uvicorn.run("wikichat.server:create_app", factory=True, host=host,
                port=port or s.port, reload=reload, server_header=False)


@app.command()
def doctor():
    """Check the setup for common security mistakes (secrets, file rights, network)."""
    from . import vault

    s = get_settings()
    problems = 0

    def ok(msg):
        typer.secho(f"  ok   {msg}", fg="green")

    def warn(msg):
        nonlocal problems
        problems += 1
        typer.secho(f"  !!   {msg}", fg="yellow")

    typer.echo("Secrets")
    env = Path(".env")
    in_env = [n for n in ("wiki_password", "wiki_cookie", "llm_api_key", "access_password") if getattr(s, n)]
    if in_env:
        warn(f"Secrets in .env or environment: {', '.join(in_env)}. On a laptop, prefer the keychain "
             "(`wikichat login`, `wikichat set-key`, `wikichat set-password`).")
    else:
        ok("No secrets in .env" + (f" (keychain has: {', '.join(vault.stored())})" if vault.stored() else ""))
    if env.exists() and os.name != "nt":
        mode = stat.S_IMODE(env.stat().st_mode)
        if mode & 0o077:
            warn(f".env can be read by other users (mode {oct(mode)}). Fix: chmod 600 .env")
        else:
            ok(".env is private (mode 600)")
    try:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", ".env"],
                                 capture_output=True, text=True).returncode == 0
        if tracked:
            warn(".env is tracked by git! Run: git rm --cached .env, then change every secret in it.")
        else:
            ok(".env is not tracked by git")
        if subprocess.run(["git", "ls-files", "--error-unmatch", str(s.data_dir)],
                          capture_output=True, text=True).returncode == 0:
            warn(f"{s.data_dir}/ (wiki content) is tracked by git! Run: git rm -r --cached {s.data_dir}")
    except FileNotFoundError:
        pass

    typer.echo("Wiki content")
    if s.data_dir.exists() and os.name != "nt" and stat.S_IMODE(s.data_dir.stat().st_mode) & 0o077:
        warn(f"{s.data_dir}/ can be read by other users. Fix: chmod -R go-rwx {s.data_dir}")
    else:
        ok(f"{s.data_dir}/ is private")
    if s.redact_secrets:
        ok("Passwords, keys and tokens in wiki text are hidden before indexing")
    else:
        warn("WIKICHAT_REDACT_SECRETS=false: passwords written in the wiki can show up in answers")

    typer.echo("Network")
    problem = open_bind_problem(s.host)
    if problem:
        warn(problem.splitlines()[0])
    elif s.secret("access_password"):
        ok("Web UI asks for a password")
    else:
        ok(f"Web UI only listens on {s.host} (this computer)")
    if s.llm_provider.lower() not in ("none", "") and "localhost" not in s.llm_base_url \
            and "127.0.0.1" not in s.llm_base_url:
        warn(f"Wiki passages are sent to an external LLM ({s.llm_base_url}). "
             "Check that this is allowed for your wiki content, or use a local model (Ollama).")
    else:
        ok("No wiki content leaves this computer for the LLM")

    typer.echo("")
    typer.secho("All good." if not problems else f"{problems} thing(s) to look at.",
                fg="green" if not problems else "yellow", bold=True)


if __name__ == "__main__":
    app()
