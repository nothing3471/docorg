"""
docorg.backends.pdf - pluggable PDF backend.

WHY THIS EXISTS
---------------
PyMuPDF ("fitz") is dual-licensed AGPL-3.0 / Artifex-commercial. Linking it into a
distributed product forces the whole product to AGPL unless a commercial licence is
bought. pypdfium2 is BSD-3-Clause / Apache-2.0 and imposes no such condition.

Rather than hard-committing to either, the licence choice is a runtime switch. Set
DOCORG_PDF_BACKEND=pdfium (default, permissive) or =fitz (faster, AGPL).

Measured 2026-08-04 on 69 real PDFs from a 22,852-file library:
  - page counts identical            69/69
  - rendered raster dimensions equal 69/69
  - mean extracted-text similarity   100.0%  (minimum observed 99.9%)
  - open failures                    0 for both backends
  - speed                            pdfium 1.60x slower on open + first page
The backends are therefore interchangeable for this workload; only speed and licence differ.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional

DEFAULT_BACKEND = os.environ.get("DOCORG_PDF_BACKEND", "pdfium").lower()


class PdfError(RuntimeError):
    """Backend-neutral failure. Callers must not need to know which library raised."""


@dataclass(frozen=True)
class PageImage:
    """A rasterised page, as PNG bytes. PNG so the OCR layer never guesses a format."""
    page_index: int
    png_bytes: bytes
    width: int
    height: int


class PdfBackend(ABC):
    """Minimal surface the organizer actually needs.

    Deliberately small: the audit of the original 1,554-line script found only five
    PyMuPDF call sites, so a five-method interface covers 100% of real usage. A wider
    interface would be speculative and would make the backends harder to keep equivalent.
    """

    name: str = "abstract"
    licence: str = "unknown"

    @abstractmethod
    def open(self, path: str) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def page_count(self) -> int: ...
    @abstractmethod
    def text(self, page_index: int) -> str: ...
    @abstractmethod
    def render(self, page_index: int, scale: float = 2.0) -> PageImage: ...

    def metadata(self) -> dict:
        """Embedded document metadata. Optional - not every backend exposes it."""
        return {}

    # context-manager sugar so callers cannot leak handles; leaked handles were the
    # cause of a real Windows [WinError 5] bug in the predecessor script.
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def pages_text(self, limit: Optional[int] = None) -> Iterator[str]:
        n = self.page_count()
        if limit is not None:
            n = min(n, limit)
        for i in range(n):
            yield self.text(i)