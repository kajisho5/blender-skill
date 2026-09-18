"""bpy side of look.py: the agent's eyes. Four modes, each producing one PNG (or SVG for uv):

- uv: the UV layout, via Blender's own uv.export_layout(mode='SVG'). PNG mode of that same
  operator needs bpy.types.GPUOffScreen, which raises SystemError("GPU functions for drawing
  are not available in background mode") under `-b` -- confirmed on a real headless Blender
  4.2.23 (no GPU, software Mesa) -- so SVG is the only mode this uses in headless mode.
- wireframe: a real render (Eevee) with every mesh object's geometry replaced by a Wireframe
  modifier, non-destructively (the modifier is removed again before returning; nothing is
  written back to the source file).
- textures: a grid of every image in bpy.data.images, downscaled and composited with numpy
  (bundled with Blender's own Python -- this runs inside Blender, not the stdlib-only host).
- compare: two existing images placed side by side, for a before/after look -- also reports a
  PSNR/SSIM similarity score (see _psnr/_ssim) when both images are the same pixel dimensions.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy
import numpy as np


def _quick_render(output: str, resolution=(480, 480), samples=32):
    scene = bpy.context.scene
    scene.render.engine = _compat.eevee_engine_id()
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.filepath = output
    scene.render.image_settings.file_format = "PNG"
    scene.eevee.taa_render_samples = samples

    if scene.camera is None:
        _auto_camera_and_light(scene)
    bpy.ops.render.render(write_still=True)


def _auto_camera_and_light(scene):
    """Frame every mesh object from a 3/4 angle and add a basic sun light, for files that carry
    no camera of their own (most raw glTF/FBX asset exports)."""
    import mathutils
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH"]
    bbox_min = mathutils.Vector((float("inf"),) * 3)
    bbox_max = mathutils.Vector((float("-inf"),) * 3)
    for o in mesh_objs:
        for corner in o.bound_box:
            world = o.matrix_world @ mathutils.Vector(corner)
            bbox_min = mathutils.Vector(min(a, b) for a, b in zip(bbox_min, world))
            bbox_max = mathutils.Vector(max(a, b) for a, b in zip(bbox_max, world))
    center = (bbox_min + bbox_max) / 2 if mesh_objs else mathutils.Vector((0, 0, 0))
    size = max((bbox_max - bbox_min).length, 1.0) if mesh_objs else 2.0

    cam_data = bpy.data.cameras.new("look_camera")
    cam_obj = bpy.data.objects.new("look_camera", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    cam_obj.location = center + mathutils.Vector((size, -size, size * 0.7))
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam_obj

    light_data = bpy.data.lights.new("look_light", type="SUN")
    light_data.energy = 3.0
    light_obj = bpy.data.objects.new("look_light", light_data)
    bpy.context.collection.objects.link(light_obj)
    light_obj.rotation_euler = (0.7, 0.2, 0.5)


def _mode_uv(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    mesh_objs = [o for o in bpy.data.objects if o.type == "MESH" and o.data.uv_layers]
    if not mesh_objs:
        return {"skipped": "no mesh has a UV map"}
    for o in mesh_objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = mesh_objs[0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.export_layout(filepath=args["output"], mode="SVG", export_all=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"meshes_with_uv": len(mesh_objs)}


def _mode_wireframe(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    added = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        mod = o.modifiers.new("look_wireframe", "WIREFRAME")
        mod.thickness = 0.01
        added.append((o, mod))
    _quick_render(args["output"], samples=args.get("samples", 16))
    for o, mod in added:
        o.modifiers.remove(mod)
    return {"objects_rendered": len(added)}


def _montage(images, tile=(256, 256), columns=4):
    """A grid of RGBA float images (each already resized to `tile`) into one composited array."""
    n = len(images)
    columns = max(1, min(columns, n))
    rows = (n + columns - 1) // columns
    w, h = tile
    canvas = np.zeros((rows * h, columns * w, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    for i, img in enumerate(images):
        r, c = divmod(i, columns)
        canvas[r * h:(r + 1) * h, c * w:(c + 1) * w, :] = img
    return canvas


def _resize(pixels: np.ndarray, size) -> np.ndarray:
    """Nearest-neighbor resize -- no PIL/scipy here, just numpy index math. Good enough for a
    thumbnail contact sheet; not meant for quality-critical output."""
    h, w = pixels.shape[:2]
    tw, th = size
    ys = (np.arange(th) * h // th).clip(0, h - 1)
    xs = (np.arange(tw) * w // tw).clip(0, w - 1)
    return pixels[ys][:, xs]


def _mode_textures(args):
    _compat.reset_scene()
    _compat.import_file(args["path"], args["format"])
    # Images imported from a packed source (the common case: glTF/FBX textures embedded in the
    # file) report has_data=False until their pixels are actually decoded -- accessing .pixels
    # is what triggers that lazy load (confirmed on Blender 4.2.23), so has_data must be checked
    # *after* touching .pixels once, not before.
    imgs = []
    for img in bpy.data.images:
        if img.name in ("Render Result", "Viewer Node"):
            continue
        if len(img.pixels) and img.has_data:
            imgs.append(img)
    if not imgs:
        return {"skipped": "no textures in this file"}
    tile = (256, 256)
    tiles = []
    for img in imgs:
        px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], img.channels)
        if img.channels == 3:
            px = np.dstack([px, np.ones(px.shape[:2], dtype=np.float32)])
        elif img.channels == 1:
            px = np.dstack([px] * 3 + [np.ones(px.shape[:2], dtype=np.float32)])
        tiles.append(_resize(np.flipud(px), tile))
    canvas = _montage(tiles, tile=tile, columns=min(4, len(tiles)))
    _save_png(canvas, args["output"])
    return {"textures": len(imgs), "names": [i.name for i in imgs]}


def _load_pixels(path: str) -> np.ndarray:
    img = bpy.data.images.load(path)
    px = np.array(img.pixels[:], dtype=np.float32).reshape(img.size[1], img.size[0], img.channels)
    if img.channels == 3:
        px = np.dstack([px, np.ones(px.shape[:2], dtype=np.float32)])
    bpy.data.images.remove(img)
    return np.flipud(px)


def _to_luminance(rgba: np.ndarray) -> np.ndarray:
    """Rec. 601 luma -- SSIM is defined on a single intensity channel, not per-channel RGB."""
    return rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587 + rgba[..., 2] * 0.114


_PSNR_IDENTICAL_DB = 100.0  # sentinel for mse=0 -- see _psnr's docstring


def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    """Peak Signal-to-Noise Ratio in dB, on the RGB channels of two same-shape float32 images
    normalized to [0, 1] (MAX=1.0). The textbook definition is +inf at mse=0, but this result is
    serialized as JSON (`json.dump`'s default `allow_nan=True` writes the bare token `Infinity`,
    which Python round-trips fine but is not valid per RFC 8259 -- a coding agent parsing this
    output with a strict JSON parser, e.g. JSON.parse in Node, would throw), so identical images
    report the finite sentinel _PSNR_IDENTICAL_DB instead of float('inf')."""
    mse = float(np.mean((a[..., :3] - b[..., :3]) ** 2))
    if mse == 0.0:
        return _PSNR_IDENTICAL_DB
    return min(10.0 * math.log10(1.0 / mse), _PSNR_IDENTICAL_DB)


_SSIM_WINDOW = 7  # odd, so _box_local_mean's padding is symmetric


def _box_local_mean(img: np.ndarray, window: int) -> np.ndarray:
    """Local mean of `img` (2D) under a `window`x`window` uniform box, same shape as `img` (edge
    padding, i.e. each border pixel's window clamps to the nearest real edge pixel instead of
    reading zeros)."""
    pad = window // 2
    padded = np.pad(img, pad, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (window, window))
    return windows.mean(axis=(-1, -2))


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Structural Similarity Index of two same-shape RGBA float32 images, on Rec.601 luminance.
    Local statistics (mean/variance/covariance) use a uniform box window via _box_local_mean --
    the SSIM literature's reference implementation uses a Gaussian window instead, so this is a
    deliberate simplification to avoid adding a scipy dependency; scores trend the same way but
    won't match a scipy/skimage `structural_similarity` call bit-for-bit."""
    la = _to_luminance(a)
    lb = _to_luminance(b)
    mu_a = _box_local_mean(la, _SSIM_WINDOW)
    mu_b = _box_local_mean(lb, _SSIM_WINDOW)
    var_a = _box_local_mean(la * la, _SSIM_WINDOW) - mu_a * mu_a
    var_b = _box_local_mean(lb * lb, _SSIM_WINDOW) - mu_b * mu_b
    cov_ab = _box_local_mean(la * lb, _SSIM_WINDOW) - mu_a * mu_b
    c1 = 0.01 ** 2
    c2 = 0.03 ** 2
    ssim_map = ((2 * mu_a * mu_b + c1) * (2 * cov_ab + c2)) / (
        (mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2)
    )
    return float(np.mean(ssim_map))


