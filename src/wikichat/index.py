"""Search index: BM25 keyword search, optionally fused with embeddings."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .chunker import Chunk, chunk_page
from .config import Settings
from .crawler import load_pages
from .redact import is_excluded, redact
from .security import ensure_private_dir

log = logging.getLogger(__name__)

STOPWORDS = set(
    """a an and are as at be by for from has have how i in is it of on or that the this to was
    what when where which who why will with you your can do does
    der die das den dem des ein eine einen einem einer und oder ist sind war wie was wer wo
    wann warum mit von zu zum zur im in an auf für fur ich du sie er es wir ihr man nicht
    auch als bei aus nach über uber kann können muss gibt""".split()
)
TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    toks = [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]
    # Cheap normalization for German umlauts so "Buero" matches "Büro".
    return [t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss") for t in toks]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class Embedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding  # optional dependency

        self.model = TextEmbedding(model_name=model)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)


class SearchIndex:
    def __init__(self, chunks: list[Chunk], meta: dict | None = None,
                 vectors: np.ndarray | None = None, embedder: Embedder | None = None):
        self.chunks = chunks
        self.meta = meta or {}
        self.vectors = vectors
        self.embedder = embedder
        # Title and section go in twice: they are strong signals on a wiki.
        docs = [tokenize(f"{c.page_title} {c.page_title} {c.section} {c.text}") for c in chunks]
        self.bm25 = BM25Okapi(docs) if docs else None

    # ----------------------------------------------------------------- build
    @classmethod
    def build(cls, settings: Settings) -> "SearchIndex":
        pages = load_pages(settings.pages_file)
        if not pages:
            raise FileNotFoundError(f"No pages in {settings.pages_file}. Run `wikichat crawl` first.")
        chunks: list[Chunk] = []
        excluded = hidden = 0
        for p in pages.values():
            if is_excluded(p.title, p.categories, settings):
                excluded += 1
                continue
            for c in chunk_page(p, settings.chunk_size, settings.chunk_overlap):
                if settings.redact_secrets:
                    c.text, n = redact(c.text)
                    hidden += n
                chunks.append(c)
        if hidden:
            log.info("Hid %d secret-looking values (passwords, keys, tokens)", hidden)
        meta = {"built_at": time.strftime("%Y-%m-%d %H:%M"), "pages": len(pages) - excluded,
                "chunks": len(chunks), "excluded_pages": excluded, "hidden_values": hidden,
                "embeddings": False}
        vectors = embedder = None
        if settings.use_embeddings:
            embedder = Embedder(settings.embedding_model)
            log.info("Embedding %d chunks with %s ...", len(chunks), settings.embedding_model)
            vectors = embedder.encode([f"{c.header}\n{c.text}" for c in chunks])
            meta["embeddings"] = settings.embedding_model
        idx = cls(chunks, meta, vectors, embedder)
        idx.save(settings.index_dir)
        return idx

    def save(self, folder: Path) -> None:
        ensure_private_dir(folder)
        with (folder / "chunks.jsonl").open("w", encoding="utf-8") as f:
            for c in self.chunks:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        (folder / "meta.json").write_text(json.dumps(self.meta, indent=2))
        if self.vectors is not None:
            np.save(folder / "vectors.npy", self.vectors)

    @classmethod
    def load(cls, settings: Settings) -> "SearchIndex":
        folder = settings.index_dir
        path = folder / "chunks.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"No index in {folder}. Run `wikichat index` first.")
        with path.open(encoding="utf-8") as f:
            chunks = [Chunk(**json.loads(line)) for line in f if line.strip()]
        meta = json.loads((folder / "meta.json").read_text()) if (folder / "meta.json").exists() else {}
        vectors = embedder = None
        if (folder / "vectors.npy").exists() and meta.get("embeddings"):
            vectors = np.load(folder / "vectors.npy")
            embedder = Embedder(meta["embeddings"])
        return cls(chunks, meta, vectors, embedder)

    # ---------------------------------------------------------------- search
    def search(self, query: str, k: int = 6) -> list[Hit]:
        if not self.chunks or not self.bm25:
            return []
        n = len(self.chunks)
        pool = min(n, max(k * 5, 30))

        bm = self.bm25.get_scores(tokenize(query))
        rankings = [np.argsort(-bm)[:pool]]
        if self.vectors is not None and self.embedder is not None:
            q = self.embedder.encode([query])[0]
            rankings.append(np.argsort(-(self.vectors @ q))[:pool])

        # Reciprocal rank fusion (works with one or two rankings).
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, i in enumerate(ranking):
                if len(rankings) == 1 and bm[i] <= 0:
                    continue
                fused[int(i)] = fused.get(int(i), 0.0) + 1.0 / (60 + rank)

        order = sorted(fused, key=fused.get, reverse=True)
        hits, per_page = [], {}
        for i in order:
            c = self.chunks[i]
            if per_page.get(c.page_title, 0) >= 2:  # keep the sources diverse
                continue
            per_page[c.page_title] = per_page.get(c.page_title, 0) + 1
            hits.append(Hit(c, fused[i]))
            if len(hits) == k:
                break
        return hits
