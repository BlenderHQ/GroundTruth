# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

from __future__ import annotations

import math
import os
import re
import xml.etree.ElementTree as ET
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


@dataclass(frozen=True)
class RcEquirectangularXmpConfig:
    version: int = 4
    calibration_prior: str = "exact"
    pose_prior: str = "exact"
    coordinates: str = "absolute"
    projection_model: str = "equirectangular"
    projection_prior: str = "exact"
    projection_convention: str = "lonlat"
    horizontal_fov_deg: float = 360.0
    vertical_fov_deg: float = 180.0
    calibration_group: int = -1
    in_texturing: int = 1
    in_meshing: int = 1


@dataclass(frozen=True)
class RcXmpCameraData:
    position_xyz: Tuple[float, float, float]
    rotation_row_major9: Tuple[float, ...]
    name: str = ""
    version: int = 3
    pose_prior: str = "exact"
    calibration_prior: str = "exact"
    coordinates: str = "absolute"
    projection_model: str = "perspective"
    projection_prior: str = "exact"
    projection_convention: str = "lonlat"
    horizontal_fov_deg: float = 360.0
    vertical_fov_deg: float = 180.0
    focal_length_35mm: float = 50.0
    skew: float = 0.0
    aspect_ratio: float = 1.0
    principal_u: float = 0.0
    principal_v: float = 0.0
    distortion_model: str = "perspective"
    distortion_coefficients: Tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    calibration_group: int = -1
    distortion_group: int = -1
    in_texturing: int = 1
    in_meshing: int = 1


_GT_XMP_POSITION = "groundtruth_xmp_position_xyz"
_GT_XMP_ROTATION = "groundtruth_xmp_rotation_row_major9"
_GT_XMP_ROTATION_MODE = "groundtruth_xmp_rotation_mode"
_GT_XMP_FOCAL = "groundtruth_xmp_focal_length35mm"
_GT_XMP_SKEW = "groundtruth_xmp_skew"
_GT_XMP_ASPECT = "groundtruth_xmp_aspect_ratio"
_GT_XMP_PRINCIPAL = "groundtruth_xmp_principal_uv"
_GT_XMP_DISTORTION_MODEL = "groundtruth_xmp_distortion_model"
_GT_XMP_DISTORTION_COEFFS = "groundtruth_xmp_distortion_coefficients"
_GT_XMP_PROJECTION_MODEL = "groundtruth_xmp_projection_model"
_GT_XMP_PROJECTION_PRIOR = "groundtruth_xmp_projection_prior"
_GT_XMP_PROJECTION_CONVENTION = "groundtruth_xmp_projection_convention"
_GT_XMP_HORIZONTAL_FOV = "groundtruth_xmp_horizontal_fov_deg"
_GT_XMP_VERTICAL_FOV = "groundtruth_xmp_vertical_fov_deg"
_GT_XMP_POSE_PRIOR = "groundtruth_xmp_pose_prior"
_GT_XMP_CALIBRATION_PRIOR = "groundtruth_xmp_calibration_prior"
_GT_XMP_COORDINATES = "groundtruth_xmp_coordinates"
_GT_XMP_CALIBRATION_GROUP = "groundtruth_xmp_calibration_group"
_GT_XMP_DISTORTION_GROUP = "groundtruth_xmp_distortion_group"
_GT_XMP_IN_TEXTURING = "groundtruth_xmp_in_texturing"
_GT_XMP_IN_MESHING = "groundtruth_xmp_in_meshing"


def _camera_pano_type(cam_data: bpy.types.Camera) -> str:
    pano_type = getattr(cam_data, "panorama_type", None)
    if pano_type is None:
        cycles = getattr(cam_data, "cycles", None)
        pano_type = getattr(cycles, "panorama_type", None) if cycles is not None else None
    return str(pano_type or "")


