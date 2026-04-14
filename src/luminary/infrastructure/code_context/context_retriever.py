"""Context retrieval orchestration for Code Context."""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from luminary.domain.config.code_context import CodeContextConfig
from luminary.domain.models.file_change import FileChange
from luminary.infrastructure.code_context.client import CodeContextClient

logger = logging.getLogger(__name__)


class CodeContextRetriever:
    """Builds compact retrieval context for a file change."""

    def __init__(self, client: CodeContextClient, config: CodeContextConfig):
        self.client = client
        self.config = config

    def retrieve_for_file_change(self, file_change: FileChange) -> Optional[str]:
        """Retrieve compact context text for a single file change."""
        queries = self._build_queries(file_change)
        if not queries:
            return None

        blocks: List[str] = []
        seen_symbols = set()
        for query in queries:
            hits = self.client.search(
                query,
                repo_name=self.config.repo_name,
                branch=self.config.branch,
                limit=self.config.search_limit,
            )
            if not hits:
                continue

            for hit in hits[: self.config.max_hits_per_query]:
                block = self._format_hit_block(hit)
                if block:
                    blocks.append(block)

                symbol_id = hit.get("symbol_id") if isinstance(hit, dict) else None
                if not symbol_id or symbol_id in seen_symbols:
                    continue
                seen_symbols.add(symbol_id)

                if self.config.max_neighbors > 0:
                    neighbors = self.client.get_symbol_neighbors(
                        symbol_id=symbol_id,
                        depth=self.config.neighbors_depth,
                    )
                    for neighbor in neighbors[: self.config.max_neighbors]:
                        neighbor_block = self._format_neighbor_block(neighbor)
                        if neighbor_block:
                            blocks.append(neighbor_block)

            if self._joined_size(blocks) >= self.config.max_context_chars:
                break

        if not blocks:
            return None

        context = "\n\n".join(blocks)
        if len(context) > self.config.max_context_chars:
            context = context[: self.config.max_context_chars].rstrip() + "\n...[retrieval truncated]"
        return context

    def _build_queries(self, file_change: FileChange) -> List[str]:
        """Generate a small set of high-signal queries from file change."""
        queries: List[str] = []
        queries.append(file_change.path)

        if file_change.hunks:
            for hunk in file_change.hunks[:2]:
                changed_lines = [
                    line[1:].strip()
                    for line in hunk.lines
                    if line and line[0] in {"+", "-"} and not line.startswith(("+++", "---"))
                ]
                if changed_lines:
                    query = self._normalize_query_text(" ".join(changed_lines[:3]))
                    if query:
                        queries.append(query)

        if file_change.new_content:
            symbols = self._extract_identifiers(file_change.new_content)
            if symbols:
                queries.append(" ".join(symbols[:6]))

        # Keep unique order and cap max queries
        uniq: List[str] = []
        seen = set()
        for query in queries:
            q = query.strip()
            if not q or q in seen:
                continue
            seen.add(q)
            uniq.append(q)
            if len(uniq) >= self.config.max_queries:
                break
        return uniq

    def _extract_identifiers(self, source: str) -> List[str]:
        """Extract potential code identifiers from source text."""
        candidates = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", source)
        # Keep stable order and filter common low-signal tokens
        stop_words = {
            "return",
            "class",
            "def",
            "true",
            "false",
            "none",
            "null",
            "this",
            "self",
            "const",
            "let",
            "var",
            "public",
            "private",
            "static",
            "import",
            "from",
        }
        out: List[str] = []
        seen = set()
        for token in candidates:
            lowered = token.lower()
            if lowered in stop_words or token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= 20:
                break
        return out

    def _normalize_query_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 180:
            return text[:180]
        return text

    def _format_hit_block(self, hit: Dict) -> Optional[str]:
        if not isinstance(hit, dict):
            return None
        path = hit.get("file_path") or hit.get("path") or "unknown"
        node_type = hit.get("node_type") or hit.get("type") or "symbol"
        node_text = hit.get("node_text") or hit.get("text") or ""
        node_text = str(node_text).strip()
        if len(node_text) > 400:
            node_text = node_text[:400].rstrip() + "..."
        return f"[hit] {path} :: {node_type}\n{node_text}"

    def _format_neighbor_block(self, neighbor: Dict) -> Optional[str]:
        if not isinstance(neighbor, dict):
            return None
        kind = neighbor.get("kind") or "related"
        name = neighbor.get("name") or neighbor.get("symbol") or "unknown"
        file_path = neighbor.get("file_path")
        if file_path:
            return f"[neighbor] {kind} {name} ({file_path})"
        return f"[neighbor] {kind} {name}"

    def _joined_size(self, blocks: List[str]) -> int:
        return sum(len(block) for block in blocks) + max(0, len(blocks) - 1) * 2
