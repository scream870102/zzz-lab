#!/usr/bin/env python3
"""Build compact browser data and WebP thumbnails; keep all source files intact.

Run: python tools/build_site.py (requires Pillow with WebP support).
Every build validates its image outputs before writing data/site.json.
"""
import hashlib
import io
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "characters": "characters.json", "teams": "teams.json",
    "registry": "icon-registry.json", "wengines": "wengines.json",
    "discs": "discs.json", "custom": "custom-icons.json",
}


def local_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError(f"Path escapes project: {value}")
    return path


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()
                if key != "sourceMetadata"}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def collect(value, icon_limit, images, guides, field=""):
    if isinstance(value, dict):
        for key, item in value.items():
            collect(item, icon_limit, images, guides, key)
    elif isinstance(value, list):
        for item in value:
            collect(item, icon_limit, images, guides, field)
    elif isinstance(value, str):
        if field in ("icon", "avatars") and value.startswith("assets/images/"):
            limit = 512 if field == "avatars" else icon_limit
            images[value] = max(images.get(value, 0), limit)
        elif field == "guide" and value:
            guides[value] = local_path(value).read_text(encoding="utf-8")


def thumbnail(source, limit):
    source_path = local_path(source)
    original = source_path.read_bytes()
    if source_path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        return source, len(original), len(original)
    with Image.open(io.BytesIO(original)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        original_size = image.size
        image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        image.save(encoded, "WEBP", quality=85, method=4)
        payload = encoded.getvalue()
        if len(payload) >= len(original):
            image.load()
            return source, len(original), len(original)
        digest = hashlib.sha256(source.encode() + b"\0" + payload).hexdigest()[:24]
        target = f"assets/images/web/{digest}.webp"
        destination = local_path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != payload:
            destination.write_bytes(payload)
        with Image.open(destination) as check:
            check.load()
            if check.size != image.size or max(check.size) > limit:
                raise ValueError(f"Invalid thumbnail dimensions: {target}")
            if check.width > original_size[0] or check.height > original_size[1]:
                raise ValueError(f"Thumbnail enlarged source: {target}")
            if abs(check.width * original_size[1] - check.height * original_size[0]) > max(original_size):
                raise ValueError(f"Thumbnail aspect ratio changed: {target}")
            # WebP stores alpha losslessly; verify the resized transparency plane.
            if check.convert("RGBA").getchannel("A").tobytes() != image.getchannel("A").tobytes():
                raise ValueError(f"Thumbnail transparency changed: {target}")
        return target, len(original), len(payload)


def replace_images(value, replacements, field=""):
    if isinstance(value, dict):
        return {key: replace_images(item, replacements, key)
                for key, item in value.items()}
    if isinstance(value, list):
        return [replace_images(item, replacements, field) for item in value]
    if field in ("icon", "avatars") and isinstance(value, str):
        return replacements.get(value, value)
    return value


def main():
    site = {key: clean(json.loads((ROOT / "data" / filename).read_text(encoding="utf-8")))
            for key, filename in INPUTS.items()}
    site["registry"] = {"entries": site["registry"]["entries"]}
    images, guides = {}, {}
    for key, value in site.items():
        collect(value, 960 if key in ("characters", "wengines", "discs") else 192,
                images, guides)
    replacements, before, after = {}, 0, 0
    sources = sorted(images)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(thumbnail, sources, [images[source] for source in sources])
        for source, (target, old_bytes, new_bytes) in zip(sources, results):
            replacements[source] = target
            before += old_bytes
            after += new_bytes
    site = replace_images(site, replacements)
    site["guides"] = dict(sorted(guides.items()))
    # Validate every rewritten or preserved image reference and embedded guide.
    output_images, output_guides = {}, {}
    collect(site, 960, output_images, output_guides)
    for source in output_images:
        if not local_path(source).is_file():
            raise ValueError(f"Missing output image: {source}")
    if output_guides != guides:
        raise ValueError("Guide contents changed during build")
    payload = json.dumps(site, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    output = ROOT / "data" / "site.json"
    output.write_bytes(payload)
    source_data = sum((ROOT / "data" / name).stat().st_size for name in INPUTS.values())
    print(f"JSON: {source_data:,} -> {len(payload):,} bytes (includes {len(guides)} guides)")
    print(f"Images: {len(images)} unique, {before:,} -> {after:,} bytes")
    print(f"WebP references: {sum(a != b for a, b in replacements.items())}")
    print(f"site.json sha256: {hashlib.sha256(payload).hexdigest()}")


if __name__ == "__main__":
    main()
