# GroundTruth Blender Add-on

This folder is a Blender add-on package.

## Install

Blender’s “Install…” expects either a single `.py` file or a `.zip`. This add-on is **multi-file**, so you must install a `.zip` (or manually copy the whole folder).

### Option A: Install from zip (recommended)

1. Build the zip:

```bash
python3 tools/build_ground_truth_addon_zip.py
```

This creates `tools/ground_truth_blender.zip`.

2. In Blender: `Edit → Preferences → Add-ons → Install…`
3. Select `tools/ground_truth_blender.zip`
4. Enable the add-on: **GroundTruth**

### Option B: Manual folder copy

Copy the entire `ground_truth_blender/` folder into:

`%APPDATA%\\Blender Foundation\\Blender\\5.1\\scripts\\addons\\ground_truth_blender`

UI location: `View3D → Sidebar → GroundTruth`.

## What it does

- Import/export RealityCapture/RealityScan-style `xcr:` XMP sidecars per frame or per camera.
- Optionally render (frame or animation) and write an XMP sidecar next to each rendered frame.
- Render backend:
  - **Internal**: uses Blender’s render UI/preview; cancellation is the normal **Esc** in the render window.
  - **External**: launches a background Blender process; cancellation kills that process (reliable).
