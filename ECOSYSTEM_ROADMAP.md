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

The agent names `blender-skill/render` in a CONFIRM handoff from a pinned contract (step 5, video-production-agent#95); it does not run it or import the output yet. Turntable MP4 is encoded by Blender's own FFmpeg, not ffmpeg-skill.

## Steps

- [x] **1. `contract --json` + `doctor --json`** — skill_id, version, provides[] in the OS schema shape
  - Done when: OS schema validation passes
- [x] **2. MCP serverInfo.version from _version.py** — hard-coded 0.1.0 today — [#171](https://github.com/kajisho5/blender-skill/pull/171)
  - Done when: test
- [x] **3. Transparent / sequence output for compositing** — `--transparent`, PNG sequence or ProRes 4444; sequence -> ffmpeg-skill/sequence — [#171](https://github.com/kajisho5/blender-skill/pull/171)
  - Done when: alpha verified on an output frame
- [x] **4. Video resolution presets (RM-058)** — 1080p / 4K / vertical — [#172](https://github.com/kajisho5/blender-skill/pull/172)
  - Done when: test per preset
- [x] **5. (agent side) SkillSpec render_3d** — CONFIRM; output becomes a B-roll asset for ffmpeg-skill/overlay — [video-production-agent#95](https://github.com/kajisho5/video-production-agent/pull/95) (ADR-080: `render_3d` → `blender-skill/render` from the pinned contract, a CONFIRM handoff; bringing the render back as an overlay asset is a separate agent step)
  - Done when: tracked in video-production-agent
- [ ] **6. Procedural motion scenes (Geometry Nodes presets)** — `scene.json` gains a typed `procedural` block naming one of the node-group presets this skill ships as its own .blend data (grid, lines, morphing shapes, instanced particles, extruded patterns) with seed, count, speed and palette; keyframes can lock to a BPM or a beat grid. Same seed, same frames. Field review 2026-11-05 (Velisa ROADMAP, third round)
  - なぜ要るか：幾何学模様の背景や企業イベントのオープニングを、3D の奥行きつきで再現性よく作れる
  - Done when: each preset renders a 2 s sequence twice with the same pixel hash on one machine; an unknown preset parameter is refused
- [ ] **7. 3D text and logo reveal** — extruded text from a local font file and a logo from an SVG, with bevel, material colours from a brand kit and a reveal animation (rotate-in, build-up, light sweep), rendered transparent
  - なぜ要るか：企業ロゴ出しや 3D タイポを、外部の 3D ソフトなしで作れる
  - Done when: a fixture renders a Japanese 3D title with the given font (glyphs checked on a frame), alpha around it
- [ ] **8. Alpha mode and render passes** — transparent output with `alpha: straight|premultiplied` stated in the result, and separate passes (beauty, shadow, AO, mist, object index) as multilayer EXR or PNG sequences for compositing
  - なぜ要るか：After Effects・NLE・スイッチャーで、影や縁を汚さずに合成できる
  - Done when: an EXR fixture lists the passes; a straight and a premultiplied render of one frame differ only where alpha is below 1
- [ ] **9. Audio-driven animation** — a beat grid (ffmpeg-skill `scenes --beats`) or a loudness envelope baked into keyframes of a preset's parameters (scale, emission, rotation), so a 3D scene reacts to the music
  - なぜ要るか：音楽に反応する 3D ビジュアライザーや待機画面を作れる
  - Done when: on a 120 BPM fixture the driven parameter peaks within one frame of every beat

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
