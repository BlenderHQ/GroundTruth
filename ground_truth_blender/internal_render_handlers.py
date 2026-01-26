# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

from __future__ import annotations

import bpy
import time
import os

from .runtime import JOB
from .utils.rc_xmp import camera_stats_at_frame, write_xmp_for_camera_at_frame, xmp_path_for_image_path


def _dbg(msg: str) -> None:
    # Printed to the system console on Windows.
    print(f"[GroundTruth] {msg}")


def _render_output_path(scene: bpy.types.Scene, frame: int, *, is_animation: bool) -> str:
    # For animation, Blender always uses frame_path naming.
    if is_animation:
        return scene.render.frame_path(frame=int(frame))

    # For still renders, Blender writes to scene.render.filepath (optionally with file_extension).
    fp = bpy.path.abspath(scene.render.filepath)
    if not fp:
        return scene.render.frame_path(frame=int(frame))

    # If filepath points to a directory, fall back to frame_path.
    if fp.endswith(os.sep) or (os.path.isdir(fp)):
        return scene.render.frame_path(frame=int(frame))

    if bool(scene.render.use_file_extension):
        ext = str(getattr(scene.render, "file_extension", "") or "")
        if ext and not fp.lower().endswith(ext.lower()):
            fp = fp + ext
    return fp


def _job_camera(scene: bpy.types.Scene) -> bpy.types.Object | None:
    if JOB.camera_name:
        obj = bpy.data.objects.get(JOB.camera_name)
        if obj and obj.type == "CAMERA":
            return obj
    if scene.camera and scene.camera.type == "CAMERA":
        return scene.camera
    return None


def _restore_internal_output(scene: bpy.types.Scene) -> None:
    if not JOB.has_internal_output_override:
        return
    scene.render.filepath = JOB.old_render_filepath
    scene.render.use_file_extension = bool(JOB.old_render_use_file_extension)
    try:
        scene.frame_step = int(JOB.old_scene_frame_step)
    except Exception:
        pass
    JOB.has_internal_output_override = False
    JOB.old_render_filepath = ""


def _job_scene() -> bpy.types.Scene | None:
    if JOB.scene_name:
        sc = bpy.data.scenes.get(JOB.scene_name)
        if sc is not None:
            return sc
    try:
        return bpy.context.scene
    except Exception:
        return None


def on_render_write(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    # Always log when this handler fires; helps debugging when users report missing XMPs.
    _dbg(f"render_write: scene={scene.name!r} frame={scene.frame_current} JOB.active={JOB.active} JOB.mode={JOB.mode!r}")

    if not (JOB.mode.startswith("internal_") or JOB.has_internal_output_override or JOB.internal_post_pending):
        return

    cam = _job_camera(scene)
    if cam is None:
        JOB.last_error = "No camera available during render_write"
        _dbg("render_write: no camera available")
        return

    if depsgraph is None:
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            _dbg("render_write: depsgraph was None, using bpy.context.evaluated_depsgraph_get()")
        except Exception as e:
            JOB.last_error = f"Depsgraph missing: {e}"
            _dbg(f"render_write: ERROR depsgraph missing: {e}")
            return

    frame = int(scene.frame_current)
    is_anim = JOB.mode == "internal_animation"
    img_path = _render_output_path(scene, frame, is_animation=is_anim)
    if not os.path.exists(img_path):
        # Some render configurations still end up writing to frame_path; try to recover by probing it.
        try:
            alt = scene.render.frame_path(frame=int(frame))
        except Exception:
            alt = ""
        if alt and alt != img_path and os.path.exists(alt):
            _dbg(f"render_write: output path fallback -> {alt}")
            img_path = alt
    JOB.last_image_path = img_path

    try:
        xmp_path = xmp_path_for_image_path(img_path)
        _dbg(f"render_write: writing xmp -> {xmp_path}")
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
            set_frame=False,
        )

        focal_35, pos3, rot9, fx, fy, cx, cy = camera_stats_at_frame(
            scene=scene, depsgraph=depsgraph, cam_obj=cam, frame=frame, rotation_mode=str(JOB.rotation_mode), set_frame=False
        )

        JOB.last_frame_done = frame
        JOB.last_focal_35mm = float(focal_35)
        JOB.last_fx_px = float(fx)
        JOB.last_fy_px = float(fy)
        JOB.last_cx_px = float(cx)
        JOB.last_cy_px = float(cy)
        JOB.last_pos_xyz = tuple(pos3)
        JOB.last_rot_row_major9 = tuple(rot9)
        JOB.wrote_xmp += 1
        _dbg(f"render_write: ok (wrote_xmp={JOB.wrote_xmp})")
    except Exception as e:
        JOB.last_error = str(e)
        _dbg(f"render_write: ERROR {e}")
        return

    # Some Blender builds call render_post before render_write; delay cleanup until after the write.
    if JOB.internal_post_pending:
        if int(scene.frame_current) >= int(JOB.frame_end):
            _dbg("render_write: finalize-after-post")
            _restore_internal_output(scene)
            JOB.active = False
            JOB.mode = ""
            JOB.internal_post_pending = False


