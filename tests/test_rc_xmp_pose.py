from __future__ import annotations

import math
import pathlib
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bpy
from mathutils import Euler, Matrix

from ground_truth_blender import operators, properties
from ground_truth_blender.utils.agisoft_xml import MetashapeCameraItem, write_metashape_xml_for_cameras, write_metashape_xml_for_frames
from ground_truth_blender.utils.rc_xmp import (
    RcXmpCameraData,
    apply_xmp_camera_data_to_camera,
    camera_data_to_xmp_data,
    camera_matrix_world_from_xmp_pose,
    camera_stats_at_frame,
    has_stored_xmp_metadata,
    read_xmp_camera_data,
    write_xmp_camera_data,
    write_xmp_for_camera_at_frame,
)


def _flatten_row_major3(m: Matrix) -> tuple[float, ...]:
    return tuple(float(m[i][j]) for i in range(3) for j in range(3))


def _independent_rotation(cam_matrix_world: Matrix, rotation_mode: str) -> tuple[float, ...]:
    _loc, rot_quat, _scale = cam_matrix_world.decompose()
    rwc_bl = rot_quat.to_matrix()

    if rotation_mode == "blender_rwc":
        return _flatten_row_major3(rwc_bl)
    if rotation_mode == "blender_rcw":
        return _flatten_row_major3(rwc_bl.transposed())

    cv_to_blender_cam = Matrix.Diagonal((1.0, -1.0, -1.0))
    rwc_cv = rwc_bl @ cv_to_blender_cam
    if rotation_mode == "rc_rwc":
        return _flatten_row_major3(rwc_cv)
    if rotation_mode == "rc_rcw":
        return _flatten_row_major3(rwc_cv.transposed())
    raise AssertionError(f"unexpected rotation_mode={rotation_mode!r}")


def _row_lengths(rot9: tuple[float, ...]) -> tuple[float, float, float]:
    rows = (rot9[0:3], rot9[3:6], rot9[6:9])
    return tuple(math.sqrt(sum(float(v) * float(v) for v in row)) for row in rows)


