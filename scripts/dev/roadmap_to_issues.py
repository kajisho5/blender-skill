#!/usr/bin/env python3
"""File a GitHub issue for every ROADMAP.md row that doesn't have one yet, and write the created
issue number back into that row. Only the real `gh issue create` path needs `gh`/network --
--dry-run parses ROADMAP.md and prints exactly what would be created, nothing else, so it can be
run anywhere (including a sandbox with no `gh` CLI or GitHub access) to sanity-check the plan.

Usage:
  python3 scripts/dev/roadmap_to_issues.py --dry-run
  python3 scripts/dev/roadmap_to_issues.py            # actually creates issues; needs `gh auth login`
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ROADMAP = ROOT / "ROADMAP.md"
ROW_RE = re.compile(r"^\|\s*(RM-\d+)\s*\|\s*([\w-]+)\s*\|\s*(.+?)\s*\|\s*(\w[\w ]*)\s*\|\s*(.*?)\s*\|\s*$")

LABELS = [
    "roadmap", "area:inspect", "area:convert", "area:optimize", "area:render", "area:bake",
    "area:scene", "area:agent-ux", "area:ecosystem", "area:reliability", "blender-version",
    "release:major", "good first issue",
]


def parse_rows():
    rows = []
    for i, line in enumerate(ROADMAP.read_text(encoding="utf-8").splitlines()):
        m = ROW_RE.match(line)
        if m:
            rows.append({"line_no": i, "id": m.group(1), "area": m.group(2), "title": m.group(3),
                         "status": m.group(4).strip(), "issue": m.group(5).strip()})
    return rows


def ensure_labels(dry_run: bool):
    for label in LABELS:
        if dry_run:
            print(f"[dry-run] gh label create {label!r} (idempotent, ignored if it exists)")
            continue
        subprocess.run(["gh", "label", "create", label], capture_output=True, text=True)


def create_issue(row: dict, dry_run: bool):
    title = f"[{row['id']}] {row['title']}"
    labels = f"roadmap,area:{row['area']}"
    body = f"Roadmap item {row['id']} ({row['area']}). See ROADMAP.md for the full list this came from."
    if dry_run:
        print(f"[dry-run] gh issue create --title {title!r} --label {labels!r}")
        return None
    existing = subprocess.run(["gh", "issue", "list", "--search", f'in:title "{title}"', "--json", "title"],
                               capture_output=True, text=True)
    if title in existing.stdout:
        print(f"skip (already exists): {title}")
        return None
    proc = subprocess.run(["gh", "issue", "create", "--title", title, "--label", labels, "--body", body],
                           capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"FAILED: {title}\n{proc.stderr}", file=sys.stderr)
        return None
    url = proc.stdout.strip().splitlines()[-1]
    number = url.rstrip("/").rsplit("/", 1)[-1]
    print(f"created #{number}: {title}")
    return number


def write_back(rows_with_issues: dict):
    if not rows_with_issues:
        return
    lines = ROADMAP.read_text(encoding="utf-8").splitlines()
    for line_no, issue_number in rows_with_issues.items():
        m = ROW_RE.match(lines[line_no])
        lines[line_no] = f"| {m.group(1)} | {m.group(2)} | {m.group(3)} | {m.group(4)} | #{issue_number} |"
    ROADMAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = parse_rows()
    todo = [r for r in rows if not r["issue"]]
    print(f"{len(rows)} roadmap row(s), {len(todo)} without an issue yet")

    ensure_labels(args.dry_run)
    created = {}
    for row in todo:
        number = create_issue(row, args.dry_run)
        if number:
            created[row["line_no"]] = number
    write_back(created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
