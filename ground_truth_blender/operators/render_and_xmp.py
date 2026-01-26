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
import time
from typing import Optional

import bpy
from bpy.types import Operator

from .. import external_render
from ..runtime import JOB
from ..utils.rc_xmp import (
    camera_stats_at_frame,
    pattern_to_hash_path,
    write_xmp_for_camera_at_frame,
    xmp_path_for_image_path,
)


def _strip_ext(path_or_name: str) -> str:
    base, _ext = os.path.splitext(path_or_name)
    return base


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _best_camera(scene: bpy.types.Scene, props) -> bpy.types.Object | None:
    cam = props.camera_obj or scene.camera
    if cam is None or cam.type != "CAMERA":
        return None
    return cam


def _ensure_renderable_blend_copy(context: bpy.types.Context, out_dir_abs: str) -> str:
    # External Blender render needs a .blend on disk; use a copy if the file is unsaved or dirty.
    if bpy.data.filepath and not bpy.data.is_dirty:
        return bpy.data.filepath

    _ensure_dir(out_dir_abs)
    ts = int(time.time())
    tmp_blend = os.path.join(out_dir_abs, f"groundtruth_tmp_{ts}.blend")
    res = bpy.ops.wm.save_as_mainfile(filepath=tmp_blend, copy=True)
    if "FINISHED" not in set(res):
        raise RuntimeError("Failed to save temporary .blend copy (save your .blend and retry)")
    return tmp_blend


def _compute_output_base(scene: bpy.types.Scene, props) -> str:
    if bool(props.use_scene_render_output):
        # Let Blender use the .blend's output path/naming.
        # For external renders this means we also omit the -o override.
        # For internal renders, we avoid changing scene.render.filepath.
        return ""
    out_dir = bpy.path.abspath(props.out_dir)
    if str(props.out_dir).startswith("//") and not bpy.data.filepath:
        raise RuntimeError("Output Dir uses // but no .blend is saved/opened")
    _ensure_dir(out_dir)

    # Convert {frame:04d} -> #### and strip extension (Blender appends extension).
    hash_name = pattern_to_hash_path(str(props.image_pattern))
    return os.path.join(out_dir, _strip_ext(hash_name))


def _frame_path_with_base(scene: bpy.types.Scene, filepath_base: str, frame: int) -> str:
    # Compute path the same way Blender does (honors current image format + extension rules).
    if not filepath_base:
        return scene.render.frame_path(frame=int(frame))
    r = scene.render
    old = r.filepath
    old_use_ext = bool(r.use_file_extension)
    try:
        r.filepath = filepath_base
        r.use_file_extension = True
        return scene.render.frame_path(frame=int(frame))
    finally:
        r.filepath = old
        r.use_file_extension = old_use_ext


def _show_image_in_any_image_editor(context: bpy.types.Context, image_path: str) -> None:
    if not image_path or not os.path.exists(image_path):
        return

    try:
        img = bpy.data.images.load(image_path, check_existing=True)
    except Exception:
        return

    wm = context.window_manager
    for win in wm.windows:
        scr = win.screen
        for area in scr.areas:
            if area.type != "IMAGE_EDITOR":
                continue
            for space in area.spaces:
                if space.type == "IMAGE_EDITOR":
                    space.image = img
                    return