def _mode_compare(args):
    a = _load_pixels(args["image_a"])
    b = _load_pixels(args["image_b"])
    score = {}
    if a.shape[:2] == b.shape[:2]:
        score["psnr"] = _psnr(a, b)
        score["ssim"] = _ssim(a, b)
    else:
        score["score_skipped"] = "images have different pixel dimensions, cannot score"
    h = max(a.shape[0], b.shape[0])
    a_fit = _resize(a, (a.shape[1], h))
    b_fit = _resize(b, (b.shape[1], h))
    canvas = np.zeros((h, a_fit.shape[1] + b_fit.shape[1] + 4, 4), dtype=np.float32)
    canvas[..., 3] = 1.0
    canvas[:, :a_fit.shape[1]] = a_fit
    canvas[:, a_fit.shape[1] + 4:] = b_fit
    _save_png(canvas, args["output"])
    return {"width": canvas.shape[1], "height": canvas.shape[0], **score}


def _save_png(pixels: np.ndarray, output: str) -> None:
    h, w = pixels.shape[:2]
    img = bpy.data.images.new("look_output", width=w, height=h, alpha=True)
    img.pixels.foreach_set(np.flipud(pixels).ravel().astype(np.float32))
    img.filepath_raw = output
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


_MODES = {"uv": _mode_uv, "wireframe": _mode_wireframe, "textures": _mode_textures, "compare": _mode_compare}


def run(args):
    mode = args["mode"]
    if mode not in _MODES:
        raise ValueError(f"unknown look mode {mode!r}")
    return {"mode": mode, **_MODES[mode](args)}


if __name__ == "__main__":
    _common.main(run)