def camera_projection_kind(cam_data: bpy.types.Camera, projection_model: str = "auto") -> str:
    requested = str(projection_model or "auto").lower()
    cam_type = str(getattr(cam_data, "type", ""))
    if requested == "equirectangular":
        return "equirectangular"
    if requested == "perspective":
        if cam_type == "PERSP":
            return "perspective"
        raise RuntimeError(
            "XMP Projection is set to Perspective, but the selected camera is not perspective "
            f"(camera.type={cam_type!r})"
        )
    if requested not in {"auto", ""}:
        raise RuntimeError(f"Unknown XMP projection model: {projection_model!r}")

    if cam_type == "PERSP":
        return "perspective"
    if cam_type == "PANO":
        pano_type = _camera_pano_type(cam_data)
        if not pano_type or pano_type.upper() == "EQUIRECTANGULAR":
            return "equirectangular"
        raise RuntimeError(
            "Only perspective and equirectangular panoramic cameras are supported "
            f"(camera.type='PANO', panorama_type={pano_type!r})"
        )
    raise RuntimeError(
        "Only perspective and equirectangular panoramic cameras are supported "
        f"(camera.type={cam_type!r})"
    )


def _pano_angle_attr(cam_data: bpy.types.Camera, name: str) -> float | None:
    value = getattr(cam_data, name, None)
    if value is None:
        cycles = getattr(cam_data, "cycles", None)
        value = getattr(cycles, name, None) if cycles is not None else None
    if value is None:
        return None
    return float(value)


def _snap_fov_deg(value: float, target: float) -> float:
    if abs(float(value) - float(target)) < 1.0e-3:
        return float(target)
    return float(value)


def equirectangular_fov_degrees(cam_data: bpy.types.Camera) -> Tuple[float, float]:
    lon_min = _pano_angle_attr(cam_data, "longitude_min")
    lon_max = _pano_angle_attr(cam_data, "longitude_max")
    lat_min = _pano_angle_attr(cam_data, "latitude_min")
    lat_max = _pano_angle_attr(cam_data, "latitude_max")

    horizontal = 360.0
    vertical = 180.0
    if lon_min is not None and lon_max is not None:
        horizontal = abs(math.degrees(float(lon_max) - float(lon_min)))
        if horizontal <= 0.0:
            horizontal = 360.0
    if lat_min is not None and lat_max is not None:
        vertical = abs(math.degrees(float(lat_max) - float(lat_min)))
        if vertical <= 0.0:
            vertical = 180.0

    horizontal = _snap_fov_deg(horizontal, 360.0)
    vertical = _snap_fov_deg(vertical, 180.0)
    return (horizontal, vertical)


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


def camera_matrix_world_from_xmp_pose(
    *,
    position_xyz: Iterable[float],
    rotation_row_major9: Iterable[float],
    rotation_mode: str,
) -> Matrix:
    vals = tuple(float(v) for v in rotation_row_major9)
    if len(vals) != 9:
        raise RuntimeError(f"Expected 9 rotation values, got {len(vals)}")
    pos = tuple(float(v) for v in position_xyz)
    if len(pos) != 3:
        raise RuntimeError(f"Expected 3 position values, got {len(pos)}")

    R_xmp = Matrix(((vals[0], vals[1], vals[2]), (vals[3], vals[4], vals[5]), (vals[6], vals[7], vals[8])))
    if rotation_mode == "blender_rwc":
        Rwc_bl = R_xmp
    elif rotation_mode == "blender_rcw":
        Rwc_bl = R_xmp.transposed()
    elif rotation_mode == "rc_rwc":
        Rwc_bl = R_xmp @ Matrix.Diagonal((1.0, -1.0, -1.0))
    elif rotation_mode == "rc_rcw":
        Rwc_bl = R_xmp.transposed() @ Matrix.Diagonal((1.0, -1.0, -1.0))
    else:
        raise RuntimeError(f"Unknown rotation_mode: {rotation_mode!r}")

    mw = Rwc_bl.to_4x4()
    mw[0][3] = pos[0]
    mw[1][3] = pos[1]
    mw[2][3] = pos[2]
    return mw


def _xml_local_name(name: str) -> str:
    if "}" in name:
        return name.rsplit("}", 1)[1]
    if ":" in name:
        return name.rsplit(":", 1)[1]
    return name


