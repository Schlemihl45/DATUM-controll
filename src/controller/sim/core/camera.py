"""sim/core/camera.py — Arcball camera with Z-up coordinate system."""
import numpy as np


# ── Matrix helpers ────────────────────────────────────────────────────────────

def _perspective(fov_deg, aspect, near, far):
    f = 1.0 / np.tan(np.radians(fov_deg) / 2.0)
    return np.array([
        [f / aspect, 0,  0,                           0],
        [0,          f,  0,                           0],
        [0,          0,  (far + near) / (near - far), (2 * far * near) / (near - far)],
        [0,          0, -1,                           0],
    ], dtype='f4')


def _look_at(eye, target, up):
    f = target - eye
    f /= np.linalg.norm(f)
    r = np.cross(f, up)
    r_len = np.linalg.norm(r)
    if r_len < 1e-6:
        # Gimbal lock: camera looks exactly along Z → fallback up vector
        up    = np.array([0.0, 1.0, 0.0], dtype='f4')
        r     = np.cross(f, up)
        r_len = np.linalg.norm(r)
    r /= r_len
    u  = np.cross(r, f)
    return np.array([
        [ r[0],  r[1],  r[2], -np.dot(r, eye)],
        [ u[0],  u[1],  u[2], -np.dot(u, eye)],
        [-f[0], -f[1], -f[2],  np.dot(f, eye)],
        [    0,      0,     0,               1],
    ], dtype='f4')


# ── Camera class ──────────────────────────────────────────────────────────────

class ArcballCamera:

    def __init__(self):
        self.target   = np.zeros(3, dtype='f4')
        self.distance = 300.0
        self.yaw      = 45.0    # Rotation around Z axis
        self.pitch    = 30.0    # Height: 0=horizon, 90=directly from above
        self.fov      = 45.0

    # ── Input handlers ────────────────────────────────────────────────

    def rotate(self, dx: float, dy: float):
        self.yaw  -= dx * 0.4
        # -89.9 allows looking from below, +89.9 almost directly from above
        self.pitch = float(np.clip(self.pitch + dy * 0.4, -89.9, 89.9))

    def zoom(self, delta: float):
        self.distance = max(5.0, self.distance - delta * 0.8)

    def pan(self, dx: float, dy: float):
        scale        = self.distance * 0.0012
        self.target -= (self._right() * dx + self._up_world() * dy) * scale

    # ── Matrix accessors ──────────────────────────────────────────────

    def eye(self) -> np.ndarray:
        yr = np.radians(self.yaw)
        pr = np.radians(self.pitch)
        # Z-up: Z carries the pitch, XY carry the yaw
        offset = np.array([
            np.cos(pr) * np.cos(yr),   # X
            np.cos(pr) * np.sin(yr),   # Y
            np.sin(pr),                 # Z ← up
        ], dtype='f4') * self.distance
        return self.target + offset

    def view_matrix(self) -> np.ndarray:
        return _look_at(
            self.eye(),
            self.target,
            np.array([0.0, 0.0, 1.0], dtype='f4'),   # Z-up
        )

    def proj_matrix(self, aspect: float) -> np.ndarray:
        return _perspective(self.fov, aspect, 0.1, 10_000.0)

    def mvp(self, aspect: float) -> np.ndarray:
        return self.proj_matrix(aspect) @ self.view_matrix()

    # ── Helper vectors ────────────────────────────────────────────────

    def _right(self) -> np.ndarray:
        # Right vector lies in the XY plane, perpendicular to yaw
        yr = np.radians(self.yaw)
        return np.array([-np.sin(yr), np.cos(yr), 0.0], dtype='f4')

    def _up_world(self) -> np.ndarray:
        # Camera-up: perpendicular to _right() and the view vector
        e  = self.eye() - self.target
        r  = self._right()
        up = np.cross(r, e)
        n  = np.linalg.norm(up)
        return up / n if n > 1e-6 else np.array([0.0, 0.0, 1.0], dtype='f4')

    # ── Convenience views ─────────────────────────────────────────────

    def focus_on(self, center: np.ndarray, size: float):
        self.target   = center.astype('f4')
        self.distance = max(size * 2.0, 50.0)

    def top_view(self):
        self.yaw   = 0.0
        self.pitch = 89.0

    def front_view(self):
        self.yaw   = 0.0
        self.pitch = 0.0

    def iso_view(self):
        self.yaw   = 45.0
        self.pitch = 30.0
