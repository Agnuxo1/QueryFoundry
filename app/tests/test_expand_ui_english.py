import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PORTUGUESE_TEXT = (
    "expandir",
    "expansao",
    "expansão",
    "origem somente leitura",
    "destino",
    "versao",
    "versão",
    "registrar versoes",
    "registrar versões",
    "atualizar interface",
    "etapa atual",
    "seu nome",
    "remover",
    "finalizando",
)


def _node_string_literals(node: ast.AST) -> list[str]:
    return [
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]


def _load_tree(relative_path: str) -> ast.Module:
    source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
    return ast.parse(source)


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )


def _find_methods(tree: ast.Module, class_name: str, method_names: set[str]) -> list[ast.AST]:
    class_node = _find_class(tree, class_name)
    return [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in method_names
    ]


class ExpandUiEnglishTests(unittest.TestCase):
    def assert_has_no_portuguese_ui_text(self, nodes: list[ast.AST]):
        text = "\n".join(
            literal
            for node in nodes
            for literal in _node_string_literals(node)
        ).lower()
        matches = [term for term in FORBIDDEN_PORTUGUESE_TEXT if term in text]
        self.assertEqual(matches, [], f"Portuguese Expand UI text found: {matches}")

    def test_expand_dialog_and_progress_dialog_are_in_english(self):
        tree = _load_tree("ui/dialogs.py")
        self.assert_has_no_portuguese_ui_text(
            [
                _find_class(tree, "OperationProgressDialog"),
                _find_class(tree, "SqlExpansionDialog"),
                _find_class(tree, "SqlExpansionProgressDialog"),
                _find_class(tree, "ExpansionTimingReportDialog"),
            ]
        )

    def test_cross_table_expansion_runtime_messages_are_in_english(self):
        tree = _load_tree("main.py")
        methods = _find_methods(
            tree,
            "App",
            {
                "open_cross_table_expansion_progress",
                "request_cancel_cross_table_expansion",
                "execute_cross_table_expansion_flow",
                "_prepare_cross_table_expansion_flow_thread",
                "_open_cross_table_expansion_dialog",
                "_execute_cross_table_expansion_thread",
            },
        )
        self.assertEqual(len(methods), 6)
        self.assert_has_no_portuguese_ui_text(methods)


if __name__ == "__main__":
    unittest.main()
