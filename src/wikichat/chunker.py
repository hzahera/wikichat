"""Turn wikitext into clean, section-aware text chunks."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import mwparserfromhell

from .crawler import Page

HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
MEDIA_LINK = re.compile(
    r"\[\[\s*(?:Kategorie|Category|Datei|File|Bild|Image|Media)\s*:[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]",
    re.I,
)
COMMENT = re.compile(r"<!--.*?-->", re.S)
SKIP_TAGS = ("ref", "references", "gallery", "syntaxhighlight_skip")


@dataclass
class Chunk:
    id: str
    page_title: str
    section: str
    url: str
    text: str
    timestamp: str = ""

    @property
    def header(self) -> str:
        return f"{self.page_title} > {self.section}" if self.section else self.page_title


def clean_wikitext(text: str) -> str:
    """Wikitext to plain text. Keeps template parameters, link labels and table cells."""
    text = COMMENT.sub("", text)
    text = MEDIA_LINK.sub("", text)
    text = re.sub(r"__[A-Z]+__", "", text)  # __TOC__, __NOTOC__ ...
    code = mwparserfromhell.parse(text)

    for tag in code.filter_tags(recursive=False):
        if str(tag.tag).lower() in SKIP_TAGS:
            try:
                code.remove(tag)
            except ValueError:
                pass

    # Templates often hold real facts (infoboxes). Render them as "key: value".
    for tpl in code.filter_templates(recursive=False):
        parts = []
        for p in tpl.params:
            val = mwparserfromhell.parse(str(p.value)).strip_code().strip()
            if not val:
                continue
            name = str(p.name).strip()
            parts.append(val if name.isdigit() else f"{name}: {val}")
        try:
            code.replace(tpl, "\n".join(parts) + ("\n" if parts else ""))
        except ValueError:
            pass

    plain = code.strip_code(normalize=True, collapse=True)
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n\s*\n+", "\n\n", plain)
    return plain.strip()


def split_sections(wikitext: str) -> list[tuple[str, str]]:
    """Return [(section_path, body_wikitext)]. Intro has an empty section name."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    last_end, current = 0, ""
    for m in HEADING.finditer(wikitext):
        out.append((current, wikitext[last_end : m.start()]))
        level, title = len(m.group(1)), mwparserfromhell.parse(m.group(2)).strip_code().strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        current = " > ".join(t for _, t in stack)
        last_end = m.end()
    out.append((current, wikitext[last_end:]))
    return out


def _windows(text: str, size: int, overlap: int) -> list[str]:
    """Pack paragraphs into windows of about `size` characters."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= size:
            pieces.append(p)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", p)
        buf = ""
        for s in sentences:
            while len(s) > size:  # one huge "sentence" (lists, tables)
                pieces.append(s[:size])
                s = s[size - overlap :]
            if len(buf) + len(s) + 1 > size and buf:
                pieces.append(buf)
                buf = buf[-overlap:] + " " + s if overlap else s
            else:
                buf = f"{buf} {s}".strip()
        if buf:
            pieces.append(buf)

    windows, buf = [], ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 2 > size:
            windows.append(buf)
            tail = buf[-overlap:] if overlap else ""
            buf = (tail + "\n\n" + piece).strip() if tail else piece
        else:
            buf = f"{buf}\n\n{piece}".strip()
    if buf:
        windows.append(buf)
    return windows


def anchor(section: str) -> str:
    last = section.split(" > ")[-1]
    return last.replace(" ", "_")


def chunk_page(page: Page, size: int = 900, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section, body in split_sections(page.wikitext):
        text = clean_wikitext(body)
        if len(text) < 20:
            continue
        url = page.url + (f"#{anchor(section)}" if section else "")
        for i, w in enumerate(_windows(text, size, overlap)):
            cid = hashlib.sha1(f"{page.pageid}|{section}|{i}".encode()).hexdigest()[:16]
            chunks.append(Chunk(cid, page.title, section, url, w, page.timestamp))
    if not chunks and page.title:
        # Very short pages still deserve a chunk (title only pages, stubs).
        text = clean_wikitext(page.wikitext) or page.title
        cid = hashlib.sha1(f"{page.pageid}|stub".encode()).hexdigest()[:16]
        chunks.append(Chunk(cid, page.title, "", page.url, text, page.timestamp))
    return chunks
