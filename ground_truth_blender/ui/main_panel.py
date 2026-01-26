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


class GROUNDTRUTH_PT_main(Panel):
    bl_idname = "GROUNDTRUTH_PT_main"
    bl_label = "GroundTruth"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GroundTruth"

    def draw_header(self, context):
        row = self.layout.row()
        if JOB.active:
            row.label(text="", icon="TIME")
        elif JOB.last_error:
            row.label(text="", icon="ERROR")
            row.alert = True
        else:
            row.label(text="", icon="CHECKMARK")

    def draw(self, context):
        layout = self.layout
        props = context.scene.groundtruth_props

        col = layout.column(align=True)

        # Status (always visible)
        box = col.box()
        box_col = box.column(align=True)
        if JOB.active:
            if JOB.cancel_requested:
                box.alert = True
            box_col.label(text=f"Job: {JOB.mode}", icon="TIME")
            box_col.label(text=f"Frame done: {JOB.last_frame_done}", icon="BLANK1")
            box_col.label(text=f"Wrote XMP: {JOB.wrote_xmp}", icon="BLANK1")
            row = box_col.row(align=True)
            row.operator("groundtruth.cancel", icon="CANCEL", text="Cancel")
            if JOB.mode.startswith("internal_") and not getattr(bpy.app, "use_event_simulate", False):
                box_col.separator()
                box_col.label(text="Internal cancel needs Esc", icon="INFO")
                box_col.label(text="Or launch with --enable-event-simulate", icon="BLANK1")
            # During render, keep the 3D view UI minimal.
            return
        elif JOB.last_error:
            box.alert = True
            box_col.label(text="Last error:", icon="ERROR")
            box_col.label(text=JOB.last_error, icon="BLANK1")
        else:
            box_col.label(text="Ready", icon="INFO")

        col.separator()

        # Inputs
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Inputs:")
        box_col.prop(props, "render_backend")
        box_col.prop(props, "camera_obj")

        col.separator()

        # Output / naming
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Output:")
        box_col.prop(props, "use_scene_render_output")
        box_col.prop(props, "use_scene_frame_range")
        box_col.prop(props, "out_dir")
        box_col.prop(props, "image_pattern")

        col.separator()

        # Export params
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="XMP Params:")
        box_col.prop(props, "prior")
        box_col.prop(props, "rotation_mode")
        box_col.prop(props, "distortion_model")
        if props.distortion_model == "brown":
            box_col.separator()
            box_col.label(text="Distortion Coeffs (RC):")
            row = box_col.row(align=True)
            row.prop(props, "distortion_k1")
            row.prop(props, "distortion_k2")
            row = box_col.row(align=True)
            row.prop(props, "distortion_k3")
            row.prop(props, "distortion_k4")
            row = box_col.row(align=True)
            row.prop(props, "distortion_t1")
            row.prop(props, "distortion_t2")

        col.separator()

        # Frame range
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Range:")
        row = box_col.row(align=True)
        row.prop(props, "frame_start")
        row.prop(props, "frame_end")
        row.prop(props, "frame_step")

        col.separator()

        # Actions
        row = col.row(align=True)
        row.operator("groundtruth.export_xmp_range", icon="EXPORT", text="Export XMP (Range)")

        col.separator()

        row = col.row(align=True)
        row.enabled = not JOB.active
        row.operator("groundtruth.render_frame_and_xmp", icon="RENDER_STILL", text="Render Frame + XMP")

        row = col.row(align=True)
        row.enabled = not JOB.active
        row.operator("groundtruth.render_animation_and_xmp", icon="RENDER_ANIMATION", text="Render Animation + XMP")

        row = col.row(align=True)
        row.enabled = JOB.active
        row.operator("groundtruth.cancel", icon="CANCEL", text="Cancel")
