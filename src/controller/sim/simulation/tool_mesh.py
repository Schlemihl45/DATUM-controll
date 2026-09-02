"""sim/simulation/tool_mesh.py — GPU-ready triangle mesh for a tool (solid of revolution)."""
from __future__ import annotations
import numpy as np
from controller.sim.simulation.tool_definition import ToolDefinition, ToolType


def build_tool_mesh(
        tool: ToolDefinition,
        segments: int = 64,  # Increased from 32 to 64 for smooth roundness
        z_steps: int = 128,  # Increased from 48 to 128 for fine Z resolution (ball nose!)
        cutting_color: tuple[float, float, float] = (1.0, 0.84, 0.0),  # Gold default
        shank_color:   tuple[float, float, float] = (0.5, 0.5, 0.5),   # Neutral grey default
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # Returns (vertices, normals, colors)
    """
    Solid of revolution from profile_radius_at(z).
    Returns (vertices, normals, colors) all as float32 arrays.

    *cutting_color*/*shank_color* are baked per-vertex at build time (not a
    shader uniform) — changing them requires rebuilding the mesh, exactly
    like changing the tool itself (see Viewport._rebuild_tool_mesh()).
    """
    # Ball and bull endmills get extra resolution dynamically
    # so the tip curvature renders extremely smooth
    if tool.tool_type in (ToolType.BALL_ENDMILL, ToolType.BULL_ENDMILL):
        z_steps = max(z_steps, 256)
        segments = max(segments, 64)

    angles = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    z_vals = np.linspace(0.0, tool.total_length, z_steps)
    radii = np.array([tool.profile_radius_at(z) for z in z_vals])

    verts: list = []
    norms: list = []
    colors: list = []

    # Color definitions (configurable — see function parameters)
    COLOR_CUTTING = list(cutting_color)
    COLOR_SHANK   = list(shank_color)

    # ── Cylindrical body ──────────────────────────────────────────────
    for k in range(len(z_vals) - 1):
        z0, z1 = z_vals[k], z_vals[k + 1]
        r0, r1 = radii[k], radii[k + 1]
        dz = z1 - z0

        # Determine whether this Z layer is in the cutting zone
        if (z0 + z1) / 2.0 <= tool.cutting_length:
            layer_color = COLOR_CUTTING
        else:
            layer_color = COLOR_SHANK

        for j in range(segments):
            a0 = angles[j]
            a1 = angles[(j + 1) % segments]
            c0, s0 = np.cos(a0), np.sin(a0)
            c1, s1 = np.cos(a1), np.sin(a1)

            p = [
                [r0 * c0, r0 * s0, z0], [r0 * c1, r0 * s1, z0],
                [r1 * c0, r1 * s0, z1], [r1 * c1, r1 * s1, z1],
            ]

            # Cone-mantle normal: radial + dz component
            dr = r0 - r1
            nz = dr / max(np.sqrt(dr ** 2 + dz ** 2), 1e-9)
            nr = dz / max(np.sqrt(dr ** 2 + dz ** 2), 1e-9)
            n = [[c0 * nr, s0 * nr, nz], [c1 * nr, s1 * nr, nz]]

            # Geometry and normals (2 triangles = 6 vertices)
            verts += [p[0], p[1], p[2], p[1], p[3], p[2]]
            norms += [n[0], n[1], n[0], n[1], n[1], n[0]]

            # Color for all 6 vertices of the two triangles
            colors += [layer_color] * 6

    # ── Tip / bottom cap ──────────────────────────────────────────────
    # The tip is at z=0 and is always part of the cutting zone
    r_tip = radii[0]
    if r_tip < 0.05:
        tip = [0.0, 0.0, 0.0]
        r1 = radii[1]
        z1 = z_vals[1]
        for j in range(segments):
            a0, a1 = angles[j], angles[(j + 1) % segments]
            verts += [tip,
                      [r1 * np.cos(a0), r1 * np.sin(a0), z1],
                      [r1 * np.cos(a1), r1 * np.sin(a1), z1]]
            norms += [[0, 0, -1]] * 3
            colors += [COLOR_CUTTING] * 3
    else:
        for j in range(segments):
            a0, a1 = angles[j], angles[(j + 1) % segments]
            verts += [[0, 0, 0],
                      [r_tip * np.cos(a0), r_tip * np.sin(a0), 0],
                      [r_tip * np.cos(a1), r_tip * np.sin(a1), 0]]
            norms += [[0, 0, -1]] * 3
            colors += [COLOR_CUTTING] * 3

    # ── Top cap ───────────────────────────────────────────────────────
    # Top face is at the shank end, colored shank grey
    r_top = radii[-1]
    z_top = z_vals[-1]
    for j in range(segments):
        a0, a1 = angles[j], angles[(j + 1) % segments]
        verts += [[0, 0, z_top],
                  [r_top * np.cos(a0), r_top * np.sin(a0), z_top],
                  [r_top * np.cos(a1), r_top * np.sin(a1), z_top]]
        norms += [[0, 0, 1]] * 3
        colors += [COLOR_SHANK] * 3

    return (
        np.array(verts, dtype='f4'),
        np.array(norms, dtype='f4'),
        np.array(colors, dtype='f4')
    )
