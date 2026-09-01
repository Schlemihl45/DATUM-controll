"""
sim/ — 3D G-code simulation sub-package.

Provides DatumSimWidget (requires moderngl + numpy). Import it directly:

    from controller.sim.ui.main_widget import DatumSimWidget

or use the lazy helper in machine_page.py:

    try:
        from controller.sim.ui.main_widget import DatumSimWidget as _SimWidget
    except ImportError:
        from controller.ui.widgets.sim_placeholder import SimPlaceholder as _SimWidget

No top-level import of DatumSimWidget here — it transitively imports PySide6
widgets and moderngl, neither of which should be triggered just by the package
being discovered by Python's importer (e.g. during test collection).
"""
