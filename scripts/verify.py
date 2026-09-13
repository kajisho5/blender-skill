#!/usr/bin/env python3
"""Run the whole toolchain (info -> convert round-trip --verify -> check) over one or more
real files and report PASS/FAIL per file. Pair with tests/corpus.py to run this against a
downloaded real-world corpus rather than just the local sample files.

Usage:
  python3 scripts/verify.py model.glb another.fbx
  python3 scripts/verify.py tests/corpus/*.glb --target three.js
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
import _formats

HERE = Path(__file__).resolve().parent


def _run(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def verify_one(path: Path, target: str, tmp_dir: Path) -> dict:
    steps = []

    rc, out, err = _run([sys.executable, str(HERE / "info.py"), str(path), "--json"])
    steps.append({"step": "info", "ok": rc == 0, "detail": err.strip().splitlines()[-1] if rc else None})
    if rc != 0:
        return {"file": str(path), "ok": False, "steps": steps}

    roundtrip = tmp_dir / f"{path.stem}_verify.glb"
    rc, out, err = _run([sys.executable, str(HERE / "convert.py"), str(path), "-o", str(roundtrip), "--verify", "--json"])
    ok = rc == 0
    detail = None
    if out:
        try:
            data = json.loads(out)
            detail = data.get("verify", {}).get("diffs")
        except json.JSONDecodeError:
            pass
    steps.append({"step": "convert --verify", "ok": ok, "detail": detail})

    rc, out, err = _run([sys.executable, str(HERE / "check.py"), str(path), "--target", target, "--json"])
    check_ok = rc == 0
    detail = None
    if out:
        try:
            detail = json.loads(out).get("overall")
        except json.JSONDecodeError:
            pass
    steps.append({"step": f"check --target {target}", "ok": check_ok, "detail": detail})

    return {"file": str(path), "ok": all(s["ok"] for s in steps[:2]), "steps": steps}  # a check WARN/FAIL doesn't fail verify.py itself


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--target", default="three.js", choices=["three.js", "unity", "unreal", "godot", "ios-ar", "android-ar", "webxr", "3d-print", "sketchfab"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import tempfile
    results = []
    with tempfile.TemporaryDirectory(prefix="blender-skill-verify-") as tmp:
        tmp_dir = Path(tmp)
        for f in args.files:
            path = Path(f)
            if not path.exists() or _formats.detect_format(str(path)) is None:
                results.append({"file": f, "ok": False, "steps": [{"step": "input", "ok": False, "detail": "missing or unrecognized extension"}]})
                continue
            results.append(verify_one(path, args.target, tmp_dir))

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for r in results:
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['file']}")
            for s in r["steps"]:
                mark = "ok" if s["ok"] else "FAIL"
                print(f"    {mark:4s} {s['step']}" + (f": {s['detail']}" if s.get("detail") else ""))
        passed = sum(1 for r in results if r["ok"])
        print(f"{passed}/{len(results)} passed")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
