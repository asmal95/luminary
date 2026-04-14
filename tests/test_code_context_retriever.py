"""Tests for Code Context retrieval adapter."""

from luminary.domain.config.code_context import CodeContextConfig
from luminary.domain.models.file_change import FileChange, Hunk
from luminary.infrastructure.code_context.context_retriever import CodeContextRetriever


class FakeCodeContextClient:
    def __init__(
        self,
        node_text: str = "def process_order(order): ...",
        neighbor_count: int = 2,
        neighbor_name: str = "validate_order",
    ):
        self.search_calls = []
        self.neighbor_calls = []
        self.node_text = node_text
        self.neighbor_count = neighbor_count
        self.neighbor_name = neighbor_name

    def search(self, query: str, **kwargs):
        self.search_calls.append((query, kwargs))
        return [
            {
                "file_path": "src/service.py",
                "node_type": "function",
                "node_text": self.node_text,
                "symbol_id": "sym-1",
            }
        ]

    def get_symbol_neighbors(self, symbol_id: str, depth: int = 2):
        self.neighbor_calls.append((symbol_id, depth))
        return [
            {"kind": "calls", "name": f"{self.neighbor_name}_{idx}", "file_path": "src/validation.py"}
            for idx in range(self.neighbor_count)
        ]


def test_retriever_returns_compact_context():
    cfg = CodeContextConfig(enabled=True, repo_name="group/project", branch="main")
    client = FakeCodeContextClient()
    retriever = CodeContextRetriever(client=client, config=cfg)
    file_change = FileChange(
        path="src/service.py",
        new_content="def process_order(order):\n    return validate(order)\n",
    )

    context = retriever.retrieve_for_file_change(file_change)

    assert context is not None
    assert "[hit] src/service.py" in context
    assert "[neighbor] calls validate_order" in context
    assert len(client.search_calls) >= 1
    assert len(client.neighbor_calls) == 1


def test_retriever_uses_hunks_for_query_generation():
    cfg = CodeContextConfig(enabled=True, max_queries=4)
    client = FakeCodeContextClient()
    retriever = CodeContextRetriever(client=client, config=cfg)
    file_change = FileChange(
        path="src/example.py",
        hunks=[
            Hunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=2,
                lines=["-old_logic()", "+new_logic()", "+validate_inputs()"],
            )
        ],
        new_content="def new_logic():\n    pass\n",
    )

    _ = retriever.retrieve_for_file_change(file_change)
    queried_texts = [query for query, _ in client.search_calls]

    assert "src/example.py" in queried_texts
    assert any("new_logic()" in query for query in queried_texts)


def test_retriever_truncates_large_context():
    cfg = CodeContextConfig(enabled=True, max_context_chars=1000, max_neighbors=50, max_queries=1)
    client = FakeCodeContextClient(node_text="x" * 5000, neighbor_count=50, neighbor_name="y" * 80)
    retriever = CodeContextRetriever(client=client, config=cfg)
    file_change = FileChange(path="src/service.py", new_content="def process_order(order): pass\n")

    context = retriever.retrieve_for_file_change(file_change)

    assert context is not None
    assert len(context) <= 1030
    assert "...[retrieval truncated]" in context
