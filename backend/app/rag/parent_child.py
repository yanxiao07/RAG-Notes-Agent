"""Parent-Child Retrieval 的本地实现。

子块负责精确召回，父上下文只在最终候选阶段拼接；所有扩展都限定在同一工作区、
知识库、文档和章节内，避免为了补上下文引入越权或无关资料。
"""

import re
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.knowledge.models import Document, DocumentChunk
from app.rag.retrieval import Evidence

CHILD_LOCATOR_PATTERN = re.compile(r"^document:(?P<document_id>[^:]+):chunk:(?P<ordinal>\d+)$")


@dataclass(frozen=True, slots=True)
class ParentChildExpansionStats:
    """上下文扩展统计，不包含正文内容。"""

    expanded_contexts: int = 0
    expanded_characters: int = 0


class ParentChildContextExpander:
    """按章节和相邻序号为命中子块补充有限上下文。"""

    def __init__(self, *, window: int = 1, max_characters: int = 2_400) -> None:
        self.window = max(0, window)
        self.max_characters = max(256, max_characters)

    def expand(
        self,
        session: Session,
        evidences: list[Evidence],
        *,
        knowledge_base_id: str,
        workspace_id: str,
    ) -> tuple[list[Evidence], ParentChildExpansionStats]:
        targets = self._targets(evidences)
        if not targets or self.window == 0:
            return evidences, ParentChildExpansionStats()

        document_ids = {document_id for document_id, _ in targets.values()}
        chunks = list(
            session.scalars(
                select(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(
                    DocumentChunk.workspace_id == workspace_id,
                    DocumentChunk.document_id.in_(document_ids),
                    Document.workspace_id == workspace_id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.status == "indexed",
                )
            )
        )
        chunks_by_document: dict[str, dict[int, DocumentChunk]] = {}
        for chunk in chunks:
            chunks_by_document.setdefault(chunk.document_id, {})[chunk.ordinal] = chunk

        expanded: list[Evidence] = []
        expanded_count = 0
        expanded_characters = 0
        for evidence in evidences:
            target = targets.get(evidence.locator)
            if target is None:
                expanded.append(evidence)
                continue
            document_id, ordinal = target
            child = chunks_by_document.get(document_id, {}).get(ordinal)
            if child is None:
                expanded.append(evidence)
                continue
            content = self._build_context(
                child,
                chunks_by_document.get(document_id, {}),
            )
            if content == evidence.content:
                expanded.append(evidence)
                continue
            expanded.append(replace(evidence, content=content))
            expanded_count += 1
            expanded_characters += len(content)

        return expanded, ParentChildExpansionStats(
            expanded_contexts=expanded_count,
            expanded_characters=expanded_characters,
        )

    @staticmethod
    def _targets(evidences: list[Evidence]) -> dict[str, tuple[str, int]]:
        targets: dict[str, tuple[str, int]] = {}
        for evidence in evidences:
            match = CHILD_LOCATOR_PATTERN.fullmatch(evidence.locator)
            if match is not None:
                targets[evidence.locator] = (
                    match.group("document_id"),
                    int(match.group("ordinal")),
                )
        return targets

    def _build_context(
        self,
        child: DocumentChunk,
        chunks: dict[int, DocumentChunk],
    ) -> str:
        child_section = self._section(child)
        selected: dict[int, DocumentChunk] = {child.ordinal: child}
        remaining = self.max_characters - len(child.content)
        for distance in range(1, self.window + 1):
            for ordinal in (child.ordinal - distance, child.ordinal + distance):
                candidate = chunks.get(ordinal)
                if candidate is None or self._section(candidate) != child_section:
                    continue
                candidate_length = len(candidate.content) + 2
                if candidate_length > remaining:
                    continue
                selected[ordinal] = candidate
                remaining -= candidate_length

        parts: list[str] = []
        seen: set[str] = set()
        for ordinal in sorted(selected):
            content = selected[ordinal].content.strip()
            normalized = " ".join(content.split())
            if not content or normalized in seen:
                continue
            seen.add(normalized)
            parts.append(content)
        return "\n\n".join(parts)[: self.max_characters]

    @staticmethod
    def _section(chunk: DocumentChunk) -> str:
        metadata = chunk.metadata_json or {}
        return str(metadata.get("section", "")).strip()