def _xmp_description(root: ET.Element) -> ET.Element:
    for element in root.iter():
        if _xml_local_name(element.tag) == "Description":
            return element
    raise RuntimeError("XMP does not contain rdf:Description")


def _xmp_value(desc: ET.Element, name: str) -> str | None:
    for child in desc:
        if _xml_local_name(child.tag) == name and child.text:
            return child.text.strip()
    for key, value in desc.attrib.items():
        if _xml_local_name(key) == name:
            return str(value).strip()
    return None


def _xmp_float(desc: ET.Element, name: str, default: float) -> float:
    value = _xmp_value(desc, name)
    if value is None or value == "":
        return float(default)
    return float(value)


def _xmp_int(desc: ET.Element, name: str, default: int) -> int:
    value = _xmp_value(desc, name)
    if value is None or value == "":
        return int(default)
    return int(value)


def _xmp_str(desc: ET.Element, name: str, default: str) -> str:
    value = _xmp_value(desc, name)
    if value is None or value == "":
        return str(default)
    return str(value)


def _float_tuple(text: str, expected_len: int, *, field_name: str) -> Tuple[float, ...]:
    vals = tuple(float(part) for part in str(text).split())
    if len(vals) != expected_len:
        raise RuntimeError(f"Expected {expected_len} values in xcr:{field_name}, got {len(vals)}")
    return vals


def read_xmp_camera_data(xmp_path: str) -> RcXmpCameraData:
    root = ET.parse(xmp_path).getroot()
    desc = _xmp_description(root)

    rotation_text = _xmp_value(desc, "Rotation")
    position_text = _xmp_value(desc, "Position")
    if not rotation_text or not position_text:
        raise RuntimeError(f"Missing xcr:Rotation or xcr:Position in {xmp_path}")

    distortion_text = _xmp_value(desc, "DistortionCoeficients") or _xmp_value(desc, "DistortionCoefficients")
    if distortion_text:
        distortion_coefficients = _float_tuple(distortion_text, 6, field_name="DistortionCoeficients")
    else:
        distortion_coefficients = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    projection_model = _xmp_str(desc, "ProjectionModel", "perspective")
    return RcXmpCameraData(
        name=os.path.splitext(os.path.basename(str(xmp_path)))[0],
        version=_xmp_int(desc, "Version", 3),
        pose_prior=_xmp_str(desc, "PosePrior", "exact"),
        calibration_prior=_xmp_str(desc, "CalibrationPrior", "exact"),
        coordinates=_xmp_str(desc, "Coordinates", "absolute"),
        projection_model=projection_model,
        projection_prior=_xmp_str(desc, "ProjectionPrior", "exact"),
        projection_convention=_xmp_str(desc, "ProjectionConvention", "lonlat"),
        horizontal_fov_deg=_xmp_float(desc, "HorizontalFovDeg", 360.0),
        vertical_fov_deg=_xmp_float(desc, "VerticalFovDeg", 180.0),
        focal_length_35mm=_xmp_float(desc, "FocalLength35mm", 50.0),
        skew=_xmp_float(desc, "Skew", 0.0),
        aspect_ratio=_xmp_float(desc, "AspectRatio", 1.0),
        principal_u=_xmp_float(desc, "PrincipalPointU", 0.0),
        principal_v=_xmp_float(desc, "PrincipalPointV", 0.0),
        distortion_model=_xmp_str(desc, "DistortionModel", "perspective"),
        distortion_coefficients=distortion_coefficients,
        calibration_group=_xmp_int(desc, "CalibrationGroup", -1),
        distortion_group=_xmp_int(desc, "DistortionGroup", -1),
        in_texturing=_xmp_int(desc, "InTexturing", 1),
        in_meshing=_xmp_int(desc, "InMeshing", 1),
        rotation_row_major9=_float_tuple(rotation_text, 9, field_name="Rotation"),
        position_xyz=_float_tuple(position_text, 3, field_name="Position"),
    )


