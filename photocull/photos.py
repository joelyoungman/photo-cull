"""Finding the photos in a shoot folder and reading them.

A "shot" is one exposure: a RAW file and its JPEG twin (same name, same folder) are one shot.
Originals are only ever opened for reading. RAW files are never fully decoded: we use the JPEG
preview the camera stores inside them (Sony ARW ~1600 px, Panasonic RW2 ~1900 px), or the JPEG twin.
"""
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
Image.MAX_IMAGE_PIXELS = None  # the photographer's own files, not untrusted uploads

JPEG_EXTS = {".jpg", ".jpeg"}
IMAGE_EXTS = JPEG_EXTS | {".heic", ".heif", ".png", ".tif", ".tiff"}
RAW_EXTS = {".arw", ".rw2", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".orf"}
SKIP_DIRS = {".photocull", "selects"}  # our own state, and copies we made (compared lower-case)


@dataclass
class Shot:
    key: str  # path of the shot relative to the shoot folder, without extension: "card1/P1000123"
    files: list = field(default_factory=list)  # every file of the shot, relative paths

    @property
    def raw(self):
        return next((f for f in self.files if Path(f).suffix.lower() in RAW_EXTS), None)

    @property
    def image(self):
        """The JPEG/HEIC twin, if there is one."""
        order = sorted((f for f in self.files if Path(f).suffix.lower() in IMAGE_EXTS),
                       key=lambda f: Path(f).suffix.lower() not in JPEG_EXTS)
        return order[0] if order else None

    @property
    def source(self):
        """The file we judge the shot by: the camera's JPEG beats a RAW's smaller preview."""
        return self.image or self.raw


def find_shots(root):
    """{key: Shot} for every photo under root (sub-folders too, e.g. one per memory card)."""
    root = Path(root)
    shots = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.lower() in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        ext = path.suffix.lower()
        if ext not in IMAGE_EXTS and ext not in RAW_EXTS or not path.is_file():
            continue
        key = rel.with_suffix("").as_posix()
        shots.setdefault(key, Shot(key)).files.append(rel.as_posix())
    return shots


def _raw_preview(path):
    """(JPEG bytes of the preview inside a RAW file, full sensor width, height)."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        size = (raw.sizes.width, raw.sizes.height)
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            thumb = None
        if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
            return thumb.data, size
        # Some phone DNGs carry no usable preview: a quick half-size decode is the last resort.
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=92)
    return buf.getvalue(), size


def open_photo(path):
    """(RGB PIL image upright, at the largest size we have cheaply, original width, height, exif source bytes or None).
    Raises on unreadable or damaged files."""
    path = Path(path)
    exif_bytes = None
    if path.suffix.lower() in RAW_EXTS:
        data, size = _raw_preview(path)
        img = Image.open(io.BytesIO(data))
        exif_bytes = data  # Panasonic keeps full EXIF in the preview; useful when the RAW's own isn't readable
    else:
        img = Image.open(path)
        size = img.size
    img.draft("RGB", (2400, 2400))  # JPEG: decode at reduced size when it's much bigger, far faster
    img = ImageOps.exif_transpose(img).convert("RGB")
    if path.suffix.lower() not in RAW_EXTS and img.width < img.height and size[0] > size[1]:
        size = size[::-1]
    return img, size, exif_bytes


def shrink(img, max_px):
    out = img.copy()
    out.thumbnail((max_px, max_px), Image.LANCZOS)
    return out


# ---------- EXIF ----------

def _date(v, subsec=None):
    """EXIF '2026:10:24 14:11:12' (+ subseconds '35') -> datetime, or None for blanks."""
    m = re.match(r"(\d{4})[:-](\d\d)[:-](\d\d)[ T](\d\d):(\d\d):(\d\d)", v or "")
    if not m or not 1980 <= int(m[1]) <= 2100 or m[2] == "00":
        return None
    try:
        t = datetime(*(int(x) for x in m.groups()))
    except ValueError:
        return None
    digits = re.sub(r"\D", "", subsec or "")
    if digits:
        t = t.replace(microsecond=int((digits + "000000")[:6]))
    return t


def read_exif(path=None, data=None):
    """{taken, camera, serial, iso, exposure, fnumber, focal}; missing tags are None. Never raises."""
    import exifread

    try:
        if data is not None:
            tags = exifread.process_file(io.BytesIO(data), details=False)
        else:
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)
    except Exception:
        return {}

    def text(key):
        v = tags.get(key)
        v = str(v).strip().strip("\x00").strip() if v is not None else ""
        return v or None

    def number(key):
        try:
            return float(tags[key].values[0])
        except (KeyError, IndexError, ValueError, ZeroDivisionError, TypeError):
            return None

    make, model = text("Image Make"), text("Image Model")
    camera = model
    if make and model and make.split()[0].lower() not in model.lower():
        camera = f"{make.split()[0].title()} {model}"
    taken = _date(text("EXIF DateTimeOriginal"), text("EXIF SubSecTimeOriginal")) or _date(text("Image DateTime"))
    iso = number("EXIF ISOSpeedRatings")
    return {
        "taken": taken.isoformat() if taken else None,
        "camera": camera or make,
        "serial": text("EXIF BodySerialNumber"),
        "iso": int(iso) if iso else None,
        "exposure": text("EXIF ExposureTime"),
        "fnumber": number("EXIF FNumber"),
        "focal": number("EXIF FocalLength"),
    }


def shot_exif(root, shot, preview_bytes=None):
    """EXIF for a shot: the JPEG twin first, then the RAW, then the RAW's preview (Panasonic RW2)."""
    root = Path(root)
    best = {}
    for source in (shot.image, shot.raw):
        if source:
            best = read_exif(root / source)
            if best.get("taken"):
                return best
    if preview_bytes:
        found = read_exif(data=preview_bytes)
        if found.get("taken"):
            return found
    return best
