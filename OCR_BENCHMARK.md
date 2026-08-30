# OCR engine selection

## Decision (superseded 31 Aug 2026 -- see "Engine reconsidered for memory ceiling" below)

Use embedded PDF text first and docTR 1.1 (`fast_base` + `crnn_vgg16_bn`) as the
fallback for scanned pages and image uploads.

**Current production engine is RapidOCR, not docTR** -- docTR's torch dependency
put every real OCR call over the deployed Render plan's memory ceiling. The
reasoning below (accuracy/latency comparison against Tesseract/EasyOCR/PaddleOCR)
is preserved as the historical record of why docTR was picked over those engines;
it wasn't wrong, it just didn't account for the deployed plan's RAM limit. See the
new section below for what changed and why.

Tesseract remains the single fastest and jointly most accurate engine measured
below, but it requires a system binary (`apt-get install tesseract-ocr` or
equivalent) that this deployment's constraints rule out: the target is Render's
native Python runtime, not a Docker build, specifically to avoid the
system-package/Docker maintenance burden (see `render.yaml`'s header comment).
Of the remaining pip-installable engines, docTR is the closest to Tesseract's
accuracy (81.72% vs. 82.71%) at a latency (~11.3s/page on the benchmark
machine) still well inside the 30-second per-page production timeout. EasyOCR
was both slower and less accurate; PaddleOCR matched Tesseract's accuracy but
took 190s/page and hit a Windows-specific runtime error on its default path;
RapidOCR was both slower and less accurate than docTR. This decision is based
on the benchmark below, not on a generic claim that one OCR engine is
universally best.

## Test method

`tools/benchmark_ocr.py` rasterizes the first page from five supplied role
specifications at 220 DPI. Their original PDF text layers are the ground truth.
Accuracy is normalized character similarity after whitespace and case
normalization; latency is wall-clock recognition time after model startup.

- Machine: Intel Core i5-9300H, 4 cores/8 threads, 7.8 GB RAM, Windows
- Production Python: 3.13.1
- Tesseract: 5.4.0, LSTM engine, automatic page segmentation
- PaddleOCR and docTR isolated runtime: Python 3.12.13
- All engines: English, CPU-only, same five pages, same rendered pixels

## Measured results

| Engine | Mean character accuracy | Mean seconds/page | Startup seconds | Relative latency |
|---|---:|---:|---:|---:|
| Tesseract 5.4 LSTM | **82.71%** | **1.657** | 0.000 | **1.0x** |
| docTR 1.1 (`fast_base` + `crnn_vgg16_bn`) | 81.72% | 11.332 | 7.414 | 6.8x |
| RapidOCR ONNX | 76.39% | 15.720 | 1.132 | 9.5x |
| EasyOCR 1.7.2 | 80.53% | 54.742 | 2.992 | 33.0x |
| PaddleOCR 3.7 / PP-OCRv6 medium | **82.71%** | 191.505 | 3.369 | 115.6x |

PaddleOCR's default oneDNN path raised
`ConvertPirAttribute2RuntimeAttribute not support` on this Windows installation.
The reported run used `enable_mkldnn=False`, the working portable CPU path. Its
process was observed reaching about 2.1 GB RSS during inference. EasyOCR was
observed around 1.4 GB RSS. The isolated environment plus downloaded-model
footprints were approximately 1.10 GB for EasyOCR, 1.00 GB for PaddleOCR, and
1.17 GB for docTR, compared with 238 MB for the installed Tesseract distribution.

The benchmark was repeated on 25 August 2026 with the same source PDFs, 220-DPI
pixels, CPU, and installed model versions. The table contains the fresh results
for Tesseract, docTR, RapidOCR, and EasyOCR. A fresh PaddleOCR run produced no
first-page result after five minutes and was stopped; its row therefore retains
the earlier completed five-page measurement. Both observations put PaddleOCR
well outside the production requirement of 30 seconds per OCR page on this host.

Per-page results are intentionally retained in the script output; the five
accuracy values for Tesseract were 79.86%, 82.55%, 83.11%, 82.05%, and 85.96%.
PaddleOCR produced the same normalized edit-distance scores, but at much higher
latency on this machine.

Command for engines installed in a given environment:

```powershell
python -u tools\benchmark_ocr.py C:\Users\abhis\Downloads\updates\documentation --engines tesseract,rapidocr,easyocr,paddleocr,doctr
```

## Python OCR library census

The benchmark covers the relevant turnkey, general-purpose, local Python OCR
recognizers: Tesseract through `pytesseract`, RapidOCR, EasyOCR, PaddleOCR, and
docTR. The other commonly encountered packages are not extra equivalent
recognizers and should not be counted as independent accuracy candidates:

| Package/family | Assessment |
|---|---|
| `tesserocr`, `pyocr` | Alternative Python bindings/wrappers for the same Tesseract recognition engine. `tesserocr` also failed its local Windows build probe because Tesseract development libraries were unavailable. |
| OCRmyPDF | PDF preprocessing/searchable-PDF orchestration that delegates recognition to Tesseract; useful when producing archival PDFs, not a separate recognizer for text extraction. |
| PyMuPDF, pypdf, pdfplumber | Extract existing PDF text or render pages; they do not recognize pixels. PyMuPDF and pypdf are already used in the native-text-first pipeline. |
| keras-ocr | Installable detection/recognition framework, but its 0.9.3 package does not declare or supply the TensorFlow backend it requires. Adding a separate TensorFlow runtime for this older framework is not an efficient production candidate here. |
| MMOCR | Model-training/inference framework whose package alone does not provide a comparable ready-to-run engine; it also needs matching OpenMMLab model/config/runtime components. |
| TrOCR / Transformers | Text-line recognizer, not a full-page OCR pipeline; it requires a separate detector, line ordering, and model choice. |
| Surya OCR | Installable transformer document stack, but requires Torch, Transformers, multiple large downloaded models, and materially more resources than this 8 GB CPU deployment. It is not a like-for-like lightweight fallback. |
| Kraken, Calamari, OCRopus | Primarily specialized/trainable historical-document or line-recognition systems rather than turnkey full-page OCR for these clean, machine-printed clinical documents. |

The package probes also included current `surya-ocr`, `mmocr`, `keras-ocr`,
`tesserocr`, and `ocrmypdf` releases on the production Python environment. This
avoids claiming that wrapper packages are additional OCR engines or presenting
an unconfigured framework as a meaningful benchmark result.

## Production behavior

- PDF pages with at least 40 embedded characters use `pypdf` directly. All
  supplied role PDFs are text-native, so they normally incur no OCR at all.
- Sparse/image-only pages render at 220 DPI and use docTR's pretrained
  detector + recognizer (see `ocr_service.py`).
- No per-page or per-request timeout is actually enforced anywhere in code
  (checked `ocr_service.py` and both upload routers for
  `asyncio.wait_for`/`signal.alarm`/equivalent -- none exists). A prior
  version of this doc claimed a 30-second per-page timeout; that was
  aspirational, not implemented. A page that hangs currently has nothing to
  cut it off.
- OCR failure keeps the upload in `NeedsReview`; it never invents missing
  clinical facts or silently finalizes extracted information. Per-page
  failures are now tracked independently -- one bad page no longer aborts
  OCR for every page after it in the same document, and a partial failure
  is surfaced (`ocr_failed_pages` / `ocr_warning`) instead of being silently
  reported as a full success.
- No system package or language pack install is required -- docTR's model
  weights are pulled from Hugging Face on first use and installed entirely
  through `pip` (`python-doctr[torch]` in `requirements.txt`).

## Architecture comparison: default vs. lightweight docTR (31 Aug 2026)

Production previously used docTR's default `fast_base` (detection) +
`crnn_vgg16_bn` (recognition) combo. Compared against the lightweight
`db_mobilenet_v3_large` + `crnn_mobilenet_v3_small` combo, same methodology
as above (five of this repo's own scenario PDFs, embedded PDF text as ground
truth, 220 DPI rasterization), each config measured in its own fresh process
so RSS readings aren't polluted by a previous config's memory:

| Config | Mean accuracy | Mean seconds/page | Peak RSS |
|---|---:|---:|---:|
| `fast_base` + `crnn_vgg16_bn` (previous default) | 99.67% | 10.463 | 597 MB |
| `db_mobilenet_v3_large` + `crnn_mobilenet_v3_small` (current) | 99.71% | 3.093 | 572 MB |

No accuracy loss on this corpus, a 3.4x latency improvement, and lower peak
RSS. Production now pins the lightweight combo in `ocr_service.py`.

This also corrects a standing error: `render.yaml` and `ocr_service.py`'s
module docstring previously attributed "~2.1GB RSS during inference" to
docTR, citing this file as the source. Rereading the actual measurement
above: that figure was PaddleOCR's process RSS (see "Measured results"
above -- "Its process was observed reaching about 2.1 GB RSS during
inference" refers to the PaddleOCR run described in the preceding sentence).
docTR's own inference RSS was never independently measured before this
comparison; measured here, both docTR configs peaked under 600MB.

Caveat: this comparison, like the one above, used clean, cleanly-rendered
synthetic PDFs. A smaller-capacity recognizer such as
`crnn_mobilenet_v3_small` may show more of an accuracy gap on genuinely
degraded real scans (crumpled paper, low-light phone photos, faxes) than it
did on this corpus -- that needs its own labelled real-scan corpus to
confirm, per the Scope limitation below.

## Engine reconsidered for memory ceiling (31 Aug 2026)

The lightweight docTR architecture above still wasn't enough: production hit a
live Render OOM ("ran out of memory (used over 512MB)") on the `starter` plan
*after* that swap. Root cause, measured directly on this machine:

```
>>> import psutil; psutil.Process().memory_info().rss / 1024 / 1024
17.1   # bare python
>>> import torch
>>> psutil.Process().memory_info().rss / 1024 / 1024
192.0  # +175MB from `import torch` alone, before any model is loaded
```

Both docTR configurations measured above (597MB and 572MB peak RSS) are
already over the 512MB ceiling before this app's own FastAPI/uvicorn process
uses any memory -- no docTR architecture choice can close that gap, since the
floor is torch's own import cost, not the model weights on top of it.

RapidOCR (`rapidocr-onnxruntime`, no torch dependency) was measured with the
same methodology -- five of this repo's own scenario PDFs, embedded PDF text
as ground truth, 220 DPI rasterization:

| Config | Mean accuracy | Mean seconds/page | Peak RSS |
|---|---:|---:|---:|
| docTR, lightweight (previous) | 99.71% | 3.093 | 572 MB |
| **RapidOCR (current)** | **94.09%** | 8.293 | **154 MB** |

RapidOCR comfortably clears the memory ceiling docTR structurally could not.
The accuracy cost is real and was accepted knowingly, not overlooked: 94.09%
mean on this clean corpus (two of five documents scored notably worse, 84.29%
and 87.11%), and on the original real-document corpus in "Measured results"
above, RapidOCR was already the lowest-accuracy engine tested (76.39%, vs.
docTR's 81.72% and Tesseract's 82.71%). This trade-off leans on the existing
human-in-the-loop design: every ClinicalFact drafted from OCR text lands
`PROPOSED` for clinician verification and is never auto-finalized (see
`cca.py`'s document upload endpoint), so a lower-accuracy engine increases
the manual-correction burden rather than the risk of an unreviewed error.

If the deployed plan is ever upgraded past docTR's ~600MB floor, moving back
to docTR (or to Tesseract via a Docker migration, which was both faster and
more accurate than everything else tested) is worth reconsidering purely for
the accuracy gain -- this was a memory-driven choice, not a claim that
RapidOCR is otherwise the better engine.

## Scope limitation

These results apply to the supplied English, machine-printed PDFs on this CPU.
Handwriting, photographed pages, tables, and severely degraded faxes need a
separate labelled corpus before changing the production engine. The
benchmark's edit-distance score also penalizes differences in reading order,
which is appropriate for the application's extracted-text use but is not a
word-detection benchmark.

docTR's pretrained recognition model targets Latin-script text and, unlike
Tesseract or EasyOCR, offers no per-language model or language-code setting.
Documents in non-Latin scripts (e.g. Devanagari) are out of scope for this
engine -- all benchmarked documents were English. If multilingual/Indic-script
document OCR becomes a real requirement, that needs its own benchmark pass
before picking an engine for it; it is not something this module currently
handles.
