"""No service imports a sibling service; services reach each other only through the mesh."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sibling_imports(root: Path) -> list[tuple[str, int, str]]:
    """Every import under root/services that names a services module other than services.base, as
    (file name, line, module). Scans the source, so an import inside a function counts."""
    found = []
    for path in sorted((root / "services").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = f"services.{module}" if module else "services"
                names = [f"{module}.{alias.name}" for alias in node.names] if module == "services" else [module]
            else:
                continue
            found.extend(
                (path.name, node.lineno, name)
                for name in names
                if name.startswith("services.") and name != "services.base"
            )
    return found


def test_no_service_imports_a_sibling():
    assert sibling_imports(ROOT) == []


def test_scanner_catches_every_import_form(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "x.py").write_text(
        "from services.base import BaseService\n"
        "from .base import read_only\n"
        "from services import meetings\n"
        "import services.notes\n"
        "from .tasks import TaskService\n"
        "\n"
        "\n"
        "def f():\n"
        "    from . import users\n"
    )
    assert sibling_imports(tmp_path) == [
        ("x.py", 3, "services.meetings"),
        ("x.py", 4, "services.notes"),
        ("x.py", 5, "services.tasks"),
        ("x.py", 9, "services.users"),
    ]
