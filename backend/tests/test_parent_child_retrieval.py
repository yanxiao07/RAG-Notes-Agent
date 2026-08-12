"""Parent-Child 上下文扩展的隔离、章节边界和引用锚点测试。"""

from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.domain.knowledge.models import Document, DocumentChunk, KnowledgeBase
from app.domain.workspace import Workspace
from app.rag.parent_child import ParentChildContextExpander
from app.rag.retrieval import Evidence


def test_parent_child_expands_same_section_without_changing_locator(
    session_factory: sessionmaker[Session],
) -> None:
    workspace_id = "00000000-0000-0000-0000-000000000001"
    knowledge_base_id = str(uuid4())
    document_id = str(uuid4())
    child_id = str(uuid4())
    with session_factory() as session:
        session.add(Workspace(id=workspace_id, name="默认工作区"))
        session.add(
            KnowledgeBase(
                id=knowledge_base_id,
                workspace_id=workspace_id,
                name="上下文测试库",
                index_status="ready",
            )
        )
        session.add(
            Document(
                id=document_id,
                workspace_id=workspace_id,
                knowledge_base_id=knowledge_base_id,
                title="上下文测试文档",
                source_type="markdown",
                raw_content="原文",
                status="indexed",
            )
        )
        session.add_all(
            [
                DocumentChunk(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ordinal=0,
                    content="章节前文",
                    metadata_json={"section": "第一章"},
                ),
                DocumentChunk(
                    id=child_id,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ordinal=1,
                    content="命中子块",
                    metadata_json={"section": "第一章"},
                ),
                DocumentChunk(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ordinal=2,
                    content="章节后文",
                    metadata_json={"section": "第一章"},
                ),
                DocumentChunk(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    document_id=document_id,
                    ordinal=3,
                    content="第二章无关内容",
                    metadata_json={"section": "第二章"},
                ),
            ]
        )
        session.commit()

        evidence = Evidence(
            "document_chunk",
            child_id,
            "上下文测试文档",
            "命中子块",
            1.0,
            f"document:{document_id}:chunk:1",
        )
        expanded, stats = ParentChildContextExpander(window=1, max_characters=500).expand(
            session,
            [evidence],
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace_id,
        )

    assert expanded[0].locator == evidence.locator
    assert "章节前文" in expanded[0].content
    assert "命中子块" in expanded[0].content
    assert "章节后文" in expanded[0].content
    assert "第二章无关内容" not in expanded[0].content
    assert stats.expanded_contexts == 1
    assert stats.expanded_characters == len(expanded[0].content)


def test_parent_child_does_not_expand_notes() -> None:
    evidence = Evidence("note", "note-1", "笔记", "笔记正文", 1.0, "note:note-1")
    expanded, stats = ParentChildContextExpander().expand(
        session=None,  # type: ignore[arg-type]
        evidences=[evidence],
        knowledge_base_id="kb",
        workspace_id="workspace",
    )

    assert expanded == [evidence]
    assert stats.expanded_contexts == 0
