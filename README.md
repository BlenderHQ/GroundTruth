# GroundTruth (Blender Add-on)

Exports **RealityCapture/RealityScan-style XMP camera priors** (`xcr:` namespace) from a Blender camera animation, optionally **while rendering**.

## Features

- Export one `.xmp` per frame (RC-style `<xcr:Rotation>`, `<xcr:Position>`, intrinsics).
- `Render Frame + XMP` and `Render Animation + XMP`:
  - **Internal** backend uses Blender’s normal render UI/preview.
  - **External** backend runs a background Blender render (reliable cancel).
- `Export XMP (Range)` writes XMPs without rendering.
- Principal point is exported as **zero** (`xcr:PrincipalPointU/V = 0`).
- Distortion model:
  - `Perspective`: coefficients exported as zeros.
  - `Brown`: manual coefficient entry in the UI (`k1 k2 k3 k4 t1 t2`), and the add-on auto-selects the RC model:
    - `brown3` / `brown4` (if `k4 != 0`)
    - `brown3t2` / `brown4t2` (if `t1 != 0` or `t2 != 0`)

## Install

1. Build the install zip:
   - `python3 build_ground_truth_addon_zip.py`
2. In Blender: `Edit → Preferences → Add-ons → Install…` and pick `ground_truth_blender.zip`.
3. Enable the add-on: **GroundTruth**.

## Usage

Open a `.blend` with an animated camera and use:

- `View3D → Sidebar → GroundTruth` panel (main controls)
- `Image Editor → Sidebar → GroundTruth` (render-time status + cancel)

Key options:

- `Camera`: defaults to the scene active camera.
- `Use Scene Render Output`: names XMP sidecars next to Blender’s render output (`scene.render.filepath` / `frame_path`).
- `Use Scene Frame Range`: uses `scene.frame_start/end/step` for animation operations.

### Output naming

XMP is written as a sidecar next to the rendered image:

- `.../frame_0001.png` → `.../frame_0001.xmp`

For still renders, Blender may write directly to `scene.render.filepath`; the add-on writes the matching `.xmp` next to that file.

## Cancel behavior

- **Internal** renders: Blender does not reliably expose a cancellable render operator via `bpy`. Cancel usually requires **Esc** in the render window. If Blender is launched with `--enable-event-simulate`, the add-on can attempt to send an Esc event.
- **External** renders: cancel terminates/kills the background Blender process.

## Notes (External Backend)

- External renders require a `.blend` on disk. If the file is unsaved/dirty, a temporary copy is saved into the output directory.
- The external process runs with isolated `BLENDER_USER_*` paths (to avoid broken user add-ons affecting the render).
