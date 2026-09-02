"""
sim/ui/viewport.py — OpenGL 3.3 Core viewport for G-code path visualization.

Renders:
  • Radial gradient background (inner/outer color from SimSettings.bg_color*)
  • XYZ axis lines + grid (Z-up coordinate system)
  • Datum origin symbol (quarter-circle toggle logo)
  • G-code path as colored LINE_STRIP (rapid=yellow, feed=cyan)
  • Tool mesh (solid of revolution, lit) or point cursor
  • Corner-fill overlay matching the host window's gradient

The corner-fill shader hardcodes window gradient colors (_win_grad_top /
_win_grad_bottom). These MUST stay in sync with the QMainWindow background
in whichever .qss is active. ThemeManager calls set_window_gradient() after
every theme switch so the corners always blend seamlessly.
"""
from __future__ import annotations

import numpy as np
import moderngl
from enum import Enum, auto

from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtCore import Qt, QEvent, QPointF
from PySide6.QtGui import QEventPoint

from controller.sim.core.camera import ArcballCamera
from controller.sim.core.settings import AppSettings
from controller.sim.gcode.path_buffer import PathBuffer
from controller.sim.simulation.tool_mesh import build_tool_mesh
from controller.sim.simulation.tool_definition import ToolDefinition
from controller.sim.simulation.tool_database import get_tool


# ── Render mode enums ────────────────────────────────────────────────────────

class PathMode(Enum):
    """How much of the G-code path to draw."""
    NONE        = auto()  # hide entirely
    FULL        = auto()  # full path at once
    PROGRESSIVE = auto()  # only up to current position


class ToolMode(Enum):
    """How to render the cutting tool."""
    NONE     = auto()  # hide tool
    POINT    = auto()  # single point / cursor dot
    CYLINDER = auto()  # lit 3D solid-of-revolution mesh


# ── GLSL shaders ─────────────────────────────────────────────────────────────

# Scene shader — grid, axes, path lines
_VERT = """
#version 330 core
in vec3 in_pos;
in vec3 in_col;
out vec3 v_col;
uniform mat4 u_mvp;
void main() {
    gl_Position = u_mvp * vec4(in_pos, 1.0);
    v_col = in_col;
}
"""
_FRAG = """
#version 330 core
in vec3 v_col;
out vec4 f_col;
void main() { f_col = vec4(v_col, 1.0); }
"""

# Tool shader — lit mesh with per-vertex color
_VERT_TOOL = """
#version 330 core
in vec3 in_pos;
in vec3 in_normal;
in vec3 in_color;
out vec3 v_color;
uniform mat4 u_mvp;
uniform vec3 u_tool_pos;
void main() {
    gl_Position = u_mvp * vec4(in_pos + u_tool_pos, 1.0);
    vec3 light       = normalize(vec3(1.0, 1.5, 2.0));
    float diff       = max(dot(normalize(in_normal), light), 0.0);
    float brightness = 0.25 + diff * 0.75;
    v_color          = in_color * brightness;
}
"""
_FRAG_TOOL = """
#version 330 core
in vec3 v_color;
out vec4 f_col;
void main() { f_col = vec4(v_color, 1.0); }
"""

