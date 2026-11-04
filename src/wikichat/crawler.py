"""Crawl every page of a MediaWiki through its API (api.php).

Steps:
  1. list=allpages for each namespace (no redirects)
  2. prop=info in batches of 50 to get the latest revision id
  3. fetch wikitext only for new or changed pages (incremental crawls)
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import quote, urljoin

import requests

from .config import Settings
from .redact import is_excluded
from .security import ensure_private_dir

log = logging.getLogger(__name__)
BATCH = 50
CATEGORY = re.compile(r"\[\[\s*(?:Kategorie|Category|Catégorie|Categoría|Categoria)\s*:\s*([^\]|]+)", re.I)


@dataclass
class Page:
    pageid: int
    title: str
    ns: int
    revid: int
    timestamp: str
    url: str
    wikitext: str
    categories: list[str] = field(default_factory=list)


class WikiError(RuntimeError):
    pass


class WikiClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.s = settings
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent
        self.has_cookie = False
        cookie = settings.secret("wiki_cookie")
        if cookie:
            from .vault import clean_cookie

            try:
                self.session.headers["Cookie"] = clean_cookie(cookie)
                self.has_cookie = True
            except ValueError as e:
                raise WikiError(f"The saved cookie is not valid ({e}). Run `wikichat login --clipboard`.") from e
        self._api = settings.wiki_api_url or None

    # ------------------------------------------------------------------ setup
    @property
    def api(self) -> str:
        if not self._api:
            self._api = self.discover_api()
        return self._api

    def discover_api(self) -> str:
        """Find api.php through the <link rel="EditURI"> tag every MediaWiki page has."""
        if not self.s.wiki_url:
            raise WikiError("Set WIKICHAT_WIKI_URL in .env to any page of your wiki.")
        r = self.session.get(self.s.wiki_url, timeout=30)
        r.raise_for_status()
        for tag in re.findall(r"<link\b[^>]*>", r.text, flags=re.I):
            if re.search(r'rel=["\']EditURI["\']', tag, flags=re.I):
                href = re.search(r'href=["\']([^"\']+)["\']', tag)
                if href:
                    api = urljoin(r.url, href.group(1).replace("&amp;", "&")).split("?")[0]
                    log.info("Discovered API endpoint: %s", api)
                    return api
        # Common fallbacks
        base = self.s.wiki_url.rsplit("/", 1)[0]
        for guess in (f"{base}/api.php", f"{base}/w/api.php"):
            try:
                if self._raw({"action": "query", "meta": "siteinfo"}, url=guess).get("query"):
                    return guess
            except Exception:  # noqa: BLE001
                continue
        raise WikiError(
            "Could not find api.php. Set WIKICHAT_WIKI_API_URL in .env "
            "(if the wiki needs a login, also run `wikichat login`)."
        )

    # ---------------------------------------------------------------- requests
    def _raw(self, params: dict, url: str | None = None, method: str = "GET") -> dict:
        params = {"format": "json", "formatversion": "2", **params}
        for attempt in range(5):
            try:
                if method == "POST":
                    r = self.session.post(url or self.api, data=params, timeout=60)
                else:
                    r = self.session.get(url or self.api, params=params, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                try:
                    data = r.json()
                except ValueError as e:
                    raise WikiError(
                        "The API did not return JSON. Most likely you need to log in "
                        "(bot password or cookie) or the API URL is wrong."
                    ) from e
                if "error" in data:
                    code = data["error"].get("code")
                    if code == "maxlag":
                        raise requests.HTTPError("maxlag")
                    hint = ""
                    if code == "readapidenied":
                        hint = ("\nThe wiki sees you as a guest. The cookie or bot password was not accepted. "
                                "Copy a fresh cookie and run `wikichat login --clipboard`, or use a bot password.")
                    raise WikiError(f"API error {code}: {data['error'].get('info')}{hint}")
                return data
            except requests.RequestException as e:
                wait = 2 ** attempt
                log.warning("Request failed (%s), retrying in %ss", e, wait)
                time.sleep(wait)
        raise WikiError("API kept failing after 5 attempts")

    def get(self, params: dict) -> dict:
        time.sleep(self.s.request_delay)
        return self._raw(params)

    def query_all(self, params: dict) -> Iterator[dict]:
        """Run an action=query request and follow 'continue' until done."""
        cont: dict = {}
        while True:
            data = self.get({"action": "query", **params, **cont})
            yield data.get("query", {})
            if "continue" not in data:
                return
            cont = data["continue"]

    # ------------------------------------------------------------------ login
    def login(self) -> None:
        user, password = self.s.secret("wiki_username"), self.s.secret("wiki_password")
        if self.has_cookie or not (user and password):
            return
        token = self.get({"action": "query", "meta": "tokens", "type": "login"})["query"]["tokens"][
            "logintoken"
        ]
        res = self._raw(
            {"action": "login", "lgname": user, "lgpassword": password, "lgtoken": token},
            method="POST",
        )
        if res.get("login", {}).get("result") != "Success":
            hint = ""
            if "@" not in user:
                hint = (
                    "\nThe user name has no '@'. The API only accepts bot passwords: create one under "
                    "Special:BotPasswords and log in as 'YourWikiName@BotName' with the generated password. "
                    "Your normal wiki password does not work here."
                )
            raise WikiError(f"Login failed: {res.get('login', {}).get('reason', 'unknown reason')}{hint}")
        log.info("Logged in as %s", user)

    def siteinfo(self) -> dict:
        return self.get(
            {"action": "query", "meta": "siteinfo", "siprop": "general|namespaces|statistics"}
        )["query"]

    # ---------------------------------------------------------------- listing
    def list_pages(self, namespaces: Iterable[int]) -> list[dict]:
        pages: list[dict] = []
        for ns in namespaces:
            for q in self.query_all(
                {"list": "allpages", "apnamespace": ns, "aplimit": "max", "apfilterredir": "nonredirects"}
            ):
                pages.extend(q.get("allpages", []))
        log.info("Found %d pages", len(pages))
        return pages

    def page_infos(self, pageids: list[int]) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for i in range(0, len(pageids), BATCH):
            ids = "|".join(map(str, pageids[i : i + BATCH]))
            q = self.get({"action": "query", "pageids": ids, "prop": "info", "inprop": "url"})["query"]
            for p in q.get("pages", []):
                if not p.get("missing"):
                    out[p["pageid"]] = p
        return out

    def _content_query(self, pageids: str) -> dict:
        return self.get(
            {
                "action": "query",
                "pageids": pageids,
                "prop": "revisions|info",
                "rvprop": "ids|timestamp|content",
                "rvslots": "main",
                "inprop": "url",
            }
        )["query"]

    def fetch_contents(self, pageids: list[int]) -> Iterator[dict]:
        for i in range(0, len(pageids), BATCH):
            batch = pageids[i : i + BATCH]
            q = self._content_query("|".join(map(str, batch)))
            got = set()
            for p in q.get("pages", []):
                if p.get("revisions") and "content" in p["revisions"][0].get("slots", {}).get("main", {}):
                    got.add(p["pageid"])
                    yield p
            # The API may skip content when a batch is too large; fetch those one by one.
            for pid in batch:
                if pid not in got:
                    for p in self._content_query(str(pid)).get("pages", []):
                        if p.get("revisions"):
                            yield p

    # ------------------------------------------------------------------ build
    def page_url(self, title: str) -> str:
        pattern = self.s.wiki_page_url or (self.s.wiki_url.rsplit("/", 1)[0] + "/{title}")
        return pattern.format(title=quote(title.replace(" ", "_")))

    def to_page(self, p: dict) -> Page:
        rev = p["revisions"][0]
        text = rev["slots"]["main"].get("content", "")
        cats = CATEGORY.findall(text)
        url = p.get("fullurl") or self.page_url(p["title"])
        return Page(
            pageid=p["pageid"],
            title=p["title"],
            ns=p.get("ns", 0),
            revid=rev["revid"],
            timestamp=rev.get("timestamp", ""),
            url=url,
            wikitext=text,
            categories=[c.strip() for c in cats],
        )


def load_pages(path: Path) -> dict[int, Page]:
    if not path.exists():
        return {}
    pages = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                pages[d["pageid"]] = Page(**d)
    return pages


def save_pages(path: Path, pages: Iterable[Page]) -> None:
    ensure_private_dir(path.parent)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
    tmp.replace(path)


def crawl(settings: Settings, full: bool = False, client: WikiClient | None = None) -> dict:
    """Crawl the wiki and write data/pages.jsonl. Returns simple stats."""
    client = client or WikiClient(settings)
    client.login()
    old = {} if full else load_pages(settings.pages_file)

    listed = client.list_pages(settings.namespace_ids)
    listed = [p for p in listed if not is_excluded(p.get("title", ""), [], settings)]
    infos = client.page_infos([p["pageid"] for p in listed])

    changed = [pid for pid, info in infos.items() if pid not in old or old[pid].revid != info.get("lastrevid")]
    log.info("%d new or changed pages, %d unchanged", len(changed), len(infos) - len(changed))

    result = {pid: old[pid] for pid in infos if pid in old}
    skipped = 0
    for i, p in enumerate(client.fetch_contents(changed), 1):
        page = client.to_page(p)
        if is_excluded(page.title, page.categories, settings):  # excluded by category: never stored
            result.pop(page.pageid, None)
            skipped += 1
            continue
        result[page.pageid] = page
        if i % 50 == 0:
            log.info("Fetched %d/%d", i, len(changed))

    removed = len(set(old) - set(result))
    save_pages(settings.pages_file, sorted(result.values(), key=lambda x: x.title))
    return {"pages": len(result), "updated": len(changed) - skipped, "removed": removed, "excluded": skipped}