def on_render_pre(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    # This fires reliably when internal render starts; use it to avoid relying on is_job_running being true.
    if JOB.mode.startswith("internal_") or JOB.has_internal_output_override:
        JOB.internal_seen_running = True
        _dbg(f"render_pre: scene={scene.name!r} mode={JOB.mode!r}")


def on_render_complete(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    if JOB.mode.startswith("internal_") or JOB.has_internal_output_override:
        _dbg(f"render_complete: scene={scene.name!r} frame={scene.frame_current}")


def on_render_cancel(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    if not (JOB.mode.startswith("internal_") or JOB.has_internal_output_override or JOB.internal_post_pending):
        return
    _dbg(f"render_cancel: scene={scene.name!r}")
    _restore_internal_output(scene)
    JOB.active = False
    JOB.mode = ""
    JOB.internal_post_pending = False


def on_render_post(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    if not (JOB.mode.startswith("internal_") or JOB.has_internal_output_override):
        return
    _dbg(f"render_post: scene={scene.name!r} (delaying finalize until render_write)")
    JOB.internal_post_pending = True


_REGISTERED = False


def _monitor_tick() -> float | None:
    # Clear stale job state if render_cancel/render_post aren't called for any reason.
    if JOB.active and JOB.mode.startswith("internal_"):
        try:
            running = bpy.app.is_job_running("RENDER")
        except Exception:
            running = False
        # Avoid spamming: only print occasionally.
        if (time.monotonic() - float(JOB.internal_start_time_s)) < 5.0:
            _dbg(f"monitor: running={running} seen_running={JOB.internal_seen_running} mode={JOB.mode!r}")
        if running:
            JOB.internal_not_running_since_s = 0.0
        else:
            # Don't race render_post/render_write; only clear if we never get callbacks for a while.
            if JOB.internal_post_pending:
                return 0.25
            now = float(time.monotonic())
            if JOB.internal_not_running_since_s == 0.0:
                JOB.internal_not_running_since_s = now
                return 0.25
            if now - float(JOB.internal_not_running_since_s) < 10.0:
                return 0.25
            sc = _job_scene()
            if sc is not None:
                _dbg("monitor: timeout finalize")
                _restore_internal_output(sc)
            JOB.active = False
            JOB.mode = ""
    return 0.25


def register():
    global _REGISTERED
    if _REGISTERED:
        return
    if on_render_write not in bpy.app.handlers.render_write:
        bpy.app.handlers.render_write.append(on_render_write)
    if on_render_pre not in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.append(on_render_pre)
    if on_render_complete not in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.append(on_render_complete)
    if on_render_cancel not in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.append(on_render_cancel)
    if on_render_post not in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.append(on_render_post)
    bpy.app.timers.register(_monitor_tick, persistent=True)
    _dbg("internal_render_handlers: registered")
    _REGISTERED = True


def unregister():
    global _REGISTERED
    if not _REGISTERED:
        return
    if on_render_write in bpy.app.handlers.render_write:
        bpy.app.handlers.render_write.remove(on_render_write)
    if on_render_pre in bpy.app.handlers.render_pre:
        bpy.app.handlers.render_pre.remove(on_render_pre)
    if on_render_complete in bpy.app.handlers.render_complete:
        bpy.app.handlers.render_complete.remove(on_render_complete)
    if on_render_cancel in bpy.app.handlers.render_cancel:
        bpy.app.handlers.render_cancel.remove(on_render_cancel)
    if on_render_post in bpy.app.handlers.render_post:
        bpy.app.handlers.render_post.remove(on_render_post)
    try:
        bpy.app.timers.unregister(_monitor_tick)
    except Exception:
        pass
    _dbg("internal_render_handlers: unregistered")
    _REGISTERED = False
