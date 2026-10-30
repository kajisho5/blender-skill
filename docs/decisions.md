# Decisions

Design decisions for blender-skill, in the same one-bullet-per-ADR form the other AI Video
Production OS skills use (`docs/decisions.md`). Numbered in the order they were made; a later ADR
that changes an earlier one says so instead of editing it.

- **ADR-1 Resolution presets (RM-058): an explicit `--resolution W H` wins over
  `--resolution-preset NAME`, which wins over the 512x512 default.** `render.py
  --resolution-preset` names one frame's size: `thumbnail` 512x512 (the size render.py has always
  defaulted to), `square` 1080x1080, `720p` 1280x720, `1080p` 1920x1080, `4k` 3840x2160 (UHD-1,
  the consumer/streaming "4K"; DCI 4096x2160 is a cinema-projection size this skill's turntables
  are not delivered at), `vertical` 1080x1920 (9:16 short-form). "One frame" is literal: a
  thumbnail, every turntable frame or video frame, and every tile of `--sheet`, whose 2x2 sheet is
  therefore twice the preset in each dimension. Every preset is even in both dimensions, so it is
  always valid for H.264's 4:2:0 chroma subsampling.
  - *Precedence, not conflict.* Both flags may be given; the explicit size is used and the result
    reports the preset it overrode (`resolution.overridden_preset`) rather than dropping it
    silently. This differs from `optimize.py`'s `--texture-max` / `--texture-auto-resolution`,
    which refuse each other: those are two *strategies*, whereas a preset is only a name for a
    size, and a caller (or an MCP agent layering its own default preset under a user's exact size)
    adding an explicit size has stated the more specific intent. The result's `resolution` block
    always carries `width`, `height`, `preset` (null when explicit) and `source`
    (`explicit`/`preset`/`default`), so which rule applied is never guessed from the output.
  - *An unknown name is an error even when an explicit size would have won.* A misspelt preset is
    a mistake in the request, and accepting it because it happened to be overridden would hide
    that. It is `SkillError(kind="input")` -- `{"status": "error", "error": {"kind": "input",
    "message": "unknown resolution preset: '8k'", "hint": "valid presets: ..."}}` under `--json`,
    exit 1, the repo's existing input-error shape -- never argparse's `choices=` (exit 2 with usage
    text on stderr, no JSON). Names match case-insensitively (`4K` is `4k`); there are no aliases,
    so the contract's list is the complete set of accepted spellings.
  - *One table.* `RESOLUTION_PRESETS` in `scripts/_render_output.py` is read by render.py's
    validation, its `--help`, the MCP `render` tool description, and the contract's
    `tools[].options["--resolution-preset"]` (choices, sizes, default, precedence, error kind), so
    no listing can offer a name the CLI refuses.
