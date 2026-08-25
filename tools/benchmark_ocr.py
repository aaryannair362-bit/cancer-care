"""Reproducible OCR benchmark against text-native reference PDFs.

Rasterizes representative pages, runs locally available OCR engines, and uses
the PDF text layer as ground truth. No patient data or network calls are used.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import statistics
import time
from pathlib import Path

import pymupdf
from PIL import Image
from rapidfuzz.distance import Levenshtein

try:
    import psutil
except ImportError:  # Optional so the benchmark remains easy to run.
    psutil = None


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def score(reference: str, candidate: str) -> float:
    ref = normalize(reference)
    return 1.0 if not ref else 1.0 - (Levenshtein.distance(ref, normalize(candidate)) / len(ref))


def tesseract_ocr(image: Image.Image, executable: str) -> str:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = executable
    return pytesseract.image_to_string(image, lang="eng", config="--oem 1 --psm 3")


def rapidocr_ocr(image: Image.Image, engine: object) -> str:
    import numpy as np

    result, _ = engine(np.asarray(image))
    return "\n".join(item[1] for item in (result or []))


def easyocr_ocr(image: Image.Image, engine: object) -> str:
    import numpy as np

    return "\n".join(engine.readtext(np.asarray(image), detail=0, paragraph=True))


def paddleocr_ocr(image: Image.Image, engine: object) -> str:
    import numpy as np

    pages = list(engine.predict(np.asarray(image)))
    texts: list[str] = []
    for page in pages:
        # PaddleX result objects implement mapping access for the OCR payload.
        texts.extend(str(value) for value in page["rec_texts"])
    return "\n".join(texts)


def doctr_ocr(image: Image.Image, engine: object) -> str:
    import numpy as np

    exported = engine([np.asarray(image)]).export()
    return "\n".join(
        word["value"]
        for page in exported["pages"]
        for block in page["blocks"]
        for line in block["lines"]
        for word in line["words"]
    )


def rss_megabytes() -> float:
    if psutil is None:
        return 0.0
    return psutil.Process().memory_info().rss / (1024 * 1024)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--tesseract", default=r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--engines", default="tesseract,rapidocr,easyocr,paddleocr,doctr")
    args = parser.parse_args()

    files = sorted(args.pdf_dir.glob("*.pdf"))
    # Broad role/document-layout coverage without biasing toward one PDF.
    sample_indices = [0, 2, 5, 9, 14]
    samples = [(files[index], 0) for index in sample_indices if index < len(files)]
    requested = {item.strip() for item in args.engines.split(",") if item.strip()}
    engines: dict[str, object] = {}
    initialization: dict[str, float] = {}
    peak_rss: dict[str, float] = {}
    if "tesseract" in requested and importlib.util.find_spec("pytesseract") and os.path.exists(args.tesseract):
        engines["tesseract"] = args.tesseract
        initialization["tesseract"] = 0.0
        peak_rss["tesseract"] = rss_megabytes()
    try:
        if "rapidocr" not in requested:
            raise ImportError
        from rapidocr_onnxruntime import RapidOCR

        started = time.perf_counter()
        engines["rapidocr"] = RapidOCR()
        initialization["rapidocr"] = time.perf_counter() - started
        peak_rss["rapidocr"] = rss_megabytes()
    except ImportError:
        pass
    try:
        if "doctr" not in requested:
            raise ImportError
        from doctr.models import ocr_predictor

        started = time.perf_counter()
        engines["doctr"] = ocr_predictor(pretrained=True)
        initialization["doctr"] = time.perf_counter() - started
        peak_rss["doctr"] = rss_megabytes()
    except ImportError:
        pass
    try:
        if "paddleocr" not in requested:
            raise ImportError
        from paddleocr import PaddleOCR

        started = time.perf_counter()
        engines["paddleocr"] = PaddleOCR(
            lang="en",
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        initialization["paddleocr"] = time.perf_counter() - started
        peak_rss["paddleocr"] = rss_megabytes()
    except ImportError:
        pass
    try:
        if "easyocr" not in requested:
            raise ImportError
        import easyocr

        started = time.perf_counter()
        engines["easyocr"] = easyocr.Reader(["en"], gpu=False)
        initialization["easyocr"] = time.perf_counter() - started
        peak_rss["easyocr"] = rss_megabytes()
    except ImportError:
        pass

    results: dict[str, list[tuple[float, float]]] = {name: [] for name in engines}
    for pdf_path, page_index in samples:
        document = pymupdf.open(pdf_path)
        page = document[page_index]
        reference = page.get_text()
        scale = args.dpi / 72
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        for name, engine in engines.items():
            started = time.perf_counter()
            if name == "tesseract":
                text = tesseract_ocr(image, str(engine))
            elif name == "rapidocr":
                text = rapidocr_ocr(image, engine)
            elif name == "easyocr":
                text = easyocr_ocr(image, engine)
            elif name == "paddleocr":
                text = paddleocr_ocr(image, engine)
            else:
                text = doctr_ocr(image, engine)
            elapsed = time.perf_counter() - started
            peak_rss[name] = max(peak_rss[name], rss_megabytes())
            accuracy = score(reference, text)
            results[name].append((accuracy, elapsed))
            print(f"{name}\t{pdf_path.name}\taccuracy={accuracy:.4f}\tseconds={elapsed:.3f}")
        document.close()

    print("\nSUMMARY")
    for name, values in results.items():
        print(
            f"{name}\tmean_accuracy={statistics.mean(v[0] for v in values):.4f}"
            f"\tmean_seconds={statistics.mean(v[1] for v in values):.3f}"
            f"\tinitialization_seconds={initialization[name]:.3f}"
            f"\tpeak_process_rss_mb={peak_rss[name]:.1f}"
        )


if __name__ == "__main__":
    main()
