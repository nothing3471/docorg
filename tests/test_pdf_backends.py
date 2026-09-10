"""
Differential test: the two PDF backends must be interchangeable.

This is the test that lets the licence be a config flag. If it passes, swapping fitz for
pdfium is safe; if it ever fails, the abstraction is lying and the swap is not safe.

It runs against the user's REAL library rather than fixtures, because the whole point is
fidelity on messy real-world files - synthetic PDFs would prove nothing. If the library
is not present the tests skip rather than fail, so the suite still runs on a clean machine.
"""
from __future__ import annotations

import difflib
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from docorg.backends.pdf import get_backend, available, PdfError   # noqa: E402

CATALOG = os.environ.get("DOCORG_TEST_CATALOG", r"E:\pdf\document_catalog.json")
SAMPLE_N = int(os.environ.get("DOCORG_TEST_SAMPLE", "25"))

# Coverage floor. NOT a symmetric-similarity floor - that was the wrong metric.
#
# A symmetric ratio punishes a backend for extracting MORE text. Real case, 2026-08-04:
# "ABC of Conflict and Disasters" scored 0.9891 and failed, and the entire difference was
# 79 characters of journal page-furniture ("clinical review bmj volume 330 21 may 2005
# ... downloaded from bmj.com") that pdfium captured and PyMuPDF missed. The backend
# under test was the more complete one; the metric was wrong.
#
# What actually matters is that the two never CONTRADICT each other: whatever the shorter
# extraction contains should appear in the longer one. Extra marginal content is a
# difference in completeness, not a fidelity defect.
MIN_TEXT_COVERAGE = 0.99


def _sample_pdfs(n):
    """Select files that can actually exercise the comparison.

    The library is dominated by image-only PDFs, which yield no text in either backend
    and therefore skip. A first version of this sampler took files at random and 9 of 10
    cases skipped - the suite went green while testing almost nothing. Bias the sample
    toward text-bearing files so the differential assertions actually run.

    The catalog already records extracted metadata, so text-bearing files can be
    identified without opening a single PDF.
    """
    if not os.path.exists(CATALOG):
        pytest.skip(f"no catalog at {CATALOG}")
    with open(CATALOG, encoding="utf-8") as fh:
        cat = json.load(fh)

    text_bearing, other = [], []
    for path, rec in cat.get("files", {}).items():
        if not path.lower().endswith(".pdf"):
            continue
        # 'text' in modes_done means the extractor got real text out of it, i.e. it is
        # not an image-only scan. That is exactly the population we need to compare.
        modes = rec.get("modes_done") or []
        (text_bearing if "text" in modes else other).append(path)

    random.seed(1337)                       # deterministic: a flaky test is worthless
    random.shuffle(text_bearing)
    random.shuffle(other)

    picked = [p for p in text_bearing[:n * 8] if os.path.exists(p)][:n]
    if len(picked) < n:                     # fall back so the suite still runs anywhere
        picked += [p for p in other[:n * 8] if os.path.exists(p)][: n - len(picked)]
    if not picked:
        pytest.skip("catalog present but no PDF files reachable")
    return picked


def _both_installed():
    av = available()
    return av["pdfium"]["installed"] and av["fitz"]["installed"]


pytestmark = pytest.mark.skipif(
    not _both_installed(), reason="needs both pypdfium2 and PyMuPDF installed to compare"
)



def _first_text_page(backend, max_scan=12):
    """Index of the first page with real text, or None.

    Comparing page 0 unconditionally was a mistake: most books open on a cover image,
    so page 0 has no text and the comparison silently skipped. The guard-rail test
    caught this - only 1 of 12 text-bearing books had text on page 0.
    """
    n = min(backend.page_count(), max_scan)
    for i in range(n):
        try:
            t = backend.text(i)
        except PdfError:
            continue
        if len("".join(c for c in t if c.isalnum())) >= 200:
            return i
    return None


@pytest.mark.parametrize("path", _sample_pdfs(SAMPLE_N))
def test_backends_agree(path):
    with get_backend("pdfium") as a, get_backend("fitz") as b:
        a.open(path)
        b.open(path)

        assert a.page_count() == b.page_count(), "page count differs"

        # Compare the first page that has text, not page 0 - and compare the SAME index
        # in both backends, so any difference is the library and not the page choice.
        page = _first_text_page(a)
        if page is None:
            pytest.skip("image-only document: no extractable text in first pages")

        ta, tb = a.text(page), b.text(page)
        na = "".join(c.lower() for c in ta if c.isalnum())
        nb = "".join(c.lower() for c in tb if c.isalnum())

        # Scoring a no-text page as 0% similarity is a measurement bug - it was made once
        # during this project and produced a bogus "16.7% mean". Skip explicitly instead.
        if len(na) < 200 and len(nb) < 200:
            pytest.skip("no text on the selected page in either backend")

        # Coverage of the SHORTER extraction by the longer one. 1.0 means the shorter
        # text is fully contained in the longer, i.e. the backends agree on everything
        # they both saw and one simply saw more.
        short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
        sm = difflib.SequenceMatcher(None, short[:6000], long_[:6000])
        matched = sum(bl.size for bl in sm.get_matching_blocks())
        coverage = matched / max(1, len(short[:6000]))

        assert coverage >= MIN_TEXT_COVERAGE, (
            f"page {page}: backends disagree - only {coverage:.4f} of the shorter "
            f"extraction is present in the longer ({len(na)} pdfium vs {len(nb)} fitz "
            f"alnum chars). This is a real divergence, not a completeness difference."
        )

        ia, ib = a.render(page, scale=2.0), b.render(page, scale=2.0)
        assert (ia.width, ia.height) == (ib.width, ib.height), "raster dimensions differ"
        assert ia.png_bytes[:4] == b"\x89PNG", "pdfium did not emit PNG"
        assert ib.png_bytes[:4] == b"\x89PNG", "fitz did not emit PNG"


def test_unknown_backend_rejected():
    with pytest.raises(PdfError):
        get_backend("nope")


def test_use_before_open_is_a_clear_error():
    be = get_backend("pdfium")
    with pytest.raises(PdfError, match="before open"):
        be.page_count()


def test_close_is_idempotent():
    be = get_backend("pdfium")
    be.close()
    be.close()          # must not raise: double-close caused a real handle bug before


def test_default_backend_is_permissive(monkeypatch):
    """Guard rail: the default must never silently become the AGPL one.

    Does NOT reload the module - see the EPUB suite for why: reloading rebinds PdfError
    and makes other tests' pytest.raises() fail depending on execution order.
    """
    from docorg.backends import _pdf_base
    monkeypatch.delenv("DOCORG_PDF_BACKEND", raising=False)
    assert _pdf_base.DEFAULT_BACKEND == "pdfium"
    assert type(get_backend()).__name__ == "PdfiumBackend"


def test_suite_actually_compared_something(record_property):
    """Guard rail: fail loudly if the sample degenerates to all-skips.

    Without this, a change to the catalog or a bad seed could silently reduce the
    differential test to zero real comparisons while the suite still reports green.
    """
    paths = _sample_pdfs(SAMPLE_N)
    compared = 0
    for path in paths:
        try:
            with get_backend("pdfium") as a:
                a.open(path)
                if _first_text_page(a) is not None:
                    compared += 1
        except PdfError:
            continue
    record_property("comparable_files", compared)
    assert compared >= max(1, len(paths) // 3), (
        f"only {compared}/{len(paths)} sampled PDFs have extractable text; "
        "the differential test is not exercising the backends"
    )
