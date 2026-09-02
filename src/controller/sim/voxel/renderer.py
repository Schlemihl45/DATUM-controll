"""
sim/voxel/renderer.py — VoxelRenderer: ModernGL mesh renderer for the voxel stock.

Renders the triangulated level-set surface produced by VoxelSimController
using a Blinn-Phong shading model.

Usage (from Viewport):
    1. Create: renderer = VoxelRenderer(ctx)   # after initializeGL
    2. Upload: renderer.upload_mesh(verts, normals, indices)  # from GUI thread
    3. Draw:   renderer.render(mvp)            # from paintGL

The renderer owns its own VAO/VBO/IBO and re-uploads only when upload_mesh()
is called. Between uploads the last mesh is rendered every frame.
"""
from __future__ import annotations

import numpy as np
import moderngl

# ── GLSL shaders ──────────────────────────────────────────────────────────────

_VERT_VOXEL = """
#version 330 core

in vec3 in_pos;
in vec3 in_normal;

out vec3 v_pos;
out vec3 v_normal;

uniform mat4 u_mvp;
uniform mat4 u_model;   // world-space transform (identity for now)

void main() {
    vec4 world_pos = u_model * vec4(in_pos, 1.0);
    gl_Position    = u_mvp * vec4(in_pos, 1.0);
    v_pos          = world_pos.xyz;
    v_normal       = mat3(u_model) * in_normal;
}
"""

_FRAG_VOXEL = """
#version 330 core

in vec3 v_pos;
in vec3 v_normal;

out vec4 f_col;

uniform vec3 u_material_color;
uniform vec3 u_light_dir;      // normalized, world space
uniform vec3 u_cam_pos;        // camera/eye position (for specular)

void main() {
    vec3 N  = normalize(v_normal);
    vec3 L  = normalize(u_light_dir);
    vec3 V  = normalize(u_cam_pos - v_pos);
    vec3 H  = normalize(L + V);

    float ambient  = 0.12;
    float diff     = max(dot(N, L), 0.0);
    float spec     = pow(max(dot(N, H), 0.0), 32.0) * 0.25;

    vec3 color = u_material_color * (ambient + diff) + vec3(spec);
    f_col = vec4(color, 1.0);
}
"""

# Default material: warm aluminium colour
_DEFAULT_COLOR = (0.72, 0.65, 0.56)
# Key light direction (world space)
_LIGHT_DIR     = (0.6, 0.8, 1.0)


class VoxelRenderer:
    """ModernGL renderer for the voxel stock mesh.

    Must be constructed inside an active OpenGL context (i.e. after
    QOpenGLWidget.initializeGL has been called and ctx is available).

    Args:
        ctx:            A moderngl.Context (from Viewport.ctx).
        material_color: RGB tuple in 0-1 range for the stock surface colour.
    """

    def __init__(
        self,
        ctx: moderngl.Context,
        material_color: tuple[float, float, float] = _DEFAULT_COLOR,
    ) -> None:
        self._ctx   = ctx
        self._color = material_color

        self._prog = ctx.program(
            vertex_shader=_VERT_VOXEL,
            fragment_shader=_FRAG_VOXEL,
        )

        # No mesh uploaded yet
        self._vao:         moderngl.VertexArray | None = None
        self._vbo_pos:     moderngl.Buffer | None      = None
        self._vbo_normal:  moderngl.Buffer | None      = None
        self._ibo:         moderngl.Buffer | None      = None
        self._index_count: int                         = 0

        self._identity = np.eye(4, dtype='f4')

    # ── Mesh upload (GUI thread, inside GL context) ──────────────────────────���

    def upload_mesh(
        self,
        vertices: np.ndarray,   # (N, 3) float32
        normals:  np.ndarray,   # (N, 3) float32
        indices:  np.ndarray,   # (M, 3) uint32
    ) -> None:
        """Upload a new mesh to the GPU.

        Call this from the main thread after VoxelSimController.get_mesh_if_dirty()
        returns new data. The GL context must be current (Viewport calls
        makeCurrent() before delegating here).
        """
        if len(indices) == 0:
            self._index_count = 0
            return

        # Release old buffers
        self._release_buffers()

        v_flat = vertices.astype('f4').reshape(-1)
        n_flat = normals.astype('f4').reshape(-1)
        i_flat = indices.astype('u4').reshape(-1)

        self._vbo_pos    = self._ctx.buffer(v_flat.tobytes())
        self._vbo_normal = self._ctx.buffer(n_flat.tobytes())
        self._ibo        = self._ctx.buffer(i_flat.tobytes())

        self._vao = self._ctx.vertex_array(
            self._prog,
            [
                (self._vbo_pos,    '3f', 'in_pos'),
                (self._vbo_normal, '3f', 'in_normal'),
            ],
            index_buffer=self._ibo,
        )
        self._index_count = len(i_flat)

    # ── Render (paintGL) ──────────────────────────────────────────────────────

    def render(
        self,
        mvp:     np.ndarray,               # (4,4) float32
        cam_pos: np.ndarray | None = None,  # (3,) world-space eye position
    ) -> None:
        """Render the stock mesh. No-op if no mesh has been uploaded yet."""
        if self._vao is None or self._index_count == 0:
            return

        self._prog['u_mvp'].write(mvp.T.tobytes())
        self._prog['u_model'].write(self._identity.tobytes())
        self._prog['u_material_color'].value = self._color
        self._prog['u_light_dir'].value      = _LIGHT_DIR
        self._prog['u_cam_pos'].value        = (
            tuple(cam_pos.tolist()) if cam_pos is not None else (0.0, 0.0, 100.0)
        )

        self._vao.render(moderngl.TRIANGLES)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def set_color(self, rgb: tuple[float, float, float]) -> None:
        """Change the stock surface colour (takes effect on next render call)."""
        self._color = rgb

    def _release_buffers(self) -> None:
        for attr in ('_vao', '_vbo_pos', '_vbo_normal', '_ibo'):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
            setattr(self, attr, None)

    def release(self) -> None:
        """Free all GPU resources. Call when the Viewport is destroyed."""
        self._release_buffers()
        if self._prog:
            try:
                self._prog.release()
            except Exception:
                pass
