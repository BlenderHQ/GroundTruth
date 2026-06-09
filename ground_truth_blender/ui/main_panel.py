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
from ..utils.rc_xmp import has_stored_xmp_metadata, xmp_metadata_summary_lines


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
        row = box_col.row(align=True)
        row.prop(props, "output_format", expand=True)
        box_col.prop(props, "render_backend")
        box_col.prop(props, "camera_obj")

        col.separator()

        # Distortion (shared by both XMP and Metashape XML outputs)
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Distortion:")
        box_col.prop(props, "distortion_model")
        if props.distortion_model == "brown":
            box_col.separator()
            box_col.label(text="Coeffs (RC order):")
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

        # Output / naming
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Output:")
        box_col.prop(props, "use_scene_render_output")
        box_col.prop(props, "use_scene_frame_range")
        naming_col = box_col.column(align=True)
        naming_col.enabled = not bool(props.use_scene_render_output) or props.xmp_camera_mode == "MULTI_CAMERA"
        naming_col.prop(props, "out_dir")
        if props.xmp_camera_mode != "MULTI_CAMERA":
            naming_col.prop(props, "image_pattern")
        if bool(props.use_scene_render_output) and props.xmp_camera_mode != "MULTI_CAMERA":
            box_col.label(text="Naming uses scene Render Output", icon="INFO")
        if props.xmp_camera_mode == "MULTI_CAMERA":
            box_col.label(text="Multi-Camera export uses Output Dir", icon="INFO")
        if str(props.output_format) == "METASHAPE":
            box_col.separator()
            box_col.prop(props, "metashape_xml_filename")

        col.separator()

        # Export params
        if str(props.output_format) == "XMP":
            box = col.box()
            box_col = box.column(align=True)
            box_col.label(text="XMP Params:")
            box_col.prop(props, "xmp_camera_mode", expand=True)
            if props.xmp_camera_mode == "MULTI_CAMERA":
                box_col.prop(props, "xmp_multi_camera_source")
                box_col.prop(props, "xmp_import_behavior")
                box_col.prop(props, "xmp_name_match_flags")
                box_col.prop(props, "xmp_preserve_imported_metadata")
            box_col.prop(props, "projection_model")
            box_col.prop(props, "prior")
            box_col.prop(props, "rotation_mode")

            active_cam = context.view_layer.objects.active
            if active_cam is None or active_cam.type != "CAMERA":
                active_cam = props.camera_obj or context.scene.camera
            if has_stored_xmp_metadata(active_cam):
                box_col.separator()
                box_col.label(text=f"Stored XMP: {active_cam.name}", icon="INFO")
                for line in xmp_metadata_summary_lines(active_cam):
                    box_col.label(text=line, icon="BLANK1")
                box_col.operator("groundtruth.clear_xmp_metadata", icon="TRASH", text="Clear Stored XMP")
        else:
            box = col.box()
            box_col = box.column(align=True)
            box_col.label(text="Metashape Params:")
            box_col.prop(props, "xmp_camera_mode", expand=True)
            if props.xmp_camera_mode == "MULTI_CAMERA":
                box_col.prop(props, "xmp_multi_camera_source")

        col.separator()

        # Frame range
        box = col.box()
        box_col = box.column(align=True)
        box_col.label(text="Range:")
        row = box_col.row(align=True)
        row.enabled = not bool(props.use_scene_frame_range)
        row.prop(props, "frame_start")
        row.prop(props, "frame_end")
        row.prop(props, "frame_step")
        if bool(props.use_scene_frame_range):
            box_col.label(text="Range uses scene frame start/end/step", icon="INFO")

        col.separator()

        # Actions
        if str(props.output_format) == "XMP":
            if props.xmp_camera_mode == "MULTI_CAMERA":
                row = col.row(align=True)
                row.operator("groundtruth.import_xmp_cameras", icon="IMPORT", text="Import XMP Cameras")
                row = col.row(align=True)
                row.operator("groundtruth.export_xmp_cameras", icon="EXPORT", text="Export XMP Cameras")
            else:
                row = col.row(align=True)
                row.operator("groundtruth.export_xmp_range", icon="EXPORT", text="Export XMP (Range)")
        else:
            if props.xmp_camera_mode == "MULTI_CAMERA":
                row = col.row(align=True)
                row.operator("groundtruth.export_metashape_xml_cameras", icon="EXPORT", text="Export Metashape XML Cameras")
            else:
                row = col.row(align=True)
                row.operator("groundtruth.export_metashape_xml_range", icon="EXPORT", text="Export Metashape XML (Range)")

        col.separator()

        if str(props.output_format) == "XMP":
            row = col.row(align=True)
            row.enabled = not JOB.active and props.xmp_camera_mode != "MULTI_CAMERA"
            row.operator("groundtruth.render_frame_and_xmp", icon="RENDER_STILL", text="Render Frame + XMP")

            row = col.row(align=True)
            row.enabled = not JOB.active and props.xmp_camera_mode != "MULTI_CAMERA"
            row.operator("groundtruth.render_animation_and_xmp", icon="RENDER_ANIMATION", text="Render Animation + XMP")
        else:
            row = col.row(align=True)
            row.enabled = not JOB.active and props.xmp_camera_mode != "MULTI_CAMERA"
            row.operator("groundtruth.render_animation_and_metashape", icon="RENDER_ANIMATION", text="Render Animation + XML")

        row = col.row(align=True)
        row.enabled = JOB.active
        row.operator("groundtruth.cancel", icon="CANCEL", text="Cancel")
