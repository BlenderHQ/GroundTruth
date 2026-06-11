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
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import bpy
from mathutils import Matrix

from .rc_xmp import (
    RcXmpCameraData,
    camera_data_to_xmp_data,
    format_f64,
    has_stored_xmp_metadata,
    intrinsics_px,
    rotation_from_camera,
    sensor_fit_effective,
)


@dataclass(frozen=True)
class MetashapeCalibration:
    width: int
    height: int
    focal_length: float
    f: float
    cx: float
    cy: float
    pixel_width: float | None
    pixel_height: float | None
    k1: float
    k2: float
    k3: float
    k4: float
    p1: float
    p2: float
    group_key: str | None = None


@dataclass(frozen=True)
class MetashapeCamera:
    camera_id: int
    sensor_id: int
    label: str
    transform_row_major16: tuple[float, ...]


@dataclass(frozen=True)
class MetashapeCameraItem:
    camera: bpy.types.Object
    label: str
    image_width: int | None = None
    image_height: int | None = None


def _effective_resolution(scene: bpy.types.Scene) -> tuple[int, int]:
    r = scene.render
    res_x = int(round(float(r.resolution_x) * float(r.resolution_percentage) / 100.0))
    res_y = int(round(float(r.resolution_y) * float(r.resolution_percentage) / 100.0))
    return (max(1, res_x), max(1, res_y))


def _rotation_rwc_metashape_cv(*, cam_matrix_world: Matrix) -> Matrix:
    # Metashape camera "transform" matches camera->world using a CV-like camera frame:
    # x right, y down, z forward. This corresponds to GroundTruth's "rc_rwc".
    return rotation_from_camera(cam_matrix_world=cam_matrix_world, rotation_mode="rc_rwc")


def _transform_camera_to_world_row_major16(*, Rwc: Matrix, C_world: Iterable[float]) -> tuple[float, ...]:
    if len(Rwc) != 3 or len(Rwc[0]) != 3:
        raise RuntimeError("Rwc must be 3x3")
    cx, cy, cz = (float(v) for v in C_world)

    # Metashape stores camera->world: [Rwc | C].
    return (
        float(Rwc[0][0]),
        float(Rwc[0][1]),
        float(Rwc[0][2]),
        float(cx),
        float(Rwc[1][0]),
        float(Rwc[1][1]),
        float(Rwc[1][2]),
        float(cy),
        float(Rwc[2][0]),
        float(Rwc[2][1]),
        float(Rwc[2][2]),
        float(cz),
        0.0,
        0.0,
        0.0,
        1.0,
    )


