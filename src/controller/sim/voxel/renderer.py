"""
sim/voxel/renderer.py — GPU raymarching renderer for the voxel stock.

Renders the material field stored in a GpuVoxelGrid's Texture3D using a
fragment-shader raymarching loop.  No mesh extraction is required — the
material texture is sampled directly on the GPU.

OpenGL 3.3 core profile compatible (sampler3D + gl_FragDepth).

Rendering design
----------------
The march starts ONE step (0.8 voxels) INSIDE the AABB instead of at the
exact boundary face.  This has two effects the user can see:

  1. The "ghost outer shell" disappears: the raymarcher never renders the
     outermost voxel layer as a flat plane.  The stock boundary is always
     slightly inside the AABB, so what is rendered is always a surface that
     has carved neighbours on at least one side.

  2. Normal robustness: the central-difference normal samples at uvw ± e
     (1.5 voxels offset).  Starting one step inside guarantees at least one
     valid in-bounds sample on every side, so the gradient is never zero
     due to a boundary clamp artefact.

mat_at() returns 0.0 (air) for any UVW outside [0, 1]³ — equivalent to
GL_CLAMP_TO_BORDER with border colour 0.  This prevents GL_REPEAT wrap
artefacts (ModernGL default) from leaking into normal computation.

Shading
-------
• Blinn-Phong with ambient 0.30 so dark faces are never pitch-black.
• Secondary fill light from below-left softens harsh shadow sides.
• gl_FragDepth written at hit so the tool mesh depth-tests correctly
  against the stock.
"""
from __future__ import annotations

import numpy as np
import moderngl

from controller.sim.voxel.gpu_grid   import GpuVoxelGrid
from controller.sim.core.settings    import AppSettings


# ── GLSL source ───────────────────────────────────────────────────────────────

