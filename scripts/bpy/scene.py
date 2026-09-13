"""bpy side of scene.py: assemble a scene from a declarative scene.json (asset placement,
lights, camera, background, render output) -- the equivalent of ffmpeg-skill's project.json,
for composing more than one asset into a single shot instead of hand-scripting bpy.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _compat

import bpy
import mathutils

_LIGHT_TYPES = {"sun", "point", "area", "spot"}


def _place_asset(spec, base_dir: Path):
    file_path = spec["file"]
    if not Path(file_path).is_absolute():
        file_path = str(base_dir / file_path)
    fmt = spec.get("format") or Path(file_path).suffix.lstrip(".").replace("glb", "gltf")
    before = set(bpy.data.objects.keys())
    _compat.import_file(file_path, fmt if fmt != "glb" else "gltf")
    new_objs = [o for name, o in ((n, bpy.data.objects[n]) for n in bpy.data.objects.keys() if n not in before)]
    roots = [o for o in new_objs if o.parent is None or o.parent not in new_objs]

    anchor = bpy.data.objects.new(f"asset_{spec.get('name', Path(file_path).stem)}", None)
    bpy.context.collection.objects.link(anchor)
    anchor.location = spec.get("position", [0, 0, 0])
    rot = spec.get("rotation", [0, 0, 0])
    anchor.rotation_euler = [math.radians(v) for v in rot]
    scale = spec.get("scale", 1.0)
    anchor.scale = (scale, scale, scale) if isinstance(scale, (int, float)) else scale
    for o in roots:
        o.parent = anchor
    return len(new_objs)


def _add_light(spec):
    kind = spec.get("type", "sun").lower()
    if kind not in _LIGHT_TYPES:
        raise ValueError(f"unknown light type {kind!r} (expected one of {sorted(_LIGHT_TYPES)})")
    data = bpy.data.lights.new(spec.get("name", kind), type=kind.upper())
    data.energy = spec.get("energy", 3.0)
    obj = bpy.data.objects.new(spec.get("name", kind), data)
    bpy.context.collection.objects.link(obj)
    obj.location = spec.get("position", [0, 0, 3])
    obj.rotation_euler = [math.radians(v) for v in spec.get("rotation", [0, 0, 0])]
    return obj


def _add_camera(spec):
    data = bpy.data.cameras.new("scene_camera")
    if spec.get("lens"):
        data.lens = spec["lens"]
    obj = bpy.data.objects.new("scene_camera", data)
    bpy.context.collection.objects.link(obj)
    obj.location = spec.get("position", [4, -4, 3])
    if "look_at" in spec:
        direction = mathutils.Vector(spec["look_at"]) - mathutils.Vector(obj.location)
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    else:
        obj.rotation_euler = [math.radians(v) for v in spec.get("rotation", [63, 0, 45])]
    bpy.context.scene.camera = obj
    return obj


def _set_background(spec, scene):
    world = scene.world or bpy.data.worlds.new("scene_world")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg and "color" in spec:
        bg.inputs[0].default_value = (*spec["color"], 1.0)
    if "strength" in spec and bg:
        bg.inputs[1].default_value = spec["strength"]


def run(args):
    spec = args["scene"]
    base_dir = Path(args["scene_dir"])
    _compat.reset_scene()

    placed = 0
    for asset in spec.get("assets", []):
        placed += _place_asset(asset, base_dir)
    for light in spec.get("lights", [{"type": "sun"}]):
        _add_light(light)
    _add_camera(spec.get("camera", {}))
    if "background" in spec:
        _set_background(spec["background"], bpy.context.scene)

    output = spec.get("output", {})
    scene = bpy.context.scene
    scene.render.engine = "CYCLES" if output.get("engine") == "cycles" else "BLENDER_EEVEE_NEXT"
    res = output.get("resolution", [512, 512])
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.film_transparent = output.get("transparent", "background" not in spec)
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = output.get("samples", 32)
    else:
        scene.eevee.taa_render_samples = output.get("samples", 32)

    render_path = args.get("output_override") or output.get("path")
    result = {"assets_placed": placed}
    if render_path:
        scene.render.filepath = render_path
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.render.render(write_still=True)
        result["rendered"] = render_path

    export_path = args.get("export")
    if export_path:
        export_fmt = args["export_format"]
        _compat.export_file(export_path, export_fmt)
        result["exported"] = export_path
    return result


if __name__ == "__main__":
    _common.main(run)
