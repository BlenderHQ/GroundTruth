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
from dataclasses import dataclass
from typing import Iterable, Tuple

import bpy
from mathutils import Matrix


@dataclass(frozen=True)
class RcXmpConfig:
    version: int = 3
    calibration_prior: str = "exact"
    pose_prior: str = "exact"
    coordinates: str = "absolute"
    distortion_model: str = "perspective"
    skew: float = 0.0
    aspect_ratio: float = 1.0
    principal_u: float = 0.0
    principal_v: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    k3: float = 0.0
    k4: float = 0.0
    t1: float = 0.0
    t2: float = 0.0
    calibration_group: int = -1
    distortion_group: int = -1
    in_texturing: int = 1
    in_meshing: int = 1


def sensor_fit_effective(scene: bpy.types.Scene, cam_data: bpy.types.Camera) -> str:
    fit = cam_data.sensor_fit
    if fit != "AUTO":
        return fit
    render = scene.render
    ax = float(render.resolution_x) * float(render.pixel_aspect_x)
    ay = float(render.resolution_y) * float(render.pixel_aspect_y)
    return "HORIZONTAL" if ax >= ay else "VERTICAL"


def focal_length_35mm(scene: bpy.types.Scene, cam_data: bpy.types.Camera) -> float:
    if cam_data.type != "PERSP":
        raise RuntimeError(f"Only perspective cameras are supported (camera.type={cam_data.type!r})")

    lens_mm = float(cam_data.lens)
    if lens_mm <= 0.0:
        raise RuntimeError(f"Invalid camera lens mm: {lens_mm}")

    fit = sensor_fit_effective(scene, cam_data)
    sensor_w = float(cam_data.sensor_width)
    sensor_h = float(cam_data.sensor_height)
    if sensor_w <= 0.0 or sensor_h <= 0.0:
        raise RuntimeError(f"Invalid camera sensor size: {sensor_w}x{sensor_h} mm")

    if fit == "HORIZONTAL":
        return lens_mm * (36.0 / sensor_w)
    return lens_mm * (24.0 / sensor_h)


def intrinsics_px(scene: bpy.types.Scene, cam_data: bpy.types.Camera) -> Tuple[float, float, float, float]:
    if cam_data.type != "PERSP":
        raise RuntimeError(f"Only perspective cameras are supported (camera.type={cam_data.type!r})")

    render = scene.render
    res_x = float(render.resolution_x) * float(render.resolution_percentage) / 100.0
    res_y = float(render.resolution_y) * float(render.resolution_percentage) / 100.0
    pa_x = float(render.pixel_aspect_x)
    pa_y = float(render.pixel_aspect_y)

    lens_mm = float(cam_data.lens)
    sensor_w = float(cam_data.sensor_width)
    sensor_h = float(cam_data.sensor_height)
    if lens_mm <= 0.0 or sensor_w <= 0.0 or sensor_h <= 0.0:
        raise RuntimeError("Invalid camera lens/sensor settings")

    fx = lens_mm * (res_x * pa_x) / sensor_w
    fy = lens_mm * (res_y * pa_y) / sensor_h
    cx = 0.5 * res_x
    cy = 0.5 * res_y
    return (fx, fy, cx, cy)


def format_f64(v: float) -> str:
    s = f"{float(v):.17f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def mat3_to_row_major9(m: Matrix) -> Tuple[float, ...]:
    if len(m) != 3 or len(m[0]) != 3:
        raise RuntimeError(f"Expected 3x3 matrix, got {len(m)}x{len(m[0]) if len(m) else 0}")
    return (
        float(m[0][0]),
        float(m[0][1]),
        float(m[0][2]),
        float(m[1][0]),
        float(m[1][1]),
        float(m[1][2]),
        float(m[2][0]),
        float(m[2][1]),
        float(m[2][2]),
    )


def _camera_rotation_world_matrix(cam_matrix_world: Matrix) -> Matrix:
    # Parent or constraint scale should not leak into exported orientation.
    _loc, rot, _scale = cam_matrix_world.decompose()
    return rot.to_matrix()


