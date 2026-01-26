# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

import bpy

from . import image_header
from .main_panel import GROUNDTRUTH_PT_main
from .render_panel_image import GROUNDTRUTH_PT_render_image


classes = (
    GROUNDTRUTH_PT_main,
    GROUNDTRUTH_PT_render_image,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    image_header.register()


def unregister():
    image_header.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
