import json

from fastapi.testclient import TestClient

from wikichat.chunker import chunk_page, clean_wikitext, split_sections
from wikichat.crawler import WikiClient, crawl, load_pages
from wikichat.index import SearchIndex, tokenize
from wikichat.server import create_app, retrieval_query

from .conftest import PAGES


# ------------------------------------------------------------------ chunker
def test_clean_wikitext_keeps_text_and_drops_markup():
    out = clean_wikitext("A '''bold''' [[Target|label]] <!-- x --> [[Kategorie:Foo]] {{Box|Kontakt=Simone}}")
    assert "bold" in out and "label" in out
    assert "Kategorie" not in out and "<!--" not in out and "'''" not in out
    assert "Kontakt: Simone" in out


def test_split_sections_builds_paths():
    secs = split_sections("intro\n== A ==\na\n=== B ===\nb\n== C ==\nc")
    assert [s for s, _ in secs] == ["", "A", "A > B", "C"]


def test_chunk_page_has_anchors():
    chunks = chunk_page(PAGES[0])
    assert any(c.section == "Access" and c.url.endswith("#Access") for c in chunks)


def test_long_text_is_split():
    from wikichat.crawler import Page

    long = Page(9, "Long", 0, 1, "", "u", ("This is a sentence. " * 300))
    chunks = chunk_page(long, size=500, overlap=50)
    assert len(chunks) > 5 and all(len(c.text) <= 600 for c in chunks)


# -------------------------------------------------------------------- index
def test_tokenize_normalizes_umlauts():
    assert "ueber" not in tokenize("über")  # stopword
    assert tokenize("Büro") == ["buero"]


def test_search_finds_the_right_page(settings):
    idx = SearchIndex.build(settings)
    assert idx.search("How do I get GPU access?")[0].chunk.page_title == "GPU Cluster"
    assert idx.search("Reisekosten abrechnen")[0].chunk.page_title == "Dienstreise"
    assert idx.search("zzzz-nothing") == []
    loaded = SearchIndex.load(settings)
    assert len(loaded.chunks) == len(idx.chunks)


# ------------------------------------------------------------------- server
def test_chat_endpoint_streams_sources_and_tokens(settings):
    SearchIndex.build(settings)
    client = TestClient(create_app(settings))
    status = client.get("/api/status").json()
    assert status["pages"] == 3  # the confidential page is excluded

    r = client.post("/api/chat", json={"message": "paper templates", "history": []})
    events = [b for b in r.text.split("\n\n") if b]
    kinds = [e.split("\n")[0].removeprefix("event: ") for e in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and "token" in kinds
    sources = json.loads(events[0].split("data: ", 1)[1])
    assert sources[0]["title"] == "Onboarding"
    assert client.get("/").status_code == 200


def test_followup_borrows_previous_question():
    q = retrieval_query("and the rules?", [{"role": "user", "content": "GPU cluster access"}])
    assert "GPU" in q


# ------------------------------------------------------------------ crawler
class FakeClient(WikiClient):
    """Answers API calls from memory instead of the network."""

    def __init__(self, settings, pages):
        super().__init__(settings)
        self._api = "http://fake/api.php"
        self.pages = {p.pageid: p for p in pages}
        self.content_calls = 0

    def get(self, params):
        if params.get("list") == "allpages":
            return {"query": {"allpages": [{"pageid": p.pageid, "title": p.title} for p in self.pages.values()]}}
        ids = [int(x) for x in str(params["pageids"]).split("|")]
        out = []
        for i in ids:
            p = self.pages[i]
            item = {"pageid": i, "title": p.title, "ns": 0, "lastrevid": p.revid, "fullurl": p.url}
            if "revisions" in params.get("prop", ""):
                self.content_calls += 1
                item["revisions"] = [{"revid": p.revid, "timestamp": p.timestamp,
                                      "slots": {"main": {"content": p.wikitext}}}]
            out.append(item)
        return {"query": {"pages": out}}


def test_crawl_is_incremental(tmp_path):
    from wikichat.config import Settings

    s = Settings(_env_file=None, data_dir=tmp_path)
    first = FakeClient(s, PAGES[:3])
    assert crawl(s, client=first)["updated"] == 3
    assert load_pages(s.pages_file)[1].categories == ["Infrastruktur"]

    second = FakeClient(s, PAGES[:3])
    assert crawl(s, client=second)["updated"] == 0 and second.content_calls == 0


def test_crawl_never_stores_excluded_pages(tmp_path):
    from wikichat.config import Settings

    s = Settings(_env_file=None, data_dir=tmp_path, exclude_categories="vertraulich", exclude_titles="^Dienst")
    stats = crawl(s, client=FakeClient(s, PAGES))
    titles = {p.title for p in load_pages(s.pages_file).values()}
    assert titles == {"GPU Cluster", "Onboarding"} and stats["excluded"] == 1
    assert (tmp_path.stat().st_mode & 0o077) == 0  # data folder is private


# ------------------------------------------------------------------ secrets
def test_secrets_come_from_keychain_when_env_is_empty(monkeypatch):
    from wikichat import vault
    from wikichat.config import Settings

    fake = {"wiki_password": "from-keychain"}
    monkeypatch.setattr(vault, "get", lambda name: fake.get(name, ""))
    assert Settings(_env_file=None).secret("wiki_password") == "from-keychain"
    assert Settings(_env_file=None, wiki_password="from-env").secret("wiki_password") == "from-env"
    assert Settings(_env_file=None).secret("llm_api_key") == ""


def test_clean_cookie():
    import pytest

    from wikichat.vault import clean_cookie, cookie_names

    c = clean_cookie("Cookie: wiki_session=abc; wikiUserName=Hamada;\n wikiUserID=7")
    assert c == "wiki_session=abc; wikiUserName=Hamada; wikiUserID=7"
    assert cookie_names(c) == ["wiki_session", "wikiUserName", "wikiUserID"]
    with pytest.raises(ValueError):
        clean_cookie('python -c "import keyring"\nwikichat check')
