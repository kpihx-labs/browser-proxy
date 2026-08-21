"""Source-wide documentation contract tests for public and internal Python functions."""

import ast
from pathlib import Path


REQUIRED_SECTIONS = ("Purpose:", "Args:", "Returns:", "Examples:")


def test_every_source_function_has_rich_typed_documentation() -> None:
    """Every callable documents purpose, typed arguments, return, and two examples."""
    failures: list[str] = []
    for path in Path("src/browser_proxy").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            docstring = ast.get_docstring(node) or ""
            missing_sections = [
                section for section in REQUIRED_SECTIONS if section not in docstring
            ]
            arguments = [
                argument.arg
                for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if argument.arg not in {"self", "cls"}
            ]
            undocumented = [argument for argument in arguments if f"{argument} (" not in docstring]
            if missing_sections or undocumented or docstring.count(">>>") < 2:
                failures.append(
                    f"{path}:{node.lineno} {node.name}: sections={missing_sections}, "
                    f"arguments={undocumented}, examples={docstring.count('>>>')}"
                )
    assert not failures, "\n".join(failures)