# Background gradient shader — fullscreen triangle, radial gradient
_VERT_GRADIENT = """
#version 330 core
out vec2 v_uv;
void main() {
    vec2 pos = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    v_uv = pos;
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""
_FRAG_GRADIENT = """
#version 330 core
in vec2 v_uv;
out vec4 f_col;
uniform vec3 u_color_inner;
uniform vec3 u_color_outer;
uniform vec2 u_center;
uniform float u_radius;
uniform float u_aspect;
void main() {
    vec2 d = v_uv - u_center;
    d.x *= u_aspect;
    float t = clamp(length(d) / u_radius, 0.0, 1.0);
    f_col = vec4(mix(u_color_inner, u_color_outer, t), 1.0);
}
"""

# Corner-fill shader — blends window gradient into viewport rounded corners.
# u_grad_top/bottom must be updated by ThemeManager on every theme switch.
_FRAG_CORNER_FILL = """
#version 330 core
out vec4 f_col;
uniform vec2 u_resolution_px;
uniform float u_radius_px;
uniform vec3 u_grad_top;
uniform vec3 u_grad_bottom;
uniform float u_window_y_offset_px;
uniform float u_window_height_px;
void main() {
    vec2 p      = gl_FragCoord.xy;
    vec2 half_r = u_resolution_px * 0.5;
    vec2 pos    = p - half_r;
    vec2 q      = abs(pos) - (half_r - u_radius_px);
    float dist  = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - u_radius_px;
    float corner_alpha = smoothstep(-1.0, 1.0, dist);

    float local_y_top_down = u_resolution_px.y - p.y;
    float win_y = u_window_y_offset_px + local_y_top_down;
    float t     = clamp(win_y / u_window_height_px, 0.0, 1.0);
    vec3 grad_color = mix(u_grad_top, u_grad_bottom, t);

    f_col = vec4(grad_color, corner_alpha);
}
"""


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _build_axes_grid(axis_len: float = 40.0, grid_range: int = 200, grid_step: int = 10):
    """Build vertex + color arrays for XYZ axes and an XY grid plane."""
    verts, cols = [], []

    # Axis lines (X=red, Y=green, Z=blue)
    for end, color in [
        ([axis_len, 0, 0], [1.0, 0.2, 0.2]),
        ([0, axis_len, 0], [0.2, 1.0, 0.2]),
        ([0, 0, axis_len], [0.4, 0.6, 1.0]),
    ]:
        verts += [[0, 0, 0], end]
        cols  += [color, color]

    # Grid lines (dark grey)
    gc = [0.22, 0.22, 0.22]
    for i in range(-grid_range, grid_range + 1, grid_step):
        verts += [[i, -grid_range, 0], [i, grid_range, 0]]
        cols  += [gc, gc]
        verts += [[-grid_range, i, 0], [grid_range, i, 0]]
        cols  += [gc, gc]

    return np.array(verts, dtype='f4'), np.array(cols, dtype='f4')


def _build_datum_symbol(radius: float = 2.0, line_ext: float = 8.0, segments: int = 32):
    """Build the datum origin marker: quarter-circle fill + ring + crosshair."""
    verts_tri, cols_tri   = [], []
    verts_line, cols_line = [], []

    c_fill = [0.15, 0.15, 0.15]  # filled quadrants
    c_bg   = [0.90, 0.90, 0.90]  # open quadrants
    c_line = [1.0, 1.0, 1.0]     # ring + crosshair

    z_tri  = 0.05   # slightly above Z=0 to avoid z-fighting with grid
    z_line = 0.06

    # Four quadrant sectors as filled triangles
    for q in range(4):
        color      = c_fill if q % 2 == 0 else c_bg
        start_idx  = q * (segments // 4)
        end_idx    = (q + 1) * (segments // 4)

        for i in range(start_idx, end_idx):
            a1 = i * 2 * np.pi / segments
            a2 = (i + 1) * 2 * np.pi / segments
            verts_tri += [
                [0, 0, z_tri],
                [radius * np.cos(a1), radius * np.sin(a1), z_tri],
                [radius * np.cos(a2), radius * np.sin(a2), z_tri],
            ]
            cols_tri += [color, color, color]

    # Outer ring line loop
    for i in range(segments):
        a1 = i * 2 * np.pi / segments
        a2 = (i + 1) * 2 * np.pi / segments
        verts_line += [
            [radius * np.cos(a1), radius * np.sin(a1), z_line],
            [radius * np.cos(a2), radius * np.sin(a2), z_line],
        ]
        cols_line += [c_line, c_line]

    # Crosshair extending beyond the ring
    verts_line += [
        [-line_ext, 0, z_line], [line_ext, 0, z_line],
        [0, -line_ext, z_line], [0, line_ext, z_line],
    ]
    cols_line += [c_line, c_line, c_line, c_line]

    return (
        np.array(verts_tri,  dtype='f4'), np.array(cols_tri,  dtype='f4'),
        np.array(verts_line, dtype='f4'), np.array(cols_line, dtype='f4'),
    )


def _dist(p1: QPointF, p2: QPointF) -> float:
    """Euclidean distance between two QPointF."""
    d = p1 - p2
    return (d.x() ** 2 + d.y() ** 2) ** 0.5


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert a CSS hex color string (#rrggbb) to a 0-1 float triple."""
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


# ── Viewport widget ───────────────────────────────────────────────────────────

