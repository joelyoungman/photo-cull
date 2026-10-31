"""How good one photo is: sharpness, exposure, and for each face whether it's in focus, eyes open, smiling.

Faces are found with OpenCV's YuNet (MIT licence) and read with Google's MediaPipe Face Landmarker
(Apache-2.0), whose blendshape scores say how closed each eye is and how much the mouth smiles.
No one is identified: faces are only used to judge the shot.
"""
import threading
from pathlib import Path

import numpy as np
from PIL import Image

MODELS = Path(__file__).resolve().parent.parent / "models"
ANALYSE_PX = 1600  # faces are looked for on a copy this size: big enough for a group shot
SHARP_PX = 1024  # sharpness is measured at this size so scores compare across cameras
MIN_FACE_PX = 36  # at ANALYSE_PX; smaller faces are too small to judge eyes
MAIN_FACE = 0.45  # faces narrower than this share of the biggest face are background people
EYES_CLOSED = 0.5  # MediaPipe eyeBlink score above this: eye closed
SMILING = 0.45  # mean mouthSmile score above this: smiling

_local = threading.local()  # OpenCV DNN and MediaPipe objects aren't safe to share between threads


def sharpness(gray):
    """Variance of the Laplacian in the sharpest of 4x4 tiles, so a sharp subject on a soft (bokeh)
    background still counts as sharp. Higher is sharper. Fog, water and night skies score low too."""
    import cv2

    lap = cv2.Laplacian(gray, cv2.CV_32F)
    h, w = lap.shape
    tiles = [lap[y * h // 4:(y + 1) * h // 4, x * w // 4:(x + 1) * w // 4] for y in range(4) for x in range(4)]
    return float(max(t.var() for t in tiles))


def exposure(gray):
    """{mean brightness 0-255, share of near-black pixels, share of blown-out pixels}."""
    return {
        "brightness": round(float(gray.mean()), 1),
        "dark": round(float((gray < 12).mean()), 4),
        "blown": round(float((gray > 250).mean()), 4),
    }


def exposure_problem(e):
    """Plain-English exposure problem, or None."""
    if e["blown"] > 0.06:
        return "blown highlights"
    if e["brightness"] > 205:
        return "too bright"
    if e["brightness"] < 35 or (e["brightness"] < 55 and e["dark"] > 0.5):
        return "too dark"
    return None


def _models():
    if not hasattr(_local, "det"):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        _local.det = cv2.FaceDetectorYN.create(str(MODELS / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.75, 0.3, 5000)
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODELS / "face_landmarker.task")),
            output_face_blendshapes=True, num_faces=1, min_face_detection_confidence=0.3,
        )
        _local.marker = vision.FaceLandmarker.create_from_options(options)
        _local.mp = mp
    return _local.det, _local.marker, _local.mp


def _face_reading(rgb, box, marker, mp):
    """(eyes closed 0-1, smile 0-1) for one face, or (None, None) if the landmarker can't read it."""
    import cv2

    x, y, w, h = box
    side = max(w, h) * 1.8  # the landmarker wants the whole head with some margin
    cx, cy = x + w / 2, y + h / 2
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(rgb.shape[1], cx + side / 2)), int(min(rgb.shape[0], cy + side / 2))
    crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
    if crop.size == 0:
        return None, None
    if max(crop.shape[:2]) < 256:
        scale = 256 / max(crop.shape[:2])
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    result = marker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=crop))
    if not result.face_blendshapes:
        return None, None
    s = {c.category_name: c.score for c in result.face_blendshapes[0]}
    closed = (s.get("eyeBlinkLeft", 0) + s.get("eyeBlinkRight", 0)) / 2
    smile = (s.get("mouthSmileLeft", 0) + s.get("mouthSmileRight", 0)) / 2
    return round(closed, 3), round(smile, 3)


def faces(img):
    """[{box: [x, y, w, h] as fractions, main, sharp, closed, smile}] for the faces in a PIL RGB image.
    'main' faces are the people the photo is of; small faces behind them are counted but not judged."""
    import cv2

    det, marker, mp = _models()
    rgb = np.asarray(img)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = bgr.shape[:2]
    det.setInputSize((width, height))
    _, found = det.detect(bgr)
    found = [] if found is None else found
    biggest = max((float(f[2]) for f in found), default=0)
    out = []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    for f in found:
        x, y, w, h = (float(v) for v in f[:4])
        face = {"box": [round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4)],
                "main": bool(w >= MIN_FACE_PX and w >= MAIN_FACE * biggest), "sharp": None, "closed": None, "smile": None}
        if face["main"]:
            x0, y0 = max(0, int(x)), max(0, int(y))
            patch = gray[y0:int(y + h), x0:int(x + w)]
            if patch.size:  # every face scaled to 112 px so big and small faces score alike
                face["sharp"] = round(float(cv2.Laplacian(cv2.resize(patch, (112, 112)).astype(np.float32), cv2.CV_32F).var()), 1)
            face["closed"], face["smile"] = _face_reading(rgb, (x, y, w, h), marker, mp)
        out.append(face)
    return out


