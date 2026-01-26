#!/usr/bin/env python3

from __future__ import annotations

import os
import pathlib
import zipfile


def _repo_root() -> pathlib.Path:
    # This script is intended to live at the root of the (sub)repo that contains
    # the `ground_truth_blender/` add-on folder.
    return pathlib.Path(__file__).resolve().parent


def main() -> None:
    root = _repo_root()
    addon_dir = root / "ground_truth_blender"
    if not addon_dir.is_dir():
        raise SystemExit(f"Add-on folder not found: {addon_dir}")

    out_zip = root / "ground_truth_blender.zip"
    out_zip_tools = root.parent / "tools" / "ground_truth_blender.zip"

    def want(p: pathlib.Path) -> bool:
        if p.is_dir():
            return False
        name = p.name
        if name == ".DS_Store":
            return False
        if name.endswith(".pyc"):
            return False
        if "__pycache__" in p.parts:
            return False
        return True

    files = [p for p in addon_dir.rglob("*") if want(p)]
    if not files:
        raise SystemExit(f"No files found in: {addon_dir}")

    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(files):
            rel = p.relative_to(addon_dir)
            arcname = str(pathlib.Path("ground_truth_blender") / rel).replace(os.sep, "/")
            z.write(p, arcname=arcname)

    print(f"Wrote: {out_zip}")
    # Convenience: if this repo is checked out inside the SFM super-repo at
    # `<SFM>/blender_addon`, also copy the zip to `<SFM>/tools`.
    if out_zip_tools.parent.is_dir():
        try:
            out_zip_tools.write_bytes(out_zip.read_bytes())
            print(f"Copied: {out_zip_tools}")
        except Exception as e:
            print(f"WARNING: Failed to copy to {out_zip_tools}: {e}")
    print("Install in Blender: Edit → Preferences → Add-ons → Install… → select ground_truth_blender.zip")


if __name__ == "__main__":
    main()
