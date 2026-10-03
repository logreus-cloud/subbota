"""Фильтр безопасных команд и нормализация путей."""
from pathlib import Path

import pytest

from subbota.config import load_config
from subbota.permissions import is_safe_shell, resolve_tool_path

PATTERNS = load_config().permissions.safe_shell


@pytest.mark.parametrize("command", [
    "Get-ChildItem C:\\", "git status", "git branch -a", "git log -5", "dir", "ipconfig /all",
    "where.exe python", "cat README.md",
])
def test_read_only_commands_allowed(command):
    assert is_safe_shell(command, PATTERNS)


@pytest.mark.parametrize("command", [
    "echo (Remove-Item x)", "git branch -D x", 'git diff "--output=x"', "git diff --output=x",
    "ipconfig /release", "Remove-Item x", "Get-Content a.txt; rm b", "   ",
    "Get-Process -OutVariable x", "tasklist /F", "Get-Content a | Out-File b",
])
def test_dangerous_or_tricky_commands_rejected(command):
    assert not is_safe_shell(command, PATTERNS)


def test_relative_path_resolves_against_session_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert resolve_tool_path("note.txt", workspace) == (workspace / "note.txt").resolve()


def test_parent_escape_is_visible(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolved = resolve_tool_path("../outside.txt", workspace)
    assert not resolved.is_relative_to(workspace.resolve())
    assert resolved == (tmp_path / "outside.txt").resolve()


def test_absolute_path_kept(tmp_path):
    target = tmp_path / "a" / "b.txt"
    assert resolve_tool_path(str(target), Path("C:/somewhere")) == target.resolve()