SFACE = ("face_recognition_sface_2021dec.onnx",
         "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
         "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79")
MIN_MATCH_PX = 32  # at the 1600 px preview; smaller faces are too blurry to tell people apart


def sface_model(folder):
    """Path to OpenCV's SFace face-recognition model (Apache-2.0, 39 MB). It's only needed for "exclude a person",
    so it's downloaded the first time that's used (and checked against its known SHA-256), not shipped in every
    install and update."""
    import hashlib
    import urllib.request

    name, url, sha = SFACE
    path = Path(folder) / name
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    from . import https_context

    with urllib.request.urlopen(url, timeout=120, context=https_context()) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != sha:
        tmp.unlink()
        raise OSError("the face-matching model didn't download correctly; try again")
    tmp.replace(path)
    return path


def face_vectors(img, boxes, model_path):
    """[unit vector, or None if too small / not found] for each face box (fractions of img, a PIL RGB image).
    YuNet finds the faces again (for the eye and mouth points SFace aligns on); each box takes the best overlap."""
    import cv2

    det, _, _ = _models()
    if getattr(_local, "rec_path", None) != str(model_path):
        _local.rec = cv2.FaceRecognizerSF.create(str(model_path), "")
        _local.rec_path = str(model_path)
    bgr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, found = det.detect(bgr)
    found = [] if found is None else found
    out = []
    for bx, by, bw, bh in boxes:
        x0, y0, x1, y1 = bx * w, by * h, (bx + bw) * w, (by + bh) * h
        best, best_iou = None, 0.3
        for f in found:
            fx0, fy0, fx1, fy1 = f[0], f[1], f[0] + f[2], f[1] + f[3]
            inter = max(0, min(x1, fx1) - max(x0, fx0)) * max(0, min(y1, fy1) - max(y0, fy0))
            iou = inter / ((x1 - x0) * (y1 - y0) + f[2] * f[3] - inter + 1e-6)
            if iou > best_iou:
                best, best_iou = f, iou
        if best is None or min(best[2], best[3]) < MIN_MATCH_PX:
            out.append(None)
            continue
        v = _local.rec.feature(_local.rec.alignCrop(bgr, best)).ravel().astype(np.float32)
        out.append(v / np.linalg.norm(v))
    return out


def embedding(img):
    """Unit vector describing what's in a small (~320 px) copy of the photo (MediaPipe image embedder, MobileNet V3, Apache-2.0).
    Close vectors mean the same kind of scene, not the same moment: grouping also needs time and pHash."""
    if not hasattr(_local, "embedder"):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        options = vision.ImageEmbedderOptions(base_options=BaseOptions(model_asset_path=str(MODELS / "mobilenet_v3_small.tflite")),
                                              l2_normalize=True)
        _local.embedder = vision.ImageEmbedder.create_from_options(options)
        _local.mp_embed = mp
    mp = _local.mp_embed
    rgb = np.ascontiguousarray(np.asarray(img))
    result = _local.embedder.embed(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    return np.asarray(result.embeddings[0].embedding, dtype=np.float32)


def phash(img):
    """64-bit perceptual hash as 16 hex digits: grayscale 32x32, DCT, the 8x8 lowest frequencies above or below
    their median. Near-identical frames differ in only a few bits."""
    import cv2

    gray = np.asarray(img.convert("L").resize((32, 32), Image.LANCZOS), dtype=np.float32)
    low = cv2.dct(gray)[:8, :8]
    bits = (low > np.median(low)).ravel()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def assess(img):
    """Everything we judge about one photo, from a PIL RGB copy ANALYSE_PX on its long side. Each smaller size
    is made from the one before, not from the full photo."""
    import cv2

    from .photos import shrink

    small = shrink(img, SHARP_PX)
    gray = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2GRAY).astype(np.float32)
    e = exposure(gray.astype(np.uint8))
    tiny = shrink(small, 320)
    return {"sharpness": round(sharpness(gray), 1), **e, "faces": faces(img),
            "phash": phash(tiny), "vec": embedding(tiny)}
