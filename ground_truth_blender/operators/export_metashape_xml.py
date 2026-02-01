# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

from __future__ import annotations

import os

import bpy
from bpy.types import Operator

from ..utils.agisoft_xml import write_metashape_xml_for_frames
from ..utils.rc_xmp import format_image_name


def _scene_output_dir(scene: bpy.types.Scene, frame: int) -> str:
    # Use frame_path to get an actual file path (honors relative //).
    p = bpy.path.abspath(scene.render.frame_path(frame=int(frame)))
    d = os.path.dirname(p)
    return d or "."


_IMAGE_EXTS = {
    ".bmp",
    ".cin",
    ".dng",
    ".dpx",
    ".exr",
    ".hdr",
    ".jpeg",
    ".jpg",
    ".jp2",
    ".png",
    ".ppm",
    ".tga",
    ".tif",
    ".tiff",
    ".webp",
}


def _warn_if_scene_render_output_looks_misconfigured(op: Operator, scene: bpy.types.Scene) -> None:
    # Common pitfall: users set Render Output to a filename like "IMG.JPG", expecting "IMG_0000.jpg",
    # but Blender will treat it as the base and append the frame + extension -> "IMG.JPG0000.jpg".
    try:
        fp = bpy.path.abspath(scene.render.filepath)
    except Exception:
        fp = str(scene.render.filepath)
    base = os.path.basename(fp)
    ext = os.path.splitext(base)[1].lower()
    if ext in _IMAGE_EXTS:
        op.report(
            {"WARNING"},
            "Render Output looks like a filename with an extension; animation naming will be like 'NAME0000.jpg'.",
        )

    try:
        if not bool(scene.render.use_file_extension):
            op.report({"WARNING"}, "Render Output: 'File Extensions' is disabled; photo labels may miss extensions.")
    except Exception:
        pass


def _warn_if_labels_dont_match_existing_photos(
    op: Operator, *, out_dir_abs: str, frames: list[int], label_for_frame
) -> None:
    try:
        names = os.listdir(out_dir_abs)
    except Exception:
        return

    photos = [n for n in names if os.path.splitext(n)[1].lower() in _IMAGE_EXTS]
    if not photos:
        return
    photo_set = set(photos)

    sample_frames = frames[: min(10, len(frames))]
    sample_labels = [os.path.basename(str(label_for_frame(int(f)))) for f in sample_frames]
    if any(lab in photo_set for lab in sample_labels):
        return

    # No matches in the directory: Metashape will import cameras but won't assign transforms to photos.
    op.report(
        {"WARNING"},
        "No matching photo filenames found for generated labels; check 'Use Scene Render Output' / 'Image Pattern'.",
    )
    try:
        print(
            f"[GroundTruth] export_metashape_xml: WARNING no label/photo matches in {out_dir_abs!r};"
            f" example_label={sample_labels[0]!r} example_photo={photos[0]!r}"
        )
    except Exception:
        pass


class GROUNDTRUTH_OT_export_metashape_xml_range(Operator):
    bl_idname = "groundtruth.export_metashape_xml_range"
    bl_label = "Export Metashape XML (Range)"
    bl_description = "Export a single Metashape/Agisoft XML containing one camera per frame (no rendering)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.groundtruth_props
        if str(getattr(props, "output_format", "XMP")) != "METASHAPE":
            self.report({"ERROR"}, "GroundTruth Output is not set to Metashape XML")
            return {"CANCELLED"}
        scene = context.scene
        cam = props.camera_obj or scene.camera
        if cam is None or cam.type != "CAMERA":
            self.report({"ERROR"}, "No camera selected (set Camera or scene active camera)")
            return {"CANCELLED"}

        if bool(props.use_scene_frame_range):
            fs = int(scene.frame_start)
            fe = int(scene.frame_end)
            step = int(scene.frame_step)
        else:
            fs = int(props.frame_start)
            fe = int(props.frame_end)
            step = int(props.frame_step)

        if step <= 0:
            self.report({"ERROR"}, "Frame step must be > 0")
            return {"CANCELLED"}
        if fe < fs:
            self.report({"ERROR"}, "End frame must be >= Start frame")
            return {"CANCELLED"}

        frames = list(range(fs, fe + 1, step))

        if bool(props.use_scene_render_output):
            _warn_if_scene_render_output_looks_misconfigured(self, scene)
            out_dir = _scene_output_dir(scene, frames[0])
            label_for_frame = lambda f: os.path.basename(scene.render.frame_path(frame=int(f)))
        else:
            out_dir = bpy.path.abspath(props.out_dir)
            if str(props.out_dir).startswith("//") and not bpy.data.filepath:
                self.report({"ERROR"}, "Output Dir uses // but no .blend is saved/opened")
                return {"CANCELLED"}
            os.makedirs(out_dir, exist_ok=True)
            label_for_frame = lambda f: os.path.basename(format_image_name(str(props.image_pattern), int(f)))

        name = str(props.metashape_xml_filename or "metashape.xml")
        if not name.lower().endswith(".xml"):
            name = name + ".xml"
        out_xml = name
        if not (os.path.isabs(out_xml) or out_xml.startswith("//")):
            out_xml = os.path.join(out_dir, out_xml)

        _warn_if_labels_dont_match_existing_photos(self, out_dir_abs=bpy.path.abspath(out_dir), frames=frames, label_for_frame=label_for_frame)

        try:
            write_metashape_xml_for_frames(
                scene=scene,
                cam_obj=cam,
                frames=frames,
                image_label_for_frame=label_for_frame,
                out_xml_path=out_xml,
                rotation_mode=str(props.rotation_mode),
                distortion_mode=str(props.distortion_model),
                k1=float(getattr(props, "distortion_k1", 0.0)),
                k2=float(getattr(props, "distortion_k2", 0.0)),
                k3=float(getattr(props, "distortion_k3", 0.0)),
                k4=float(getattr(props, "distortion_k4", 0.0)),
                t1=float(getattr(props, "distortion_t1", 0.0)),
                t2=float(getattr(props, "distortion_t2", 0.0)),
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to write Metashape XML: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"GroundTruth: Wrote Metashape XML: {bpy.path.abspath(out_xml)}")
        print(f"[GroundTruth] export_metashape_xml: wrote {bpy.path.abspath(out_xml)!r}")
        return {"FINISHED"}