def _start_job(context: bpy.types.Context, *, mode: str, frame_start: int, frame_end: int, frame_step: int) -> None:
    scene = context.scene
    props = scene.groundtruth_props

    cam = _best_camera(scene, props)
    if cam is None:
        raise RuntimeError("No camera selected (set Camera or scene active camera)")

    out_base = _compute_output_base(scene, props)
    out_dir = os.path.dirname(out_base) if out_base else bpy.path.abspath(props.out_dir)
    if str(props.out_dir).startswith("//") and not bpy.data.filepath:
        raise RuntimeError("Output Dir uses // but no .blend is saved/opened")
    _ensure_dir(out_dir)
    blend_path = _ensure_renderable_blend_copy(context, out_dir)

    blender_exe = bpy.app.binary_path
    if not blender_exe:
        raise RuntimeError("bpy.app.binary_path is empty (cannot launch external Blender)")

    # Use factory startup to avoid user addons/preferences breaking background renders.
    cmd = [blender_exe, "--factory-startup", "-b", blend_path]
    if out_base:
        cmd += ["-o", out_base]
    if mode == "external_frame":
        cmd += ["-f", str(int(frame_start))]
    elif mode == "external_animation":
        if bool(props.use_scene_frame_range):
            cmd += ["-a"]
        else:
            cmd += ["-s", str(int(frame_start)), "-e", str(int(frame_end)), "-j", str(int(frame_step)), "-a"]
    else:
        raise RuntimeError(f"Unknown job mode: {mode!r}")

    # Initialize shared job state.
    JOB.active = True
    JOB.mode = mode
    JOB.camera_name = cam.name
    JOB.out_dir = out_dir
    JOB.image_pattern = str(props.image_pattern)
    JOB.prior = str(props.prior)
    JOB.rotation_mode = str(props.rotation_mode)
    JOB.distortion_model = str(props.distortion_model)
    JOB.distortion_k1 = float(getattr(props, "distortion_k1", 0.0))
    JOB.distortion_k2 = float(getattr(props, "distortion_k2", 0.0))
    JOB.distortion_k3 = float(getattr(props, "distortion_k3", 0.0))
    JOB.distortion_k4 = float(getattr(props, "distortion_k4", 0.0))
    JOB.distortion_t1 = float(getattr(props, "distortion_t1", 0.0))
    JOB.distortion_t2 = float(getattr(props, "distortion_t2", 0.0))
    JOB.last_error = ""
    JOB.wrote_xmp = 0
    JOB.blend_path = blend_path
    JOB.restore_frame = int(scene.frame_current)
    JOB.frame_start = int(frame_start)
    JOB.frame_end = int(frame_end)
    JOB.frame_step = int(frame_step)
    JOB.next_frame_to_finalize = int(frame_start)
    JOB.cancel_requested = False
    JOB.cancel_time_s = 0.0
    JOB.proc_pid = 0
    JOB.proc_returncode = 0
    JOB.last_frame_done = int(frame_start)
    JOB.last_image_path = ""

    # Isolate from user config/addons for background renders.
    env = os.environ.copy()
    user_root = os.path.join(out_dir, "_groundtruth_blender_user")
    env["BLENDER_USER_CONFIG"] = os.path.join(user_root, "config")
    env["BLENDER_USER_SCRIPTS"] = os.path.join(user_root, "scripts")
    env["BLENDER_USER_DATAFILES"] = os.path.join(user_root, "datafiles")
    env["BLENDER_USER_EXTENSIONS"] = os.path.join(user_root, "extensions")
    for k in ("BLENDER_USER_CONFIG", "BLENDER_USER_SCRIPTS", "BLENDER_USER_DATAFILES", "BLENDER_USER_EXTENSIONS"):
        _ensure_dir(env[k])

    proc = external_render.start(cmd, env=env)
    JOB.proc_pid = int(proc.pid or 0)


def _internal_apply_output_override(scene: bpy.types.Scene, props, *, is_animation: bool) -> None:
    if bool(props.use_scene_render_output):
        return
    out_dir = bpy.path.abspath(props.out_dir)
    if str(props.out_dir).startswith("//") and not bpy.data.filepath:
        raise RuntimeError("Output Dir uses // but no .blend is saved/opened")
    _ensure_dir(out_dir)

    JOB.has_internal_output_override = True
    JOB.old_render_filepath = scene.render.filepath
    JOB.old_render_use_file_extension = bool(scene.render.use_file_extension)

    if is_animation:
        hash_name = pattern_to_hash_path(str(props.image_pattern))
        scene.render.filepath = os.path.join(out_dir, _strip_ext(hash_name))
        scene.render.use_file_extension = True
    else:
        frame = int(scene.frame_current)
        stem = _strip_ext(str(props.image_pattern).format(frame=frame))
        scene.render.filepath = os.path.join(out_dir, stem)
        scene.render.use_file_extension = True


