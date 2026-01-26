# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
from bpy.types import Panel

from ..runtime import JOB


class GROUNDTRUTH_PT_render_image(Panel):
    bl_idname = "GROUNDTRUTH_PT_render_image"
    bl_label = "GroundTruth (Render)"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "GroundTruth"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout

        col = layout.column(align=True)
        if not JOB.active:
            box = col.box()
            box_col = box.column(align=True)
            box_col.label(text="No active GroundTruth render job", icon="INFO")
            box_col.label(text="Start from View3D → Sidebar → GroundTruth", icon="BLANK1")
            return

        box = col.box()
        if JOB.cancel_requested:
            box.alert = True
        box_col = box.column(align=True)
        box_col.label(text=f"Job: {JOB.mode}", icon="TIME")
        box_col.label(text=f"PID: {JOB.proc_pid}", icon="BLANK1")
        box_col.label(text=f"Frame done: {JOB.last_frame_done}", icon="BLANK1")
        box_col.label(text=f"Next finalize: {JOB.next_frame_to_finalize}", icon="BLANK1")
        box_col.label(text=f"Wrote XMP: {JOB.wrote_xmp}", icon="BLANK1")

        if JOB.last_error:
            box_col.separator()
            box_col.label(text="Last error:", icon="ERROR")
            box_col.label(text=str(JOB.last_error), icon="BLANK1")

        col.separator()

        row = col.row(align=True)
        row.operator("groundtruth.cancel", icon="CANCEL", text="Cancel")
        if JOB.mode.startswith("internal_") and not getattr(bpy.app, "use_event_simulate", False):
            col.label(text="Internal cancel: press Esc", icon="INFO")

        col.separator()

        # Stats table (last finalized frame).
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Intrinsics (last):", icon="CAMERA_DATA")
        box_col.label(text=f"F35: {JOB.last_focal_35mm:.6g} mm", icon="BLANK1")
        box_col.label(text=f"fx,fy: {JOB.last_fx_px:.4g}, {JOB.last_fy_px:.4g} px", icon="BLANK1")
        box_col.label(text=f"cx,cy: {JOB.last_cx_px:.4g}, {JOB.last_cy_px:.4g} px", icon="BLANK1")

        box_col.separator()
        box_col.label(text="Extrinsics (last):", icon="EMPTY_AXIS")
        x, y, z = JOB.last_pos_xyz
        box_col.label(text=f"C: {x:.6g} {y:.6g} {z:.6g}", icon="BLANK1")
        r = JOB.last_rot_row_major9
        if len(r) == 9:
            box_col.label(text=f"R0: {r[0]:.6g} {r[1]:.6g} {r[2]:.6g}", icon="BLANK1")
            box_col.label(text=f"R1: {r[3]:.6g} {r[4]:.6g} {r[5]:.6g}", icon="BLANK1")
            box_col.label(text=f"R2: {r[6]:.6g} {r[7]:.6g} {r[8]:.6g}", icon="BLANK1")