class Viewport(QOpenGLWidget):
    """ModernGL-backed QOpenGLWidget that renders a 3D G-code path.

    The widget holds no business logic — it only renders whatever data is
    pushed to it via set_path(), set_tool_position(), set_progress(), etc.
    All interactions (mouse/touch) modify only the ArcballCamera.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = ArcballCamera()

        # Mouse / touch state
        self._mouse_last         = QPointF()
        self._mouse_btn          = None
        self._rotate_accumulated = QPointF(0, 0)
        self._multi_touch_active = False
        self.ROTATE_THRESHOLD    = 16          # pixels before single-finger rotate fires

        # Render settings
        self._path_mode      = PathMode.FULL
        self._tool_mode      = ToolMode.CYLINDER
        self._tool_pos       = np.zeros(3, dtype='f4')
        self._path_split_idx = 0
        self._path_vert_count = 0
        self._active_line    = 0
        self._path_vao       = None
        self._path_arc_lengths = np.array([0.0])
        self._cyl_vao        = None
        self._show_grid      = True
        self._perf           = None            # optional PerfMonitor, set externally

        # Background colors from settings
        s = AppSettings.instance()
        self._bg  = _hex_to_rgb(s.bg_color)
        self._bg2 = _hex_to_rgb(s.bg_color_2)

        # Window gradient colors for corner-fill shader.
        # Default matches the dark theme; ThemeManager updates these on switch.
        self._win_grad_top    = _hex_to_rgb("#141b26")
        self._win_grad_bottom = _hex_to_rgb("#0b0f16")

        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setMinimumSize(400, 300)

        # Live color updates from settings
        s.bg_color_changed.connect(self._on_bg_changed)
        s.bg_color_2_changed.connect(self._on_bg2_changed)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_path_mode(self, mode: PathMode) -> None:
        self._path_mode = mode
        self.update()

    def set_tool_mode(self, mode: ToolMode) -> None:
        self._tool_mode = mode
        self.update()

    def set_tool_definition(self, tool: ToolDefinition) -> None:
        """Upload a new tool mesh. Safe to call before initializeGL."""
        self._current_tool = tool
        if hasattr(self, 'ctx'):
            self.makeCurrent()
            self._rebuild_tool_mesh(tool)
            self.doneCurrent()
        else:
            self._pending_tool = tool
        self.update()

    def set_path(self, path: PathBuffer) -> None:
        """Upload path data to GPU. Safe to call before initializeGL."""
        if not hasattr(self, 'ctx'):
            self._pending_path = path
            return
        self.makeCurrent()
        self._upload_path(path)
        self.doneCurrent()

    def set_tool_position(self, pos: np.ndarray) -> None:
        self._tool_pos = pos.astype('f4')
        if hasattr(self, '_cursor_vbo'):
            self.makeCurrent()
            self._cursor_vbo.write(self._tool_pos.tobytes())
            self.doneCurrent()

    def set_active_line(self, line_idx: int) -> None:
        self._active_line = line_idx

    def set_progress(self, s: float) -> None:
        """Update the progressive path split index from an arc-length value."""
        if len(self._path_arc_lengths) < 2:
            return
        self._path_split_idx = int(
            np.searchsorted(self._path_arc_lengths, s, side='right')
        )

    def set_voxel_renderer(self, renderer) -> None:
        """Attach a VoxelRenderer to draw the stock mesh during paintGL.

        Must be called after initializeGL so the renderer can use self.ctx.
        Pass None to detach an existing renderer.
        """
        self._voxel_renderer = renderer

    def set_window_gradient(self, top: str, bottom: str) -> None:
        """Update corner-fill gradient colors to match the active theme.

        Called by ThemeManager whenever the application theme changes so the
        viewport corners blend seamlessly into the host window background.

        Args:
            top:    CSS hex color (#rrggbb) for the gradient top.
            bottom: CSS hex color (#rrggbb) for the gradient bottom.
        """
        self._win_grad_top    = _hex_to_rgb(top)
        self._win_grad_bottom = _hex_to_rgb(bottom)
        self.update()

    # ── OpenGL lifecycle ──────────────────────────────────────────────────────

    def initializeGL(self) -> None:
        self.ctx = moderngl.create_context()
        self.ctx.enable(moderngl.DEPTH_TEST)

        # Compile shader programs
        self._prog = self.ctx.program(
            vertex_shader=_VERT, fragment_shader=_FRAG
        )
        self._tool_prog = self.ctx.program(
            vertex_shader=_VERT_TOOL, fragment_shader=_FRAG_TOOL
        )
        self._gradient_prog = self.ctx.program(
            vertex_shader=_VERT_GRADIENT, fragment_shader=_FRAG_GRADIENT
        )
        self._gradient_vao = self.ctx.vertex_array(self._gradient_prog, [])
        self._corner_fill_prog = self.ctx.program(
            vertex_shader=_VERT_GRADIENT, fragment_shader=_FRAG_CORNER_FILL
        )
        self._corner_fill_vao = self.ctx.vertex_array(self._corner_fill_prog, [])
        self._corner_radius_px = 12.0

        # Upload static geometry
        verts, colors = _build_axes_grid()
        self._scene_vao = self.ctx.vertex_array(
            self._prog,
            [
                (self.ctx.buffer(verts.tobytes()),  '3f', 'in_pos'),
                (self.ctx.buffer(colors.tobytes()), '3f', 'in_col'),
            ],
        )
        self._scene_vert_count = len(verts)

        vt, ct, vl, cl = _build_datum_symbol(radius=2.0, line_ext=5.0)
        self._datum_tri_vao = self.ctx.vertex_array(
            self._prog,
            [
                (self.ctx.buffer(vt.tobytes()), '3f', 'in_pos'),
                (self.ctx.buffer(ct.tobytes()), '3f', 'in_col'),
            ],
        )
        self._datum_tri_count = len(vt)
        self._datum_line_vao = self.ctx.vertex_array(
            self._prog,
            [
                (self.ctx.buffer(vl.tobytes()), '3f', 'in_pos'),
                (self.ctx.buffer(cl.tobytes()), '3f', 'in_col'),
            ],
        )
        self._datum_line_count = len(vl)

        # Dynamic cursor (point) buffer
        self._cursor_vbo = self.ctx.buffer(self._tool_pos.tobytes(), dynamic=True)
        self._cursor_vao = self.ctx.vertex_array(
            self._prog,
            [
                (self._cursor_vbo, '3f', 'in_pos'),
                (self.ctx.buffer(
                    np.array([1.0, 0.84, 0.0], dtype='f4').tobytes()
                ), '3f', 'in_col'),
            ],
        )

        # Resolve pending data uploaded before GL was ready
        if hasattr(self, '_pending_tool'):
            self._rebuild_tool_mesh(self._pending_tool)
            del self._pending_tool
        else:
            default = get_tool(1)
            if default:
                self._rebuild_tool_mesh(default)

        if hasattr(self, '_pending_path'):
            self._upload_path(self._pending_path)
            del self._pending_path

        # Grid visibility from settings
        s = AppSettings.instance()
        self._show_grid = s.show_grid
        s.show_grid_changed.connect(self._on_show_grid_changed)

        # VoxelRenderer — set externally via set_voxel_renderer()
        self._voxel_renderer = None

    def resizeGL(self, w: int, h: int) -> None:
        if hasattr(self, 'ctx'):
            self.ctx.viewport = (0, 0, w, h)

    def paintGL(self) -> None:
        fbo = self.ctx.detect_framebuffer(self.defaultFramebufferObject())
        fbo.use()
        fbo.clear(0.0, 0.0, 0.0, 1.0)

        # 1. Background gradient (no depth test — fullscreen quad)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self._gradient_prog['u_color_inner'].value = self._bg
        self._gradient_prog['u_color_outer'].value = self._bg2
        self._gradient_prog['u_center'].value  = (0.15, 0.9)
        self._gradient_prog['u_radius'].value  = 1.1
        self._gradient_prog['u_aspect'].value  = self.width() / max(self.height(), 1)
        self._gradient_vao.render(moderngl.TRIANGLES, vertices=3)
        self.ctx.enable(moderngl.DEPTH_TEST)

        # 2. Scene geometry
        aspect = self.width() / max(self.height(), 1)
        mvp    = self.camera.mvp(aspect)
        self._prog['u_mvp'].write(mvp.T.tobytes())

        if self._show_grid:
            self._scene_vao.render(moderngl.LINES, vertices=self._scene_vert_count)

        self._datum_tri_vao.render(moderngl.TRIANGLES, vertices=self._datum_tri_count)
        self._datum_line_vao.render(moderngl.LINES,    vertices=self._datum_line_count)

        # 3. G-code path
        if self._path_vao and self._path_vert_count > 1:
            if self._path_mode == PathMode.FULL:
                self._path_vao.render(moderngl.LINE_STRIP, vertices=self._path_vert_count)
            elif self._path_mode == PathMode.PROGRESSIVE and self._path_split_idx > 1:
                self._path_vao.render(moderngl.LINE_STRIP, vertices=self._path_split_idx)

        # 4. Voxel stock mesh (between path and tool so tool renders on top)
        if self._voxel_renderer is not None:
            cam_pos = self.camera.eye()
            self._voxel_renderer.render(mvp, cam_pos)

        # 5. Tool mesh (with depth test)
        if self._tool_mode == ToolMode.CYLINDER and self._cyl_vao is not None:
            self._tool_prog['u_mvp'].write(mvp.T.tobytes())
            self._tool_prog['u_tool_pos'].write(self._tool_pos.tobytes())
            self._cyl_vao.render(moderngl.TRIANGLES)

        # 6. Point cursor (always on top — no depth test)
        if self._tool_mode == ToolMode.POINT:
            self.ctx.disable(moderngl.DEPTH_TEST)
            self.ctx.point_size = 12.0
            self._cursor_vao.render(moderngl.POINTS, vertices=1)
            self.ctx.enable(moderngl.DEPTH_TEST)

        if self._perf:
            self._perf.tick()

        # 7. Corner-fill to blend viewport edge with host window gradient
        win = self.window()
        dpr = self.devicePixelRatioF()
        if win is not None:
            top_left           = self.mapTo(win, self.rect().topLeft())
            window_y_offset_px = top_left.y() * dpr
            window_height_px   = win.height() * dpr
        else:
            window_y_offset_px = 0.0
            window_height_px   = max(self.height() * dpr, 1.0)

        self.ctx.enable(moderngl.BLEND)
        self.ctx.disable(moderngl.DEPTH_TEST)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self._corner_fill_prog['u_resolution_px'].value    = (self.width() * dpr, self.height() * dpr)
        self._corner_fill_prog['u_radius_px'].value        = self._corner_radius_px * dpr
        self._corner_fill_prog['u_grad_top'].value         = self._win_grad_top
        self._corner_fill_prog['u_grad_bottom'].value      = self._win_grad_bottom
        self._corner_fill_prog['u_window_y_offset_px'].value = window_y_offset_px
        self._corner_fill_prog['u_window_height_px'].value = window_height_px
        self._corner_fill_vao.render(moderngl.TRIANGLES, vertices=3)
        self.ctx.disable(moderngl.BLEND)
        self.ctx.enable(moderngl.DEPTH_TEST)

    # ── Internal GPU helpers ──────────────────────────────────────────────────

    def _rebuild_tool_mesh(self, tool: ToolDefinition) -> None:
        """Upload a new tool solid-of-revolution mesh to the GPU."""
        verts, norms, colors = build_tool_mesh(tool)

        if self._cyl_vao is not None:
            self._cyl_vao.release()
            if hasattr(self, '_vbo_verts'):
                self._vbo_verts.release()
                self._vbo_norms.release()
                self._vbo_colors.release()

        self._vbo_verts  = self.ctx.buffer(verts.tobytes())
        self._vbo_norms  = self.ctx.buffer(norms.tobytes())
        self._vbo_colors = self.ctx.buffer(colors.tobytes())

        self._cyl_vao = self.ctx.vertex_array(
            self._tool_prog,
            [
                (self._vbo_verts,  '3f', 'in_pos'),
                (self._vbo_norms,  '3f', 'in_normal'),
                (self._vbo_colors, '3f', 'in_color'),
            ],
        )

    def _upload_path(self, path: PathBuffer) -> None:
        """Tessellate and upload the path to the GPU as a LINE_STRIP."""
        verts    = path.points.astype('f4')
        is_rapid = path.feed_rates < 1e-6
        colors   = np.where(
            is_rapid[:, None],
            [[1.0, 0.8, 0.0]],   # rapid = yellow
            [[0.0, 0.9, 1.0]],   # feed  = cyan
        ).astype('f4')

        if self._path_vao is not None:
            self._path_vao.release()

        self._path_vao = self.ctx.vertex_array(
            self._prog,
            [
                (self.ctx.buffer(verts.tobytes()),  '3f', 'in_pos'),
                (self.ctx.buffer(colors.tobytes()), '3f', 'in_col'),
            ],
        )
        self._path_vert_count  = len(verts)
        self._path_arc_lengths = path.arc_lengths
        self._path_split_idx   = 0
        self.update()

    # ── Settings callbacks ────────────────────────────────────────────────────

    def _on_bg_changed(self, hex_color: str) -> None:
        self._bg = _hex_to_rgb(hex_color)
        self.update()

    def _on_bg2_changed(self, hex_color: str) -> None:
        self._bg2 = _hex_to_rgb(hex_color)
        self.update()

    def _on_show_grid_changed(self, visible: bool) -> None:
        self._show_grid = visible
        self.update()

    # ── Mouse & touch interaction ─────────────────────────────────────────────

    def mousePressEvent(self, e) -> None:
        if e.source() != Qt.MouseEventNotSynthesized:
            return
        self._mouse_last = e.position()
        self._mouse_btn  = e.button()

    def mouseReleaseEvent(self, e) -> None:
        if e.source() != Qt.MouseEventNotSynthesized:
            return
        self._mouse_btn = None

    def mouseMoveEvent(self, e) -> None:
        if e.source() != Qt.MouseEventNotSynthesized:
            return
        if self._mouse_btn is None:
            return
        s = AppSettings.instance()
        d = e.position() - self._mouse_last
        self._mouse_last = e.position()

        if self._mouse_btn == Qt.LeftButton:
            sx = -1 if s.invert_rotate_x else 1
            sy = -1 if s.invert_rotate_y else 1
            self.camera.rotate(d.x() * s.rotate_speed * sx, d.y() * s.rotate_speed * sy)
        elif self._mouse_btn == Qt.MiddleButton:
            sx = -1 if s.invert_pan_x else 1
            sy = -1 if s.invert_pan_y else 1
            self.camera.pan(d.x() * s.pan_speed * sx, d.y() * s.pan_speed * sy)
        self.update()

    def wheelEvent(self, e) -> None:
        s   = AppSettings.instance()
        inv = -1 if s.invert_zoom else 1
        self.camera.zoom(e.angleDelta().y() * 0.5 * s.zoom_speed * inv)
        self.update()

    def event(self, e) -> bool:
        t = e.type()
        if t not in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd):
            return super().event(e)

        all_pts      = e.points()
        fingers_down = [p for p in all_pts if p.state() != QEventPoint.State.Released]

        if len(fingers_down) >= 2:
            self._multi_touch_active = True
        if len(fingers_down) == 0:
            self._multi_touch_active = False
            self._rotate_accumulated = QPointF(0, 0)

        active = [p for p in all_pts if p.state() in (
            QEventPoint.State.Pressed, QEventPoint.State.Updated,
            QEventPoint.State.Stationary,
        )]
        s = AppSettings.instance()

        if len(active) == 1 and len(fingers_down) == 1 and not self._multi_touch_active:
            d = active[0].position() - active[0].lastPosition()
            self._rotate_accumulated += d
            acc = (self._rotate_accumulated.x() ** 2 + self._rotate_accumulated.y() ** 2) ** 0.5
            if acc >= self.ROTATE_THRESHOLD:
                sx = -1 if s.invert_rotate_x else 1
                sy = -1 if s.invert_rotate_y else 1
                self.camera.rotate(d.x() * s.rotate_speed * sx, d.y() * s.rotate_speed * sy)

        elif len(active) >= 2:
            p1, p2 = active[0], active[1]
            self._rotate_accumulated = QPointF(0, 0)

            cur_dist  = _dist(p1.position(),     p2.position())
            last_dist = _dist(p1.lastPosition(), p2.lastPosition())
            inv       = -1 if s.invert_zoom else 1
            self.camera.zoom((cur_dist - last_dist) * 0.5 * s.zoom_speed * inv)

            d  = (p1.position()     + p2.position())     / 2
            d -= (p1.lastPosition() + p2.lastPosition()) / 2
            sx = -1 if s.invert_pan_x else 1
            sy = -1 if s.invert_pan_y else 1
            self.camera.pan(d.x() * s.pan_speed * sx, d.y() * s.pan_speed * sy)

        self.update()
        return True
