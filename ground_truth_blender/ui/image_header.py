# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

import bpy

from ..runtime import JOB


def _draw_image_header(self, context):
    if not JOB.active:
        return
    layout = self.layout
    row = layout.row(align=True)
    row.alert = JOB.cancel_requested
    row.label(text=f"GroundTruth: {JOB.mode} (frame {JOB.last_frame_done})", icon="TIME")
    row.operator("groundtruth.cancel", text="Cancel", icon="CANCEL")


_REGISTERED = False


def register():
    global _REGISTERED
    if _REGISTERED:
        return
    bpy.types.IMAGE_HT_header.append(_draw_image_header)
    _REGISTERED = True


def unregister():
    global _REGISTERED
    if not _REGISTERED:
        return
    try:
        bpy.types.IMAGE_HT_header.remove(_draw_image_header)
    except Exception:
        pass
    _REGISTERED = False

