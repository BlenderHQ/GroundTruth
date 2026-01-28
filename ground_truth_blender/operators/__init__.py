# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

import bpy

from .export_metashape_xml import GROUNDTRUTH_OT_export_metashape_xml_range
from .export_xmp_range import GROUNDTRUTH_OT_export_xmp_range
from .render_and_xmp import (
    GROUNDTRUTH_OT_cancel,
    GROUNDTRUTH_OT_render_animation_and_metashape,
    GROUNDTRUTH_OT_render_animation_and_xmp,
    GROUNDTRUTH_OT_render_frame_and_xmp,
)


classes = (
    GROUNDTRUTH_OT_export_xmp_range,
    GROUNDTRUTH_OT_export_metashape_xml_range,
    GROUNDTRUTH_OT_render_frame_and_xmp,
    GROUNDTRUTH_OT_render_animation_and_xmp,
    GROUNDTRUTH_OT_render_animation_and_metashape,
    GROUNDTRUTH_OT_cancel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