def _camera_set_equirectangular(cam_data: bpy.types.Camera) -> None:
    cam_data.type = "PANO"
    if hasattr(cam_data, "panorama_type"):
        cam_data.panorama_type = "EQUIRECTANGULAR"
    cycles = getattr(cam_data, "cycles", None)
    if cycles is not None and hasattr(cycles, "panorama_type"):
        cycles.panorama_type = "EQUIRECTANGULAR"


def store_xmp_camera_data(cam_obj: bpy.types.Object, data: RcXmpCameraData, *, rotation_mode: str) -> None:
    cam_obj[_GT_XMP_POSITION] = tuple(float(v) for v in data.position_xyz)
    cam_obj[_GT_XMP_ROTATION] = tuple(float(v) for v in data.rotation_row_major9)
    cam_obj[_GT_XMP_ROTATION_MODE] = str(rotation_mode)

    cam_data = cam_obj.data
    cam_data[_GT_XMP_FOCAL] = float(data.focal_length_35mm)
    cam_data[_GT_XMP_SKEW] = float(data.skew)
    cam_data[_GT_XMP_ASPECT] = float(data.aspect_ratio)
    cam_data[_GT_XMP_PRINCIPAL] = (float(data.principal_u), float(data.principal_v))
    cam_data[_GT_XMP_DISTORTION_MODEL] = str(data.distortion_model)
    cam_data[_GT_XMP_DISTORTION_COEFFS] = tuple(float(v) for v in data.distortion_coefficients)
    cam_data[_GT_XMP_PROJECTION_MODEL] = str(data.projection_model)
    cam_data[_GT_XMP_PROJECTION_PRIOR] = str(data.projection_prior)
    cam_data[_GT_XMP_PROJECTION_CONVENTION] = str(data.projection_convention)
    cam_data[_GT_XMP_HORIZONTAL_FOV] = float(data.horizontal_fov_deg)
    cam_data[_GT_XMP_VERTICAL_FOV] = float(data.vertical_fov_deg)
    cam_data[_GT_XMP_POSE_PRIOR] = str(data.pose_prior)
    cam_data[_GT_XMP_CALIBRATION_PRIOR] = str(data.calibration_prior)
    cam_data[_GT_XMP_COORDINATES] = str(data.coordinates)
    cam_data[_GT_XMP_CALIBRATION_GROUP] = int(data.calibration_group)
    cam_data[_GT_XMP_DISTORTION_GROUP] = int(data.distortion_group)
    cam_data[_GT_XMP_IN_TEXTURING] = int(data.in_texturing)
    cam_data[_GT_XMP_IN_MESHING] = int(data.in_meshing)


def apply_xmp_camera_data_to_camera(
    *,
    cam_obj: bpy.types.Object,
    data: RcXmpCameraData,
    rotation_mode: str,
) -> None:
    if cam_obj.type != "CAMERA":
        raise RuntimeError("cam_obj must be a CAMERA object")

    cam_data = cam_obj.data
    if str(data.projection_model).lower() == "equirectangular":
        _camera_set_equirectangular(cam_data)
    else:
        cam_data.type = "PERSP"
        cam_data.sensor_fit = "HORIZONTAL"
        cam_data.sensor_width = 36.0
        cam_data.sensor_height = 24.0
        cam_data.lens = max(1.0e-6, float(data.focal_length_35mm))

    cam_obj.matrix_world = camera_matrix_world_from_xmp_pose(
        position_xyz=data.position_xyz,
        rotation_row_major9=data.rotation_row_major9,
        rotation_mode=rotation_mode,
    )
    store_xmp_camera_data(cam_obj, data, rotation_mode=rotation_mode)


def _stored_float_tuple(owner, key: str, expected_len: int) -> Tuple[float, ...] | None:
    value = owner.get(key)
    if value is None:
        return None
    try:
        ret = tuple(float(v) for v in value)
    except TypeError:
        return None
    if len(ret) != expected_len:
        return None
    return ret


