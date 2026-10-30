"""Make a small sample shoot for tests: no real client photos, ever.

The face is NASA's public-domain portrait of astronaut Eileen Collins (shipped with scikit-image as
astronaut.png). Scenes are drawn from random shapes. Optional RAW samples come from raw.pixls.us (CC0).

  python tests/make_samples.py OUT_DIR [--raw]
"""
import argparse
import io
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ASTRONAUT = "https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png"
RAWS = {  # CC0 samples from raw.pixls.us: one Panasonic GH5, one Sony A7 III
    "cardB/P1000123.RW2": "https://raw.pixls.us/data/Panasonic/DC-GH5/_T012010.RW2",
    "cardC/DSC09009.ARW": "https://raw.pixls.us/data/Sony/ILCE-7M3/_DSC0009.ARW",
}
T0 = datetime(2026, 10, 24, 14, 0, 0)
RIGHT_EYE = [33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7]
LEFT_EYE = [263, 466, 388, 387, 386, 385, 384, 398, 362, 382, 381, 380, 374, 373, 390, 249]


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def save(img, path, when, model="ILCE-7M3", make="SONY"):
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
    ifd[0x9291] = f"{when.microsecond // 10000:02d}"  # SubSecTimeOriginal
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=92, exif=exif)


def close_eyes(img):
    """Paint the eyelids shut, using MediaPipe's landmarks to find the eyes."""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    models = Path(__file__).resolve().parent.parent / "models"
    src = np.asarray(img)
    options = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(models / "face_landmarker.task")), num_faces=1)
    with vision.FaceLandmarker.create_from_options(options) as marker:
        lm = marker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(src))).face_landmarks[0]
    out = src.copy()
    h, w = src.shape[:2]
    for eye in (RIGHT_EYE, LEFT_EYE):
        pts = np.array([[lm[i].x * w, lm[i].y * h] for i in eye], np.float32)
        c = pts.mean(0)
        pts = ((pts - c) * [1.15, 2.4] + c).astype(np.int32)
        x, y, ww, hh = cv2.boundingRect(pts)
        skin = src[y + hh + 2:y + hh + 8, x:x + ww].reshape(-1, 3).mean(0)
        cv2.fillPoly(out, [cv2.convexHull(pts)], skin.tolist())
        cv2.ellipse(out, (x + ww // 2, y + hh // 2), (ww // 2 - 2, max(1, hh // 6)), 0, 10, 170, (50, 35, 30), 6)
    return Image.fromarray(cv2.GaussianBlur(out, (5, 5), 0))


def scene(seed, size=(1800, 1200)):
    """A made-up 'landscape': sky gradient, hills and lots of hard-edged shapes so it's clearly sharp."""
    rng = np.random.default_rng(seed)
    w, h = size
    top, bottom = rng.integers(60, 230, 3), rng.integers(30, 200, 3)
    grad = np.linspace(0, 1, h)[:, None, None]
    arr = (top * (1 - grad) + bottom * grad).astype(np.uint8).repeat(w, 1)
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    for _ in range(160):
        x, y = int(rng.integers(0, w)), int(rng.integers(h // 3, h))
        r = int(rng.integers(8, 90))
        col = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        else:
            d.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=col, outline=(0, 0, 0), width=3)
    return img


def make(out, with_raw=False):
    out = Path(out)
    face = Image.open(io.BytesIO(fetch(ASTRONAUT))).convert("RGB").resize((1536, 1536), Image.LANCZOS)
    a = out / "cardA"
    # A burst of four frames 0.4 s apart: sharp, blurred, eyes shut, sharp again (slightly re-framed).
    save(face, a / "DSC00101.JPG", T0)
    save(face.filter(ImageFilter.GaussianBlur(6)), a / "DSC00102.JPG", T0 + timedelta(seconds=0.4))
    save(close_eyes(face), a / "DSC00103.JPG", T0 + timedelta(seconds=0.8))
    save(face.crop((40, 40, 1536, 1536)).resize((1536, 1536)), a / "DSC00104.JPG", T0 + timedelta(seconds=1.2))
    # Three different scenes a couple of minutes apart, then a dark one and a blurry one of their own.
    for i in range(3):
        save(scene(i), a / f"DSC0011{i}.JPG", T0 + timedelta(minutes=2 + 2 * i))
    dark = Image.eval(scene(7), lambda v: v // 7)
    save(dark, a / "DSC00120.JPG", T0 + timedelta(minutes=12))
    save(scene(8).filter(ImageFilter.GaussianBlur(9)), a / "DSC00121.JPG", T0 + timedelta(minutes=14))
    # RAW + JPEG pair: one shot. The "RAW" here is fake bytes unless --raw brings real ones.
    if with_raw:
        for rel, url in RAWS.items():
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / rel).write_bytes(fetch(url))
    return out


def make_big(out, n):
    """n full-size (24 MP, 6000x4000) camera JPEGs in bursts of 3, for timing a realistic shoot."""
    out = Path(out)
    for i in range(n):
        img = scene(i // 3, (1800, 1200)).resize((6000, 4000), Image.BICUBIC)
        if i % 3:
            img = img.filter(ImageFilter.GaussianBlur(i % 3))
        save(img, out / f"DSC{1000 + i:05d}.JPG", T0 + timedelta(seconds=i // 3 * 60 + i % 3 * 0.3))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--raw", action="store_true", help="also download two CC0 RAW files (~50 MB)")
    ap.add_argument("--big", type=int, metavar="N", help="instead: N full-size 24 MP JPEGs, for timing")
    args = ap.parse_args()
    print(make_big(args.out, args.big) if args.big else make(args.out, args.raw))
