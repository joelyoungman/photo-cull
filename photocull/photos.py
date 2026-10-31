"""Finding the photos in a shoot folder and reading them.

A "shot" is one exposure: a RAW file and its JPEG twin (same name, same folder) are one shot.
Originals are only ever opened for reading. RAW files are never fully decoded: we use the JPEG
preview the camera stores inside them (Sony ARW ~1600 px, Panasonic RW2 ~1900 px), or the JPEG twin.
"""
import io
import re
import struct
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
SKIP_DIRS = {".photocull", "selects", "deletes"}  # our own state, copies we made, photos set aside (lower-case)


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


# LibRaw's flip code for how the camera was held -> the PIL transpose that turns the preview upright
_FLIP = {3: Image.Transpose.ROTATE_180, 5: Image.Transpose.ROTATE_90, 6: Image.Transpose.ROTATE_270}


def _raw_preview(path):
    """(JPEG bytes of the preview inside a RAW file, full sensor width, height, LibRaw flip code).
    The flip says how the camera was held; previews often don't carry it themselves."""
    import rawpy

    with rawpy.imread(str(path)) as raw:
        size = (raw.sizes.width, raw.sizes.height)
        flip = raw.sizes.flip
        try:
            thumb = raw.extract_thumb()
        except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
            thumb = None
        if thumb is not None and thumb.format == rawpy.ThumbFormat.JPEG:
            return thumb.data, size, flip
        # Some phone DNGs carry no usable preview: a quick half-size decode is the last resort (already upright).
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, no_auto_bright=False, output_bps=8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=92)
    return buf.getvalue(), size, 0


def open_photo(path):
    """(RGB PIL image upright, at the largest size we have cheaply, original width, height, exif source bytes or None).
    Raises on unreadable or damaged files."""
    path = Path(path)
    exif_bytes, flip = None, 0
    if path.suffix.lower() in RAW_EXTS:
        data, size, flip = _raw_preview(path)
        img = Image.open(io.BytesIO(data))
        exif_bytes = data  # Panasonic keeps full EXIF in the preview; useful when the RAW's own isn't readable
    else:
        img = Image.open(path)
        size = img.size
    tagged = img.getexif().get(0x0112, 1) != 1  # the preview/JPEG says how to turn itself upright
    img.draft("RGB", (2400, 2400))  # JPEG: decode at reduced size when it's much bigger, far faster
    img = ImageOps.exif_transpose(img).convert("RGB")
    if not tagged and flip in _FLIP:  # RAW preview stored sideways, orientation only in the RAW itself
        img = img.transpose(_FLIP[flip])
    if (img.width < img.height) != (size[0] < size[1]):
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
    # Panasonic RW2 files keep ISO in their own tag 0x0017; newer cameras may use the EXIF 2.3 names
    iso = (number("EXIF ISOSpeedRatings") or number("EXIF PhotographicSensitivity")
           or number("EXIF RecommendedExposureIndex") or number("Image Tag 0x0017"))
    return {
        "taken": taken.isoformat() if taken else None,
        "camera": camera or make,
        "serial": text("EXIF BodySerialNumber"),
        "iso": int(iso) if iso else None,
        "exposure": text("EXIF ExposureTime"),
        "fnumber": number("EXIF FNumber"),
        "focal": number("EXIF FocalLength"),
    }


# ---------- where the camera focused ----------

_TIFF_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def _ifd(buf, base, offset, e):
    """{tag: (type, value bytes)} for the TIFF directory at base + offset."""
    out = {}
    n = struct.unpack_from(e + "H", buf, base + offset)[0]
    for i in range(min(n, 1000)):
        p = base + offset + 2 + 12 * i
        tag, kind, count = struct.unpack_from(e + "HHI", buf, p)
        size = _TIFF_SIZES.get(kind, 1) * count
        at = p + 8 if size <= 4 else base + struct.unpack_from(e + "I", buf, p + 8)[0]
        out[tag] = (kind, buf[at:at + size])
    return out


def focus_point(data):
    """(x, y) as fractions of the upright photo where the camera focused, from the maker notes of a Sony (ARW or
    its JPEG) or Panasonic (JPEG, or a RW2's preview) photo; None when the camera doesn't say. data: the file's
    first bytes (or a preview JPEG). Never raises."""
    try:
        base = 0 if data[:4] in (b"II*\x00", b"MM\x00*") else data.find(b"Exif\x00\x00", 0, 65536) + 6
        if base < 6 and base != 0:
            return None
        e = "<" if data[base:base + 2] == b"II" else ">"
        top = _ifd(data, base, struct.unpack_from(e + "I", data, base + 4)[0], e)
        orientation = struct.unpack(e + "H", top[0x0112][1][:2])[0] if 0x0112 in top else 1
        exif = _ifd(data, base, struct.unpack_from(e + "I", top[0x8769][1])[0], e)
        note = exif[0x927C][1]
        at = data.find(note[:64], base)
        if note.startswith(b"Panasonic"):
            kind, v = _ifd(data, base, at + 12 - base, e).get(0x004D, (0, b""))  # AFPointPosition, two rationals
            n = struct.unpack(e + "4I", v)
            x, y = n[0] / n[1], n[2] / n[3]
        elif note.startswith(b"SONY") or top.get(0x010F, (0, b""))[1].upper().startswith(b"SONY"):
            # Sony's maker note is a bare directory (older bodies prefix "SONY DSC"); FocusLocation is
            # width, height, x, y in sensor pixels
            tags = _ifd(data, base, at + (12 if note.startswith(b"SONY") else 0) - base, e)
            w, h, px, py = struct.unpack(e + "4H", tags[0x2027][1])
            if not (w and h and (px, py) != (0, 0)):
                return None
            x, y = px / w, py / h
        else:
            return None
    except Exception:
        return None
    if not (0 < x < 1 and 0 < y < 1):
        return None
    # the camera reports it on the sensor; turn it the way the photo is shown
    x, y = {3: (1 - x, 1 - y), 6: (1 - y, x), 8: (y, 1 - x)}.get(orientation, (x, y))
    return round(x, 4), round(y, 4)


def shot_focus_point(root, shot, preview_bytes=None):
    """Where the camera focused for a shot: from the JPEG twin, the RAW, or the RAW's preview."""
    for source in (shot.image, shot.raw):
        if source:
            try:
                with open(Path(root) / source, "rb") as f:
                    found = focus_point(f.read(1 << 20))
            except OSError:
                found = None
            if found:
                return found
    return focus_point(preview_bytes) if preview_bytes else None


def shot_exif(root, shot, preview_bytes=None):
    """EXIF for a shot: the JPEG twin first, then the RAW, then the RAW's preview (Panasonic RW2). A setting one of
    them leaves out is taken from the next."""
    root = Path(root)
    found = [read_exif(root / source) for source in (shot.image, shot.raw) if source]
    if preview_bytes:
        found.append(read_exif(data=preview_bytes))
    found = [f for f in found if f]
    found.sort(key=lambda f: not f.get("taken"))  # a file with no date taken is likely missing the rest too
    best = {}
    for f in found:
        for k, v in f.items():
            if best.get(k) is None:
                best[k] = v
    return best
