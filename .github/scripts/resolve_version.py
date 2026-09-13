#!/usr/bin/env python3
"""Decide the next release version from the labels of the PRs merged since the last tag.

Used by .github/workflows/release.yml.

Rules:
  dependencies (Dependabot)          -> not releasable, whatever else it carries
  major                              -> refuse (exit 2): a major is a hand edit of package.json
  minor / feature / enhancement      -> minor
  patch / fix / bug                  -> patch
  only chore / ci / docs / dependencies (or any other label) -> not releasable
  no label at all                    -> patch (a PR nobody labelled still ships)

Pure logic lives in resolve(); the CLI wraps it with `git log` and `gh api`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Callable, Dict, Iterable, List, Optional, Tuple

MAJOR = {"major"}
MINOR = {"minor", "feature", "enhancement"}
PATCH = {"patch", "fix", "bug"}
NOT_RELEASABLE_TITLE = re.compile(r"^(chore|ci|docs|build)\b", re.I)
PR_NUMBER = re.compile(r"\(#(\d+)\)\s*$")


def bump_for_labels(labels: Iterable[str]) -> Optional[str]:
    """'major' / 'minor' / 'patch' / None (not releasable) for one PR's labels."""
    names = {l.lower() for l in labels}
    if "dependencies" in names:
        return None
    if names & MAJOR:
        return "major"
    if names & MINOR:
        return "minor"
    if names & PATCH:
        return "patch"
    if not names:
        return "patch"
    return None


def resolve(last_version: str, subjects: List[str],
            labels_for: Callable[[int], List[str]]) -> Tuple[Optional[str], List[Dict[str, object]]]:
    """Return (next_version or None, per-commit decisions).

    subjects: commit subjects on main since the last tag, newest first (git log order).
    labels_for: PR number -> its label names.
    """
    decisions: List[Dict[str, object]] = []
    bumps: List[str] = []
    for subject in subjects:
        m = PR_NUMBER.search(subject)
        if m:
            number = int(m.group(1))
            labels = labels_for(number)
            bump = bump_for_labels(labels)
            decisions.append({"subject": subject, "pr": number, "labels": sorted(labels), "bump": bump})
        else:
            bump = None if NOT_RELEASABLE_TITLE.match(subject) else "patch"
            decisions.append({"subject": subject, "pr": None, "labels": [], "bump": bump})
        if bump:
            bumps.append(bump)
    if not bumps:
        return None, decisions
    if "major" in bumps:
        raise SystemExit(
            "refusing to resolve a major version automatically; a major release is a deliberate, "
            "hand-made package.json bump in a PR")
    parts = [int(p) for p in last_version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if "minor" in bumps:
        return f"{major}.{minor + 1}.0", decisions
    return f"{major}.{minor}.{patch + 1}", decisions


def _gh_labels(repo: str) -> Callable[[int], List[str]]:
    def labels_for(number: int) -> List[str]:
        out = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{number}", "--jq", "[.labels[].name]"],
            capture_output=True, text=True, check=True).stdout
        return list(json.loads(out or "[]"))
    return labels_for


def main() -> int:
    last = os.environ["LAST_VERSION"]
    repo = os.environ["GITHUB_REPOSITORY"]
    rev_range = f"v{last}..HEAD" if last != "0.0.0" else "HEAD"
    subjects = [s for s in subprocess.run(
        ["git", "log", rev_range, "--format=%s"], capture_output=True, text=True, check=True
    ).stdout.splitlines() if s.strip()]
    version, decisions = resolve(last, subjects, _gh_labels(repo))
    for d in decisions:
        tag = d["bump"] or "-"
        print(f"  {tag:6} {'#' + str(d['pr']) if d['pr'] else '(no PR)':8} {','.join(d['labels']) or '(unlabeled)':30} {d['subject']}")
    print(f"last tag v{last} -> {'no release' if version is None else version}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"resolved_version={version or ''}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
