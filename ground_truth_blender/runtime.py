# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GroundTruthJob:
    active: bool = False
    mode: str = ""  # "internal_frame", "internal_animation", "external_frame", "external_animation"
    camera_name: str = ""
    scene_name: str = ""
    out_dir: str = ""
    image_pattern: str = "frame_{frame:04d}.png"
    prior: str = "exact"
    rotation_mode: str = "rc_rcw"
    distortion_model: str = "perspective"
    distortion_k1: float = 0.0
    distortion_k2: float = 0.0
    distortion_k3: float = 0.0
    distortion_k4: float = 0.0
    distortion_t1: float = 0.0
    distortion_t2: float = 0.0
    last_error: str = ""
    wrote_xmp: int = 0

    blend_path: str = ""
    restore_frame: int = 0

    frame_start: int = 0
    frame_end: int = 0
    frame_step: int = 1
    next_frame_to_finalize: int = 0

    cancel_requested: bool = False
    cancel_time_s: float = 0.0
    proc_pid: int = 0
    proc_returncode: int = 0

    # Internal render override (restore on render_post/render_cancel).
    has_internal_output_override: bool = False
    old_render_filepath: str = ""
    old_render_use_file_extension: bool = True
    old_scene_frame_step: int = 1
    internal_start_time_s: float = 0.0
    internal_seen_running: bool = False
    internal_post_pending: bool = False
    internal_not_running_since_s: float = 0.0

    last_frame_done: int = 0
    last_image_path: str = ""
    last_focal_35mm: float = 0.0
    last_fx_px: float = 0.0
    last_fy_px: float = 0.0
    last_cx_px: float = 0.0
    last_cy_px: float = 0.0
    last_pos_xyz: tuple[float, float, float] = (0.0, 0.0, 0.0)
    last_rot_row_major9: tuple[float, ...] = (0.0,) * 9


JOB = GroundTruthJob()
