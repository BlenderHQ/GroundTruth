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

from ..utils.agisoft_xml import MetashapeCameraItem, write_metashape_xml_for_cameras, write_metashape_xml_for_frames
from ..utils.rc_xmp import format_image_name
from .xmp_cameras import _iter_export_cameras


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


def _warn_if_camera_labels_dont_match_existing_photos(
    op: Operator, *, out_dir_abs: str, labels: list[str]
) -> None:
    try:
        names = os.listdir(out_dir_abs)
    except Exception:
        return

    photos = [n for n in names if os.path.splitext(n)[1].lower() in _IMAGE_EXTS]
    if not photos or not labels:
        return
    photo_set = set(photos)

    sample_labels = [os.path.basename(str(label)) for label in labels[: min(10, len(labels))]]
    if any(lab in photo_set for lab in sample_labels):
        return

    op.report(
        {"WARNING"},
        "No matching photo filenames found for camera labels; Metashape may import cameras without assigning photos.",
    )
    try:
        print(
            f"[GroundTruth] export_metashape_xml_cameras: WARNING no label/photo matches in {out_dir_abs!r};"
            f" example_label={sample_labels[0]!r} example_photo={photos[0]!r}"
        )
    except Exception:
        pass


def _photo_path_for_label(*, out_dir_abs: str, label: str) -> str | None:
    label = os.path.basename(str(label))
    if not label:
        return None

    exact = os.path.join(out_dir_abs, label)
    if os.path.isfile(exact):
        return exact

    label_stem = os.path.splitext(label)[0].lower()
    try:
        names = os.listdir(out_dir_abs)
    except Exception:
        return None
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() in _IMAGE_EXTS and stem.lower() == label_stem:
            return os.path.join(out_dir_abs, name)
    return None


def _image_size_for_path(path: str) -> tuple[int, int] | None:
    try:
        img = bpy.data.images.load(path, check_existing=True)
        width, height = img.size
        width_i = int(width)
        height_i = int(height)
        if width_i > 0 and height_i > 0:
            return (width_i, height_i)
    except Exception:
        return None
    return None


def _camera_items_with_photo_sizes(*, cameras: list[bpy.types.Object], labels: list[str], out_dir_abs: str) -> list[MetashapeCameraItem]:
    items: list[MetashapeCameraItem] = []
    for cam, label in zip(cameras, labels):
        size = None
        photo_path = _photo_path_for_label(out_dir_abs=out_dir_abs, label=label)
        if photo_path is not None:
            size = _image_size_for_path(photo_path)
        if size is None:
            items.append(MetashapeCameraItem(camera=cam, label=label))
        else:
            items.append(MetashapeCameraItem(camera=cam, label=label, image_width=size[0], image_height=size[1]))
    return items


def _metashape_xml_path(out_dir: str, name: str) -> str:
    out_name = str(name or "metashape.xml")
    if not out_name.lower().endswith(".xml"):
        out_name = out_name + ".xml"
    if os.path.isabs(out_name) or out_name.startswith("//"):
        return out_name
    return os.path.join(out_dir, out_name)


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

        out_xml = _metashape_xml_path(out_dir, str(props.metashape_xml_filename or "metashape.xml"))

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


class GROUNDTRUTH_OT_export_metashape_xml_cameras(Operator):
    bl_idname = "groundtruth.export_metashape_xml_cameras"
    bl_label = "Export Metashape XML Cameras"
    bl_description = "Export one Metashape/Agisoft XML containing the selected scene cameras at the current frame"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.groundtruth_props
        if str(getattr(props, "output_format", "XMP")) != "METASHAPE":
            self.report({"ERROR"}, "GroundTruth Output is not set to Metashape XML")
            return {"CANCELLED"}

        if str(props.out_dir).startswith("//") and not bpy.data.filepath:
            self.report({"ERROR"}, "Output Dir uses // but no .blend is saved/opened")
            return {"CANCELLED"}

        cameras = _iter_export_cameras(context, str(props.xmp_multi_camera_source))
        if not cameras:
            self.report({"ERROR"}, "No cameras found for Multi-Camera Metashape export")
            return {"CANCELLED"}

        out_dir = bpy.path.abspath(props.out_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_xml = _metashape_xml_path(out_dir, str(props.metashape_xml_filename or "metashape.xml"))

        labels = [str(cam.name) for cam in cameras]
        _warn_if_camera_labels_dont_match_existing_photos(self, out_dir_abs=bpy.path.abspath(out_dir), labels=labels)
        camera_items = _camera_items_with_photo_sizes(
            cameras=cameras,
            labels=labels,
            out_dir_abs=bpy.path.abspath(out_dir),
        )

        try:
            write_metashape_xml_for_cameras(
                scene=context.scene,
                camera_items=camera_items,
                out_xml_path=out_xml,
                rotation_mode=str(props.rotation_mode),
                distortion_mode=str(props.distortion_model),
                k1=float(getattr(props, "distortion_k1", 0.0)),
                k2=float(getattr(props, "distortion_k2", 0.0)),
                k3=float(getattr(props, "distortion_k3", 0.0)),
                k4=float(getattr(props, "distortion_k4", 0.0)),
                t1=float(getattr(props, "distortion_t1", 0.0)),
                t2=float(getattr(props, "distortion_t2", 0.0)),
                frame=int(context.scene.frame_current),
            )
        except Exception as e:
            self.report({"ERROR"}, f"Failed to write Metashape XML: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"GroundTruth: Wrote {len(cameras)} Metashape camera(s): {bpy.path.abspath(out_xml)}")
        print(f"[GroundTruth] export_metashape_xml_cameras: wrote {bpy.path.abspath(out_xml)!r}")
        return {"FINISHED"}