def _internal_start_render(context: bpy.types.Context, *, is_animation: bool) -> None:
    scene = context.scene
    props = scene.groundtruth_props

    cam = _best_camera(scene, props)
    if cam is None:
        raise RuntimeError("No camera selected (set Camera or scene active camera)")

    _internal_apply_output_override(scene, props, is_animation=is_animation)

    JOB.active = True
    JOB.mode = "internal_animation" if is_animation else "internal_frame"
    JOB.camera_name = cam.name
    JOB.scene_name = scene.name
    JOB.out_dir = os.path.dirname(scene.render.filepath) if bool(props.use_scene_render_output) else bpy.path.abspath(props.out_dir)
    JOB.image_pattern = str(props.image_pattern)
    JOB.prior = str(props.prior)
    JOB.rotation_mode = str(props.rotation_mode)
    JOB.distortion_model = str(props.distortion_model)
    JOB.distortion_k1 = float(getattr(props, "distortion_k1", 0.0))
    JOB.distortion_k2 = float(getattr(props, "distortion_k2", 0.0))
    JOB.distortion_k3 = float(getattr(props, "distortion_k3", 0.0))
    JOB.distortion_k4 = float(getattr(props, "distortion_k4", 0.0))
    JOB.distortion_t1 = float(getattr(props, "distortion_t1", 0.0))
    JOB.distortion_t2 = float(getattr(props, "distortion_t2", 0.0))
    JOB.last_error = ""
    JOB.wrote_xmp = 0
    JOB.cancel_requested = False
    JOB.internal_start_time_s = float(time.monotonic())
    JOB.internal_seen_running = False
    JOB.internal_post_pending = False
    JOB.internal_not_running_since_s = 0.0

    # For animation, scene.frame_step is used by the render engine.
    JOB.old_scene_frame_step = int(scene.frame_step)
    if is_animation:
        if bool(props.use_scene_frame_range):
            JOB.frame_start = int(scene.frame_start)
            JOB.frame_end = int(scene.frame_end)
            JOB.frame_step = int(scene.frame_step)
        else:
            scene.frame_step = int(props.frame_step)
            JOB.frame_start = int(props.frame_start)
            JOB.frame_end = int(props.frame_end)
            JOB.frame_step = int(props.frame_step)
    else:
        JOB.frame_start = int(scene.frame_current)
        JOB.frame_end = int(scene.frame_current)
        JOB.frame_step = 1

    # Ensure render result is visible.
    try:
        bpy.ops.render.view_show("INVOKE_DEFAULT")
    except Exception:
        pass

    if is_animation:
        if bool(props.use_scene_frame_range):
            bpy.ops.render.render("INVOKE_DEFAULT", animation=True)
        else:
            bpy.ops.render.render(
                "INVOKE_DEFAULT",
                animation=True,
                frame_start=int(props.frame_start),
                frame_end=int(props.frame_end),
            )
    else:
        bpy.ops.render.render("INVOKE_DEFAULT", write_still=True)


def _finalize_available_frames(context: bpy.types.Context, *, out_base: str) -> None:
    if not JOB.active:
        return

    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()
    cam = bpy.data.objects.get(JOB.camera_name)
    if cam is None or cam.type != "CAMERA":
        JOB.last_error = "Camera missing while finalizing"
        return

    while True:
        frame = int(JOB.next_frame_to_finalize)
        if frame > int(JOB.frame_end):
            break

        img_path = _frame_path_with_base(scene, out_base, frame)
        if not os.path.exists(img_path):
            break

        xmp_path = xmp_path_for_image_path(img_path)
        try:
            print(f"[GroundTruth] finalize: frame={frame} img={img_path!r} xmp={xmp_path!r}")
            write_xmp_for_camera_at_frame(
                scene=scene,
                depsgraph=depsgraph,
                cam_obj=cam,
                frame=frame,
                xmp_path=xmp_path,
                prior=str(JOB.prior),
                rotation_mode=str(JOB.rotation_mode),
                distortion_model=str(JOB.distortion_model),
                k1=float(JOB.distortion_k1),
                k2=float(JOB.distortion_k2),
                k3=float(JOB.distortion_k3),
                k4=float(JOB.distortion_k4),
                t1=float(JOB.distortion_t1),
                t2=float(JOB.distortion_t2),
                set_frame=True,
            )

            focal_35, pos3, rot9, fx, fy, cx, cy = camera_stats_at_frame(
                scene=scene, depsgraph=depsgraph, cam_obj=cam, frame=frame, rotation_mode=str(JOB.rotation_mode), set_frame=False
            )

            JOB.last_frame_done = frame
            JOB.last_image_path = img_path
            JOB.last_focal_35mm = float(focal_35)
            JOB.last_fx_px = float(fx)
            JOB.last_fy_px = float(fy)
            JOB.last_cx_px = float(cx)
            JOB.last_cy_px = float(cy)
            JOB.last_pos_xyz = tuple(pos3)
            JOB.last_rot_row_major9 = tuple(rot9)
            JOB.wrote_xmp += 1
        except Exception as e:
            JOB.last_error = str(e)
            print(f"[GroundTruth] finalize: ERROR frame={frame} err={e!r}")
            break

        _show_image_in_any_image_editor(context, img_path)
        JOB.next_frame_to_finalize = frame + int(JOB.frame_step)


def _finish_job(context: bpy.types.Context) -> None:
    scene = context.scene
    if JOB.restore_frame:
        try:
            scene.frame_set(int(JOB.restore_frame))
        except Exception:
            pass
    JOB.active = False
    JOB.mode = ""


