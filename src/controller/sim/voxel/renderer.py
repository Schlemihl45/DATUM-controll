"""
sim/voxel/renderer.py — GPU raymarching renderer for the voxel stock.

Renders the material field stored in a GpuVoxelGrid's Texture3D using a
fragment-shader raymarching loop.  No mesh extraction is required — the
material texture is sampled directly on the GPU.

OpenGL 3.3 core profile compatible (sampler3D + gl_FragDepth).

Shading
-------
• Blinn-Phong with ambient 0.15, warm aluminium base colour.
• Surface normal estimated via central-difference gradient of the
  material field at the hit voxel.
• gl_FragDepth is written at the hit point so the tool mesh (rendered
  afterwards with depth test) correctly occludes / is occluded by the stock.

Extension points (future)
-------------------------
Physics visualisation
    Additional sampler units will be added for temperature / stress
    textures.  The fragment shader will blend material colour with a
    heat-map or false-colour overlay based on a uniform mode flag.
"""
from __future__ import annotations

import numpy as np
import moderngl

from controller.sim.voxel.gpu_grid   import GpuVoxelGrid
from controller.sim.core.settings    import AppSettings


# ── GLSL source ───────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
// Attribute-less full-screen triangle (positions hard-coded in VS).
// Covers the entire NDC clip space with a single oversized triangle.
const vec2 NDC[3] = vec2[3](
    vec2(-1.0, -1.0),
    vec2( 3.0, -1.0),
    vec2(-1.0,  3.0)
);
void main() {
    gl_Position = vec4(NDC[gl_VertexID], 0.0, 1.0);
}
"""

_FRAG = """
#version 330 core

// ── Uniforms ────────────────────────────────────────────────────────────────
uniform sampler3D u_material;     // r8 — 1.0 = workpiece, 0.0 = air
uniform mat4      u_mvp;          // for writing gl_FragDepth at hit point
uniform mat4      u_inv_mvp;      // for reconstructing world-space ray
uniform vec3      u_cam_pos;      // world-space camera eye
uniform vec2      u_viewport;     // (width, height) in pixels
uniform vec3      u_grid_origin;  // world mm — bbox min corner
uniform vec3      u_grid_size;    // world mm — bbox extents
uniform float     u_voxel_size;   // mm per voxel edge

// ── Material / lighting ───────────────────────────────────────────────────────
uniform vec3 u_base_color;                           // set from AppSettings
const vec3  LIGHT_DIR   = normalize(vec3(0.55, 0.80, 1.0));
const float AMBIENT     = 0.15;
const float SPEC_POW    = 32.0;
const float SPEC_STR    = 0.30;

// ── Marching parameters ───────────────────────────────────────────────────────
// Step = 0.8 voxel for sub-voxel precision without too many iterations.
// 1024 max iterations handles grids up to ~900 voxels along the diagonal.
const int   MAX_STEPS   = 1024;
const float STEP_FACTOR = 0.8;

out vec4 fragColor;

