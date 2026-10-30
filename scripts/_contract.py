#!/usr/bin/env python3
"""Machine-readable contract and environment report for blender-skill (internal module, not a tool).

    python3 scripts/_contract.py --json          # skill_id, version, provides[], tools
    python3 scripts/_contract.py doctor --json   # Blender / gltf-transform / ktx found or not

The contract is in the shape the AI Video Production OS registry reads (skill_id, version,
contract_version, provides, dependencies, not_provided). The tool list is the MCP server's own
(mcp/server.py TOOLS and descriptions), so the two cannot drift apart.
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "mcp"))
import _delegate  # noqa: E402
import _render_output  # noqa: E402
import _run  # noqa: E402
import server  # noqa: E402
from _common import SkillError, emit  # noqa: E402
from _version import __version__  # noqa: E402

SKILL_ID = "blender-skill"
CONTRACT_VERSION = "1.0"
MIN_BLENDER = (4, 2)


def provides() -> List[Dict[str, str]]:
    return [{"id": f"3d.{name}", "lifecycle": "EXPERIMENTAL", "tool_id": f"{SKILL_ID}/{name}"} for name in server.TOOLS]


def tool_options() -> Dict[str, Dict[str, Any]]:
    """Enumerated option values a caller can choose from, per tool, read from the same tables the
    scripts validate against (so the contract cannot list a value the CLI would refuse)."""
    return {
        "render": {
            "--resolution-preset": {
                "choices": list(_render_output.RESOLUTION_PRESETS),
                "values": {k: {"width": w, "height": h} for k, (w, h) in _render_output.RESOLUTION_PRESETS.items()},
                "default": _render_output.DEFAULT_RESOLUTION_PRESET,
                "precedence": ["--resolution W H", "--resolution-preset NAME", "default"],
                "invalid": {"kind": "input", "exit_code": 1},
            },
        },
    }


def build() -> Dict[str, Any]:
    return {
        "skill_id": SKILL_ID,
        "id": SKILL_ID,
        "version": __version__,
        "contract_version": CONTRACT_VERSION,
        "provides": provides(),
        # No Skill dependencies: Blender is an external binary, and a turntable MP4 is encoded by
        # Blender's own FFmpeg, not ffmpeg-skill.
        "dependencies": [],
        "external_tools": {
            "required": [{"id": "blender", "min_version": ".".join(map(str, MIN_BLENDER)),
                          "located_by": ["--blender", "$BLENDER", "PATH", "the OS's usual install location"]}],
            "optional": [{"id": "gltf-transform", "used_for": "Draco/meshopt geometry compression", "install": _delegate.GLTF_TRANSFORM_INSTALL},
                         {"id": "ktx", "used_for": "KTX2/Basis texture compression", "install": _delegate.KTX_INSTALL}],
        },
        "not_provided": ["interactive modeling", "video editing or encoding through ffmpeg-skill",
                         "network access of any kind", "API keys or cloud rendering"],
        "tools": [dict({"tool_id": f"{SKILL_ID}/{name}", "script": f"scripts/{name}.py", "description": server._DESCRIPTIONS[name]},
                       **({"options": tool_options()[name]} if name in tool_options() else {}))
                  for name in server.TOOLS],
        "execution": {"canonical_invocation": ["python3", "scripts/<tool>.py", "...", "--json"], "network": False, "shell": False,
                      "engine": "blender -b --factory-startup (headless), arguments and results as JSON files"},
        "errors": {"input": 1, "internal": 1, "missing_tool": 2, "timeout": 3},
    }


def _blender_version(binary: str) -> Any:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Blender (\d+)\.(\d+)(?:\.(\d+))?", out)
    return m.group(0).split()[1] if m else None


def doctor(blender: Any = None) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {
        "python": {"status": "ok" if sys.version_info >= (3, 9) else "fail", "detail": platform.python_version()},
    }
    try:
        path = _run.find_blender(blender)
        ver = _blender_version(path)
        nums = tuple(int(x) for x in ver.split(".")[:2]) if ver else None
        checks["blender"] = {"status": "ok" if nums is None or nums >= MIN_BLENDER else "fail", "path": path, "version": ver,
                             "detail": f"needs {'.'.join(map(str, MIN_BLENDER))}+"}
    except SkillError as e:
        checks["blender"] = {"status": "fail", "detail": e.message, "hint": e.hint}
    for tool, install in (("gltf-transform", _delegate.GLTF_TRANSFORM_INSTALL), ("ktx", _delegate.KTX_INSTALL)):
        found = shutil.which(tool)
        checks[tool] = {"status": "ok" if found else "degraded", "path": found, "detail": None if found else f"optional; {install}"}
    return {"skill_id": SKILL_ID, "version": __version__, "ok": all(c["status"] != "fail" for c in checks.values()), "checks": checks}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        ap = argparse.ArgumentParser(description="blender-skill environment report")
        ap.add_argument("--json", action="store_true", help="print the report as JSON")
        ap.add_argument("--blender", help="path to the Blender executable (overrides $BLENDER and PATH search)")
        a = ap.parse_args(sys.argv[2:])
        d = doctor(a.blender)
        emit(d, a.json, "\n".join(f"{k}: {v['status']} {v.get('path') or v.get('detail') or ''}" for k, v in d["checks"].items()))
        return 0 if d["ok"] else 2
    ap = argparse.ArgumentParser(description="blender-skill machine-readable contract")
    ap.add_argument("--json", action="store_true", help="print the contract as JSON (the default output)")
    ap.parse_args()
    print(json.dumps(build(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
