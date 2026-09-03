"""controller/persistence — the app's persistence layer (beyond QSettings).

Currently just the tool database (tool_db.py). QSettings (via
controller.sim.core.settings.AppSettings) remains the store for per-user UI
preferences; this package is for structured, queryable domain data — tools
today, more (workpieces, jobs, ...) as the app's roadmap gets there.
"""
