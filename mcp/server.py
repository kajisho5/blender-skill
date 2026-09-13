#!/usr/bin/env python3
"""blender-skill as an MCP server (stdio, JSON-RPC 2.0) -- standard library only.

Every public script in ../scripts becomes a tool. Unlike a structured-argument mapping (which
needs a schema per script kept in sync by hand), each tool takes one argument, `argv`: the exact
command-line arguments you would pass to that script, as a list of strings. `--json` is appended
automatically if the caller didn't include it, so results are always the script's own
machine-readable output -- there is no separate translation layer that could drift from what the
CLI actually does.

Run:
  python3 mcp/server.py
Claude Desktop / Claude Code config example:
  {"mcpServers": {"blender-skill": {"command": "python3", "args": ["/path/to/blender-skill/mcp/server.py"]}}}
  On Windows, use "python" instead of "python3" unless Python was installed from the Microsoft
  Store (a python.org install exposes python/py, not python3).
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
PROTOCOL_VERSION = "2024-11-05"

TOOLS = ["info", "convert", "optimize", "render", "look", "check", "bake", "scene", "batch", "verify"]

_DESCRIPTIONS = {
    "info": "Inspect a 3D file: object/mesh/material/texture/animation counts, unit scale, bounding box, non-manifold/duplicate-vertex checks, UV presence.",
    "convert": "Convert between 3D formats (glb/gltf/fbx/obj/stl/usd/usdz/ply/abc/.blend), optionally verifying the round-trip.",
    "optimize": "Decimate, weld vertices, recalc normals, triangulate, cap texture size, purge unused data.",
    "render": "Render a thumbnail, turntable animation, or 4-view sheet.",
    "look": "UV layout, wireframe render, texture grid, or before/after comparison image.",
    "check": "PASS/WARN/FAIL a file against a delivery target's budget (three.js, Unity, Unreal, Godot, iOS/Android AR, WebXR, 3D printing, Sketchfab).",
    "bake": "Bake a material to a texture (AO/normal/roughness/diffuse/combined), optionally atlasing several objects.",
    "scene": "Assemble a declarative scene.json (asset placement, lights, camera, background) into a render and/or exported file.",
    "batch": "Run a recipe (a chain of this skill's scripts) over every 3D file in a folder, with content-hash caching.",
    "verify": "Run the whole toolchain (info -> convert --verify -> check) over one or more files and report PASS/FAIL.",
}


def list_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "description": _DESCRIPTIONS[name] + " Pass the exact CLI arguments as `argv` (see `argv: [\"--help\"]` for the full flag list).",
            "inputSchema": {
                "type": "object",
                "properties": {"argv": {"type": "array", "items": {"type": "string"}, "description": "CLI arguments, e.g. [\"model.glb\", \"-o\", \"out.glb\"]"}},
                "required": ["argv"],
            },
        }
        for name in TOOLS
    ]


def call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    script = SCRIPTS / f"{name}.py"
    if name not in TOOLS or not script.exists():
        return {"isError": True, "content": [{"type": "text", "text": f"unknown tool {name}"}]}
    argv = [str(a) for a in (args or {}).get("argv", [])]
    if "--json" not in argv and "--help" not in argv and "-h" not in argv:
        argv = argv + ["--json"]

    import subprocess
    proc = subprocess.run([sys.executable, str(script)] + argv, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    structured = None
    try:
        structured = json.loads(stdout) if stdout.startswith("{") or stdout.startswith("[") else None
    except ValueError:
        structured = None

    if proc.returncode not in (0, 1):  # 1 is a "real" FAIL result from check/verify, not a crash
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        failed: Dict[str, Any] = {"isError": True, "content": [{"type": "text", "text": f"{name} failed (exit {proc.returncode})\n{tail}"}]}
        if isinstance(structured, dict):
            failed["structuredContent"] = structured
        return failed

    if structured is None:
        return {"content": [{"type": "text", "text": stdout or "(no output)"}]}
    return {"content": [{"type": "text", "text": json.dumps(structured, indent=2)}], "structuredContent": structured}


def _send(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _handle(msg: Dict[str, Any]) -> None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "blender-skill", "version": "0.1.0"},
        }})
    elif method == "notifications/initialized":
        pass  # no response expected for a notification
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": list_tools()}})
    elif method == "tools/call":
        params = msg.get("params", {})
        result = call_tool(params.get("name"), params.get("arguments") or {})
        _send({"jsonrpc": "2.0", "id": msg_id, "result": result})
    elif msg_id is not None:
        _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"unknown method {method}"}})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        _handle(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