def rotation_from_camera(*, cam_matrix_world: Matrix, rotation_mode: str) -> Matrix:
    # Blender camera local frame: x right, y up, -z forward.
    Rwc_bl = _camera_rotation_world_matrix(cam_matrix_world)

    if rotation_mode in ("blender_rwc", "blender_rcw"):
        R = Rwc_bl
        if rotation_mode == "blender_rcw":
            R = R.transposed()
        return R

    # CV-like camera frame: x right, y down, z forward.
    M_cv_to_blcam = Matrix.Diagonal((1.0, -1.0, -1.0))
    Rwc_cv = Rwc_bl @ M_cv_to_blcam

    if rotation_mode == "rc_rwc":
        return Rwc_cv
    if rotation_mode == "rc_rcw":
        return Rwc_cv.transposed()

    raise RuntimeError(f"Unknown rotation_mode: {rotation_mode!r}")


def xmp_text(
    *,
    cfg: RcXmpConfig,
    focal_length_35mm_value: float,
    rotation_row_major9: Iterable[float],
    position_xyz: Iterable[float],
) -> str:
    rot_s = " ".join(format_f64(v) for v in rotation_row_major9)
    pos_s = " ".join(format_f64(v) for v in position_xyz)
    dist_s = " ".join(format_f64(v) for v in (cfg.k1, cfg.k2, cfg.k3, cfg.k4, cfg.t1, cfg.t2))

    return (
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">\n"
        "  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
        "    <rdf:Description xmlns:xcr=\"http://www.capturingreality.com/ns/xcr/1.1#\""
        f" xcr:Version=\"{cfg.version}\"\n"
        f"       xcr:PosePrior=\"{cfg.pose_prior}\" xcr:Coordinates=\"{cfg.coordinates}\""
        f" xcr:DistortionModel=\"{cfg.distortion_model}\"\n"
        f"       xcr:FocalLength35mm=\"{format_f64(focal_length_35mm_value)}\" xcr:Skew=\"{format_f64(cfg.skew)}\""
        f" xcr:AspectRatio=\"{format_f64(cfg.aspect_ratio)}\"\n"
        f"       xcr:PrincipalPointU=\"{format_f64(cfg.principal_u)}\" xcr:PrincipalPointV=\"{format_f64(cfg.principal_v)}\"\n"
        f"       xcr:CalibrationPrior=\"{cfg.calibration_prior}\" xcr:CalibrationGroup=\"{cfg.calibration_group}\""
        f" xcr:DistortionGroup=\"{cfg.distortion_group}\"\n"
        f"       xcr:InTexturing=\"{cfg.in_texturing}\" xcr:InMeshing=\"{cfg.in_meshing}\">\n"
        f"      <xcr:Rotation>{rot_s}</xcr:Rotation>\n"
        f"      <xcr:Position>{pos_s}</xcr:Position>\n"
        f"      <xcr:DistortionCoeficients>{dist_s}</xcr:DistortionCoeficients>\n"
        "    </rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
    )


