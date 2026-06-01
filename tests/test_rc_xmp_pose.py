from __future__ import annotations

import math
import pathlib
import re
import tempfile
import unittest

import bpy
from mathutils import Euler, Matrix

from ground_truth_blender.utils.rc_xmp import camera_stats_at_frame, write_xmp_for_camera_at_frame


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


if __name__ == "__main__":
    unittest.main()
