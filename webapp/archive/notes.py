"""Notes kept next to the images: a file Notizen.md (or notes.md) in a session or target folder.

Read on every change (a few KB) and shown as Markdown on the session and catalog pages.
"""
from __future__ import annotations

import html
import logging
import os
import re

from markupsafe import Markup
from sqlalchemy import delete, select

from webapp import db
from webapp.models import IndexRun, Note

log = logging.getLogger("photonledger.notes")
NAMES = {"notizen.md", "notes.md"}
MAX_BYTES = 200_000


def find(dirpath: str, files: list[str]) -> list[str]:
    """Notes files among the files of one folder (absolute paths)."""
    return [os.path.join(dirpath, f) for f in files if f.lower() in NAMES]


def sync(cfg: dict, run: IndexRun, paths: list[str], protect: set[str] | None = None) -> None:
    from . import archives
    from .scanner import keep
    existing = {n.path: n for n in db.session.scalars(select(Note))}
    for full in paths:
        rel = archives.to_rel(cfg, full)
        try:
            st = os.stat(full)
        except OSError:
            continue
        n = existing.get(rel)
        if n and n.size == st.st_size and abs(n.mtime - st.st_mtime) < 1e-3:
            n.seen_run = run.id
            continue
        n = n or Note(path=rel, folder=rel.rsplit("/", 1)[0] if "/" in rel else "")
        try:
            with open(full, "rb") as fh:
                raw = fh.read(MAX_BYTES)
            n.text = raw.decode("utf-8", errors="replace")
        except OSError as e:
            log.warning("Notizen nicht lesbar: %s (%s)", rel, e)
            continue
        n.size, n.mtime, n.seen_run = st.st_size, st.st_mtime, run.id
        db.session.add(n)
        log.info("Notizen gelesen: %s", rel)
    db.session.commit()
    db.session.execute(delete(Note).where(Note.seen_run != run.id, *keep(Note.path, protect or set())))
    db.session.commit()


# --------------------------------------------------------------------------- Markdown (safe subset)

_INLINE = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*|__(.+?)__"), lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)"),
     lambda m: f"<em>{m.group(1) or m.group(2)}</em>"),
    (re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)"), r'<a href="\2" target="_blank" rel="noopener">\1</a>'),
    (re.compile(r"(?<![\"'>=])(https?://[^\s<]+)"), r'<a href="\1" target="_blank" rel="noopener">\1</a>'),
]


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for rx, rep in _INLINE:
        out = rx.sub(rep, out)
    return out


def render(text: str) -> Markup:
    """Headings, paragraphs, bullet and numbered lists, checkboxes, quotes, code blocks, bold, italic,
    code and links. Everything else is escaped - raw HTML in the notes is shown as text."""
    out: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    para: list[str] = []
    list_tag = None
    in_code = False
    code: list[str] = []

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code.clear()
            else:
                flush_para(), close_list()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        s = line.strip()
        if not s:
            flush_para(), close_list()
            continue
        if m := re.match(r"(#{1,6})\s+(.*)", s):
            flush_para(), close_list()
            level = min(len(m.group(1)) + 2, 6)                 # # -> h3: below the page headings
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if re.match(r"^(-{3,}|\*{3,})$", s):
            flush_para(), close_list()
            out.append("<hr>")
            continue
        if m := re.match(r"[-*+]\s+(.*)", s) or re.match(r"(\d+)[.)]\s+(.*)", s):
            flush_para()
            tag = "ol" if s[0].isdigit() else "ul"
            if list_tag != tag:
                close_list()
                out.append(f"<{tag}>")
                list_tag = tag
            item = m.group(m.lastindex)
            if c := re.match(r"\[([ xX])\]\s+(.*)", item):
                box = "bi-check-square" if c.group(1).lower() == "x" else "bi-square"
                out.append(f'<li class="pl-task"><i class="bi {box}"></i> {_inline(c.group(2))}</li>')
            else:
                out.append(f"<li>{_inline(item)}</li>")
            continue
        if s.startswith(">"):
            flush_para(), close_list()
            out.append(f"<blockquote>{_inline(s.lstrip('> '))}</blockquote>")
            continue
        close_list()
        para.append(s)
    if in_code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para(), close_list()
    return Markup("\n".join(out))
