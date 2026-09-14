# Blender version support

| Blender | Status | Notes |
|---------|--------|-------|
| 4.2 LTS | tested | Floor version. `scripts/bpy/_compat.py`'s operator choices target this. |
| 4.5 LTS | tested | No API differences found against 4.2 in this skill's own toolchain. |
| 5.2 | tested | `bpy.types.RenderSettings`'s `engine` enum renamed `BLENDER_EEVEE_NEXT` (4.2-4.5) back to `BLENDER_EEVEE` (Blender merged the "next" Eevee back into the one engine); handled by `_compat.eevee_engine_id()`. |

Older than 4.2, or a version between the ones above, is untested (not necessarily broken --
just not run against a real Blender by this project). `.github/workflows/blender-versions.yml`
checks weekly for a new release not listed here and files an issue; it does not add anything to
the CI matrix on its own -- that needs a human to actually run the suite against it first.
