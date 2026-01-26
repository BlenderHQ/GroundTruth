# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
# ##### END GPL LICENSE BLOCK #####

from __future__ import annotations

import subprocess
import sys
from typing import Dict, List, Optional


_PROC: Optional[subprocess.Popen] = None


def start(cmd: List[str], *, env: Dict[str, str] | None = None) -> subprocess.Popen:
    global _PROC
    if _PROC is not None and _PROC.poll() is None:
        raise RuntimeError("External Blender render already running")
    kwargs = {}
    if sys.platform.startswith("win"):
        # Put the child into its own process group so it's easier to terminate/kill cleanly.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    _PROC = subprocess.Popen(cmd, env=env, **kwargs)
    return _PROC


def poll() -> int | None:
    if _PROC is None:
        return None
    return _PROC.poll()


def pid() -> int:
    if _PROC is None:
        return 0
    return int(_PROC.pid or 0)


def terminate() -> None:
    if _PROC is None:
        return
    try:
        _PROC.terminate()
    except Exception:
        pass


def kill() -> None:
    if _PROC is None:
        return
    try:
        _PROC.kill()
    except Exception:
        pass