def write_xmp_for_camera_at_frame(
    *,
    scene: bpy.types.Scene,
    depsgraph: bpy.types.Depsgraph | None,
    cam_obj: bpy.types.Object,
    frame: int,
    xmp_path: str,
    prior: str,
    rotation_mode: str,
    distortion_model: str,
    k1: float = 0.0,
    k2: float = 0.0,
    k3: float = 0.0,
    k4: float = 0.0,
    t1: float = 0.0,
    t2: float = 0.0,
    set_frame: bool = True,
) -> None:
    def _is_nonzero(v: float) -> bool:
        # Treat tiny values as non-zero only if user explicitly wants them.
        return abs(float(v)) > 0.0

    rc_distortion_model = str(distortion_model)
    if rc_distortion_model == "perspective":
        k1 = k2 = k3 = k4 = t1 = t2 = 0.0
    elif rc_distortion_model == "brown":
        has_k4 = _is_nonzero(k4)
        has_t2 = _is_nonzero(t1) or _is_nonzero(t2)
        if has_k4:
            rc_distortion_model = "brown4t2" if has_t2 else "brown4"
        else:
            rc_distortion_model = "brown3t2" if has_t2 else "brown3"
    elif rc_distortion_model in {"brown3", "brown4", "brown3t2", "brown4t2"}:
        # Allow legacy/explicit RC model selection if something passes it through.
        pass
    else:
        # Unknown value: fall back to perspective-safe output.
        rc_distortion_model = "perspective"
        k1 = k2 = k3 = k4 = t1 = t2 = 0.0
    cfg = RcXmpConfig(
        calibration_prior=prior,
        pose_prior=prior,
        distortion_model=rc_distortion_model,
        principal_u=0.0,
        principal_v=0.0,
        k1=float(k1),
        k2=float(k2),
        k3=float(k3),
        k4=float(k4),
        t1=float(t1),
        t2=float(t2),
    )

    if set_frame:
        scene.frame_set(int(frame))
        # After setting the frame, ensure depsgraph matches the new evaluation state.
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        except Exception:
            pass

    if depsgraph is None:
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        except Exception:
            depsgraph = None

    if depsgraph is not None:
        try:
            cam_eval = cam_obj.evaluated_get(depsgraph)
        except Exception:
            # Try a fresh depsgraph once more (some handlers pass a depsgraph that isn't usable here).
            try:
                depsgraph = bpy.context.evaluated_depsgraph_get()
                cam_eval = cam_obj.evaluated_get(depsgraph)
            except Exception:
                cam_eval = cam_obj
    else:
        cam_eval = cam_obj
    focal_35 = focal_length_35mm(scene, cam_eval.data)

    mw = cam_eval.matrix_world.copy()
    C = mw.to_translation()
    R = rotation_from_camera(cam_matrix_world=mw, rotation_mode=rotation_mode)
    rot9 = mat3_to_row_major9(R)
    pos3 = (float(C.x), float(C.y), float(C.z))

    txt = xmp_text(cfg=cfg, focal_length_35mm_value=focal_35, rotation_row_major9=rot9, position_xyz=pos3)
    out_dir = os.path.dirname(xmp_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(xmp_path, "wb") as f:
        f.write(txt.encode("utf-8"))


def camera_stats_at_frame(
    *,
    scene: bpy.types.Scene,
    depsgraph: bpy.types.Depsgraph | None,
    cam_obj: bpy.types.Object,
    frame: int,
    rotation_mode: str,
    set_frame: bool = True,
) -> Tuple[float, Tuple[float, float, float], Tuple[float, ...], float, float, float, float]:
    if set_frame:
        scene.frame_set(int(frame))
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        except Exception:
            pass

    if depsgraph is None:
        try:
            depsgraph = bpy.context.evaluated_depsgraph_get()
        except Exception:
            depsgraph = None

    if depsgraph is not None:
        try:
            cam_eval = cam_obj.evaluated_get(depsgraph)
        except Exception:
            try:
                depsgraph = bpy.context.evaluated_depsgraph_get()
                cam_eval = cam_obj.evaluated_get(depsgraph)
            except Exception:
                cam_eval = cam_obj
    else:
        cam_eval = cam_obj

    focal_35 = focal_length_35mm(scene, cam_eval.data)
    fx, fy, cx, cy = intrinsics_px(scene, cam_eval.data)

    mw = cam_eval.matrix_world.copy()
    C = mw.to_translation()
    R = rotation_from_camera(cam_matrix_world=mw, rotation_mode=rotation_mode)
    rot9 = mat3_to_row_major9(R)
    pos3 = (float(C.x), float(C.y), float(C.z))
    return (focal_35, pos3, rot9, fx, fy, cx, cy)


_FRAME_RE = re.compile(r"\{frame(?::0?(\d+)d)?\}")


def format_image_name(pattern: str, frame: int) -> str:
    return str(pattern).format(frame=int(frame))


def pattern_to_hash_path(pattern: str) -> str:
    # Convert `frame_{frame:04d}.png` -> `frame_####.png` (digits count preserved when specified).
    m = _FRAME_RE.search(pattern)
    if not m:
        raise RuntimeError("image_pattern must include {frame} or {frame:04d}")
    digits = int(m.group(1)) if m.group(1) else 4
    hashes = "#" * max(1, digits)
    return pattern[: m.start()] + hashes + pattern[m.end() :]


def xmp_path_for_image_path(image_path: str) -> str:
    base, _ext = os.path.splitext(image_path)
    return base + ".xmp"