def _stored_pose_if_current(
    *,
    scene: bpy.types.Scene,
    depsgraph: bpy.types.Depsgraph | None,
    cam_obj: bpy.types.Object,
    frame: int,
    rotation_mode: str,
    set_frame: bool,
) -> tuple[Tuple[float, float, float], Tuple[float, ...]] | None:
    stored_mode = str(cam_obj.get(_GT_XMP_ROTATION_MODE, ""))
    stored_pos = _stored_float_tuple(cam_obj, _GT_XMP_POSITION, 3)
    stored_rot = _stored_float_tuple(cam_obj, _GT_XMP_ROTATION, 9)
    if stored_mode != str(rotation_mode) or stored_pos is None or stored_rot is None:
        return None

    _focal, live_pos, live_rot, _fx, _fy, _cx, _cy = camera_stats_at_frame(
        scene=scene,
        depsgraph=depsgraph,
        cam_obj=cam_obj,
        frame=frame,
        rotation_mode=rotation_mode,
        set_frame=set_frame,
    )
    max_delta = 0.0
    max_allowed_delta = 1.0e-5
    for expected, actual in zip(stored_pos, live_pos):
        max_delta = max(max_delta, abs(float(expected) - float(actual)))
        max_allowed_delta = max(max_allowed_delta, abs(float(expected)) * 1.0e-9)
    for expected, actual in zip(stored_rot, live_rot):
        max_delta = max(max_delta, abs(float(expected) - float(actual)))
    if max_delta <= max_allowed_delta:
        return (stored_pos, stored_rot)
    return None


def camera_data_to_xmp_data(
    *,
    scene: bpy.types.Scene,
    depsgraph: bpy.types.Depsgraph | None,
    cam_obj: bpy.types.Object,
    frame: int,
    prior: str,
    rotation_mode: str,
    distortion_model: str,
    projection_model: str = "auto",
    k1: float = 0.0,
    k2: float = 0.0,
    k3: float = 0.0,
    k4: float = 0.0,
    t1: float = 0.0,
    t2: float = 0.0,
    set_frame: bool = True,
    use_stored_xmp: bool = True,
) -> RcXmpCameraData:
    stored_pose = None
    if use_stored_xmp:
        stored_pose = _stored_pose_if_current(
            scene=scene,
            depsgraph=depsgraph,
            cam_obj=cam_obj,
            frame=frame,
            rotation_mode=rotation_mode,
            set_frame=set_frame,
        )

    focal_35, pos3, rot9, _fx, _fy, _cx, _cy = camera_stats_at_frame(
        scene=scene,
        depsgraph=depsgraph,
        cam_obj=cam_obj,
        frame=frame,
        rotation_mode=rotation_mode,
        projection_model=projection_model,
        set_frame=set_frame,
    )
    if stored_pose is not None:
        pos3, rot9 = stored_pose

    cam_data = cam_obj.data
    stored_focal = cam_data.get(_GT_XMP_FOCAL)
    if stored_focal is not None and abs(float(stored_focal) - float(focal_35)) <= 1.0e-5:
        focal_35 = float(stored_focal)

    projection_kind = camera_projection_kind(cam_data, projection_model=projection_model)
    if cam_data.get(_GT_XMP_PROJECTION_MODEL) == "equirectangular":
        projection_kind = "equirectangular"

    stored_coeffs = _stored_float_tuple(cam_data, _GT_XMP_DISTORTION_COEFFS, 6)
    if stored_coeffs is not None:
        k1, k2, k3, k4, t1, t2 = stored_coeffs
        distortion_model = str(cam_data.get(_GT_XMP_DISTORTION_MODEL, distortion_model))

    principal = _stored_float_tuple(cam_data, _GT_XMP_PRINCIPAL, 2) or (0.0, 0.0)
    return RcXmpCameraData(
        name=str(cam_obj.name),
        version=4 if str(projection_kind) == "equirectangular" else 3,
        position_xyz=pos3,
        rotation_row_major9=rot9,
        pose_prior=str(cam_data.get(_GT_XMP_POSE_PRIOR, prior)),
        calibration_prior=str(cam_data.get(_GT_XMP_CALIBRATION_PRIOR, prior)),
        coordinates=str(cam_data.get(_GT_XMP_COORDINATES, "absolute")),
        projection_model=str(cam_data.get(_GT_XMP_PROJECTION_MODEL, projection_kind)),
        projection_prior=str(cam_data.get(_GT_XMP_PROJECTION_PRIOR, prior)),
        projection_convention=str(cam_data.get(_GT_XMP_PROJECTION_CONVENTION, "lonlat")),
        horizontal_fov_deg=float(cam_data.get(_GT_XMP_HORIZONTAL_FOV, 360.0)),
        vertical_fov_deg=float(cam_data.get(_GT_XMP_VERTICAL_FOV, 180.0)),
        focal_length_35mm=float(focal_35),
        skew=float(cam_data.get(_GT_XMP_SKEW, 0.0)),
        aspect_ratio=float(cam_data.get(_GT_XMP_ASPECT, 1.0)),
        principal_u=float(principal[0]),
        principal_v=float(principal[1]),
        distortion_model=str(distortion_model),
        distortion_coefficients=(float(k1), float(k2), float(k3), float(k4), float(t1), float(t2)),
        calibration_group=int(cam_data.get(_GT_XMP_CALIBRATION_GROUP, -1)),
        distortion_group=int(cam_data.get(_GT_XMP_DISTORTION_GROUP, -1)),
        in_texturing=int(cam_data.get(_GT_XMP_IN_TEXTURING, 1)),
        in_meshing=int(cam_data.get(_GT_XMP_IN_MESHING, 1)),
    )


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


