"""CMM 1.1 ZIP 容器；成员只在内存读取，不解包到磁盘。"""
from __future__ import annotations

import io
import json
import math
import zipfile
import zlib
from PIL import Image


def read_archive(path, *, on_warning=None):
    with open(path, "rb") as source:
        if source.read(2) != b"PK":
            return None
    entries = {}
    satellite_error = None
    limits = {"city.json": 512 * 1024**2, "satellite.png": 256 * 1024**2,
              "edits.json": 64 * 1024**2}
    for name in ("land", "water", "water_color_reference", "water_black_reference", "water_white_reference"):
        limits[f"satellite/{name}.png"] = 256 * 1024**2
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.filename not in limits:
                    continue
                try:
                    if entry.filename in entries or entry.is_dir():
                        raise ValueError("Duplicate or invalid CMM member: " + entry.filename)
                    if entry.file_size > limits[entry.filename]:
                        raise ValueError("CMM member exceeds size limit: " + entry.filename)
                    data = archive.read(entry)
                    if entry.filename == "edits.json":
                        data = json.loads(data.decode("utf-8-sig"))
                        if not isinstance(data, dict):
                            raise ValueError("Expected an edits object")
                    entries[entry.filename] = data
                except (ValueError, zipfile.BadZipFile, zlib.error, EOFError, RecursionError) as error:
                    if on_warning is None or entry.filename == "city.json":
                        raise
                    entries[entry.filename] = None
                    if entry.filename == "edits.json":
                        on_warning("READ_EDITS_SKIPPED", f"edits.json: {error}")
                    else:
                        satellite_error = f"{entry.filename}: {error}"
    except (zipfile.BadZipFile, zlib.error, EOFError) as error:
        raise ValueError("Invalid CMM archive") from error
    if "city.json" not in entries:
        raise ValueError("CMM archive is missing city.json")
    text = entries["city.json"].decode("utf-8-sig")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema_version") != "cmm1.1":
        raise ValueError("CMM ZIP requires schema_version cmm1.1")
    try:
        if satellite_error is not None:
            raise ValueError(satellite_error)
        png, info, layer_bytes = _satellite(raw.get("satellite"), entries)
    except (ValueError, OSError, SyntaxError, OverflowError, Image.DecompressionBombError) as error:
        if on_warning is None:
            raise
        on_warning("READ_SATELLITE_SKIPPED", str(error))
        png, info, layer_bytes = None, None, {}
    return text, png, info, entries.get("edits.json"), layer_bytes


def _satellite(info, entries):
    png = entries.get("satellite.png")
    layers = info.get("layers") if isinstance(info, dict) else None
    if layers is None and any(name.startswith("satellite/") for name in entries):
        raise ValueError("CMM satellite layers have no metadata")
    if (png is None and layers is None) != (info is None):
        raise ValueError("CMM satellite image and coordinates must both be present")
    if info is not None:
        bounds = info.get("world_bounds_xz") if isinstance(info, dict) else None
        if (not isinstance(bounds, list) or len(bounds) != 4
                or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]):
            raise ValueError("Invalid CMM satellite coordinates")
    layer_bytes = {}
    if layers is not None:
        if png is not None or not isinstance(layers, dict) or not {"land", "water"} <= layers.keys():
            raise ValueError("Invalid CMM satellite layers")
        if bounds[2] - bounds[0] != bounds[3] - bounds[1]:
            raise ValueError("CMM satellite layers require square world bounds")
        dimensions = None
        for name, member in layers.items():
            if member != f"satellite/{name}.png" or member not in entries:
                raise ValueError(f"Missing or invalid CMM satellite member: {name}")
            data = entries[member]
            with Image.open(io.BytesIO(data)) as image:
                if (image.format != "PNG" or image.mode not in ("RGB", "RGBA")
                        or (name == "water" and image.mode != "RGBA")
                        or (name == "land" and image.mode != "RGB")
                        or image.width != image.height or image.width not in (4096, 8192)
                        or (dimensions is not None and image.size != dimensions)):
                    raise ValueError(f"Invalid CMM satellite layer: {name}")
                dimensions = image.size
                image.verify()
            layer_bytes[name] = data
        target = info.get("output_size")
        if type(target) is not int or target not in (4096, 8192) or target > dimensions[0]:
            raise ValueError("Invalid CMM satellite output size")
    if png is not None:
        with Image.open(io.BytesIO(png)) as image:
            if (image.format != "PNG" or image.mode not in ("RGB", "RGBA")
                    or image.width != image.height or image.width > 8192):
                raise ValueError("Invalid CMM satellite PNG")
            target = info.get("output_size")
            if target is not None and (type(target) is not int or target not in (4096, 8192)
                                       or image.size != (target, target)):
                raise ValueError("Invalid CMM satellite output size")
            image.verify()
    return png, info, layer_bytes


def write_archive(document, path):
    """保留随地图加载的影像和编辑数据，避免另存时丢失。"""
    import os
    import tempfile
    from pathlib import Path
    path = Path(path)
    raw = document.to_dict()
    raw["schema_version"] = "cmm1.1"
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as temporary:
        temp = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("city.json", json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            if document._satellite_png is not None:
                archive.writestr("satellite.png", document._satellite_png, compress_type=zipfile.ZIP_STORED)
            for name, data in document._satellite_layers.items():
                archive.writestr(document._satellite_info["layers"][name], data, compress_type=zipfile.ZIP_STORED)
            if document._archive_edits is not None:
                archive.writestr("edits.json", json.dumps(document._archive_edits, ensure_ascii=False))
        read_archive(temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
