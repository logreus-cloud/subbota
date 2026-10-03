from __future__ import annotations

from typing import TYPE_CHECKING

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server

from jarvis.tools import code, memory, reminders, system

if TYPE_CHECKING:
    from jarvis.context import AppContext


DANGEROUS_TOOLS = {"power_action", "close_window", "set_clipboard", "cancel_code_task"}


def build_jarvis_server(ctx: AppContext) -> McpSdkServerConfig:
    tools = [
        *system.make_tools(ctx),
        *reminders.make_tools(ctx),
        *memory.make_tools(ctx),
        *code.make_tools(ctx),
    ]
    return create_sdk_mcp_server(name="jarvis", version="0.1.0", tools=tools)