def equirectangular_xmp_text(
    *,
    cfg: RcEquirectangularXmpConfig,
    rotation_row_major9: Iterable[float],
    position_xyz: Iterable[float],
) -> str:
    rot_s = " ".join(format_f64(v) for v in rotation_row_major9)
    pos_s = " ".join(format_f64(v) for v in position_xyz)

    return (
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\">\n"
        "  <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\n"
        "    <rdf:Description xmlns:xcr=\"http://www.capturingreality.com/ns/xcr/1.1#\""
        f" xcr:Version=\"{cfg.version}\"\n"
        f"       xcr:PosePrior=\"{cfg.pose_prior}\" xcr:Coordinates=\"{cfg.coordinates}\"\n"
        f"       xcr:ProjectionModel=\"{cfg.projection_model}\" xcr:ProjectionPrior=\"{cfg.projection_prior}\"\n"
        f"       xcr:ProjectionConvention=\"{cfg.projection_convention}\""
        f" xcr:HorizontalFovDeg=\"{format_f64(cfg.horizontal_fov_deg)}\""
        f" xcr:VerticalFovDeg=\"{format_f64(cfg.vertical_fov_deg)}\"\n"
        f"       xcr:CalibrationPrior=\"{cfg.calibration_prior}\" xcr:CalibrationGroup=\"{cfg.calibration_group}\"\n"
        f"       xcr:InTexturing=\"{cfg.in_texturing}\" xcr:InMeshing=\"{cfg.in_meshing}\">\n"
        f"      <xcr:Rotation>{rot_s}</xcr:Rotation>\n"
        f"      <xcr:Position>{pos_s}</xcr:Position>\n"
        "    </rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
    )


