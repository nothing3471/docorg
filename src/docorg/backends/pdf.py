"""Concrete PDF backends. Import cost is deferred: neither library is imported until
its backend is actually instantiated, so an AGPL library is never even loaded into the
process when the permissive backend is selected. That matters legally as well as for
start-up time."""
from __future__ import annotations

import io
from typing import Optional

from ._pdf_base import PdfBackend, PageImage, PdfError, DEFAULT_BACKEND  # re-exported


class PdfiumBackend(PdfBackend):
    """pypdfium2 - BSD-3-Clause / Apache-2.0. Default. Safe to ship in a closed product."""

    name = "pdfium"
    licence = "BSD-3-Clause OR Apache-2.0"

    def __init__(self) -> None:
        self._doc = None

    def open(self, path: str) -> None:
        try:
            import pypdfium2 as pdfium
        except ImportError as e:
            raise PdfError("pypdfium2 is not installed") from e
        try:
            self._doc = pdfium.PdfDocument(path)
        except Exception as e:
            raise PdfError(f"cannot open {path}: {type(e).__name__}: {e}") from e

    def close(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass          # closing must never mask the caller's real error
            self._doc = None

    def _require(self):
        if self._doc is None:
            raise PdfError("backend used before open() or after close()")
        return self._doc

    def page_count(self) -> int:
        return len(self._require())

    def text(self, page_index: int) -> str:
        d = self._require()
        try:
            return d[page_index].get_textpage().get_text_range()
        except Exception as e:
            raise PdfError(f"text extraction failed on page {page_index}: {e}") from e

    def render(self, page_index: int, scale: float = 2.0) -> PageImage:
        d = self._require()
        try:
            img = d[page_index].render(scale=scale).to_pil()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return PageImage(page_index, buf.getvalue(), img.width, img.height)
        except Exception as e:
            raise PdfError(f"render failed on page {page_index}: {e}") from e

    def metadata(self) -> dict:
        d = self._require()
        try:
            return dict(d.get_metadata_dict() or {})
        except Exception:
            return {}


class FitzBackend(PdfBackend):
    """PyMuPDF - AGPL-3.0 OR Artifex commercial. Faster. Selecting this backend imposes
    AGPL obligations on anything you distribute unless you hold a commercial licence."""

    name = "fitz"
    licence = "AGPL-3.0 OR Artifex-Commercial"

    def __init__(self) -> None:
        self._doc = None

    def open(self, path: str) -> None:
        try:
            import fitz
        except ImportError as e:
            raise PdfError("PyMuPDF is not installed") from e
        try:
            self._doc = fitz.open(path)
        except Exception as e:
            raise PdfError(f"cannot open {path}: {type(e).__name__}: {e}") from e

    def close(self) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None

    def _require(self):
        if self._doc is None:
            raise PdfError("backend used before open() or after close()")
        return self._doc

    def page_count(self) -> int:
        return self._require().page_count

    def text(self, page_index: int) -> str:
        try:
            return self._require()[page_index].get_text()
        except Exception as e:
            raise PdfError(f"text extraction failed on page {page_index}: {e}") from e

    def render(self, page_index: int, scale: float = 2.0) -> PageImage:
        d = self._require()
        try:
            # fitz takes dpi, pdfium takes a scale factor. 72 dpi is the PDF base unit,
            # so dpi = 72 * scale keeps the two backends pixel-identical - this is the
            # line that makes the 69/69 raster-dimension match hold.
            pm = d[page_index].get_pixmap(dpi=int(72 * scale))
            return PageImage(page_index, pm.tobytes("png"), pm.width, pm.height)
        except Exception as e:
            raise PdfError(f"render failed on page {page_index}: {e}") from e

    def metadata(self) -> dict:
        try:
            return dict(self._require().metadata or {})
        except Exception:
            return {}


_BACKENDS = {"pdfium": PdfiumBackend, "fitz": FitzBackend}


def get_backend(name: Optional[str] = None) -> PdfBackend:
    """Factory. `name` overrides DOCORG_PDF_BACKEND; default is the permissive one."""
    # read through the module so monkeypatching DEFAULT_BACKEND in tests takes effect
    from . import _pdf_base
    key = (name or _pdf_base.DEFAULT_BACKEND).lower()
    if key not in _BACKENDS:
        raise PdfError(f"unknown PDF backend {key!r}; available: {sorted(_BACKENDS)}")
    return _BACKENDS[key]()


def available() -> dict:
    """Which backends can actually run here, and under what licence. Used by the CLI's
    `--licence-report` so a user can see their own obligations before shipping."""
    out = {}
    for key, cls in _BACKENDS.items():
        try:
            __import__("pypdfium2" if key == "pdfium" else "fitz")
            ok = True
        except Exception:
            ok = False
        out[key] = {"installed": ok, "licence": cls.licence}
    return out