# OCR engine selection

## Decision

Use embedded PDF text first and Tesseract 5.4 LSTM as the fallback for scanned
pages and image uploads. On the supplied documents and the target Windows CPU,
Tesseract tied the best measured character accuracy and was by far the fastest
turnkey recognizer. This decision is based on the benchmark below, not on a
generic claim that one OCR engine is universally best.

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
- Sparse/image-only pages render at 220 DPI and use Tesseract LSTM with
  automatic page segmentation.
- Each OCR page has a 30-second timeout.
- OCR failure keeps the upload in `NeedsReview`; it never invents missing
  clinical facts or silently finalizes extracted information.
- Deployments must install the Tesseract executable plus every language pack
  configured by `OCR_LANGUAGES`.

## Scope limitation

These results apply to the supplied English, machine-printed PDFs on this CPU.
Handwriting, photographed pages, multilingual scans, tables, and severely
degraded faxes need a separate labelled corpus before changing the production
engine. The benchmark's edit-distance score also penalizes differences in
reading order, which is appropriate for the application's extracted-text use
but is not a word-detection benchmark.
