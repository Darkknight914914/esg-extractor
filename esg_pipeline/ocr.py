"""OCR for image-only pages (scanned reports, tables pasted in as pictures).

Engines, tried in this order (override with ESG_OCR=rapidocr|tesseract|off):
  1. RapidOCR  - a pip package (`rapidocr_onnxruntime`) with the models bundled in.
                 Nothing to install outside Python, works on Windows/Mac/Linux.
  2. Tesseract - classic engine; needs the separate Tesseract program installed.
If neither is available, image pages are skipped (and reported as skipped).

RapidOCR returns text boxes, not lines. `boxes_to_text` rebuilds reading order:
boxes on the same visual line are joined, and wide horizontal gaps become
" | " so table rows look like the rows pdfplumber produces for digital PDFs.
That keeps the label and its values on one line for the extractors.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

RESOLUTION = 300            # dpi for OCR; 220 dpi dropped decimal points in tests (4.1 -> 44)
WINDOWS_TESSERACT = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                     r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]

_ENGINE: str | None | bool = False      # False = not probed yet
_RAPID = None


# ----------------------------------------------------------------- engine discovery
def _load_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR          # 1.x package
    except ImportError:
        try:
            from rapidocr import RapidOCR                  # 2.x package
        except ImportError:
            return None
    return RapidOCR()


def _tesseract_ok() -> bool:
    try:
        import pytesseract
    except ImportError:
        return False
    cmd = os.environ.get("TESSERACT_CMD") or next((p for p in WINDOWS_TESSERACT if Path(p).exists()), None)
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def engine() -> str | None:
    """Name of the OCR engine in use ('rapidocr' / 'tesseract'), or None. Probed once."""
    global _ENGINE, _RAPID
    if _ENGINE is not False:
        return _ENGINE
    choice = os.environ.get("ESG_OCR", "auto").lower()
    _ENGINE = None
    if choice in ("auto", "rapidocr"):
        try:
            _RAPID = _load_rapidocr()
        except Exception as e:                       # model load failure etc.
            log.warning("RapidOCR could not start: %s", e)
        if _RAPID is not None:
            _ENGINE = "rapidocr"
    if _ENGINE is None and choice in ("auto", "tesseract") and _tesseract_ok():
        _ENGINE = "tesseract"
    if _ENGINE is None and choice != "off":
        log.warning("No OCR engine found - image-only pages will be skipped. "
                    "Fix: pip install rapidocr_onnxruntime")
    return _ENGINE


# ----------------------------------------------------------------- layout rebuild
def boxes_to_text(items: list[tuple[list, str]]) -> str:
    """items: [(box, text)] with box = 4 corner points [[x,y],...]. Returns lines in reading order."""
    words = []
    for box, text in items:
        if not text or not str(text).strip():
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        words.append({"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys), "t": str(text).strip()})
    if not words:
        return ""
    words.sort(key=lambda w: (w["y0"] + w["y1"]) / 2)

    lines: list[list[dict]] = []
    for w in words:
        cy, h = (w["y0"] + w["y1"]) / 2, w["y1"] - w["y0"]
        if lines:
            last = lines[-1]
            ly = sum((x["y0"] + x["y1"]) / 2 for x in last) / len(last)
            lh = sum(x["y1"] - x["y0"] for x in last) / len(last)
            if abs(cy - ly) < 0.5 * max(h, lh):          # same visual line
                last.append(w)
                continue
        lines.append([w])

    out = []
    for line in lines:
        line.sort(key=lambda w: w["x0"])
        height = sum(w["y1"] - w["y0"] for w in line) / len(line)
        text = line[0]["t"]
        for prev, cur in zip(line, line[1:]):
            gap = cur["x0"] - prev["x1"]
            text += (" | " if gap > 1.8 * height else " ") + cur["t"]
        out.append(text)
    return "\n".join(out)


def _rapid_items(result) -> list[tuple[list, str]]:
    if result is None:
        return []
    if isinstance(result, tuple):                       # 1.x: (list | None, elapse)
        result = result[0] or []
    if hasattr(result, "boxes") and hasattr(result, "txts"):   # 2.x result object
        if result.boxes is None:
            return []
        return [(b.tolist() if hasattr(b, "tolist") else b, t) for b, t in zip(result.boxes, result.txts)]
    return [(r[0], r[1]) for r in result]               # 1.x rows: [box, text, score]


# ----------------------------------------------------------------- public API
def read_page(page) -> str:
    """OCR one pdfplumber page with the active engine."""
    name = engine()
    if name is None:
        raise RuntimeError("no OCR engine available")
    image = page.to_image(resolution=RESOLUTION).original.convert("RGB")
    if name == "rapidocr":
        import numpy as np
        return boxes_to_text(_rapid_items(_RAPID(np.array(image))))
    import pytesseract
    return pytesseract.image_to_string(image, config="--psm 6")
