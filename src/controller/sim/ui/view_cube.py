"""
sim/ui/view_cube.py — Navigation cube geometry, face-label texture atlas,
and click-to-face hit-testing for Viewport's bottom-left view cube.

Pure geometry/data-building helpers (numpy + QImage/QPainter) — no GL
context needed here; Viewport wires the results into moderngl VAOs/
textures and does the actual click routing.

Face convention (Z-up, matching core/camera.py's ArcballCamera):
  FACES maps each label to (axis, sign, yaw, pitch):
    axis/sign — which local cube face this is: the face whose outward
                normal is +/-X (axis 0), +/-Y (axis 1), or +/-Z (axis 2).
    yaw/pitch — the camera angles that look at that face head-on ("normal
                to the face"), reusing ArcballCamera's own yaw/pitch
                convention so a snapped view and a rendered cube face agree
                pixel-for-pixel (front_view() elsewhere already uses
                yaw=0,pitch=0 — Front here matches that).
Right/Left/Top/Bottom are derived from the camera's own screen-right/
screen-up basis at each face's viewing angle (see _face_basis()) rather
than guessed, so a face's printed label always matches what you'd see if
you actually rotated the camera to face it head-on.
"""
from __future__ import annotations

import numpy as np

from controller.sim.core.camera import _look_at

# name -> (axis, sign, yaw, pitch)
#
# Top/Bottom use +-89.9 rather than the mathematically "exact" +-90: at
# pitch=+-90 the view direction is exactly parallel to the world Z axis,
# which is the gimbal-lock case _look_at() (core/camera.py) falls back on
# — a fixed, yaw-independent up-vector rather than the natural,
# yaw-dependent limit you'd get approaching the pole continuously. That
# fallback is fine for a one-off "look straight down" snap, but it made
# the cube's own baked mesh (built once, in build_cube_mesh() below) and
# its live per-frame render (paintGL, always using the *current* camera
# angles) disagree the moment either one actually hit the exact pole,
# rotating the Top/Bottom labels 90 deg out of sync with each other.
# 89.9 is also exactly ArcballCamera's own drag-clamp bound (see
# camera.py's on_drag()), so clicking Top/Bottom lands the main camera on
# a pitch a user could reach by dragging anyway — not a new, cube-only
# camera state.
FACES: dict[str, tuple[int, int, float, float]] = {
    "Front":  (0, +1,   0.0,   0.0),
    "Back":   (0, -1, 180.0,   0.0),
    "Right":  (1, +1,  90.0,   0.0),
    "Left":   (1, -1, -90.0,   0.0),
    "Top":    (2, +1,   0.0,  89.9),
    "Bottom": (2, -1,   0.0, -89.9),
}

# Reverse lookup for hit-testing: (axis, sign) -> label
_AXIS_SIGN_TO_NAME: dict[tuple[int, int], str] = {
    (axis, sign): name for name, (axis, sign, _yaw, _pitch) in FACES.items()
}

# Camera angles to snap to, keyed by face label — the click-handling side
# of FACES; kept as its own dict so callers don't need to know the tuple
# layout.
FACE_VIEWS: dict[str, tuple[float, float]] = {
    name: (yaw, pitch) for name, (_axis, _sign, yaw, pitch) in FACES.items()
}

CUBE_HALF     = 1.0    # cube mesh half-extent, local units
CUBE_DISTANCE = 4.0    # fixed synthetic camera distance for the mini view
ORTHO_HALF    = 1.8    # orthographic half-extent — a bit past the cube's
                        # corner-to-corner radius (sqrt(3) ~= 1.73) so the
                        # whole cube stays in frame at any rotation

ATLAS_COLS, ATLAS_ROWS = 3, 2
CELL_PX = 128
ATLAS_W, ATLAS_H = ATLAS_COLS * CELL_PX, ATLAS_ROWS * CELL_PX


# ── Rendering matrices ────────────────────────────────────────────────────────

