#!/usr/bin/env python3
"""Batch retouch prewedding / portrait photos (offline, OpenCV).

Steps applied to each photo:
  1. White balance correction (gray-world, partial strength)
  2. Auto levels + gentle local contrast (CLAHE on luminance)
  3. Skin smoothing limited to detected faces/skin, texture kept
  4. Light sharpening on non-skin areas
  5. Optional crop to an aspect ratio (centred on faces) and resize
  6. Save as high-quality JPEG

Usage examples:
  python retouch_photos.py "D:\\Photos\\Prewedding"
  python retouch_photos.py "D:\\Photos\\Prewedding" -o "D:\\Out" --smooth 50 --ratio 4:5
  python retouch_photos.py photo.jpg --long-edge 2048
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


# ---------- file IO that works with Unicode (e.g. Khmer) paths on Windows ----------
def read_image(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def write_jpeg(path: Path, img: np.ndarray, quality: int) -> None:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"cannot encode {path}")
    buf.tofile(str(path))


# ---------- adjustments ----------
def white_balance(img: np.ndarray, strength: float = 0.6) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    for ch in (1, 2):
        shift = lab[..., ch].mean() - 128.0
        lab[..., ch] -= shift * strength
    return cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def auto_tone(img: np.ndarray, contrast: float = 0.5) -> np.ndarray:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lo, hi = np.percentile(l, (0.5, 99.5))
    if hi - lo > 10:
        l = np.clip((l.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8))
    l = cv2.addWeighted(clahe.apply(l), contrast, l, 1.0 - contrast, 0)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


_FACE_CASCADE = None


def detect_faces(img: np.ndarray) -> list[tuple[int, int, int, int]]:
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        try:
            _FACE_CASCADE = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        except AttributeError:  # OpenCV build without Haar cascades
            _FACE_CASCADE = False
    if _FACE_CASCADE is False or _FACE_CASCADE.empty():
        return []
    h, w = img.shape[:2]
    scale = 1000.0 / max(h, w) if max(h, w) > 1000 else 1.0
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale != 1.0 else img
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    min_side = max(24, int(min(small.shape[:2]) * 0.04))
    faces = _FACE_CASCADE.detectMultiScale(gray, 1.1, 5, minSize=(min_side, min_side))
    return [tuple(int(v / scale) for v in f) for f in faces]


def skin_mask(img: np.ndarray, faces) -> np.ndarray:
    ycrcb = cv2.cvtColor(img, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (40, 135, 85), (255, 180, 135))
    if faces:
        # Only keep skin near faces (face, neck, shoulders) to protect clothes/background.
        region = np.zeros_like(mask)
        for x, y, w, h in faces:
            cv2.rectangle(region, (x - w // 2, y - h // 2), (x + w + w // 2, y + h * 2), 255, -1)
        mask = cv2.bitwise_and(mask, region)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    k = max(3, (min(img.shape[:2]) // 150) | 1)
    return cv2.GaussianBlur(mask, (k * 2 + 1, k * 2 + 1), 0).astype(np.float32) / 255.0


def smooth_skin(img: np.ndarray, mask: np.ndarray, strength: float) -> np.ndarray:
    if strength <= 0:
        return img
    d = max(5, min(img.shape[:2]) // 200)
    smooth = cv2.bilateralFilter(img, d, 35, d)
    # Keep a bit of the fine texture so skin does not look plastic.
    detail = img.astype(np.float32) - cv2.GaussianBlur(img, (0, 0), 1.0).astype(np.float32)
    smooth = smooth.astype(np.float32) + detail * 0.35
    alpha = (mask * strength)[..., None]
    out = img.astype(np.float32) * (1 - alpha) + smooth * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def sharpen(img: np.ndarray, mask: np.ndarray, amount: float = 0.35) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), 1.2)
    sharp = cv2.addWeighted(img, 1 + amount, blur, -amount, 0)
    alpha = (1.0 - mask)[..., None]
    out = img.astype(np.float32) * (1 - alpha) + sharp.astype(np.float32) * alpha
    return np.clip(out, 0, 255).astype(np.uint8)


def crop_ratio(img: np.ndarray, ratio: str, faces) -> np.ndarray:
    rw, rh = (float(v) for v in ratio.split(":"))
    h, w = img.shape[:2]
    if w / h > rw / rh:
        cw, ch = int(h * rw / rh), h
    else:
        cw, ch = w, int(w * rh / rw)
    if faces:
        cx = int(np.mean([x + fw / 2 for x, _, fw, _ in faces]))
        cy = int(np.mean([y + fh / 2 for _, y, _, fh in faces]))
    else:
        cx, cy = w // 2, h // 2
    x0 = int(np.clip(cx - cw // 2, 0, w - cw))
    y0 = int(np.clip(cy - ch // 3, 0, h - ch))  # faces sit in the upper third
    return img[y0:y0 + ch, x0:x0 + cw]


def resize_long_edge(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1:
        return img
    return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def retouch(img: np.ndarray, args) -> np.ndarray:
    img = white_balance(img, args.wb)
    img = auto_tone(img, args.contrast)
    faces = detect_faces(img)
    mask = skin_mask(img, faces)
    img = smooth_skin(img, mask, args.smooth / 100.0)
    img = sharpen(img, mask, args.sharpen)
    if args.ratio:
        img = crop_ratio(img, args.ratio, faces)
    if args.long_edge:
        img = resize_long_edge(img, args.long_edge)
    return img


def collect(inp: Path) -> list[Path]:
    if inp.is_file():
        return [inp]
    return sorted(p for p in inp.rglob("*") if p.suffix.lower() in IMAGE_EXTS and "_retouched" not in p.parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch retouch prewedding photos")
    ap.add_argument("input", help="photo file or folder")
    ap.add_argument("-o", "--output", help="output folder (default: <input>/_retouched)")
    ap.add_argument("--smooth", type=float, default=40, help="skin smoothing 0-100 (default 40)")
    ap.add_argument("--wb", type=float, default=0.6, help="white balance strength 0-1 (default 0.6)")
    ap.add_argument("--contrast", type=float, default=0.5, help="local contrast 0-1 (default 0.5)")
    ap.add_argument("--sharpen", type=float, default=0.35, help="sharpen amount 0-1 (default 0.35)")
    ap.add_argument("--ratio", help="crop ratio, e.g. 3:2, 4:5, 1:1, 16:9, 9:16")
    ap.add_argument("--long-edge", type=int, help="resize so the long edge is at most N px (e.g. 2048)")
    ap.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    args = ap.parse_args()

    inp = Path(args.input.strip('"'))
    if not inp.exists():
        print(f"[ERROR] not found: {inp}")
        return 1
    base = inp if inp.is_dir() else inp.parent
    out_dir = Path(args.output) if args.output else base / "_retouched"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = collect(inp)
    if not files:
        print("[ERROR] no images found")
        return 1
    failed = 0
    for i, f in enumerate(files, 1):
        img = read_image(f)
        if img is None:
            print(f"[{i}/{len(files)}] SKIP (cannot read) {f.name}")
            failed += 1
            continue
        rel = f.relative_to(base).with_suffix(".jpg")
        dst = out_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        write_jpeg(dst, retouch(img, args), args.quality)
        print(f"[{i}/{len(files)}] OK  {rel}")
    print(f"\nDone. {len(files) - failed} photo(s) saved to: {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