class _GROUNDTRUTH_OT_render_base(Operator):
    bl_options = {"REGISTER"}

    _timer = None
    _out_base: str = ""

    def _start(self, context: bpy.types.Context, *, mode: str, frame_start: int, frame_end: int, frame_step: int) -> None:
        _start_job(context, mode=mode, frame_start=frame_start, frame_end=frame_end, frame_step=frame_step)
        self._out_base = _compute_output_base(context.scene, context.scene.groundtruth_props)

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)

    def modal(self, context, event):
        if event.type in {"ESC"}:
            # Allow ESC to cancel without blocking UI.
            if JOB.active and not JOB.cancel_requested:
                JOB.cancel_requested = True
                external_render.terminate()
            return {"RUNNING_MODAL"}

        if event.type == "TIMER":
            _finalize_available_frames(context, out_base=self._out_base)

            rc = external_render.poll()
            if rc is not None:
                JOB.proc_returncode = int(rc)
                # Final flush in case last frame appeared just before process exit.
                _finalize_available_frames(context, out_base=self._out_base)
                self._stop_modal(context)
                _finish_job(context)
                if rc != 0 and not JOB.cancel_requested:
                    self.report({"ERROR"}, f"External render failed (code {rc})")
                    return {"CANCELLED"}
                return {"FINISHED"}

            if JOB.cancel_requested:
                now = time.monotonic()
                if JOB.cancel_time_s == 0.0:
                    JOB.cancel_time_s = float(now)
                elif now - float(JOB.cancel_time_s) > 2.0:
                    external_render.kill()

            return {"RUNNING_MODAL"}

        # Let other UI events pass through so buttons/panels remain clickable.
        return {"PASS_THROUGH"}

    def _stop_modal(self, context: bpy.types.Context) -> None:
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None


class GROUNDTRUTH_OT_render_frame_and_xmp(_GROUNDTRUTH_OT_render_base):
    bl_idname = "groundtruth.render_frame_and_xmp"
    bl_label = "Render Frame + XMP"
    bl_description = "Render current frame and write matching .xmp sidecar (backend selectable)."

    def invoke(self, context, event):
        if JOB.active:
            self.report({"ERROR"}, "GroundTruth job already active")
            return {"CANCELLED"}
        props = context.scene.groundtruth_props
        if str(props.render_backend) == "INTERNAL":
            try:
                _internal_start_render(context, is_animation=False)
            except Exception as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            return {"FINISHED"}
        try:
            frame = int(context.scene.frame_current)
            self._start(context, mode="external_frame", frame_start=frame, frame_end=frame, frame_step=1)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class GROUNDTRUTH_OT_render_animation_and_xmp(_GROUNDTRUTH_OT_render_base):
    bl_idname = "groundtruth.render_animation_and_xmp"
    bl_label = "Render Animation + XMP"
    bl_description = "Render a frame range and write .xmp sidecars (backend selectable)."

    def invoke(self, context, event):
        if JOB.active:
            self.report({"ERROR"}, "GroundTruth job already active")
            return {"CANCELLED"}
        props = context.scene.groundtruth_props
        if props.frame_step <= 0:
            self.report({"ERROR"}, "Frame step must be > 0")
            return {"CANCELLED"}
        if props.frame_end < props.frame_start:
            self.report({"ERROR"}, "End frame must be >= Start frame")
            return {"CANCELLED"}
        if str(props.render_backend) == "INTERNAL":
            try:
                _internal_start_render(context, is_animation=True)
            except Exception as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            return {"FINISHED"}
        try:
            if bool(props.use_scene_frame_range):
                self._start(
                    context,
                    mode="external_animation",
                    frame_start=int(context.scene.frame_start),
                    frame_end=int(context.scene.frame_end),
                    frame_step=int(context.scene.frame_step),
                )
            else:
                self._start(
                    context,
                    mode="external_animation",
                    frame_start=int(props.frame_start),
                    frame_end=int(props.frame_end),
                    frame_step=int(props.frame_step),
                )
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class GROUNDTRUTH_OT_cancel(Operator):
    bl_idname = "groundtruth.cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the active GroundTruth render job"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if not JOB.active:
            self.report({"INFO"}, "No active GroundTruth job")
            return {"CANCELLED"}
        if JOB.mode.startswith("internal_"):
            JOB.cancel_requested = True
            if getattr(bpy.app, "use_event_simulate", False) and hasattr(context.window, "event_simulate"):
                try:
                    context.window.event_simulate(type="ESC", value="PRESS")
                    self.report({"INFO"}, "Cancel requested (sent ESC to internal render)")
                    return {"FINISHED"}
                except Exception as e:
                    self.report({"WARNING"}, f"Failed to send ESC: {e}")
            self.report({"INFO"}, "Press Esc in the render window to cancel (or start Blender with --enable-event-simulate)")
            return {"FINISHED"}

        JOB.cancel_requested = True
        external_render.terminate()
        self.report({"INFO"}, "Cancel requested (terminating external Blender render)")
        return {"FINISHED"}
