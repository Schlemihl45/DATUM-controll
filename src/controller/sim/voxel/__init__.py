"""
sim/voxel/ — Voxel-based material-removal simulation sub-package.

No top-level imports here. The C++ extension module (voxel_mod) is imported
lazily inside controller.py so that a missing OpenVDB build does not break the
rest of the application on import.

Public API (lazy-import these directly where needed):
    from controller.sim.voxel.stock      import StockDefinition, SourceType, BoundingBox
    from controller.sim.voxel.controller import VoxelSimController
    from controller.sim.voxel.renderer   import VoxelRenderer
"""
