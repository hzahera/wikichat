import httpx

from wikichat.chunker import Chunk
from wikichat.index import Hit
from wikichat.llm import LLM


def test_extra_body_is_sent_to_openai_compatible_server(settings, monkeypatch):
    sent = {}

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"Hi [1]"}}]}'
            yield "data: [DONE]"

    def fake_stream(method, url, headers, json, timeout):
        sent.update(json)
        return FakeStream()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    s = settings.model_copy(update={"llm_provider": "openai", "llm_extra_body": '{"think": false}'})
    hit = Hit(Chunk("1", "Page", "", "https://wiki.example/Page", "text"), 1.0)
    assert "".join(LLM(s).stream("q", [hit])) == "Hi [1]"
    assert sent["think"] is False and sent["stream"] is True
