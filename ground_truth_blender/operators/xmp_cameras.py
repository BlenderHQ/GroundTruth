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
import re

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement

from ..utils.rc_xmp import (
    apply_xmp_camera_data_to_camera,
    camera_data_to_xmp_data,
    read_xmp_camera_data,
    write_xmp_camera_data,
)


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _camera_name_from_path(path: str) -> str:
    name = os.path.splitext(os.path.basename(str(path)))[0].strip()
    return name or "Camera"


def _find_camera_by_name(scene: bpy.types.Scene, name: str) -> bpy.types.Object | None:
    target = str(name).lower()
    for obj in scene.objects:
        if obj.type == "CAMERA" and (obj.name.lower() == target or obj.data.name.lower() == target):
            return obj
    return None


def _new_camera(context, name: str) -> bpy.types.Object:
    cam_data = bpy.data.cameras.new(name=name)
    cam_obj = bpy.data.objects.new(name=name, object_data=cam_data)
    context.collection.objects.link(cam_obj)
    return cam_obj


def _is_visible_camera(context, obj: bpy.types.Object) -> bool:
    if obj.type != "CAMERA":
        return False
    try:
        return bool(obj.visible_get(view_layer=context.view_layer))
    except TypeError:
        return bool(obj.visible_get())
    except Exception:
        return not bool(obj.hide_get())


def _iter_export_cameras(context, source: str) -> list[bpy.types.Object]:
    source = str(source)
    if source == "SELECTED":
        cameras = [obj for obj in context.selected_objects if obj.type == "CAMERA"]
    elif source == "ALL":
        cameras = [obj for obj in context.scene.objects if obj.type == "CAMERA"]
    else:
        cameras = [obj for obj in context.scene.objects if _is_visible_camera(context, obj)]
    return sorted(cameras, key=lambda obj: obj.name.lower())


def _safe_xmp_path(out_dir: str, camera_name: str, used_names: set[str]) -> str:
    stem = _SAFE_NAME_RE.sub("_", str(camera_name)).strip("._")
    if not stem:
        stem = "camera"
    base = stem
    index = 2
    while stem.lower() in used_names:
        stem = f"{base}_{index}"
        index += 1
    used_names.add(stem.lower())
    return os.path.join(out_dir, stem + ".xmp")


class GROUNDTRUTH_OT_import_xmp_cameras(Operator):
    bl_idname = "groundtruth.import_xmp_cameras"
    bl_label = "Import XMP Cameras"
    bl_description = "Import one or more RC/RS XMP files as scene cameras"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    files: CollectionProperty(type=OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    filter_glob: StringProperty(default="*.xmp", options={"HIDDEN"})

    def invoke(self, context, event):
        props = context.scene.groundtruth_props
        out_dir = bpy.path.abspath(props.out_dir)
        if out_dir and os.path.isdir(out_dir):
            self.directory = out_dir
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        props = context.scene.groundtruth_props
        paths: list[str] = []
        if self.files:
            paths = [os.path.join(self.directory, item.name) for item in self.files if item.name.lower().endswith(".xmp")]
        elif self.filepath:
            paths = [self.filepath]

        if not paths:
            self.report({"ERROR"}, "No XMP files selected")
            return {"CANCELLED"}

        imported = 0
        created = 0
        first_obj = None
        for path in sorted(paths):
            try:
                data = read_xmp_camera_data(path)
                name = data.name or _camera_name_from_path(path)
                cam_obj = _find_camera_by_name(context.scene, name)
                if cam_obj is None:
                    cam_obj = _new_camera(context, name)
                    created += 1
                apply_xmp_camera_data_to_camera(cam_obj=cam_obj, data=data, rotation_mode=str(props.rotation_mode))
                imported += 1
                if first_obj is None:
                    first_obj = cam_obj
            except Exception as e:
                self.report({"WARNING"}, f"Skipped {os.path.basename(path)}: {e}")

        if first_obj is not None:
            context.scene.camera = first_obj
            context.view_layer.objects.active = first_obj
            first_obj.select_set(True)

        if imported == 0:
            self.report({"ERROR"}, f"Unable to import {len(paths)} XMP file(s)")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported {imported} XMP camera(s), created {created}")
        return {"FINISHED"}


class GROUNDTRUTH_OT_export_xmp_cameras(Operator):
    bl_idname = "groundtruth.export_xmp_cameras"
    bl_label = "Export XMP Cameras"
    bl_description = "Export one RC/RS XMP file per scene camera"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.groundtruth_props
        if str(props.out_dir).startswith("//") and not bpy.data.filepath:
            self.report({"ERROR"}, "Output Dir uses // but no .blend is saved/opened")
            return {"CANCELLED"}

        cameras = _iter_export_cameras(context, str(props.xmp_multi_camera_source))
        if not cameras:
            self.report({"ERROR"}, "No cameras found for Multi-Camera export")
            return {"CANCELLED"}

        out_dir = bpy.path.abspath(props.out_dir)
        os.makedirs(out_dir, exist_ok=True)

        frame = int(context.scene.frame_current)
        depsgraph = context.evaluated_depsgraph_get()
        used_names: set[str] = set()
        exported = 0
        for cam_obj in cameras:
            try:
                data = camera_data_to_xmp_data(
                    scene=context.scene,
                    depsgraph=depsgraph,
                    cam_obj=cam_obj,
                    frame=frame,
                    prior=str(props.prior),
                    rotation_mode=str(props.rotation_mode),
                    distortion_model=str(props.distortion_model),
                    projection_model=str(props.projection_model),
                    k1=float(props.distortion_k1),
                    k2=float(props.distortion_k2),
                    k3=float(props.distortion_k3),
                    k4=float(props.distortion_k4),
                    t1=float(props.distortion_t1),
                    t2=float(props.distortion_t2),
                    set_frame=True,
                    use_stored_xmp=True,
                )
                xmp_path = _safe_xmp_path(out_dir, cam_obj.name, used_names)
                write_xmp_camera_data(data=data, xmp_path=xmp_path)
                exported += 1
            except Exception as e:
                self.report({"WARNING"}, f"Skipped {cam_obj.name}: {e}")

        if exported == 0:
            self.report({"ERROR"}, f"Unable to export {len(cameras)} camera(s)")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported {exported} XMP camera(s) to {out_dir}")
        return {"FINISHED"}