def _rc_distortion_model_and_coeffs(
    *,
    distortion_model: str,
    coefficients: Iterable[float],
) -> tuple[str, Tuple[float, float, float, float, float, float]]:
    vals = tuple(float(v) for v in coefficients)
    if len(vals) != 6:
        raise RuntimeError(f"Expected 6 distortion coefficients, got {len(vals)}")
    k1, k2, k3, k4, t1, t2 = vals

    rc_distortion_model = str(distortion_model or "perspective")
    if rc_distortion_model == "perspective":
        return ("perspective", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    if rc_distortion_model == "brown":
        has_k4 = abs(float(k4)) > 0.0
        has_t2 = abs(float(t1)) > 0.0 or abs(float(t2)) > 0.0
        if has_k4:
            rc_distortion_model = "brown4t2" if has_t2 else "brown4"
        else:
            rc_distortion_model = "brown3t2" if has_t2 else "brown3"
    elif rc_distortion_model not in {"brown3", "brown4", "brown3t2", "brown4t2", "division"}:
        return ("perspective", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    return (rc_distortion_model, (k1, k2, k3, k4, t1, t2))


def xmp_text_from_camera_data(data: RcXmpCameraData) -> str:
    if str(data.projection_model).lower() == "equirectangular":
        cfg = RcEquirectangularXmpConfig(
            version=max(4, int(data.version)),
            calibration_prior=data.calibration_prior,
            pose_prior=data.pose_prior,
            coordinates=data.coordinates,
            projection_model="equirectangular",
            projection_prior=data.projection_prior,
            projection_convention=data.projection_convention,
            horizontal_fov_deg=data.horizontal_fov_deg,
            vertical_fov_deg=data.vertical_fov_deg,
            calibration_group=data.calibration_group,
            in_texturing=data.in_texturing,
            in_meshing=data.in_meshing,
        )
        return equirectangular_xmp_text(
            cfg=cfg,
            rotation_row_major9=data.rotation_row_major9,
            position_xyz=data.position_xyz,
        )

    rc_distortion_model, coeffs = _rc_distortion_model_and_coeffs(
        distortion_model=data.distortion_model,
        coefficients=data.distortion_coefficients,
    )
    cfg = RcXmpConfig(
        version=int(data.version) if int(data.version) > 0 else 3,
        calibration_prior=data.calibration_prior,
        pose_prior=data.pose_prior,
        coordinates=data.coordinates,
        distortion_model=rc_distortion_model,
        skew=data.skew,
        aspect_ratio=data.aspect_ratio,
        principal_u=data.principal_u,
        principal_v=data.principal_v,
        k1=coeffs[0],
        k2=coeffs[1],
        k3=coeffs[2],
        k4=coeffs[3],
        t1=coeffs[4],
        t2=coeffs[5],
        calibration_group=data.calibration_group,
        distortion_group=data.distortion_group,
        in_texturing=data.in_texturing,
        in_meshing=data.in_meshing,
    )
    return xmp_text(
        cfg=cfg,
        focal_length_35mm_value=data.focal_length_35mm,
        rotation_row_major9=data.rotation_row_major9,
        position_xyz=data.position_xyz,
    )


def write_xmp_camera_data(*, data: RcXmpCameraData, xmp_path: str) -> None:
    out_dir = os.path.dirname(xmp_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(xmp_path, "wb") as f:
        f.write(xmp_text_from_camera_data(data).encode("utf-8"))


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
    projection_model: str = "auto",
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

    mw = cam_eval.matrix_world.copy()
    C = mw.to_translation()
    R = rotation_from_camera(cam_matrix_world=mw, rotation_mode=rotation_mode)
    rot9 = mat3_to_row_major9(R)
    pos3 = (float(C.x), float(C.y), float(C.z))

    projection_kind = camera_projection_kind(cam_eval.data, projection_model=projection_model)
    if projection_kind == "equirectangular":
        horizontal_fov_deg, vertical_fov_deg = equirectangular_fov_degrees(cam_eval.data)
        pano_cfg = RcEquirectangularXmpConfig(
            calibration_prior=prior,
            pose_prior=prior,
            projection_prior=prior,
            horizontal_fov_deg=horizontal_fov_deg,
            vertical_fov_deg=vertical_fov_deg,
        )
        txt = equirectangular_xmp_text(
            cfg=pano_cfg,
            rotation_row_major9=rot9,
            position_xyz=pos3,
        )
    else:
        focal_35 = focal_length_35mm(scene, cam_eval.data)
        txt = xmp_text(
            cfg=cfg,
            focal_length_35mm_value=focal_35,
            rotation_row_major9=rot9,
            position_xyz=pos3,
        )

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
    projection_model: str = "auto",
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

    projection_kind = camera_projection_kind(cam_eval.data, projection_model=projection_model)
    if projection_kind == "equirectangular":
        focal_35 = 0.0
        fx = fy = cx = cy = 0.0
    else:
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
