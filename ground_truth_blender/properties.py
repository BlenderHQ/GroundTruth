# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup


class GroundTruthSceneProperties(PropertyGroup):
    render_backend: EnumProperty(
        name="Render Backend",
        description="Internal uses Blender's render UI/preview; External is cancelable and isolated",
        items=[
            ("INTERNAL", "Internal", "Use Blender's internal render (full preview). Cancel requires Esc (or --enable-event-simulate)."),
            ("EXTERNAL", "External", "Run an external Blender background render (cancelable by killing the process)."),
        ],
        default="INTERNAL",
    )

    camera_obj: PointerProperty(
        name="Camera",
        description="Camera object to export (default: scene active camera)",
        type=bpy.types.Object,
    )

    use_scene_frame_range: BoolProperty(
        name="Use Scene Frame Range",
        description="Use scene frame start/end/step for animation renders (and XMP range export)",
        default=True,
    )

    out_dir: StringProperty(
        name="Output Dir",
        description="Directory for rendered frames and/or XMP sidecars (supports // relative paths)",
        default="//ground_truth",
        subtype="DIR_PATH",
    )

    image_pattern: StringProperty(
        name="Image Pattern",
        description="Image filename pattern used to name XMPs (e.g. frame_{frame:04d}.png)",
        default="frame_{frame:04d}.png",
    )

    use_scene_render_output: BoolProperty(
        name="Use Scene Render Output",
        description="Use scene Render Output path/format/naming (ignores Output Dir/Image Pattern for render ops and XMP naming)",
        default=True,
    )

    frame_start: IntProperty(name="Start", default=1, min=-1000000, max=1000000)
    frame_end: IntProperty(name="End", default=250, min=-1000000, max=1000000)
    frame_step: IntProperty(name="Step", default=1, min=1, max=1000000)

    prior: EnumProperty(
        name="Prior",
        description="xcr:CalibrationPrior and xcr:PosePrior",
        items=[
            ("initial", "initial", ""),
            ("exact", "exact", ""),
            ("locked", "locked", ""),
        ],
        default="exact",
    )

    rotation_mode: EnumProperty(
        name="Rotation Mode",
        description="Rotation matrix convention written into xcr:Rotation",
        items=[
            ("rc_rcw", "RC (Rcw)", "World→camera rotation (CV camera frame: x right, y down, z forward)"),
            ("rc_rwc", "RC (Rwc)", "Camera→world rotation (CV camera frame: x right, y down, z forward)"),
            ("blender_rcw", "Blender (Rcw)", "World→camera rotation (Blender camera frame: x right, y up, -z forward)"),
            ("blender_rwc", "Blender (Rwc)", "Camera→world rotation (Blender camera frame: x right, y up, -z forward)"),
        ],
        default="rc_rcw",
    )

    distortion_model: EnumProperty(
        name="Distortion",
        description="Perspective exports zero distortion. Brown exports coefficients and auto-selects RC brown3/brown4/brown3t2/brown4t2",
        items=[
            ("perspective", "Perspective", "xcr:DistortionModel=perspective, all coefficients exported as 0"),
            ("brown", "Brown", "Auto-selects RC Brown model based on coefficients (k4 and tangential t1/t2)"),
        ],
        default="perspective",
    )

    # RC coefficient order: k1 k2 k3 k4 t1 t2
    distortion_k1: FloatProperty(name="k1", default=0.0, precision=8)
    distortion_k2: FloatProperty(name="k2", default=0.0, precision=8)
    distortion_k3: FloatProperty(name="k3", default=0.0, precision=8)
    distortion_k4: FloatProperty(name="k4", default=0.0, precision=8)
    distortion_t1: FloatProperty(name="t1", default=0.0, precision=8)
    distortion_t2: FloatProperty(name="t2", default=0.0, precision=8)


classes = (GroundTruthSceneProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
