"""
docorg.backends.epub - pluggable EPUB backend.

EbookLib is AGPL-3.0. As with the PDF layer, the licence is a runtime choice and the
permissive path is the default. Set DOCORG_EPUB_BACKEND=rawzip (default) or =ebooklib.

The rawzip backend is not a degraded fallback. It was written because EbookLib raises on
several EPUB quirks that are not corruption - EPUB3 nav documents with no
<nav *='toc'>, resources declared in the OPF but missing from the zip, and unusual zip
offsets. Measured 2026-08-04: those accounted for ~5% of one 918-book library, about 500
books that were being silently dropped. Reading the container directly recovered them.

It also fixes a real defect in the original fallback: that code read spine documents in
ALPHABETICAL filename order, so 'chapter10.xhtml' sorted before 'chapter2.xhtml' and the
extracted text was out of order. This version follows the OPF spine, which is the
authoritative reading order.
"""
from __future__ import annotations

import html
import os
import re
import zipfile
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple

DEFAULT_EPUB_BACKEND = os.environ.get("DOCORG_EPUB_BACKEND", "rawzip").lower()

DC_FIELDS = ("title", "creator", "subject", "description",
             "language", "publisher", "date", "identifier")


class EpubError(RuntimeError):
    """Backend-neutral EPUB failure."""


def _strip_markup(markup: str) -> str:
    """HTML -> plain text.

    Script and style BODIES are removed, not merely stripped of tags: otherwise their
    contents leak into the extracted text and poison metadata extraction.

    Entities are decoded last. Skipping this leaves raw '&amp;' and '&#8217;' in titles,
    which then propagate into every downstream record - observed on a real book whose
    title came out as 'the Kicking &amp; Screaming'.
    """
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = s.replace("\xa0", " ")          # nbsp survives unescape as a real character
    s = re.sub(r"\s+", " ", s)
    return s.strip()


class EpubBackend(ABC):
    name = "abstract"
    licence = "unknown"

    @abstractmethod
    def read(self, path: str, max_chars: int = 12000,
             max_docs: int = 30) -> Tuple[str, Dict[str, str]]: ...


class RawZipEpubBackend(EpubBackend):
    """Stdlib only - no third-party licence obligations of any kind."""

    name = "rawzip"
    licence = "none (stdlib only)"

    def _opf_path(self, z: zipfile.ZipFile) -> str | None:
        """Locate the OPF via META-INF/container.xml, which is where the spec says it is.
        Falling back to 'first *.opf in the archive' is a guess and picks the wrong file
        in multi-rendition EPUBs."""
        try:
            container = z.read("META-INF/container.xml").decode("utf-8", "ignore")
            m = re.search(r'full-path\s*=\s*["\']([^"\']+)["\']', container, re.I)
            if m:
                return m.group(1)
        except Exception:
            pass
        return next((n for n in z.namelist() if n.lower().endswith(".opf")), None)

    def _spine_order(self, opf_xml: str, opf_dir: str, names: List[str]) -> List[str]:
        """Resolve the OPF spine into archive paths, in reading order."""
        manifest: Dict[str, str] = {}
        for m in re.finditer(r"<item\b([^>]*)>", opf_xml, re.I):
            attrs = m.group(1)
            iid = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            href = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if iid and href:
                manifest[iid.group(1)] = href.group(1)

        ordered: List[str] = []
        for m in re.finditer(r"<itemref\b([^>]*)>", opf_xml, re.I):
            idref = re.search(r'\bidref\s*=\s*["\']([^"\']+)["\']', m.group(1), re.I)
            if not idref:
                continue
            href = manifest.get(idref.group(1))
            if not href:
                continue
            cand = os.path.normpath(os.path.join(opf_dir, href)).replace("\\", "/")
            if cand in names:
                ordered.append(cand)
            else:                       # some EPUBs store hrefs already archive-relative
                alt = href.replace("\\", "/")
                if alt in names:
                    ordered.append(alt)
        return ordered

    def read(self, path, max_chars=12000, max_docs=30):
        parts: List[str] = []
        meta: Dict[str, str] = {}
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                opf = self._opf_path(z)
                docs: List[str] = []

                if opf:
                    try:
                        raw = z.read(opf).decode("utf-8", "ignore")
                        for tag in DC_FIELDS:
                            m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", raw, re.I | re.S)
                            if m:
                                val = _strip_markup(m.group(1))
                                if val:
                                    meta[tag] = val[:500]
                        docs = self._spine_order(raw, os.path.dirname(opf), names)
                    except Exception:
                        docs = []

                if not docs:
                    # No usable spine. Alphabetical is a guess and is often wrong, but it
                    # beats returning nothing - flag it so callers know the order is
                    # unreliable rather than assuming it is correct.
                    docs = sorted(n for n in names
                                  if n.lower().endswith((".xhtml", ".html", ".htm")))
                    if docs:
                        meta["_docorg_order"] = "filename (no spine found; order unreliable)"
                else:
                    meta["_docorg_order"] = "spine"

                running = 0
                for i, n in enumerate(docs):
                    if i >= max_docs or running >= max_chars:
                        break
                    try:
                        clean = _strip_markup(z.read(n).decode("utf-8", "ignore"))
                    except Exception:
                        continue
                    if clean:
                        parts.append(clean)
                        running += len(clean)
        except zipfile.BadZipFile as e:
            raise EpubError(f"not a valid EPUB container: {e}") from e
        except Exception as e:
            raise EpubError(f"{type(e).__name__}: {e}") from e

        return "\n".join(parts), meta


class EbooklibEpubBackend(EpubBackend):
    """EbookLib - AGPL-3.0. Opt-in only."""

    name = "ebooklib"
    licence = "AGPL-3.0"

    def read(self, path, max_chars=12000, max_docs=30):
        try:
            import ebooklib
            from ebooklib import epub
        except ImportError as e:
            raise EpubError("EbookLib is not installed") from e

        parts: List[str] = []
        meta: Dict[str, str] = {}
        try:
            book = epub.read_epub(str(path), options={"ignore_ncx": True})
        except Exception as e:
            raise EpubError(f"EbookLib failed: {type(e).__name__}: {e}") from e

        # Shape is {namespace: {name: [(text, attrs), ...]}}. Iterating the inner dict
        # and unpacking each key into two names raises "too many values to unpack" on
        # essentially every real EPUB - that bug cost 897 of 918 books before it was found.
        for ns, entries in (book.metadata or {}).items():
            if not isinstance(entries, dict):
                continue
            for nm, values in entries.items():
                if not values:
                    continue
                key = f"{ns}:{nm}" if ns != "None" else nm
                first = values[0]
                meta[key] = str(first[0]) if isinstance(first, (tuple, list)) and first else str(first)

        running = 0
        for i, item in enumerate(book.get_items_of_type(ebooklib.ITEM_DOCUMENT)):
            if i >= max_docs or running >= max_chars:
                break
            clean = _strip_markup(item.get_content().decode("utf-8", "ignore"))
            if clean:
                parts.append(clean)
                running += len(clean)
        return "\n".join(parts), meta


_EPUB_BACKENDS = {"rawzip": RawZipEpubBackend, "ebooklib": EbooklibEpubBackend}


def get_epub_backend(name: str | None = None) -> EpubBackend:
    key = (name or DEFAULT_EPUB_BACKEND).lower()
    if key not in _EPUB_BACKENDS:
        raise EpubError(f"unknown EPUB backend {key!r}; available: {sorted(_EPUB_BACKENDS)}")
    return _EPUB_BACKENDS[key]()