class RcXmpPoseTests(unittest.TestCase):
    rotation_mode = "rc_rcw"

    def setUp(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        self.scene.frame_start = 1
        self.scene.frame_end = 20

    def _camera(self, name: str) -> bpy.types.Object:
        cam_data = bpy.data.cameras.new(f"{name}Data")
        cam_obj = bpy.data.objects.new(name, cam_data)
        self.scene.collection.objects.link(cam_obj)
        return cam_obj

    def _empty(self, name: str) -> bpy.types.Object:
        obj = bpy.data.objects.new(name, None)
        self.scene.collection.objects.link(obj)
        return obj

    def _expected_pose(self, cam_obj: bpy.types.Object, frame: int) -> tuple[tuple[float, float, float], tuple[float, ...]]:
        self.scene.frame_set(int(frame))
        depsgraph = bpy.context.evaluated_depsgraph_get()
        cam_eval = cam_obj.evaluated_get(depsgraph)
        mw = cam_eval.matrix_world.copy()
        pos = tuple(float(v) for v in mw.to_translation())
        rot = _independent_rotation(mw, self.rotation_mode)
        return pos, rot

    def _assert_pose_matches(self, cam_obj: bpy.types.Object, frame: int) -> None:
        expected_pos, expected_rot = self._expected_pose(cam_obj, frame)
        _focal_35, pos3, rot9, _fx, _fy, _cx, _cy = camera_stats_at_frame(
            scene=self.scene,
            depsgraph=None,
            cam_obj=cam_obj,
            frame=frame,
            rotation_mode=self.rotation_mode,
            set_frame=True,
        )
        self.assertSequenceAlmostEqual(expected_pos, pos3)
        self.assertSequenceAlmostEqual(expected_rot, rot9)

    def assertSequenceAlmostEqual(self, expected, actual, places: int = 6) -> None:
        self.assertEqual(len(expected), len(actual))
        for exp, got in zip(expected, actual):
            self.assertAlmostEqual(float(exp), float(got), places=places)

    def test_parented_camera_uses_evaluated_world_pose(self) -> None:
        cam = self._camera("CamParented")
        parent = self._empty("RigParent")
        cam.parent = parent
        cam.matrix_parent_inverse.identity()
        cam.location = (1.25, -2.0, 3.5)
        cam.rotation_euler = Euler((0.2, -0.1, 0.35), "XYZ")

        parent.location = (10.0, 20.0, -5.0)
        parent.rotation_euler = Euler((0.5, -0.25, 1.1), "XYZ")
        parent.keyframe_insert(data_path="location", frame=1)
        parent.keyframe_insert(data_path="rotation_euler", frame=1)
        parent.location = (-4.0, 7.0, 12.0)
        parent.rotation_euler = Euler((-0.6, 0.4, -1.2), "XYZ")
        parent.keyframe_insert(data_path="location", frame=10)
        parent.keyframe_insert(data_path="rotation_euler", frame=10)

        self._assert_pose_matches(cam, frame=1)
        self._assert_pose_matches(cam, frame=10)

    def test_constraint_driven_camera_uses_evaluated_world_pose(self) -> None:
        cam = self._camera("CamConstraint")
        target = self._empty("ConstraintTarget")
        constraint = cam.constraints.new(type="COPY_TRANSFORMS")
        constraint.target = target

        target.location = (3.0, 4.0, 5.0)
        target.rotation_euler = Euler((0.3, 0.6, -0.2), "XYZ")
        target.keyframe_insert(data_path="location", frame=1)
        target.keyframe_insert(data_path="rotation_euler", frame=1)
        target.location = (-8.0, 1.0, 2.5)
        target.rotation_euler = Euler((0.9, -0.1, 0.75), "XYZ")
        target.keyframe_insert(data_path="location", frame=10)
        target.keyframe_insert(data_path="rotation_euler", frame=10)

        self._assert_pose_matches(cam, frame=1)
        self._assert_pose_matches(cam, frame=10)

    def test_xmp_rotation_strips_parent_scale(self) -> None:
        cam = self._camera("CamScaledParent")
        parent = self._empty("ScaledParent")
        cam.parent = parent
        cam.matrix_parent_inverse.identity()
        cam.location = (1.0, 2.0, 3.0)
        cam.rotation_euler = Euler((0.2, 0.3, 0.4), "XYZ")
        parent.rotation_euler = Euler((0.5, -0.25, 1.0), "XYZ")
        parent.scale = (2.0, 3.0, 4.0)

        expected_pos, expected_rot = self._expected_pose(cam, frame=1)
        with tempfile.TemporaryDirectory(prefix="groundtruth_xmp_test_") as tmp_dir:
            xmp_path = pathlib.Path(tmp_dir) / "frame_0001.xmp"
            write_xmp_for_camera_at_frame(
                scene=self.scene,
                depsgraph=None,
                cam_obj=cam,
                frame=1,
                xmp_path=str(xmp_path),
                prior="exact",
                rotation_mode=self.rotation_mode,
                distortion_model="perspective",
                set_frame=True,
            )

            text = xmp_path.read_text(encoding="utf-8")
            rot_match = re.search(r"<xcr:Rotation>([^<]+)</xcr:Rotation>", text)
            pos_match = re.search(r"<xcr:Position>([^<]+)</xcr:Position>", text)
            self.assertIsNotNone(rot_match)
            self.assertIsNotNone(pos_match)

            rot9 = tuple(float(v) for v in rot_match.group(1).split())
            pos3 = tuple(float(v) for v in pos_match.group(1).split())

        self.assertSequenceAlmostEqual(expected_pos, pos3)
        self.assertSequenceAlmostEqual(expected_rot, rot9)
        for length in _row_lengths(rot9):
            self.assertAlmostEqual(1.0, float(length), places=6)

    def test_equirectangular_projection_can_be_forced_for_pano_camera(self) -> None:
        cam = self._camera("CamPano")
        cam.data.type = "PANO"
        cam.location = (-4.0, -6.0, 2.0)

        with tempfile.TemporaryDirectory(prefix="groundtruth_pano_xmp_test_") as tmp_dir:
            xmp_path = pathlib.Path(tmp_dir) / "0001.xmp"
            write_xmp_for_camera_at_frame(
                scene=self.scene,
                depsgraph=None,
                cam_obj=cam,
                frame=1,
                xmp_path=str(xmp_path),
                prior="exact",
                rotation_mode=self.rotation_mode,
                distortion_model="perspective",
                projection_model="equirectangular",
                set_frame=True,
            )

            text = xmp_path.read_text(encoding="utf-8")

        self.assertIn('xcr:ProjectionModel="equirectangular"', text)
        self.assertIn('xcr:ProjectionConvention="lonlat"', text)
        self.assertIn('xcr:HorizontalFovDeg="360"', text)
        self.assertIn('xcr:VerticalFovDeg="180"', text)
        self.assertNotIn("xcr:FocalLength35mm", text)
        self.assertNotIn("xcr:DistortionModel", text)

        focal_35, _pos3, _rot9, fx, fy, cx, cy = camera_stats_at_frame(
            scene=self.scene,
            depsgraph=None,
            cam_obj=cam,
            frame=1,
            rotation_mode=self.rotation_mode,
            projection_model="equirectangular",
            set_frame=True,
        )
        self.assertEqual(0.0, focal_35)
        self.assertEqual((0.0, 0.0, 0.0, 0.0), (fx, fy, cx, cy))

    def test_xmp_pose_inverse_matches_export_rotation_modes(self) -> None:
        cam = self._camera("CamInverse")
        cam.location = (12.5, -7.25, 3.125)
        cam.rotation_euler = Euler((0.33, -0.44, 0.55), "XYZ")
        self.scene.frame_set(1)
        mw = cam.matrix_world.copy()
        expected_pos = tuple(float(v) for v in mw.to_translation())

        for rotation_mode in ("rc_rcw", "rc_rwc", "blender_rcw", "blender_rwc"):
            rot9 = _independent_rotation(mw, rotation_mode)
            imported_mw = camera_matrix_world_from_xmp_pose(
                position_xyz=expected_pos,
                rotation_row_major9=rot9,
                rotation_mode=rotation_mode,
            )
            self.assertSequenceAlmostEqual(expected_pos, tuple(float(v) for v in imported_mw.to_translation()))
            self.assertSequenceAlmostEqual(
                _flatten_row_major3(mw.to_3x3()),
                _flatten_row_major3(imported_mw.to_3x3()),
            )

    def test_imported_xmp_metadata_is_preserved_for_camera_export(self) -> None:
        cam = self._camera("ImportedXmpCam")
        data = RcXmpCameraData(
            name=cam.name,
            position_xyz=(1.25, -2.5, 3.75),
            rotation_row_major9=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            focal_length_35mm=35.12345678901235,
            skew=0.012345678901,
            aspect_ratio=1.000123456789,
            principal_u=0.123456789012,
            principal_v=-0.234567890123,
            distortion_model="brown4t2",
            distortion_coefficients=(0.1, -0.02, 0.003, -0.0004, 0.00005, -0.000006),
            calibration_group=7,
            distortion_group=9,
            in_texturing=0,
            in_meshing=1,
        )
        apply_xmp_camera_data_to_camera(cam_obj=cam, data=data, rotation_mode=self.rotation_mode)

        exported = camera_data_to_xmp_data(
            scene=self.scene,
            depsgraph=None,
            cam_obj=cam,
            frame=1,
            prior="exact",
            rotation_mode=self.rotation_mode,
            distortion_model="perspective",
            set_frame=True,
            use_stored_xmp=True,
        )

        self.assertAlmostEqual(data.focal_length_35mm, exported.focal_length_35mm, places=12)
        self.assertAlmostEqual(data.principal_u, exported.principal_u, places=12)
        self.assertAlmostEqual(data.principal_v, exported.principal_v, places=12)
        self.assertEqual(data.distortion_model, exported.distortion_model)
        self.assertSequenceAlmostEqual(data.distortion_coefficients, exported.distortion_coefficients, places=12)
        self.assertEqual(data.calibration_group, exported.calibration_group)
        self.assertEqual(data.distortion_group, exported.distortion_group)

        with tempfile.TemporaryDirectory(prefix="groundtruth_xmp_roundtrip_test_") as tmp_dir:
            xmp_path = pathlib.Path(tmp_dir) / "ImportedXmpCam.xmp"
            write_xmp_camera_data(data=exported, xmp_path=str(xmp_path))
            parsed = read_xmp_camera_data(str(xmp_path))

        self.assertAlmostEqual(data.focal_length_35mm, parsed.focal_length_35mm, places=12)
        self.assertAlmostEqual(data.principal_u, parsed.principal_u, places=12)
        self.assertAlmostEqual(data.principal_v, parsed.principal_v, places=12)
        self.assertEqual(data.distortion_model, parsed.distortion_model)
        self.assertSequenceAlmostEqual(data.distortion_coefficients, parsed.distortion_coefficients, places=12)
        self.assertSequenceAlmostEqual(data.position_xyz, parsed.position_xyz)
        self.assertSequenceAlmostEqual(data.rotation_row_major9, parsed.rotation_row_major9)

    def test_metashape_xml_writer_includes_native_import_fields(self) -> None:
        cam = self._camera("MetashapeCam")
        cam.data.lens = 28.0
        cam.data.sensor_fit = "HORIZONTAL"
        cam.data.sensor_width = 36.0
        cam.location = (1.0, 2.0, 3.0)
        cam.rotation_euler = Euler((0.1, 0.2, 0.3), "XYZ")

        with tempfile.TemporaryDirectory(prefix="groundtruth_metashape_xml_test_") as tmp_dir:
            xml_path = pathlib.Path(tmp_dir) / "metashape.xml"
            write_metashape_xml_for_frames(
                scene=self.scene,
                cam_obj=cam,
                frames=[1, 2],
                image_label_for_frame=lambda frame: f"frame_{frame:04d}.png",
                out_xml_path=str(xml_path),
                rotation_mode="rc_rwc",
                distortion_mode="perspective",
                k1=0.0,
                k2=0.0,
                k3=0.0,
                k4=0.0,
                t1=0.0,
                t2=0.0,
            )
            root = ET.parse(xml_path).getroot()

        self.assertEqual("1.2.0", root.attrib.get("version"))
        sensor = root.find("./chunk/sensors/sensor")
        self.assertIsNotNone(sensor)
        sensor_props = {item.attrib.get("name"): item.attrib.get("value") for item in sensor.findall("property")}
        self.assertIn("pixel_width", sensor_props)
        self.assertIn("pixel_height", sensor_props)
        self.assertIn("focal_length", sensor_props)
        self.assertIsNotNone(root.find("./chunk/components/component/transform/rotation"))
        self.assertIsNotNone(root.find("./chunk/components/component/transform/translation"))

        cameras = root.findall("./chunk/cameras/camera")
        self.assertEqual(2, len(cameras))
        self.assertEqual("0", cameras[0].attrib.get("sensor_id"))
        self.assertEqual("frame_0001.png", cameras[0].attrib.get("label"))
        transform_values = cameras[0].findtext("transform", "").split()
        self.assertEqual(16, len(transform_values))
        self.assertIsNone(cameras[0].find("rotation_covariance"))
        self.assertIsNone(cameras[0].find("location_covariance"))
        self.assertIsNone(cameras[1].find("rotation_covariance"))
        self.assertIsNone(cameras[1].find("location_covariance"))

    def test_metashape_multi_camera_export_groups_stored_xmp_intrinsics_as_sensors(self) -> None:
        cam_a = self._camera("CamGroupA")
        cam_b = self._camera("CamGroupB")
        cam_c = self._camera("CamGroupC")

        shared = RcXmpCameraData(
            name="shared",
            position_xyz=(0.0, 0.0, 0.0),
            rotation_row_major9=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            focal_length_35mm=153.84615384615384,
            principal_u=0.1,
            principal_v=-0.2,
            distortion_model="brown3t2",
            distortion_coefficients=(0.01, -0.02, 0.003, 0.0, 0.0004, -0.0005),
            calibration_group=10,
            distortion_group=20,
        )
        second = RcXmpCameraData(
            name="second",
            position_xyz=(0.0, 0.0, 0.0),
            rotation_row_major9=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            focal_length_35mm=155.0,
            principal_u=-0.05,
            principal_v=0.03,
            distortion_model="brown3",
            distortion_coefficients=(-0.1, 0.2, -0.03, 0.0, 0.0, 0.0),
            calibration_group=11,
            distortion_group=21,
        )
        apply_xmp_camera_data_to_camera(cam_obj=cam_a, data=shared, rotation_mode=self.rotation_mode)
        apply_xmp_camera_data_to_camera(cam_obj=cam_b, data=shared, rotation_mode=self.rotation_mode)
        apply_xmp_camera_data_to_camera(cam_obj=cam_c, data=second, rotation_mode=self.rotation_mode)

        with tempfile.TemporaryDirectory(prefix="groundtruth_metashape_xmp_groups_test_") as tmp_dir:
            xml_path = pathlib.Path(tmp_dir) / "grouped.xml"
            write_metashape_xml_for_cameras(
                scene=self.scene,
                camera_items=[
                    MetashapeCameraItem(cam_a, "A.jpg", 5304, 7952),
                    MetashapeCameraItem(cam_b, "B.jpg", 5304, 7952),
                    MetashapeCameraItem(cam_c, "C.jpg", 5301, 7952),
                ],
                out_xml_path=str(xml_path),
                rotation_mode=self.rotation_mode,
                distortion_mode="perspective",
                k1=0.0,
                k2=0.0,
                k3=0.0,
                k4=0.0,
                t1=0.0,
                t2=0.0,
                frame=1,
            )
            root = ET.parse(xml_path).getroot()

        sensors = root.findall("./chunk/sensors/sensor")
        cameras = root.findall("./chunk/cameras/camera")
        self.assertEqual(2, len(sensors))
        self.assertEqual(["0", "0", "1"], [cam.attrib.get("sensor_id") for cam in cameras])

        calib0 = sensors[0].find("calibration")
        self.assertEqual("5304", sensors[0].find("resolution").attrib.get("width"))
        self.assertAlmostEqual(22666.666666666668, float(calib0.findtext("f")), places=6)
        self.assertAlmostEqual(530.4, float(calib0.findtext("cx")), places=6)
        self.assertAlmostEqual(1590.4, float(calib0.findtext("cy")), places=6)
        self.assertAlmostEqual(0.01, float(calib0.findtext("k1")), places=12)
        self.assertAlmostEqual(0.0004, float(calib0.findtext("p1")), places=12)
        self.assertIsNone(sensors[0].find("property[@name='pixel_width']"))


class GroundTruthXmpOperatorTests(unittest.TestCase):
    rotation_mode = "rc_rcw"
    _properties_registered = False
    _operators_registered = False
    _scene_pointer_registered = False

    @classmethod
    def setUpClass(cls) -> None:
        try:
            properties.register()
            cls._properties_registered = True
        except ValueError:
            cls._properties_registered = False

        if not hasattr(bpy.types.Scene, "groundtruth_props"):
            bpy.types.Scene.groundtruth_props = bpy.props.PointerProperty(type=properties.GroundTruthSceneProperties)
            cls._scene_pointer_registered = True

        try:
            operators.register()
            cls._operators_registered = True
        except ValueError:
            cls._operators_registered = False

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._operators_registered:
            operators.unregister()
        if cls._scene_pointer_registered and hasattr(bpy.types.Scene, "groundtruth_props"):
            del bpy.types.Scene.groundtruth_props
        if cls._properties_registered:
            properties.unregister()

    def setUp(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        self.scene = bpy.context.scene
        props = self.scene.groundtruth_props
        props.output_format = "XMP"
        props.xmp_camera_mode = "MULTI_CAMERA"
        props.rotation_mode = self.rotation_mode
        props.use_scene_render_output = False

    def _camera(self, name: str) -> bpy.types.Object:
        cam_data = bpy.data.cameras.new(f"{name}Data")
        cam_obj = bpy.data.objects.new(name, cam_data)
        self.scene.collection.objects.link(cam_obj)
        return cam_obj

    def _sample_xmp_data(self, name: str) -> RcXmpCameraData:
        return RcXmpCameraData(
            name=name,
            position_xyz=(2.0, -3.0, 4.0),
            rotation_row_major9=(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            focal_length_35mm=42.25,
            principal_u=0.125,
            principal_v=-0.25,
            distortion_model="brown3",
            distortion_coefficients=(0.01, -0.02, 0.003, 0.0, 0.0, 0.0),
            calibration_group=3,
            distortion_group=4,
        )

    def _camera_count(self) -> int:
        return sum(1 for obj in self.scene.objects if obj.type == "CAMERA")

    def test_import_xmp_cameras_updates_existing_camera_by_name_flags(self) -> None:
        existing = self._camera("samplecam")
        with tempfile.TemporaryDirectory(prefix="groundtruth_import_op_test_") as tmp_dir:
            xmp_path = pathlib.Path(tmp_dir) / "SampleCam.xmp"
            write_xmp_camera_data(data=self._sample_xmp_data("SampleCam"), xmp_path=str(xmp_path))

            result = bpy.ops.groundtruth.import_xmp_cameras(
                "EXEC_DEFAULT",
                directory=str(pathlib.Path(tmp_dir)) + "/",
                files=[{"name": xmp_path.name}],
            )

        self.assertEqual({"FINISHED"}, result)
        self.assertEqual(1, self._camera_count())
        self.assertTrue(has_stored_xmp_metadata(existing))
        self.assertAlmostEqual(42.25, float(existing.data["groundtruth_xmp_focal_length35mm"]), places=12)
        self.assertAlmostEqual(2.0, float(existing.location.x), places=6)

    def test_import_xmp_cameras_can_always_create_new_camera(self) -> None:
        self._camera("DuplicateCam")
        self.scene.groundtruth_props.xmp_import_behavior = "ALWAYS_CREATE"
        with tempfile.TemporaryDirectory(prefix="groundtruth_import_create_op_test_") as tmp_dir:
            xmp_path = pathlib.Path(tmp_dir) / "DuplicateCam.xmp"
            write_xmp_camera_data(data=self._sample_xmp_data("DuplicateCam"), xmp_path=str(xmp_path))

            result = bpy.ops.groundtruth.import_xmp_cameras(
                "EXEC_DEFAULT",
                directory=str(pathlib.Path(tmp_dir)) + "/",
                files=[{"name": xmp_path.name}],
            )

        self.assertEqual({"FINISHED"}, result)
        self.assertEqual(2, self._camera_count())

    def test_export_xmp_cameras_uses_selected_source_and_preserved_metadata(self) -> None:
        cam_a = self._camera("CamA")
        cam_b = self._camera("CamB")
        apply_xmp_camera_data_to_camera(cam_obj=cam_b, data=self._sample_xmp_data("CamB"), rotation_mode=self.rotation_mode)
        cam_a.select_set(False)
        cam_b.select_set(True)
        bpy.context.view_layer.objects.active = cam_b

        props = self.scene.groundtruth_props
        props.xmp_multi_camera_source = "SELECTED"
        with tempfile.TemporaryDirectory(prefix="groundtruth_export_op_test_") as tmp_dir:
            props.out_dir = tmp_dir
            result = bpy.ops.groundtruth.export_xmp_cameras("EXEC_DEFAULT")
            out_a = pathlib.Path(tmp_dir) / "CamA.xmp"
            out_b = pathlib.Path(tmp_dir) / "CamB.xmp"
            out_a_exists = out_a.exists()
            out_b_exists = out_b.exists()
            parsed = read_xmp_camera_data(str(out_b))

        self.assertEqual({"FINISHED"}, result)
        self.assertFalse(out_a_exists)
        self.assertTrue(out_b_exists)
        self.assertAlmostEqual(42.25, parsed.focal_length_35mm, places=12)
        self.assertAlmostEqual(0.125, parsed.principal_u, places=12)
        self.assertEqual("brown3", parsed.distortion_model)

    def test_clear_xmp_metadata_operator_clears_selected_cameras(self) -> None:
        cam = self._camera("ClearCam")
        apply_xmp_camera_data_to_camera(cam_obj=cam, data=self._sample_xmp_data("ClearCam"), rotation_mode=self.rotation_mode)
        cam.select_set(True)
        bpy.context.view_layer.objects.active = cam

        result = bpy.ops.groundtruth.clear_xmp_metadata("EXEC_DEFAULT")

        self.assertEqual({"FINISHED"}, result)
        self.assertFalse(has_stored_xmp_metadata(cam))

    def test_export_metashape_xml_cameras_uses_selected_source(self) -> None:
        cam_a = self._camera("CamA")
        cam_b = self._camera("CamB")
        cam_b.location = (4.0, 5.0, 6.0)
        cam_a.select_set(False)
        cam_b.select_set(True)
        bpy.context.view_layer.objects.active = cam_b

        props = self.scene.groundtruth_props
        props.output_format = "METASHAPE"
        props.xmp_camera_mode = "MULTI_CAMERA"
        props.xmp_multi_camera_source = "SELECTED"
        with tempfile.TemporaryDirectory(prefix="groundtruth_export_metashape_op_test_") as tmp_dir:
            props.out_dir = tmp_dir
            props.metashape_xml_filename = "selected.xml"
            result = bpy.ops.groundtruth.export_metashape_xml_cameras("EXEC_DEFAULT")
            root = ET.parse(pathlib.Path(tmp_dir) / "selected.xml").getroot()

        self.assertEqual({"FINISHED"}, result)
        self.assertEqual("1.2.0", root.attrib.get("version"))
        cameras = root.findall("./chunk/cameras/camera")
        self.assertEqual(1, len(cameras))
        self.assertEqual("CamB", cameras[0].attrib.get("label"))
        self.assertEqual("0", cameras[0].attrib.get("sensor_id"))
        self.assertEqual(16, len(cameras[0].findtext("transform", "").split()))
        self.assertIsNone(cameras[0].find("rotation_covariance"))
        self.assertIsNone(cameras[0].find("location_covariance"))
        self.assertIsNotNone(root.find("./chunk/sensors/sensor/property[@name='pixel_width']"))
        self.assertIsNotNone(root.find("./chunk/components/component/transform/rotation"))


if __name__ == "__main__":
    unittest.main()
