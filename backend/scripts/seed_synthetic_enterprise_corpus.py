"""向显式指定的知识库写入可复现实验语料。

语料仅用于离线 RAG 评测和压测，不含真实组织、用户或业务数据。脚本复用正式
IngestionService，而不是直接写 DocumentChunk，确保解析、脱敏、切分、Embedding 与图谱
索引链路同产品环境一致。默认绝不覆盖或删除已有资产。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.ingestion_service import IngestionService  # noqa: E402
from app.application.knowledge_service import KnowledgeService  # noqa: E402
from app.core.database import get_session_factory  # noqa: E402
from app.core.workspace import ensure_workspace  # noqa: E402
from app.domain.knowledge.models import Document  # noqa: E402

DEFAULT_CORPUS = BACKEND_ROOT / "evaluations" / "synthetic-enterprise-corpus.json"


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """评测文档的最小输入契约，避免把运行时字段混入版本化语料。"""

    identifier: str
    title: str
    content: str


def load_corpus(path: Path) -> list[CorpusDocument]:
    raw_items = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("语料文件必须是非空 JSON 数组。")

    corpus: list[CorpusDocument] = []
    ids: set[str] = set()
    titles: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("语料中的每一项必须为对象。")
        identifier = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        content = str(item.get("content", "")).strip()
        if not identifier or not title or not content:
            raise ValueError("语料项必须包含非空 id、title 和 content。")
        if identifier in ids or title in titles:
            raise ValueError(f"语料中的 id 或标题重复: {identifier}")
        ids.add(identifier)
        titles.add(title)
        corpus.append(CorpusDocument(identifier=identifier, title=title, content=content))
    return corpus


def _existing_documents(
    session, *, knowledge_base_id: str, workspace_id: str
) -> dict[str, Document]:
    documents: Iterable[Document] = session.scalars(
        select(Document).where(
            Document.knowledge_base_id == knowledge_base_id,
            Document.workspace_id == workspace_id,
            Document.status != "archived",
        )
    )
    return {document.title: document for document in documents}


def seed_corpus(
    *,
    knowledge_base_id: str,
    workspace_id: str | None,
    corpus: list[CorpusDocument],
) -> dict[str, int | str]:
    """写入缺失语料并同步执行任务；冲突立即失败，防止污染错误知识库。"""

    session_factory = get_session_factory()
    created = 0
    skipped = 0
    with session_factory() as session:
        workspace = ensure_workspace(session, workspace_id=workspace_id)
        KnowledgeService().get_knowledge_base(
            session,
            knowledge_base_id,
            workspace_id=workspace.id,
        )
        existing_by_title = _existing_documents(
            session,
            knowledge_base_id=knowledge_base_id,
            workspace_id=workspace.id,
        )
        service = IngestionService()
        for item in corpus:
            content_hash = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
            existing = existing_by_title.get(item.title)
            if existing is not None:
                if existing.content_hash != content_hash:
                    raise ValueError(
                        f"知识库中已存在同标题但内容不同的文档: {item.title}；"
                        "请创建新的评测知识库，脚本不会覆盖已有文档。"
                    )
                skipped += 1
                continue

            document, job = service.create_document(
                session,
                knowledge_base_id=knowledge_base_id,
                title=item.title,
                source_type="markdown",
                raw_content=item.content,
                parser_name="markdown",
                chunker_name="structured",
                workspace_id=workspace.id,
            )
            completed = service.run_job(session, job_id=job.id, workspace_id=workspace.id)
            if completed.state != "succeeded" or document.status != "indexed":
                raise RuntimeError(f"评测语料入库失败: {item.identifier}")
            created += 1

    return {
        "knowledgeBaseId": knowledge_base_id,
        "corpusDocuments": len(corpus),
        "created": created,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="写入合成企业 RAG 评测语料")
    parser.add_argument("--knowledge-base-id", required=True, help="已存在的隔离评测知识库 ID")
    parser.add_argument("--workspace-id", default=None)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    arguments = parser.parse_args()

    corpus = load_corpus(arguments.corpus)
    result = seed_corpus(
        knowledge_base_id=arguments.knowledge_base_id,
        workspace_id=arguments.workspace_id,
        corpus=corpus,
    )
    # 输出只含数量和 ID，不回显文档正文或评测问题。
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