_VERT = """
#version 330 core
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

// ── Uniforms ─────────────────────────────────────────────────────────────────
uniform sampler3D u_material;
uniform mat4      u_mvp;
uniform mat4      u_inv_mvp;
uniform vec3      u_cam_pos;
uniform vec2      u_viewport;
uniform vec3      u_grid_origin;
uniform vec3      u_grid_size;
uniform float     u_voxel_size;
uniform vec3      u_base_color;

// ── Lighting ──────────────────────────────────────────────────────────────────
const vec3  LIGHT_DIR = normalize(vec3( 0.50,  0.70, 1.00));
const vec3  FILL_DIR  = normalize(vec3(-0.40, -0.60, 0.50));
const float AMBIENT   = 0.30;
const float FILL_STR  = 0.22;
const float SPEC_POW  = 24.0;
const float SPEC_STR  = 0.28;

// ── March ─────────────────────────────────────────────────────────────────────
const int   MAX_STEPS   = 1024;
const float STEP_FACTOR = 0.8;

out vec4 fragColor;

// ── Boundary-safe sampler ─────────────────────────────────────────────────────
// Returns 0.0 (air) for any UVW strictly outside [0, 1]^3.
// Prevents GL_REPEAT wrap-around artefacts in the normal computation.
float mat_at(vec3 uvw_s) {
    if (any(lessThan   (uvw_s, vec3(0.0))) ||
        any(greaterThan(uvw_s, vec3(1.0))))
        return 0.0;
    return texture(u_material, uvw_s).r;
}

void main() {
    // ── Reconstruct view ray from inverse MVP ─────────────────────────────────
    vec2 ndc    = (gl_FragCoord.xy / u_viewport) * 2.0 - 1.0;
    vec4 near_h = u_inv_mvp * vec4(ndc, -1.0, 1.0);
    vec4 far_h  = u_inv_mvp * vec4(ndc,  1.0, 1.0);
    near_h /= near_h.w;
    far_h  /= far_h.w;

    vec3 ray_dir = normalize(far_h.xyz - near_h.xyz);
    vec3 ray_o   = u_cam_pos;

    // ── Ray vs AABB (slab test) ───────────────────────────────────────────────
    vec3 inv_dir = 1.0 / ray_dir;
    vec3 t0s   = (u_grid_origin                - ray_o) * inv_dir;
    vec3 t1s   = (u_grid_origin + u_grid_size  - ray_o) * inv_dir;
    vec3 tmins = min(t0s, t1s);
    vec3 tmaxs = max(t0s, t1s);
    float tenter = max(max(tmins.x, tmins.y), tmins.z);
    float texit  = min(min(tmaxs.x, tmaxs.y), tmaxs.z);

    if (texit < 0.0 || tenter > texit) discard;

    float step = u_voxel_size * STEP_FACTOR;

    // Start ONE step inside the AABB instead of at the exact boundary face.
    // This prevents the outermost voxel layer from rendering as a flat plane
    // ("ghost box" artefact).  With a 5 mm stock margin and 0.5 mm voxels
    // the stock appears indistinguishable from its nominal size.
    float t = max(tenter, 0.0) + step;

    // ── Raymarching loop ──────────────────────────────────────────────────────
    for (int i = 0; i < MAX_STEPS; i++) {
        if (t > texit) break;

        vec3 pos = ray_o + t * ray_dir;
        vec3 uvw = (pos - u_grid_origin) / u_grid_size;

        float mat = mat_at(uvw);

        if (mat > 0.5) {
            // ── Central-difference surface normal ─────────────────────────────
            // mat_at returns 0 for out-of-bounds → outward-pointing gradient
            // at boundary voxels without wrap artefacts.
            vec3 e = vec3(
                1.5 * u_voxel_size / u_grid_size.x,
                1.5 * u_voxel_size / u_grid_size.y,
                1.5 * u_voxel_size / u_grid_size.z
            );
            vec3 grad = vec3(
                mat_at(uvw - vec3(e.x, 0.0, 0.0)) - mat_at(uvw + vec3(e.x, 0.0, 0.0)),
                mat_at(uvw - vec3(0.0, e.y, 0.0)) - mat_at(uvw + vec3(0.0, e.y, 0.0)),
                mat_at(uvw - vec3(0.0, 0.0, e.z)) - mat_at(uvw + vec3(0.0, 0.0, e.z))
            );
            // Guard against zero-length gradient (fully enclosed voxel):
            // fall back to camera direction for a non-black result.
            float g_len = length(grad);
            vec3 n = (g_len > 1e-4) ? (grad / g_len) : normalize(u_cam_pos - pos);

            // ── Blinn-Phong shading ───────────────────────────────────────────
            vec3  V    = normalize(u_cam_pos - pos);
            vec3  H    = normalize(LIGHT_DIR + V);
            float diff = max(dot(n, LIGHT_DIR), 0.0);
            float fill = max(dot(n, FILL_DIR),  0.0) * FILL_STR;
            float spec = pow(max(dot(n, H),     0.0), SPEC_POW) * SPEC_STR;

            vec3 color = u_base_color * (AMBIENT + diff + fill) + vec3(spec);

            // ── Write correct fragment depth ──────────────────────────────────
            vec4 clip = u_mvp * vec4(pos, 1.0);
            gl_FragDepth = (clip.z / clip.w) * 0.5 + 0.5;

            fragColor = vec4(color, 1.0);
            return;
        }

        t += step;
    }

    discard;
}
"""


# ── Python class ──────────────────────────────────────────────────────────────

class VoxelRenderer:
    """
    Renders a GpuVoxelGrid via GLSL raymarching.

    Must be constructed with the GL context current (called from
    ``DatumSimWidget._create_voxel_sim()``).
    """

    def __init__(self, ctx: moderngl.Context, grid: GpuVoxelGrid) -> None:
        self._ctx  = ctx
        self._grid = grid

        self._prog = ctx.program(vertex_shader=_VERT, fragment_shader=_FRAG)
        self._vao  = ctx.vertex_array(self._prog, [])

        self._s = AppSettings.instance()
        self._apply_color(self._s.voxel_color_rgb())
        self._s.voxel_color_changed.connect(
            lambda _name: self._apply_color(self._s.voxel_color_rgb())
        )

    def _apply_color(self, rgb: tuple[float, float, float]) -> None:
        self._prog["u_base_color"].value = rgb

    def render(self, mvp: np.ndarray, cam_pos: np.ndarray) -> None:
        """Render the voxel stock.  Must be called inside paintGL."""
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

        self._grid.texture.use(location=0)
        self._prog["u_material"].value = 0

        self._vao.render(moderngl.TRIANGLES, vertices=3)

    def release(self) -> None:
        self._prog.release()
        self._vao.release()