def _indent_xml(elem, level: int = 0) -> None:
    # Minimal pretty-printer to match the example's layout.
    i = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        for child in elem:
            _indent_xml(child, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i


def _format_floats(values: Sequence[float]) -> str:
    return " ".join(format_f64(v) for v in values)


def _metashape_focal_pixels(scene: bpy.types.Scene, cam_data: bpy.types.Camera) -> float:
    fx, fy, _cx_px, _cy_px = intrinsics_px(scene, cam_data)
    fit = sensor_fit_effective(scene, cam_data)
    return float(fy if fit == "VERTICAL" else fx)


def _metashape_pixel_size_mm(cam_data: bpy.types.Camera, f_px: float) -> float:
    lens_mm = float(cam_data.lens)
    if lens_mm <= 0.0 or float(f_px) <= 0.0:
        raise RuntimeError("Invalid camera lens/focal settings")
    return lens_mm / float(f_px)


def _metashape_calibration_from_scene(
    *,
    scene: bpy.types.Scene,
    cam_data: bpy.types.Camera,
    distortion_mode: str,
    k1: float,
    k2: float,
    k3: float,
    k4: float,
    p1: float,
    p2: float,
) -> MetashapeCalibration:
    w, h = _effective_resolution(scene)
    f_px = _metashape_focal_pixels(scene, cam_data)
    pixel_size_mm = _metashape_pixel_size_mm(cam_data, f_px)

    if str(distortion_mode) == "perspective":
        k1 = k2 = k3 = k4 = p1 = p2 = 0.0

    # Metashape calibration uses cx/cy as principal point offset relative to image center (pixels).
    # GroundTruth uses nulled principal point.
    return MetashapeCalibration(
        width=w,
        height=h,
        focal_length=float(cam_data.lens),
        f=float(f_px),
        cx=0.0,
        cy=0.0,
        pixel_width=float(pixel_size_mm),
        pixel_height=float(pixel_size_mm),
        k1=float(k1),
        k2=float(k2),
        k3=float(k3),
        k4=float(k4),
        p1=float(p1),
        p2=float(p2),
    )


def _image_size_or_scene(scene: bpy.types.Scene, item: MetashapeCameraItem) -> tuple[int, int]:
    if item.image_width is not None and item.image_height is not None:
        w = int(item.image_width)
        h = int(item.image_height)
        if w > 0 and h > 0:
            return (w, h)
    return _effective_resolution(scene)


def _metashape_calibration_from_xmp_data(
    *,
    scene: bpy.types.Scene,
    item: MetashapeCameraItem,
    data: RcXmpCameraData,
) -> MetashapeCalibration:
    w, h = _image_size_or_scene(scene, item)
    focal_35 = float(data.focal_length_35mm)
    f_px = focal_35 * float(w) / 36.0
    k1, k2, k3, k4, t1, t2 = (float(v) for v in data.distortion_coefficients)
    if str(data.distortion_model).lower() == "perspective":
        k1 = k2 = k3 = k4 = t1 = t2 = 0.0

    group_key = None
    if int(data.calibration_group) >= 0 or int(data.distortion_group) >= 0:
        group_key = f"xmp:{int(data.calibration_group)}:{int(data.distortion_group)}"

    return MetashapeCalibration(
        width=int(w),
        height=int(h),
        focal_length=float(focal_35),
        f=float(f_px),
        cx=float(data.principal_u) * float(w),
        cy=-float(data.principal_v) * float(h),
        pixel_width=None,
        pixel_height=None,
        k1=float(k1),
        k2=float(k2),
        k3=float(k3),
        k4=float(k4),
        p1=float(t1),
        p2=float(t2),
        group_key=group_key,
    )


def _calibration_key(calib: MetashapeCalibration) -> tuple[object, ...]:
    if calib.group_key is not None:
        return (
            int(calib.width),
            int(calib.height),
            str(calib.group_key),
        )
    return (
        int(calib.width),
        int(calib.height),
        format_f64(calib.focal_length),
        format_f64(calib.f),
        format_f64(calib.cx),
        format_f64(calib.cy),
        "" if calib.pixel_width is None else format_f64(calib.pixel_width),
        "" if calib.pixel_height is None else format_f64(calib.pixel_height),
        format_f64(calib.k1),
        format_f64(calib.k2),
        format_f64(calib.k3),
        format_f64(calib.k4),
        format_f64(calib.p1),
        format_f64(calib.p2),
    )


def _add_sensor_xml(parent, *, sensor_id: int, calib: MetashapeCalibration) -> None:
    import xml.etree.ElementTree as ET

    sensor = ET.SubElement(parent, "sensor", attrib={"id": str(sensor_id), "label": f"Sensor {sensor_id + 1}", "type": "frame"})
    ET.SubElement(sensor, "resolution", attrib={"width": str(calib.width), "height": str(calib.height)})
    ET.SubElement(sensor, "property", attrib={"name": "focal_length", "value": format_f64(calib.focal_length)})
    if calib.pixel_width is not None:
        ET.SubElement(sensor, "property", attrib={"name": "pixel_width", "value": format_f64(calib.pixel_width)})
    if calib.pixel_height is not None:
        ET.SubElement(sensor, "property", attrib={"name": "pixel_height", "value": format_f64(calib.pixel_height)})
    ET.SubElement(sensor, "property", attrib={"name": "layer_index", "value": "0"})
    bands = ET.SubElement(sensor, "bands")
    ET.SubElement(bands, "band", attrib={"label": "Red"})
    ET.SubElement(bands, "band", attrib={"label": "Green"})
    ET.SubElement(bands, "band", attrib={"label": "Blue"})
    dt = ET.SubElement(sensor, "data_type")
    dt.text = "uint8"

    calib_el = ET.SubElement(sensor, "calibration", attrib={"type": "frame", "class": "adjusted"})
    ET.SubElement(calib_el, "resolution", attrib={"width": str(calib.width), "height": str(calib.height)})
    ET.SubElement(calib_el, "f").text = format_f64(calib.f)
    ET.SubElement(calib_el, "cx").text = format_f64(calib.cx)
    ET.SubElement(calib_el, "cy").text = format_f64(calib.cy)
    ET.SubElement(calib_el, "k1").text = format_f64(calib.k1)
    ET.SubElement(calib_el, "k2").text = format_f64(calib.k2)
    ET.SubElement(calib_el, "k3").text = format_f64(calib.k3)
    if abs(calib.k4) > 0.0:
        ET.SubElement(calib_el, "k4").text = format_f64(calib.k4)
    ET.SubElement(calib_el, "p1").text = format_f64(calib.p1)
    ET.SubElement(calib_el, "p2").text = format_f64(calib.p2)


def _write_metashape_document(
    *,
    calibrations: Sequence[MetashapeCalibration],
    cameras_out: Sequence[MetashapeCamera],
    out_xml_path: str,
) -> None:
    import xml.etree.ElementTree as ET

    # Metashape's current camera XML shape is a document/chunk with sensors,
    # components, cameras, and an identity component transform for local coords.
    doc = ET.Element("document", attrib={"version": "1.2.0"})
    chunk = ET.SubElement(doc, "chunk", attrib={"label": "Chunk 1", "enabled": "true"})

    sensors = ET.SubElement(chunk, "sensors", attrib={"next_id": str(len(calibrations))})
    for sensor_id, calib in enumerate(calibrations):
        _add_sensor_xml(sensors, sensor_id=sensor_id, calib=calib)

    comps = ET.SubElement(chunk, "components", attrib={"next_id": "1", "active_id": "0"})
    comp0 = ET.SubElement(comps, "component", attrib={"id": "0", "label": "Component 1"})
    comp_transform = ET.SubElement(comp0, "transform")
    ET.SubElement(comp_transform, "rotation", attrib={"locked": "false"}).text = "1 0 0 0 1 0 0 0 1"
    ET.SubElement(comp_transform, "translation").text = "0 0 0"
    ET.SubElement(comp_transform, "scale").text = "1"
    part_root = ET.SubElement(comp0, "partition")
    part0 = ET.SubElement(part_root, "partition")
    cam_ids = ET.SubElement(part0, "camera_ids")
    cam_ids.text = " ".join(str(c.camera_id) for c in cameras_out)

    cams = ET.SubElement(chunk, "cameras", attrib={"next_id": str(len(cameras_out)), "next_group_id": "0"})
    for c in cameras_out:
        cam_el = ET.SubElement(
            cams,
            "camera",
            attrib={
                "id": str(c.camera_id),
                "sensor_id": str(c.sensor_id),
                "component_id": "0",
                "label": str(c.label),
            },
        )
        ET.SubElement(cam_el, "transform").text = _format_floats(c.transform_row_major16)

    ref = ET.SubElement(chunk, "reference")
    ref.text = 'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]'

    _indent_xml(doc)
    tree = ET.ElementTree(doc)

    out_xml_path = bpy.path.abspath(out_xml_path)
    os.makedirs(os.path.dirname(out_xml_path) or ".", exist_ok=True)
    tree.write(out_xml_path, encoding="UTF-8", xml_declaration=True)


def _camera_item_from_tuple(item: MetashapeCameraItem | tuple[bpy.types.Object, str]) -> MetashapeCameraItem:
    if isinstance(item, MetashapeCameraItem):
        return item
    if len(item) == 2:
        cam_obj, label = item
        return MetashapeCameraItem(camera=cam_obj, label=str(label))
    if len(item) == 4:
        cam_obj, label, width, height = item
        return MetashapeCameraItem(camera=cam_obj, label=str(label), image_width=int(width), image_height=int(height))
    raise RuntimeError(f"Expected camera item as (camera, label) or (camera, label, width, height), got {len(item)} values")


def _stored_xmp_data_for_metashape(
    *,
    scene: bpy.types.Scene,
    depsgraph: bpy.types.Depsgraph,
    cam_obj: bpy.types.Object,
    frame: int,
    rotation_mode: str,
) -> RcXmpCameraData | None:
    if not has_stored_xmp_metadata(cam_obj):
        return None
    try:
        data = camera_data_to_xmp_data(
            scene=scene,
            depsgraph=depsgraph,
            cam_obj=cam_obj,
            frame=int(frame),
            prior="exact",
            rotation_mode=str(rotation_mode),
            distortion_model="perspective",
            projection_model="perspective",
            set_frame=False,
            use_stored_xmp=True,
        )
    except Exception:
        return None
    if str(data.projection_model).lower() != "perspective":
        return None
    return data


def write_metashape_xml_for_cameras(
    *,
    scene: bpy.types.Scene,
    camera_items: Sequence[MetashapeCameraItem | tuple[bpy.types.Object, str]],
    out_xml_path: str,
    rotation_mode: str,
    distortion_mode: str,
    k1: float,
    k2: float,
    k3: float,
    k4: float,
    t1: float,
    t2: float,
    frame: int | None = None,
) -> None:
    if not camera_items:
        raise RuntimeError("camera_items list is empty")

    restore_frame = int(scene.frame_current)
    if frame is not None:
        scene.frame_set(int(frame))

    calibrations: list[MetashapeCalibration] = []
    calibration_ids: dict[tuple[object, ...], int] = {}
    cameras_out: list[MetashapeCamera] = []
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for idx, raw_item in enumerate(camera_items):
            item = _camera_item_from_tuple(raw_item)
            cam_obj = item.camera
            if cam_obj.type != "CAMERA":
                raise RuntimeError(f"{cam_obj.name!r} is not a CAMERA object")

            cam_eval = cam_obj.evaluated_get(depsgraph)
            xmp_data = _stored_xmp_data_for_metashape(
                scene=scene,
                depsgraph=depsgraph,
                cam_obj=cam_obj,
                frame=int(frame if frame is not None else scene.frame_current),
                rotation_mode=rotation_mode,
            )
            if xmp_data is not None:
                calib = _metashape_calibration_from_xmp_data(scene=scene, item=item, data=xmp_data)
            else:
                calib = _metashape_calibration_from_scene(
                    scene=scene,
                    cam_data=cam_eval.data,
                    distortion_mode=distortion_mode,
                    k1=k1,
                    k2=k2,
                    k3=k3,
                    k4=k4,
                    p1=t1,
                    p2=t2,
                )
            calib_key = _calibration_key(calib)
            sensor_id = calibration_ids.get(calib_key)
            if sensor_id is None:
                sensor_id = len(calibrations)
                calibration_ids[calib_key] = sensor_id
                calibrations.append(calib)

            mw = cam_eval.matrix_world.copy()
            C = mw.to_translation()
            Rwc = _rotation_rwc_metashape_cv(cam_matrix_world=mw)
            T = _transform_camera_to_world_row_major16(Rwc=Rwc, C_world=(C.x, C.y, C.z))

            label = os.path.basename(str(item.label)) or str(cam_obj.name)
            cameras_out.append(
                MetashapeCamera(
                    camera_id=int(idx),
                    sensor_id=int(sensor_id),
                    label=label,
                    transform_row_major16=T,
                )
            )
    finally:
        if frame is not None:
            try:
                scene.frame_set(int(restore_frame))
            except Exception:
                pass

    _write_metashape_document(calibrations=calibrations, cameras_out=cameras_out, out_xml_path=out_xml_path)


def write_metashape_xml_for_frames(
    *,
    scene: bpy.types.Scene,
    cam_obj: bpy.types.Object,
    frames: Sequence[int],
    image_label_for_frame: Callable[[int], str],
    out_xml_path: str,
    rotation_mode: str,
    distortion_mode: str,
    k1: float,
    k2: float,
    k3: float,
    k4: float,
    t1: float,
    t2: float,
) -> None:
    if cam_obj.type != "CAMERA":
        raise RuntimeError("cam_obj must be a CAMERA object")

    if not frames:
        raise RuntimeError("frames list is empty")

    calibrations: list[MetashapeCalibration] = []
    calibration_ids: dict[tuple[object, ...], int] = {}
    cameras_out: list[MetashapeCamera] = []
    restore_frame = int(scene.frame_current)
    try:
        for idx, frame in enumerate(frames):
            scene.frame_set(int(frame))
            depsgraph = bpy.context.evaluated_depsgraph_get()
            cam_eval = cam_obj.evaluated_get(depsgraph)
            calib = _metashape_calibration_from_scene(
                scene=scene,
                cam_data=cam_eval.data,
                distortion_mode=distortion_mode,
                k1=k1,
                k2=k2,
                k3=k3,
                k4=k4,
                p1=t1,
                p2=t2,
            )
            calib_key = _calibration_key(calib)
            sensor_id = calibration_ids.get(calib_key)
            if sensor_id is None:
                sensor_id = len(calibrations)
                calibration_ids[calib_key] = sensor_id
                calibrations.append(calib)

            mw = cam_eval.matrix_world.copy()
            C = mw.to_translation()
            Rwc = _rotation_rwc_metashape_cv(cam_matrix_world=mw)
            T = _transform_camera_to_world_row_major16(Rwc=Rwc, C_world=(C.x, C.y, C.z))

            label = os.path.basename(str(image_label_for_frame(int(frame))))
            cameras_out.append(
                MetashapeCamera(
                    camera_id=int(idx),
                    sensor_id=int(sensor_id),
                    label=label,
                    transform_row_major16=T,
                )
            )
    finally:
        try:
            scene.frame_set(int(restore_frame))
        except Exception:
            pass

    _write_metashape_document(calibrations=calibrations, cameras_out=cameras_out, out_xml_path=out_xml_path)
