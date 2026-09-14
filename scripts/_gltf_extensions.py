#!/usr/bin/env python3
"""Read `extensionsUsed`/`extensionsRequired` straight out of a raw .gltf/.glb file's JSON --
never through Blender. Blender's glTF importer translates KHR_* extensions into its own
material/mesh representation on import and doesn't expose which extensions the source file
declared, so this parses the file's own JSON directly instead (stdlib only: `json`, `struct`).

Only meaningful for the gltf/glb format; callers should skip this entirely for every other
format info.py supports.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Optional

# Short, factual descriptions of the extensions a real-world glTF file is actually likely to
# carry -- what each one *is*, per the Khronos glTF extension registry naming/purpose, not a
# claim about whether this skill's own pipeline (Blender's importer, or a given engine) supports
# it. An extension not in this table is still reported, just with description=None rather than a
# guess.
KNOWN_EXTENSIONS = {
    "KHR_draco_mesh_compression": "Draco-compressed mesh data (requires a Draco decoder to read).",
    "KHR_texture_transform": "UV offset/scale/rotation applied at the texture-sampler level.",
    "KHR_texture_basisu": "KTX2/Basis Universal compressed textures.",
    "KHR_lights_punctual": "Point/spot/directional lights (glTF's core spec has no lights).",
    "KHR_materials_unlit": "A shadeless/unlit material (ignores lighting entirely).",
    "KHR_materials_pbrSpecularGlossiness": "Legacy specular/glossiness workflow, superseded by the core metallic/roughness model -- deprecated by Khronos.",
    "KHR_materials_transmission": "Physically-based light transmission (glass, thin transparent surfaces).",
    "KHR_materials_volume": "Volumetric absorption for a transmissive material (refraction, tinted glass).",
    "KHR_materials_ior": "A custom index-of-refraction value (default 1.5 otherwise).",
    "KHR_materials_specular": "Per-material control over specular reflection strength/color/tint.",
    "KHR_materials_clearcoat": "A secondary clear-coat layer (car paint, lacquered wood).",
    "KHR_materials_sheen": "A sheen/fuzz layer for cloth-like materials.",
    "KHR_materials_iridescence": "Thin-film iridescence (soap bubbles, oil slicks, some insect wings).",
    "KHR_materials_emissive_strength": "An emissive intensity multiplier beyond the core spec's clamped [0,1] range.",
    "KHR_materials_variants": "Multiple named material variants for the same mesh (e.g. color swatches).",
    "KHR_mesh_quantization": "Vertex attributes stored in smaller integer types instead of float32.",
    "KHR_texture_transform_multi_uv": "Independent transforms for the second UV channel.",
    "KHR_animation_pointer": "An animation channel targeting an arbitrary property by JSON pointer.",
    "KHR_xmp_json_ld": "Embedded XMP metadata.",
    "EXT_mesh_gpu_instancing": "Per-instance transforms for GPU-instanced repeated meshes.",
    "EXT_texture_webp": "WebP-encoded textures.",
    "EXT_meshopt_compression": "meshopt-compressed mesh/animation data (requires a meshopt decoder to read).",
}


def _read_glb_json_chunk(path: Path) -> Optional[dict]:
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            return None
        magic, _version, _length = struct.unpack("<4sII", header)
        if magic != b"glTF":
            return None
        chunk_header = f.read(8)
        if len(chunk_header) < 8:
            return None
        chunk_len, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != 0x4E4F534A:  # 'JSON' little-endian
            return None
        return json.loads(f.read(chunk_len))


def read_extensions(path: str) -> dict:
    """Returns {"used": [...], "required": [...], "descriptions": {name: text-or-None}} for a
    .gltf/.glb file, sorted for stable output. Any parse failure (malformed file, unexpected
    layout) returns empty lists rather than raising -- this is a best-effort enrichment on top
    of info.py's real, Blender-verified data, not something worth failing the whole command over.
    """
    p = Path(path)
    try:
        if p.suffix.lower() == ".glb":
            gltf = _read_glb_json_chunk(p)
        else:
            gltf = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, struct.error):
        gltf = None
    if not isinstance(gltf, dict):
        return {"used": [], "required": [], "descriptions": {}}
    used = sorted(set(gltf.get("extensionsUsed") or []))
    required = sorted(set(gltf.get("extensionsRequired") or []))
    descriptions = {name: KNOWN_EXTENSIONS.get(name) for name in used}
    return {"used": used, "required": required, "descriptions": descriptions}
