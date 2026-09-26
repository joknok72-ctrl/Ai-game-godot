# Verification checklist — what "done" means

The agent may only claim success after `godot_verify` reports **PASS**. The pipeline
(godotai/verify.py) runs the pinned engine headless:

1. **engine version** — binary prints `4.7.2.stable…`; any other version aborts.
2. **project.godot** — `config_version=5`, `config/features` contains `"4.7"`, main scene set.
3. **`--import`** — every resource imports; malformed `.tscn`/`.tres`/images fail here.
4. **`--check-only` per script** — the GDScript analyzer parses each `.gd` with static typing;
   unknown identifiers, wrong types, Godot-3 syntax, bad casts all fail here with file:line.
5. **smoke test** — `tests/smoke_test.gd` (`extends SceneTree`) instantiates the main scene,
   runs ≈2 s of engine time, asserts gameplay invariants, prints `SMOKE_TEST_OK`; without a smoke
   test the main scene is run with `--quit-after N` and the output scanned for `SCRIPT ERROR`.
6. **gdlint** (advisory, if installed) — style findings are reported, never block.

## Before submitting a plan

- Have I read the existing project (`list_files`, `read_file`) instead of assuming?
- Does every step list its paths? Is there a final `verify` step?
- Does `verification` mention `godot_import`, `check_scripts`, `smoke_test`?
- Is the plan honest about assumptions (art placeholders, missing audio, untested devices)?

## While implementing

- After each script: `godot_check_script` on it. Fix before moving on.
- After scenes: `godot_verify` (import catches scene mistakes early).
- Prefer `edit_file` for changes < 30 lines; whole-file rewrites hide regressions.
- Log one line per step for the human.

## Before claiming success

- `godot_verify` → PASS (paste the report summary).
- If an APK was requested: `godot_export` produced a non-empty `.apk` **or** the GitHub Actions
  run finished `success` with an artifact — quote the artifact name/size or run URL.
- List what is *not* verified (e.g. touch feel on a real device, performance on low-end phones).

## Things the pipeline cannot verify (say so explicitly)

- Visual quality, game feel, audio mixing, frame rate on real Android hardware.
- Play Store policy compliance, signing with the user's release key (unless env vars provided).
- Behaviour of GPU features not available to the headless renderer.
