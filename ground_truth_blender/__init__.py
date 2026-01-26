# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
    "name": "GroundTruth",
    "author": "Erium Vladlen",
    "version": (0, 5, 0),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > GroundTruth",
    "description": "Export RC/RealityScan XMP camera priors (optionally while rendering)",
    "warning": "",
    "category": "Import-Export",
}

import bpy

from . import internal_render_handlers
from . import operators
from . import properties
from . import ui


def register():
    print("GroundTruth: Registering...")

    properties.register()
    operators.register()
    ui.register()
    internal_render_handlers.register()

    bpy.types.Scene.groundtruth_props = bpy.props.PointerProperty(type=properties.GroundTruthSceneProperties)

    print("GroundTruth: Registration complete!")


def unregister():
    print("GroundTruth: Unregistering...")

    del bpy.types.Scene.groundtruth_props

    internal_render_handlers.unregister()
    ui.unregister()
    operators.unregister()
    properties.unregister()

    print("GroundTruth: Unregistration complete!")


if __name__ == "__main__":
    register()
