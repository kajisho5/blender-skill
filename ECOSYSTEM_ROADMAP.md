# Roadmap — blender-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **映像 (Visual)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR. This repository's own feature roadmap stays in [ROADMAP.md](ROADMAP.md); this file covers only its place in the ecosystem.

## Where it stands

v0.31.0. info, convert, optimize, lod, split, anim, bake, scene, render (thumbnail, turntable, sheet), look, check, fit, verify, batch; MCP server. 137 tests, CI on 3 OSes x Blender 4.2/4.5/latest.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 3D素材・3Dレンダリング | done |  |

## Wiring

Not reachable from the agent. Turntable MP4 is encoded by Blender's own FFmpeg, not ffmpeg-skill.

## Steps

- [x] **1. `contract --json` + `doctor --json`** — skill_id, version, provides[] in the OS schema shape
  - Done when: OS schema validation passes
- [ ] **2. MCP serverInfo.version from _version.py** — hard-coded 0.1.0 today
  - Done when: test
- [ ] **3. Transparent / sequence output for compositing** — `--transparent`, PNG sequence or ProRes 4444; sequence -> ffmpeg-skill/sequence
  - Done when: alpha verified on an output frame
- [ ] **4. Video resolution presets (RM-058)** — 1080p / 4K / vertical
  - Done when: test per preset
- [ ] **5. (agent side) SkillSpec render_3d** — CONFIRM; output becomes a B-roll asset for ffmpeg-skill/overlay
  - Done when: tracked in video-production-agent

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
