"""Discover source plugins through Python entry points.

Any installed package can add a source by declaring, in its pyproject.toml:

    [project.entry-points."fly_monitor.sources"]
    myservice = "my_package.module:MyServiceSource"
"""

from __future__ import annotations

from importlib.metadata import entry_points

from .plugin import SourcePlugin

GROUP = "fly_monitor.sources"


def discover() -> dict[str, SourcePlugin]:
    plugins: dict[str, SourcePlugin] = {}
    for ep in entry_points(group=GROUP):
        plugin = ep.load()()
        if not isinstance(plugin, SourcePlugin):
            raise TypeError(f"entry point {ep.name!r} does not implement SourcePlugin")
        plugins[plugin.name] = plugin
    return plugins