def _eye_for(yaw: float, pitch: float, distance: float) -> np.ndarray:
    """Eye position for (yaw, pitch) at *distance* from the origin, Z-up —
    identical convention to ArcballCamera.eye()."""
    yr, pr = np.radians(yaw), np.radians(pitch)
    return distance * np.array([
        np.cos(pr) * np.cos(yr), np.cos(pr) * np.sin(yr), np.sin(pr),
    ], dtype='f4')


def view_matrix(yaw: float, pitch: float, distance: float = CUBE_DISTANCE) -> np.ndarray:
    """View matrix for the mini cube scene at the given camera angles."""
    eye = _eye_for(yaw, pitch, distance)
    return _look_at(eye, np.zeros(3, dtype='f4'), np.array([0.0, 0.0, 1.0], dtype='f4'))


def ortho_matrix(half: float = ORTHO_HALF, near: float = 0.1, far: float = 10.0) -> np.ndarray:
    """Symmetric orthographic projection, half_w == half_h == *half*."""
    return np.array([
        [1.0 / half, 0.0,        0.0,                          0.0],
        [0.0,        1.0 / half, 0.0,                          0.0],
        [0.0,        0.0,       -2.0 / (far - near), -(far + near) / (far - near)],
        [0.0,        0.0,        0.0,                          1.0],
    ], dtype='f4')


# ── Shared basis math ────────────────────────────────────────────────────────

