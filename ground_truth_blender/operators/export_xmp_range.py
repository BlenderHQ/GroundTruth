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
from typing import List

import bpy
from bpy.types import Operator

from ..utils.rc_xmp import format_image_name, write_xmp_for_camera_at_frame, xmp_path_for_image_path


class GROUNDTRUTH_OT_export_xmp_range(Operator):
    bl_idname = "groundtruth.export_xmp_range"
    bl_label = "Export XMP (Range)"
    bl_description = "Export one XMP per frame (no rendering). Press Esc to cancel."
    bl_options = {"REGISTER", "UNDO"}

    _timer = None
    _frames: List[int] = []
    _frame_index: int = 0
    _out_dir: str = ""
    _restore_frame: int = 0

    def _dbg(self, msg: str) -> None:
        print(f"[GroundTruth] export_xmp_range: {msg}")

    def invoke(self, context, event):
        props = context.scene.groundtruth_props
        cam = props.camera_obj or context.scene.camera
        if cam is None or cam.type != "CAMERA":
            self.report({"ERROR"}, "No camera selected (set Camera or scene active camera)")
            return {"CANCELLED"}

        if bool(props.use_scene_frame_range):
            fs = int(context.scene.frame_start)
            fe = int(context.scene.frame_end)
            step = int(context.scene.frame_step)
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

        if bool(props.use_scene_render_output):
            # Naming comes from scene.render.filepath & image format.
            self._out_dir = ""
        else:
            out_dir = bpy.path.abspath(props.out_dir)
            if str(props.out_dir).startswith("//") and not bpy.data.filepath:
                self.report({"ERROR"}, "Output Dir uses // but no .blend is saved/opened")
                return {"CANCELLED"}
            os.makedirs(out_dir, exist_ok=True)
            self._out_dir = out_dir

        self._frames = list(range(fs, fe + 1, step))
        self._frame_index = 0
        self._restore_frame = int(context.scene.frame_current)
        self._dbg(f"start frames={fs}..{fe} step={step} use_scene_render_output={bool(props.use_scene_render_output)}")

        context.window_manager.progress_begin(0, len(self._frames))

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"ESC"}:
            self._finish(context, cancelled=True)
            return {"CANCELLED"}

        if event.type != "TIMER":
            return {"RUNNING_MODAL"}

        props = context.scene.groundtruth_props
        scene = context.scene
        depsgraph = context.evaluated_depsgraph_get()
        cam = props.camera_obj or scene.camera
        if cam is None or cam.type != "CAMERA":
            self.report({"ERROR"}, "Camera disappeared (no CAMERA object available)")
            self._finish(context, cancelled=True)
            return {"CANCELLED"}

        if self._frame_index >= len(self._frames):
            self._finish(context, cancelled=False)
            return {"FINISHED"}

        frame = int(self._frames[self._frame_index])
        self._frame_index += 1

        if bool(props.use_scene_render_output):
            # Use Blender's render output naming without changing output settings.
            scene.frame_set(frame)
            img_path = scene.render.frame_path(frame=frame)
            xmp_path = xmp_path_for_image_path(img_path)
        else:
            img_name = format_image_name(props.image_pattern, frame)
            img_path = os.path.join(self._out_dir, img_name)
            xmp_path = xmp_path_for_image_path(img_path)
        try:
            write_xmp_for_camera_at_frame(
                scene=scene,
                depsgraph=depsgraph,
                cam_obj=cam,
                frame=frame,
                xmp_path=xmp_path,
                prior=str(props.prior),
                rotation_mode=str(props.rotation_mode),
                distortion_model=str(props.distortion_model),
                k1=float(getattr(props, "distortion_k1", 0.0)),
                k2=float(getattr(props, "distortion_k2", 0.0)),
                k3=float(getattr(props, "distortion_k3", 0.0)),
                k4=float(getattr(props, "distortion_k4", 0.0)),
                t1=float(getattr(props, "distortion_t1", 0.0)),
                t2=float(getattr(props, "distortion_t2", 0.0)),
            )
        except Exception as e:
            self._dbg(f"ERROR frame={frame} xmp={xmp_path!r} err={e!r}")
            self.report({"ERROR"}, f"Failed at frame {frame}: {e}")
            self._finish(context, cancelled=True)
            return {"CANCELLED"}

        context.window_manager.progress_update(self._frame_index)

        return {"RUNNING_MODAL"}

    def _finish(self, context, cancelled: bool) -> None:
        scene = context.scene
        scene.frame_set(self._restore_frame)

        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None

        wm.progress_end()

        if cancelled:
            self.report({"INFO"}, "GroundTruth: XMP export cancelled")
            self._dbg("cancelled")
        else:
            self.report({"INFO"}, f"GroundTruth: Exported {len(self._frames)} XMP files to {self._out_dir}")
            self._dbg(f"done wrote={len(self._frames)}")
