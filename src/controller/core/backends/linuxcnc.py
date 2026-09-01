"""
core/backends/linuxcnc.py — Real-hardware backend (Phase 2, not started).

Once implemented, LinuxCNCBackend must subclass AbstractBackend (see
core/backends/base.py) and implement every abstract method there,
wrapping the `linuxcnc` Python module: `linuxcnc.stat()` for reads,
`linuxcnc.command()` for writes, `linuxcnc.error_channel()` for the
error queue. See base.py's docstrings for the exact LinuxCNC call
each method maps to.

main.py deliberately refuses to instantiate this class until it is
implemented (see main._create_backend) — an incomplete AbstractBackend
subclass would otherwise fail with a confusing
"Can't instantiate abstract class" TypeError instead of a clear
NotImplementedError.
"""

from __future__ import annotations