def _face_basis(
    yaw: float, pitch: float, distance: float = CUBE_DISTANCE,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(eye, right, up) for viewing the origin from (yaw, pitch) at
    *distance*, Z-up. Reused for both building the cube mesh's per-face UV
    orientation and for turning a click into a world-space ray, so "which
    way is up/right on screen" always agrees between the rendered cube and
    the hit-test — both read off the same view_matrix() used to render it.
    """
    m = view_matrix(yaw, pitch, distance)
    eye   = _eye_for(yaw, pitch, distance)
    right = m[0, :3].copy()
    up    = m[1, :3].copy()
    return eye, right, up


# ── Label atlas (QImage, built with QPainter — no GL needed) ─────────────────

def build_label_atlas():
    """Return a QImage (Format_RGBA8888, ATLAS_W x ATLAS_H) with one
    labeled cell per face, in FACES' iteration order (row-major, ATLAS_COLS
    per row) — atlas_uv(index) must be read with the same order."""
    from PySide6.QtGui import QImage, QPainter, QColor, QFont
    from PySide6.QtCore import Qt as _Qt

    img = QImage(ATLAS_W, ATLAS_H, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Sans")
    font.setPixelSize(22)
    font.setBold(True)
    p.setFont(font)

    for i, name in enumerate(FACES):
        col, row = i % ATLAS_COLS, i // ATLAS_COLS
        x, y = col * CELL_PX, row * CELL_PX
        p.setPen(QColor(255, 255, 255, 50))
        p.setBrush(QColor(58, 68, 84, 235))
        p.drawRoundedRect(x + 3, y + 3, CELL_PX - 6, CELL_PX - 6, 12, 12)
        p.setPen(QColor(228, 234, 240, 255))
        p.drawText(x, y, CELL_PX, CELL_PX, _Qt.AlignmentFlag.AlignCenter, name)
    p.end()
    return img


def atlas_uv(index: int) -> tuple[float, float, float, float]:
    """(u0, v0, u1, v1) for atlas cell *index* (FACES iteration order).

    v0 < v1 corresponds to the TOP < BOTTOM of the cell as drawn in the
    QImage (row 0 = top). Texture rows are uploaded in the same order the
    QImage stores them (row 0 of the buffer = row 0 of the image), so v=0
    in the shader samples that same top row — no separate Y-flip needed,
    build_cube_mesh() relies on this when it maps each corner's "up" side
    to the smaller-v (top-of-label) edge.
    """
    col, row = index % ATLAS_COLS, index // ATLAS_COLS
    u0, u1 = col / ATLAS_COLS, (col + 1) / ATLAS_COLS
    v0, v1 = row / ATLAS_ROWS, (row + 1) / ATLAS_ROWS
    return u0, v0, u1, v1


# ── Cube mesh (verts + uvs, textured-quad shader) ─────────────────────────────

def build_cube_mesh(half: float = CUBE_HALF) -> tuple[np.ndarray, np.ndarray]:
    """(verts (36,3) f4, uvs (36,2) f4) — 6 faces x 2 triangles x 3 verts.

    Each face's quad is built from that face's OWN (right, up) basis (see
    _face_basis, evaluated at the yaw/pitch that looks at the face head-on)
    rather than a generic per-axis rule — this is what keeps every label
    right-way-up and unmirrored: e.g. Front and Back share the Z axis as
    their vertical, but Back's horizontal is mirrored relative to Front's
    because the camera's own screen-right flips when you turn to face it,
    and the mesh now mirrors the same way.
    """
    verts: list[list[float]] = []
    uvs:   list[list[float]] = []

    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]   # (right, up) signs, CCW

    for i, (name, (axis, sign, yaw, pitch)) in enumerate(FACES.items()):
        _eye, right, up = _face_basis(yaw, pitch)
        axis_vec = np.zeros(3, dtype='f4')
        axis_vec[axis] = sign

        u0, v0, u1, v1 = atlas_uv(i)
        # up=+1 (screen-top of label) -> v0 (top row of the atlas cell);
        # up=-1 (screen-bottom) -> v1. See atlas_uv()'s docstring.
        quad_uv = [(u0, v1), (u1, v1), (u1, v0), (u0, v0)]

        pts = [
            (axis_vec * half + right * c1 * half + up * c2 * half)
            for c1, c2 in corners
        ]
        for idx in (0, 1, 2, 0, 2, 3):
            verts.append(pts[idx].tolist())
            uvs.append(list(quad_uv[idx]))

    return np.array(verts, dtype='f4'), np.array(uvs, dtype='f4')


# ── Hit-testing ───────────────────────────────────────────────────────────────

def screen_to_ray(
    ndc_x: float, ndc_y: float, yaw: float, pitch: float,
    half_w: float = ORTHO_HALF, half_h: float = ORTHO_HALF,
    distance: float = CUBE_DISTANCE,
) -> tuple[np.ndarray, np.ndarray]:
    """World-space (ray_origin, ray_dir) for an orthographic click at NDC
    (ndc_x, ndc_y) in [-1, 1], for the mini-view looking at the origin from
    (yaw, pitch) — same basis as build_cube_mesh()'s per-face projection,
    so a click is tested against the exact geometry that was rendered."""
    eye, right, up = _face_basis(yaw, pitch, distance)
    forward = -eye / np.linalg.norm(eye)
    origin = eye + right * ndc_x * half_w + up * ndc_y * half_h
    return origin.astype('f4'), forward.astype('f4')


def pick_face(
    ray_origin: np.ndarray, ray_dir: np.ndarray, half: float = CUBE_HALF,
) -> str | None:
    """Ray-vs-axis-aligned-cube (slab method); returns the label of the
    face first hit, or None if the ray misses the cube entirely."""
    t_min, t_max = -np.inf, np.inf
    for axis in range(3):
        d = float(ray_dir[axis])
        o = float(ray_origin[axis])
        if abs(d) < 1e-9:
            if o < -half or o > half:
                return None
            continue
        t1 = (-half - o) / d
        t2 = (half - o) / d
        if t1 > t2:
            t1, t2 = t2, t1
        t_min = max(t_min, t1)
        t_max = min(t_max, t2)
        if t_min > t_max:
            return None
    if t_max < 0:
        return None
    t_hit = t_min if t_min >= 0 else t_max
    p = ray_origin + t_hit * ray_dir
    axis = int(np.argmax(np.abs(p)))
    sign = 1 if p[axis] >= 0 else -1
    return _AXIS_SIGN_TO_NAME.get((axis, sign))


def pick_face_at(ndc_x: float, ndc_y: float, yaw: float, pitch: float) -> str | None:
    """Convenience: NDC click position + current camera angles -> face label."""
    origin, direction = screen_to_ray(ndc_x, ndc_y, yaw, pitch)
    return pick_face(origin, direction)
