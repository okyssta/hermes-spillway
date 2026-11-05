"""register() must claim exactly the surface the manifest declares."""

from __future__ import annotations

from spillway import register, schemas


class _RecordingCtx:
    def __init__(self):
        self.tools: list[str] = []
        self.hooks: list[str] = []
        self.skills: list[str] = []

    def register_tool(self, name, toolset, schema, handler):
        self.tools.append(name)

    def register_hook(self, name, callback):
        self.hooks.append(name)

    def register_skill(self, name, path):
        self.skills.append(name)

    def get_config(self, key, default=None):
        return default


def test_register_wires_five_tools_one_hook_and_the_skill(hermes_home):
    ctx = _RecordingCtx()
    register(ctx)
    assert ctx.tools == list(schemas.TOOL_NAMES)
    assert ctx.hooks == ["transform_tool_result"]
    assert ctx.skills == ["spillway"]
