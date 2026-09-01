# DATUM Control

Custom HMI (PySide6) for a LinuxCNC-based CNC mill. Talks to the
machine exclusively through the `AbstractBackend` interface
(`src/controller/core/backends/base.py`), so the UI never knows
whether it's driving a real machine or a simulator.

## Status

Phase 1 (simulation only). `SimulatedBackend` is fully functional;
`LinuxCNCBackend` is not implemented yet (see its docstring). Only the
**Machine** page exists — Tools/Setup/Programs/Statistics/Settings are
disabled placeholders on the home screen. See the project's roadmap
discussion for the planned build-out order (persistence layer first,
then one page at a time).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
datum-control --simulate          # simulated backend, no hardware needed
datum-control --simulate --log-level DEBUG
```

Without installing the package:

```bash
python -m controller.main --simulate
```

Headless (CI, containers without a display):

```bash
QT_QPA_PLATFORM=offscreen datum-control --simulate
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen pytest
```

Tests assert against `SimulatedBackend`/`MachineController` state
(and, for `MainWindow`, that every button that claims to control the
machine actually reaches the backend) — not against pixels.

## Project structure

```
src/controller/
  core/
    backends/     AbstractBackend + SimulatedBackend + LinuxCNCBackend (stub)
    machine/      MachineController — 50ms poll loop, delta-signals, preconditions
  domain/
    models.py     Plain dataclasses: MachineState, Position, Tool, Workpiece, Job, ...
                  (no persistence yet — see roadmap)
  ui/
    main_window.py   App shell: nav grid + quick buttons (light/coolant/back)
    pages/           One page per screen (currently: machine_page.py only)
    widgets/         Reusable widgets (Card, CardButton, GCodeViewer, ...)
    resources/       Icons (SVG) + main.qss (single fixed stylesheet, no theming)
```

### Architecture rule

`ui/` only ever talks to `core/` through `MachineController` — never
directly to a backend, and never with `core`-specific business logic
duplicated in the UI. Every widget that shows live machine state does
so by connecting to a `MachineController` signal (`*_changed`); every
button that issues a machine command calls a `MachineController`
method (which applies preconditions before delegating to the
backend). This is what keeps `SimulatedBackend` and the future
`LinuxCNCBackend` interchangeable without touching UI code.
