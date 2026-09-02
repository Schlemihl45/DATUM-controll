"""
sim/voxel — GPU-accelerated voxel material-removal simulation.

No C++ extension or external build tools required.
All simulation logic is implemented in pure Python + numpy.
Rendering uses GLSL raymarching via the existing ModernGL context (OpenGL 3.3+).

Sub-modules
-----------
stock       — BoundingBox + StockDefinition (workpiece geometry)
gpu_grid    — GpuVoxelGrid: numpy CPU array + ModernGL Texture3D (GPU)
carver      — VoxelCarver: numpy tool-footprint subtraction
controller  — VoxelSimController: High-Water-Mark path dispatch
renderer    — VoxelRenderer: GLSL raymarching, Blinn-Phong shading

Extension points
----------------
physics/    (future) — temperature and force fields via GPU compute or Taichi
"""
