import os
import site
from pathlib import Path

_ADDED: set[Path] = set()
_HANDLES: list[object] = []


def add_nvidia_dll_dirs() -> None:
    """Добавляет папки DLL NVIDIA в поиск Windows."""
    try:
        bases = site.getsitepackages()
    except (AttributeError, OSError):
        return
    for base in bases:
        try:
            for folder in Path(base).glob("nvidia/*/bin"):
                path = folder.resolve()
                if path in _ADDED or not path.is_dir():
                    continue
                _HANDLES.append(os.add_dll_directory(str(path)))
                os.environ["PATH"] = f"{path}{os.pathsep}{os.environ.get('PATH', '')}"
                _ADDED.add(path)
        except (AttributeError, OSError):
            continue