void main() {
    // ── Ray reconstruction via inverse MVP ───────────────────────────────────
    vec2 ndc     = (gl_FragCoord.xy / u_viewport) * 2.0 - 1.0;
    vec4 near_h  = u_inv_mvp * vec4(ndc, -1.0, 1.0);
    vec4 far_h   = u_inv_mvp * vec4(ndc,  1.0, 1.0);
    near_h /= near_h.w;
    far_h  /= far_h.w;

    vec3 ray_dir = normalize(far_h.xyz - near_h.xyz);
    vec3 ray_o   = u_cam_pos;

    // ── Ray vs AABB (slab method) ─────────────────────────────────────────────
    vec3 inv_dir = 1.0 / ray_dir;
    vec3 t0s     = (u_grid_origin              - ray_o) * inv_dir;
    vec3 t1s     = (u_grid_origin + u_grid_size - ray_o) * inv_dir;
    vec3 tmins   = min(t0s, t1s);
    vec3 tmaxs   = max(t0s, t1s);
    float tenter = max(max(tmins.x, tmins.y), tmins.z);
    float texit  = min(min(tmaxs.x, tmaxs.y), tmaxs.z);

    if (texit < 0.0 || tenter > texit) discard;

    float t    = max(tenter, 0.0);
    float step = u_voxel_size * STEP_FACTOR;

    // ── Raymarching loop ──────────────────────────────────────────────────────
    for (int i = 0; i < MAX_STEPS; i++) {
        if (t > texit) break;

        vec3 pos = ray_o + t * ray_dir;
        vec3 uvw = (pos - u_grid_origin) / u_grid_size;

        float mat = texture(u_material, uvw).r;

        if (mat > 0.5) {
            // ── Surface normal via central-difference gradient ────────────────
            // Offset = 1.5 voxels in UVW space for robust normal at surface
            vec3 e = vec3(
                1.5 * u_voxel_size / u_grid_size.x,
                1.5 * u_voxel_size / u_grid_size.y,
                1.5 * u_voxel_size / u_grid_size.z
            );
            // Gradient direction: mat(uvw - e) - mat(uvw + e)
            // This gives an OUTWARD-pointing normal (from solid into air).
            // The naive (uvw+e)-(uvw-e) would point INTO the material and
            // produce a near-zero diffuse term → dark cube visual bug.
            vec3 n = normalize(vec3(
                texture(u_material, uvw - vec3(e.x, 0.0, 0.0)).r
                    - texture(u_material, uvw + vec3(e.x, 0.0, 0.0)).r,
                texture(u_material, uvw - vec3(0.0, e.y, 0.0)).r
                    - texture(u_material, uvw + vec3(0.0, e.y, 0.0)).r,
                texture(u_material, uvw - vec3(0.0, 0.0, e.z)).r
                    - texture(u_material, uvw + vec3(0.0, 0.0, e.z)).r
            ));

            // ── Blinn-Phong shading ────────────────────────────────────────────
            vec3 V    = normalize(u_cam_pos - pos);
            vec3 H    = normalize(LIGHT_DIR + V);
            float diff = max(dot(n, LIGHT_DIR), 0.0);
            float spec = pow(max(dot(n, H),    0.0), SPEC_POW) * SPEC_STR;

            vec3 color = u_base_color * (AMBIENT + diff) + vec3(spec);

            // ── Depth: write correct value so tool/path depth-test works ──────
            vec4 clip = u_mvp * vec4(pos, 1.0);
            gl_FragDepth = (clip.z / clip.w) * 0.5 + 0.5;

            fragColor = vec4(color, 1.0);
            return;
        }

        t += step;
    }

    discard;   // ray passed through without hitting material → transparent
}
"""


# ── Python class ──────────────────────────────────────────────────────────────

class VoxelRenderer:
    """
    Renders a GpuVoxelGrid via GLSL raymarching.

    Must be constructed with the GL context current (called from
    ``DatumSimWidget._create_voxel_sim()``).

    Parameters
    ----------
    ctx :
        Active ModernGL context.
    grid :
        The voxel grid whose ``texture`` is rendered.
    """

    def __init__(self, ctx: moderngl.Context, grid: GpuVoxelGrid) -> None:
        self._ctx  = ctx
        self._grid = grid

        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)

        # Full-screen triangle — no vertex attributes needed
        self._vao  = ctx.vertex_array(self._prog, [])

        # Sync colour from settings and listen for live changes
        self._s = AppSettings.instance()
        self._apply_color(self._s.voxel_color_rgb())
        self._s.voxel_color_changed.connect(
            lambda _name: self._apply_color(self._s.voxel_color_rgb())
        )

    # ── Public interface ──────────────────────────────────────────────────────

    def _apply_color(self, rgb: tuple[float, float, float]) -> None:
        """Write the base colour uniform (can be called outside paintGL)."""
        self._prog["u_base_color"].value = rgb

    def render(self, mvp: np.ndarray, cam_pos: np.ndarray) -> None:
        """
        Render the voxel stock into the currently bound framebuffer.

        Parameters
        ----------
        mvp :
            (4, 4) float32 model-view-projection matrix (column-major).
        cam_pos :
            (3,) float32 world-space camera eye position.

        Must be called from inside ``paintGL`` with the GL context current.
        """
        bbox = self._grid.bbox
        vs   = self._grid.voxel_size

        inv_mvp = np.linalg.inv(mvp).astype("f4")

        self._prog["u_mvp"].write(mvp.T.tobytes())
        self._prog["u_inv_mvp"].write(inv_mvp.T.tobytes())
        self._prog["u_cam_pos"].write(cam_pos.astype("f4").tobytes())
        self._prog["u_viewport"].value = (
            float(self._ctx.viewport[2]),
            float(self._ctx.viewport[3]),
        )
        self._prog["u_grid_origin"].write(bbox.origin().tobytes())
        self._prog["u_grid_size"].write(bbox.size().tobytes())
        self._prog["u_voxel_size"].value = float(vs)

        # Bind material texture to unit 0
        self._grid.texture.use(location=0)
        self._prog["u_material"].value = 0

        # Disable depth writes during the fullscreen pass so the background
        # geometry is not overwritten by "air" pixels; gl_FragDepth is still
        # written for hit pixels (discard handles misses).
        self._ctx.depth_func = "<="    # allow equal depth (surface re-hits)
        self._vao.render(moderngl.TRIANGLES, vertices=3)
        self._ctx.depth_func = "<"     # restore default

    def release(self) -> None:
        """Release GPU shader resources."""
        self._prog.release()
        self._vao.release()
