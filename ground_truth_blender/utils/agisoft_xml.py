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

from .rc_xmp import format_f64, intrinsics_px, rotation_from_camera


@dataclass(frozen=True)
class MetashapeCalibration:
    width: int
    height: int
    f: float
    cx: float
    cy: float
    k1: float
    k2: float
    k3: float
    k4: float
    p1: float
    p2: float


@dataclass(frozen=True)
class MetashapeCamera:
    camera_id: int
    label: str
    transform_row_major16: tuple[float, ...]


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
    fx, _fy, _cx_px, _cy_px = intrinsics_px(scene, cam_data)

    if str(distortion_mode) == "perspective":
        k1 = k2 = k3 = k4 = p1 = p2 = 0.0

    # Metashape calibration uses cx/cy as principal point offset relative to image center (pixels).
    # GroundTruth uses nulled principal point.
    return MetashapeCalibration(
        width=w,
        height=h,
        f=float(fx),
        cx=0.0,
        cy=0.0,
        k1=float(k1),
        k2=float(k2),
        k3=float(k3),
        k4=float(k4),
        p1=float(p1),
        p2=float(p2),
    )


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
    import xml.etree.ElementTree as ET

    if cam_obj.type != "CAMERA":
        raise RuntimeError("cam_obj must be a CAMERA object")

    if not frames:
        raise RuntimeError("frames list is empty")

    depsgraph = bpy.context.evaluated_depsgraph_get()
    cam_eval0 = cam_obj.evaluated_get(depsgraph)
    calib = _metashape_calibration_from_scene(
        scene=scene,
        cam_data=cam_eval0.data,
        distortion_mode=distortion_mode,
        k1=k1,
        k2=k2,
        k3=k3,
        k4=k4,
        p1=t1,
        p2=t2,
    )

    cameras_out: list[MetashapeCamera] = []
    restore_frame = int(scene.frame_current)
    try:
        for idx, frame in enumerate(frames):
            scene.frame_set(int(frame))
            depsgraph = bpy.context.evaluated_depsgraph_get()
            cam_eval = cam_obj.evaluated_get(depsgraph)

            mw = cam_eval.matrix_world.copy()
            C = mw.to_translation()
            Rwc = _rotation_rwc_metashape_cv(cam_matrix_world=mw)
            T = _transform_camera_to_world_row_major16(Rwc=Rwc, C_world=(C.x, C.y, C.z))

            label = os.path.basename(str(image_label_for_frame(int(frame))))
            cameras_out.append(MetashapeCamera(camera_id=int(idx), label=label, transform_row_major16=T))
    finally:
        try:
            scene.frame_set(int(restore_frame))
        except Exception:
            pass

    # Build document mirroring the example structure in tests/test_sfm/Motor24mmAgi.xml.
    doc = ET.Element("document", attrib={"version": "1.2.0"})
    chunk = ET.SubElement(doc, "chunk", attrib={"label": "Chunk 1", "enabled": "true"})

    sensors = ET.SubElement(chunk, "sensors", attrib={"next_id": "1"})
    sensor = ET.SubElement(sensors, "sensor", attrib={"id": "0", "label": "unknown", "type": "frame"})
    ET.SubElement(sensor, "resolution", attrib={"width": str(calib.width), "height": str(calib.height)})
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

    # Components: keep it minimal (single component, single partition).
    comps = ET.SubElement(chunk, "components", attrib={"next_id": "1", "active_id": "0"})
    comp0 = ET.SubElement(comps, "component", attrib={"id": "0", "label": "Component 1"})
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
                "sensor_id": "0",
                "component_id": "0",
                "label": str(c.label),
            },
        )
        ET.SubElement(cam_el, "transform").text = _format_floats(c.transform_row_major16)

    ref = ET.SubElement(
        chunk,
        "reference",
    )
    ref.text = 'LOCAL_CS["Local Coordinates (m)",LOCAL_DATUM["Local Datum",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]]]'

    _indent_xml(doc)
    tree = ET.ElementTree(doc)

    out_xml_path = bpy.path.abspath(out_xml_path)
    os.makedirs(os.path.dirname(out_xml_path) or ".", exist_ok=True)
    tree.write(out_xml_path, encoding="UTF-8", xml_declaration=True)
