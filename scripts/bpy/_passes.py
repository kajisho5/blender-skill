"""bpy side of render.py's frame-by-frame output (docs/decisions.md ADR-2): render passes into a
multilayer EXR or cut into one PNG per pass, and a premultiplied PNG beauty.

The plan comes from the host (scripts/_render_output.py `_frame_plan`), so every path and
setting is visible in `--dry-run`. Each frame is rendered once; the Render Result is then saved
once per output with `save_render` (the scene's view transform for a PNG, linear data for an
EXR). Per-pass PNGs are cut from an uncompressed float EXR of that same render, which is deleted
afterwards -- Blender's Python API has no way to read one pass of a Render Result directly.
"""
from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import bpy
import numpy as np

_spec = importlib.util.spec_from_file_location("_exr", str(Path(__file__).resolve().parent.parent / "_exr.py"))
_exr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_exr)

# multilayer EXR layer names, Blender 4.x and 5.x spellings (see scripts/_render_output.py PASSES)
_LAYERS = {"shadow": ("Shadow",), "ao": ("AO", "Ambient Occlusion"), "mist": ("Mist",),
           "object_index": ("IndexOB", "Object Index")}


def write_png(path: str, array, bit_depth: int) -> None:
    """Write a (height, width[, channels]) unsigned array as a PNG: 1 channel gray, 3 RGB, 4 RGBA;
    8 or 16 bits; filter 0 on every row, row 0 at the top."""
    arr = np.asarray(array)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    height, width, channels = arr.shape
    ctype = {1: 0, 3: 2, 4: 6}[channels]
    dtype = np.dtype(">u2") if bit_depth == 16 else np.dtype("u1")
    rows = arr.astype(dtype).reshape(height, width * channels).view(np.uint8).reshape(height, -1)
    raw = np.concatenate([np.zeros((height, 1), dtype=np.uint8), rows], axis=1).tobytes()

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, ctype, 0, 0, 0)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6))
                           + chunk(b"IEND", b""))


def premultiply_png(path: str) -> None:
    """Rewrite an 8-bit straight-alpha RGBA PNG with its colour multiplied by its alpha
    (round(c * a / 255)). A pixel with alpha 255 keeps its colour exactly."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = img.size
        px = np.array(img.pixels[:], dtype=np.float32).reshape(height, width, img.channels)
    finally:
        bpy.data.images.remove(img)
    if px.shape[2] != 4:
        raise RuntimeError(f"{path} has no alpha channel to premultiply")
    straight = np.rint(np.flipud(px) * 255.0).astype(np.uint32)
    alpha = straight[:, :, 3:4]
    rgb = (straight[:, :, :3] * alpha + 127) // 255
    write_png(path, np.concatenate([rgb, alpha], axis=2), 8)


def _find(channels, layer_names):
    for name in sorted(channels):
        full, _, comp = name.rpartition(".")
        if full.rpartition(".")[2] in layer_names:
            yield comp, channels[name]


def write_pass_pngs(exr_path: str, targets) -> list:
    """Cut each pass of an uncompressed EXR into its PNG (see PASSES' `png` encodings);
    returns the EXR's layer names."""
    channels = _exr.read_scanlines(exr_path)
    for name, path in targets.items():
        comps = dict(_find(channels, _LAYERS[name]))
        if not comps:
            raise RuntimeError(f"the {name} pass is not in the render ({exr_path})")
        if name == "object_index":
            write_png(path, np.clip(np.rint(comps["X"]), 0, 65535), 16)
        elif name == "mist":
            write_png(path, np.rint(np.clip(comps["Z"], 0.0, 1.0) * 65535.0), 16)
        else:
            rgb = np.stack([comps["R"], comps["G"], comps["B"]], axis=2)
            write_png(path, np.rint(np.clip(rgb, 0.0, 1.0) * 65535.0), 16)
    return sorted(_exr.layers(_exr.read_header(exr_path)["channels"]))


def _assign_object_indices():
    """Give each mesh object its own pass index (1, 2, ... in name order) when none has one, so
    the object_index pass tells the objects apart; returns {object name: index}."""
    meshes = sorted((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: o.name)
    if meshes and all(o.pass_index == 0 for o in meshes):
        for i, o in enumerate(meshes, start=1):
            o.pass_index = i
    return {o.name: o.pass_index for o in meshes}


def _setup_mist(scene, center, size):
    """Mist from just in front of the model to just behind it, linear, so the pass spans 0..1
    over the model instead of Blender's fixed 5..30 m default."""
    if scene.world is None:
        world = bpy.data.worlds.new("render_world")
        world.color = (0.0, 0.0, 0.0)  # what a scene without a world renders: no ambient light
        scene.world = world
    distance = (scene.camera.location - center).length if scene.camera else size * 2
    mist = scene.world.mist_settings
    mist.start = max(0.0, distance - size)
    mist.depth = max(2.0 * size, 1e-3)
    mist.falloff = "LINEAR"
    return {"start": mist.start, "depth": mist.depth, "falloff": "LINEAR"}


def render_plan(scene, plan, apply_image_settings, center, size):
    """Render every frame of `plan` and write its outputs; returns the result fields to add."""
    view_layer = bpy.context.view_layer
    for prop in plan["properties"].values():
        setattr(view_layer, prop, True)
    extra = {}
    if "object_index" in plan["passes"]:
        extra["object_index_map"] = _assign_object_indices()
    if "mist" in plan["passes"]:
        extra["mist"] = _setup_mist(scene, center, size)

    def at(pattern, frame):
        return pattern % frame if plan["sequence"] else pattern

    layers = None
    for frame in plan["frames"]:
        scene.frame_set(frame)
        bpy.ops.render.render()
        result = bpy.data.images["Render Result"]
        if plan["beauty"]:
            apply_image_settings(scene, plan["beauty"]["image_settings"])
            path = at(plan["beauty"]["path"], frame)
            result.save_render(path, scene=scene)
            if plan["premultiply_png"]:
                premultiply_png(path)
        if plan["exr"]:
            apply_image_settings(scene, plan["exr"]["image_settings"])
            exr_path = at(plan["exr"]["path"], frame)
            result.save_render(exr_path, scene=scene)
            if plan["pass_pngs"]:
                layers = write_pass_pngs(exr_path, {n: at(p, frame) for n, p in plan["pass_pngs"].items()})
            if not plan["exr"]["keep"]:
                Path(exr_path).unlink(missing_ok=True)
    if layers is not None:
        extra["exr_layers"] = layers
    return extra
