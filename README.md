# docorg

Local-first document metadata extraction and cataloguing for large personal libraries.

Point it at a folder of PDFs, EPUBs and DOCX files and it extracts text, reads whatever
metadata already exists, asks a locally-hosted language model to work out what each
document actually *is*, and writes the result back into the file or into a sidecar.
Nothing leaves your machine.

It was built against a real library of ~44,000 files on an SD card, and most of the design
decisions here are scar tissue from that. Where this README states a number, that number
was measured on that library and the method is given so you can disagree with it.

## Licence

Copyright (C) 2026 Manuel Pacheco.

**AGPL-3.0-or-later.** See `LICENSE`.

If you run a modified version of docorg and let other people use it over a network, you
have to offer them your source. That is deliberate. If that does not suit your use case,
this is not the right project for you, and that is fine.

Run `docorg licences` at any time to see which backends are active and what they oblige
you to do.

## Install

Not on PyPI. Install from the repository:

```bash
pip install "git+https://github.com/nothing3471/docorg"

# with the optional extras
pip install "docorg[ocr] @ git+https://github.com/nothing3471/docorg"           # + PaddleOCR
pip install "docorg[alt-backends] @ git+https://github.com/nothing3471/docorg"  # + PyMuPDF / EbookLib
```

Python 3.10 or newer. The core install pulls three permissive dependencies and
nothing copyleft — see Swappable backends for why that is deliberate.

You also need a local model server. docorg talks to [Ollama](https://ollama.com) at
`http://localhost:11434` by default.

## Usage

```bash
docorg licences                     # what am I obliged to do?
docorg extract book.pdf             # text + metadata from one file
docorg extract book.epub --json     # machine-readable
```

## Swappable backends

The PDF and EPUB layers are interchangeable implementations behind a small interface:

| kind | backend | licence | default |
|---|---|---|---|
| PDF | `pdfium` (pypdfium2) | BSD-3-Clause / Apache-2.0 | yes |
| PDF | `fitz` (PyMuPDF) | AGPL-3.0 / Artifex commercial | |
| EPUB | `rawzip` (stdlib only) | none | yes |
| EPUB | `ebooklib` | AGPL-3.0 | |

```bash
export DOCORG_PDF_BACKEND=fitz
export DOCORG_EPUB_BACKEND=ebooklib
```

An unselected backend is never imported, so its library is not loaded into the process
at all.

The permissive backends are the defaults on merit rather than for licence reasons —
docorg is AGPL either way. Measured across 69 real PDFs, pdfium and PyMuPDF produced
**identical page counts (69/69)**, **identical raster dimensions (69/69)**, and text
agreeing to a **99.9% floor**, with **zero open failures** on either side.

On speed they are effectively tied. An earlier draft of this README claimed pdfium was
1.6x slower; that was wrong. The benchmark had read each file with one library first, so
the second one hit a warm OS page cache. Re-run with the cache pre-warmed and the running
order alternated, text extraction came out at 0.77x mean / 0.90x median and rendering at
0.96x — pdfium faster or level. Either way it is noise: extraction is **0.34–0.44% of the
5.64s median per-file runtime** (measured from 22,413 catalogued files). The model call
dominates everything else.

## Why the EPUB reader does not use EbookLib by default

EbookLib raises on several EPUB quirks that are not actually corruption: EPUB3 navigation
documents with no `<nav *="toc">`, resources declared in the OPF but absent from the zip,
and unusual zip offsets. Measured on a 918-book library, that was ~5% of books — roughly
500 titles being silently dropped across the full collection.

The `rawzip` backend reads the container directly and recovers them. It also follows the
**OPF spine** for reading order. Its predecessor sorted spine documents alphabetically,
which puts `chapter10.xhtml` before `chapter2.xhtml` and quietly scrambles the extracted
text of any book with more than nine chapters.

## Known limitations

Stated plainly, because finding these yourself later is worse.

- **Comic OCR is slow.** Verified working on a real 32-page CBR: 30 pages, 20,100
  characters, 2.5s per page. That is roughly 13x the 5.64s median for an ordinary
  document, so a large comic collection adds hours. CBR archives need an external
  extractor — `unrar`, or `7z`, which rarfile will fall back to.
- **The catalog is keyed on absolute path.** Move a file and its cache entry is orphaned;
  the file is reprocessed from scratch. On the reference library 41.2% of catalog entries
  point at paths that no longer exist. Deduplicate by hash before trusting an entry count
  as a progress measure.
- **Image-only documents yield no text**, by design. They are routed to OCR if the `ocr`
  extra is installed, and skipped otherwise. Roughly half the reference library is
  image-only.
- **Reading order can differ between PDF backends** on multi-column layouts. The text is
  the same; the traversal order is not. This is visible in ~5% of text-bearing files and
  does not appear to affect metadata quality, but it has not been measured against
  downstream accuracy.
- **Non-standard font encodings** are handled differently by each PDF backend —
  a curly apostrophe may come out as `don't`, `donít`, or `don t`. Neither backend is
  reliably correct.
- **No Windows/macOS/Linux CI yet.** It is developed and tested on Windows.

## Testing

```bash
git clone https://github.com/nothing3471/docorg
cd docorg
pip install -e ".[dev]"
pytest
```

The differential suite runs both backends against real files and fails if they diverge.
It needs a document catalog to sample from; without one it skips rather than fails.

```bash
DOCORG_TEST_CATALOG=/path/to/document_catalog.json pytest
```

Two things the suite deliberately guards against, both of which happened during
development:

- A **degenerate sample**. Most PDFs in a real library are image-only and skip, so a
  green run can mean nothing was compared. `test_suite_actually_compared_something`
  fails if fewer than a third of sampled files had comparable text.
- A **metric that rewards the wrong thing**. The original comparison scored symmetric
  similarity, which penalises a backend for extracting *more* text — the first failure it
  produced was 79 characters of journal footer that one backend caught and the other
  missed. It now tests that the backends never contradict each other, which is the
  property that actually matters.

## Origins

This did not start as a product. It started as a script to sort out one very large and
very disorganised personal library, and it grew because that library kept exposing ways
for the script to fail silently — writing metadata that never landed, reading books it
reported as unreadable, reporting success on files it had skipped. Most of the
engineering here is the result of chasing those.

Because it began as a private tool, the early history is not documented. Systematic
records begin partway through, and the reasoning behind the earliest decisions was
reconstructed after the fact rather than recorded at the time.