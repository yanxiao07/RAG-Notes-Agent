"""GraphRAG-lite 的实体、关系和图辅助召回。

本模块刻意把“图结构”与“原始证据”分开：实体和关系只负责缩小候选范围，最终返回的
Evidence 始终来自 DocumentChunk。这样即使规则抽取不完整，也不会凭空生成一条没有来源
的事实；未来接入 LLM 抽取器时只需替换 ``RuleGraphExtractor``，不用改问答链路。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ModelUnavailableError
from app.core.model_resilience import call_with_model_resilience
from app.domain.knowledge.models import (
    ChunkEntityMention,
    Document,
    DocumentChunk,
    KnowledgeEntity,
    KnowledgeRelation,
)

if TYPE_CHECKING:
    from app.rag.retrieval import Evidence


RELATION_PATTERN = re.compile(
    r"(?P<source>[\u4e00-\u9fffA-Za-z0-9_./:-]{2,24})\s*"
    r"(?P<relation>依赖于|依赖|导致|影响|包含|使用|属于|关联|连接|支持|实现|调用|"
    r"通过|对应|来自|位于|提升|降低|阻止|解决|depends on|causes|affects|uses|"
    r"contains|supports|implements|calls|relates to)\s*"
    r"(?P<target>[\u4e00-\u9fffA-Za-z0-9_./:-]{2,24})",
    re.IGNORECASE,
)
ENGLISH_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9_.:/-]{1,63}\b")
CODE_ENTITY_PATTERN = re.compile(r"`([^`\n]{2,80})`")
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
SENTENCE_PATTERN = re.compile(r"[^。！？!?;；\n]+[。！？!?;；]?")

STOP_ENTITIES = {
    "这是",
    "可以",
    "如果",
    "因为",
    "所以",
    "我们",
    "系统",
    "用户",
    "文档",
    "内容",
    "问题",
    "结果",
    "方法",
    "the",
    "this",
    "that",
    "with",
    "from",
}


def normalize_entity_name(value: str) -> str:
    """使用兼容 Unicode 规范化，保证全角符号和大小写不会产生重复节点。"""

    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return re.sub(
        r"^[\s,，。.!！？?;；:：()（）\[\]【】{}]+|[\s,，。.!！？?;；:：()（）\[\]【】{}]+$",
        "",
        normalized,
    )


def _valid_entity(value: str) -> bool:
    normalized = normalize_entity_name(value)
    if len(normalized) < 2 or len(normalized) > 160:
        return False
    if normalized in STOP_ENTITIES or normalized.isdecimal():
        return False
    # 过滤整句被误识别为节点的情况；标题、代码标识和连接词两侧短语仍会保留。
    return not any(mark in normalized for mark in ("\n", "```", "。", "！", "？"))


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    name: str
    normalized_name: str
    entity_type: str = "concept"


@dataclass(frozen=True, slots=True)
class ExtractedRelation:
    source: str
    target: str
    relation_type: str
    confidence: float = 0.55


@dataclass(frozen=True, slots=True)
class GraphExtraction:
    entities: tuple[ExtractedEntity, ...]
    relations: tuple[ExtractedRelation, ...]


class RuleGraphExtractor:
    """可重复、无外部模型依赖的保守实体/关系抽取器。

    只从标题、反引号代码标识、英文专名和明确关系连接词提取候选，避免把普通中文
    句子拆成大量噪声节点。关系置信度用于后续排序，不代表模型意义上的真实性。
    """

    def extract(self, text: str) -> GraphExtraction:
        entities: dict[str, ExtractedEntity] = {}
        relations: dict[tuple[str, str, str], ExtractedRelation] = {}

        def add_entity(raw: str, *, entity_type: str = "concept") -> str | None:
            if not _valid_entity(raw):
                return None
            normalized = normalize_entity_name(raw)
            entities.setdefault(
                normalized,
                ExtractedEntity(
                    name=raw.strip(), normalized_name=normalized, entity_type=entity_type
                ),
            )
            return normalized

        for line in text.splitlines():
            heading = HEADING_PATTERN.match(line)
            if heading:
                add_entity(heading.group(1), entity_type="topic")

        for match in CODE_ENTITY_PATTERN.finditer(text):
            value = match.group(1).strip()
            if not value.startswith(("http://", "https://")):
                add_entity(value, entity_type="technology")

        for match in ENGLISH_ENTITY_PATTERN.finditer(text):
            add_entity(match.group(0), entity_type="proper_noun")

        for sentence_match in SENTENCE_PATTERN.finditer(text):
            sentence = sentence_match.group(0).strip()
            sentence_entities: list[str] = []
            for relation_match in RELATION_PATTERN.finditer(sentence):
                source = add_entity(relation_match.group("source"))
                target = add_entity(relation_match.group("target"))
                if source and target and source != target:
                    relation_type = relation_match.group("relation").lower()
                    key = (source, target, relation_type)
                    relations[key] = ExtractedRelation(
                        source=source,
                        target=target,
                        relation_type=relation_type,
                        confidence=0.85,
                    )
                    sentence_entities.extend((source, target))

            # 没有明确连接词时，仅把同一句中可识别的专名建立低置信共现边。
            for candidate in entities.values():
                if candidate.normalized_name in normalize_entity_name(sentence):
                    sentence_entities.append(candidate.normalized_name)
            unique_entities = list(dict.fromkeys(sentence_entities))[:8]
            for index, source in enumerate(unique_entities):
                for target in unique_entities[index + 1 :]:
                    if source == target:
                        continue
                    key = (source, target, "co_occurs")
                    relations.setdefault(
                        key,
                        ExtractedRelation(
                            source=source,
                            target=target,
                            relation_type="co_occurs",
                            confidence=0.45,
                        ),
                    )

        return GraphExtraction(tuple(entities.values()), tuple(relations.values()))


class LLMGraphExtractor(RuleGraphExtractor):
    """OpenAI 兼容结构化图谱抽取器，失败时由调用方回退规则抽取。

    继承规则抽取器是有意的：模型只负责补充实体和关系，解析失败或返回字段不完整时
    仍可安全执行 ``super().extract``，不会因为一个坏切块丢失整个文档的图索引。
    """

    name = "llm_graph_extractor"
    version = "v1"

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_api_key or not settings.llm_model:
            raise ModelUnavailableError(message="图谱 LLM 抽取需要先配置问答模型。")
        self._model = settings.llm_model
        self._url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        self._api_key = settings.llm_api_key
        self._timeout = settings.graph_llm_extraction_timeout_seconds
        self._max_chars = settings.graph_llm_extraction_max_chars
        self._settings = settings

    def extract(self, text: str) -> GraphExtraction:
        prompt = (
            "你是企业知识图谱抽取器。只根据输入文本抽取明确出现的实体和有方向的关系，"
            "不要补充常识或猜测。严格输出 JSON，不要 Markdown。格式："
            '{"entities":[{"name":"...","type":"concept"}],'
            '"relations":[{"source":"...","target":"...",'
            '"relation":"...","confidence":0.0}]}\n\n文本：' + text[: self._max_chars]
        )
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": "只返回合法 JSON 对象。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            response = call_with_model_resilience(
                lambda: _post_and_raise(
                    self._url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout,
                ),
                settings=self._settings,
                operation="graph_extraction",
            )
            content = response.json()["choices"][0]["message"]["content"]
            parsed = self._parse_json(str(content))
            extraction = self._validate_payload(parsed)
            if not extraction.entities:
                raise ValueError("LLM 未返回实体")
            return extraction
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelUnavailableError(message="图谱 LLM 抽取失败，将回退规则抽取。") from exc

    @staticmethod
    def _parse_json(content: str) -> dict[str, object]:
        import json

        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        value = json.loads(cleaned)
        if not isinstance(value, dict):
            raise ValueError("图谱抽取结果不是 JSON 对象")
        return value

    def _validate_payload(self, payload: dict[str, object]) -> GraphExtraction:
        entities: dict[str, ExtractedEntity] = {}
        raw_entities = payload.get("entities", [])
        if not isinstance(raw_entities, list):
            raw_entities = []
        for item in raw_entities:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            normalized = normalize_entity_name(name)
            if not _valid_entity(name):
                continue
            entities.setdefault(
                normalized,
                ExtractedEntity(
                    name=name[:160],
                    normalized_name=normalized[:160],
                    entity_type=str(item.get("type") or "concept")[:40],
                ),
            )
        relations: dict[tuple[str, str, str], ExtractedRelation] = {}
        raw_relations = payload.get("relations", [])
        if not isinstance(raw_relations, list):
            raw_relations = []
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            source = normalize_entity_name(str(item.get("source") or ""))
            target = normalize_entity_name(str(item.get("target") or ""))
            relation = str(item.get("relation") or "related_to").strip()[:60]
            if source not in entities or target not in entities or source == target:
                continue
            try:
                confidence = min(1.0, max(0.0, float(item.get("confidence", 0.65))))
            except (TypeError, ValueError):
                confidence = 0.65
            relations[(source, target, relation)] = ExtractedRelation(
                source=source,
                target=target,
                relation_type=relation,
                confidence=confidence,
            )
        return GraphExtraction(tuple(entities.values()), tuple(relations.values()))


def build_graph_extractor(settings: Settings) -> RuleGraphExtractor:
    """根据显式开关选择图谱抽取器；真实模型不可用时由索引服务回退规则实现。"""

    if settings.graph_llm_extraction_enabled and settings.llm_provider == "openai_compatible":
        try:
            return LLMGraphExtractor(settings)
        except ModelUnavailableError:
            pass
    return RuleGraphExtractor()


def _post_and_raise(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, object],
    timeout: float,
) -> httpx.Response:
    response = httpx.post(url, headers=headers, json=json, timeout=timeout)
    response.raise_for_status()
    return response


@dataclass(frozen=True, slots=True)
class GraphRetrievalStats:
    mode: str = "local"
    matched_entities: int = 0
    expanded_entities: int = 0
    graph_candidates: int = 0
    covered_documents: int = 0


def classify_graph_mode(query: str) -> str:
    """只把明确的关系/全局问题交给图谱，普通事实问题继续走低延迟 Hybrid。"""

    normalized = normalize_entity_name(query)
    if re.search(r"全局|整体|总体|概览|归纳|总结|趋势|主题|有哪些类别|全库|所有文档", normalized):
        return "global"
    if re.search(
        r"关系|关联|如何影响|为什么导致|因果|依赖|之间|链路|共同原因|上下游|影响了|如何连接",
        normalized,
    ):
        return "multi_hop"
    return "local"


class GraphIndexService:
    """维护单文档图索引，重复执行会先清理该文档旧边和旧关联。"""

    def __init__(self, extractor: RuleGraphExtractor | None = None) -> None:
        self.extractor = extractor or RuleGraphExtractor()

    def index_document(
        self,
        session: Session,
        *,
        document_id: str,
        workspace_id: str,
        knowledge_base_id: str,
        chunks: Iterable[DocumentChunk] | None = None,
    ) -> int:
        chunk_items = list(
            chunks
            or session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.workspace_id == workspace_id,
                )
                .order_by(DocumentChunk.ordinal.asc())
            )
        )
        self.delete_document(session, document_id=document_id, workspace_id=workspace_id)
        session.flush()

        entities = {
            item.normalized_name: item
            for item in session.scalars(
                select(KnowledgeEntity).where(
                    KnowledgeEntity.workspace_id == workspace_id,
                    KnowledgeEntity.knowledge_base_id == knowledge_base_id,
                )
            )
        }
        indexed_mentions = 0
        for chunk in chunk_items:
            try:
                extraction = self.extractor.extract(chunk.content)
            except Exception:
                # LLM 抽取是增强层，任何超时、限流或脏 JSON 都安全回退规则抽取。
                extraction = RuleGraphExtractor().extract(chunk.content)
            entity_by_name: dict[str, KnowledgeEntity] = {}
            for candidate in extraction.entities:
                entity = entities.get(candidate.normalized_name)
                if entity is None:
                    entity = KnowledgeEntity(
                        workspace_id=workspace_id,
                        knowledge_base_id=knowledge_base_id,
                        name=candidate.name[:160],
                        normalized_name=candidate.normalized_name[:160],
                        entity_type=candidate.entity_type,
                        mention_count=0,
                    )
                    session.add(entity)
                    session.flush()
                    entities[candidate.normalized_name] = entity
                entity_by_name[candidate.normalized_name] = entity
                session.add(
                    ChunkEntityMention(
                        workspace_id=workspace_id,
                        document_chunk_id=chunk.id,
                        entity_id=entity.id,
                        mention_count=max(
                            1, chunk.content.casefold().count(candidate.normalized_name)
                        ),
                    )
                )
                entity.mention_count += 1
                indexed_mentions += 1

            for relation in extraction.relations:
                source = entity_by_name.get(relation.source)
                target = entity_by_name.get(relation.target)
                if source is None or target is None or source.id == target.id:
                    continue
                session.add(
                    KnowledgeRelation(
                        workspace_id=workspace_id,
                        knowledge_base_id=knowledge_base_id,
                        document_chunk_id=chunk.id,
                        source_entity_id=source.id,
                        target_entity_id=target.id,
                        relation_type=relation.relation_type[:60],
                        confidence=relation.confidence,
                    )
                )
        session.flush()
        return indexed_mentions

    def delete_document(self, session: Session, *, document_id: str, workspace_id: str) -> None:
        """删除文档的图索引；切块本身由文档仓储负责删除。"""

        chunk_ids = select(DocumentChunk.id).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.workspace_id == workspace_id,
        )
        entity_ids = list(
            session.scalars(
                select(ChunkEntityMention.entity_id).where(
                    ChunkEntityMention.workspace_id == workspace_id,
                    ChunkEntityMention.document_chunk_id.in_(chunk_ids),
                )
            )
        )
        session.query(KnowledgeRelation).filter(
            KnowledgeRelation.workspace_id == workspace_id,
            KnowledgeRelation.document_chunk_id.in_(chunk_ids),
        ).delete(synchronize_session=False)
        session.query(ChunkEntityMention).filter(
            ChunkEntityMention.workspace_id == workspace_id,
            ChunkEntityMention.document_chunk_id.in_(chunk_ids),
        ).delete(synchronize_session=False)
        for entity_id in set(entity_ids):
            entity = session.get(KnowledgeEntity, entity_id)
            if entity is None:
                continue
            remaining = session.scalar(
                select(func.count(ChunkEntityMention.id)).where(
                    ChunkEntityMention.entity_id == entity_id,
                    ChunkEntityMention.workspace_id == workspace_id,
                )
            )
            entity.mention_count = int(remaining or 0)
            if entity.mention_count == 0:
                session.delete(entity)


class GraphRetriever:
    """图辅助召回器：实体匹配后扩展一跳，再返回原始切块证据。"""

    def __init__(self, *, max_entities: int = 24, max_candidates: int = 60) -> None:
        self.max_entities = max_entities
        self.max_candidates = max_candidates
        self.last_stats = GraphRetrievalStats()

    def retrieve(
        self,
        session: Session,
        *,
        knowledge_base_id: str,
        workspace_id: str,
        query: str,
        limit: int,
        mode: str,
    ) -> list[Evidence]:
        from app.rag.retrieval import Evidence

        entities = list(
            session.scalars(
                select(KnowledgeEntity)
                .where(
                    KnowledgeEntity.workspace_id == workspace_id,
                    KnowledgeEntity.knowledge_base_id == knowledge_base_id,
                )
                .order_by(KnowledgeEntity.mention_count.desc(), KnowledgeEntity.name.asc())
                .limit(self.max_entities * 4)
            )
        )
        query_normalized = normalize_entity_name(query)
        matched = [
            entity
            for entity in entities
            if entity.normalized_name in query_normalized
            or any(
                token and token in entity.normalized_name
                for token in query_normalized.split()
                if len(token) >= 2
            )
        ][: self.max_entities]
        if mode == "global" and not matched:
            matched = entities[: self.max_entities]
        matched_ids = {entity.id for entity in matched}
        expanded_ids = set(matched_ids)
        if matched_ids and mode in {"multi_hop", "global"}:
            relation_rows = session.execute(
                select(KnowledgeRelation.source_entity_id, KnowledgeRelation.target_entity_id)
                .where(
                    KnowledgeRelation.workspace_id == workspace_id,
                    KnowledgeRelation.knowledge_base_id == knowledge_base_id,
                    or_(
                        KnowledgeRelation.source_entity_id.in_(matched_ids),
                        KnowledgeRelation.target_entity_id.in_(matched_ids),
                    ),
                )
                .limit(self.max_entities * 4)
            )
            for source_id, target_id in relation_rows:
                expanded_ids.update((source_id, target_id))
        expanded_ids = set(list(expanded_ids)[: self.max_entities * 2])
        if not expanded_ids:
            self.last_stats = GraphRetrievalStats(mode=mode)
            return []

        rows = session.execute(
            select(ChunkEntityMention, KnowledgeEntity, DocumentChunk, Document)
            .join(KnowledgeEntity, KnowledgeEntity.id == ChunkEntityMention.entity_id)
            .join(DocumentChunk, DocumentChunk.id == ChunkEntityMention.document_chunk_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                ChunkEntityMention.workspace_id == workspace_id,
                ChunkEntityMention.entity_id.in_(expanded_ids),
                DocumentChunk.workspace_id == workspace_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.workspace_id == workspace_id,
                Document.status == "indexed",
            )
        )
        grouped: dict[str, tuple[float, DocumentChunk, Document]] = {}
        direct = {entity.id for entity in matched}
        for mention, entity, chunk, document in rows:
            score = (2.0 if entity.id in direct else 0.75) + min(mention.mention_count, 4) * 0.1
            current = grouped.get(chunk.id)
            if current is None or score > current[0]:
                grouped[chunk.id] = (score, chunk, document)
        ranked = sorted(grouped.values(), key=lambda item: (-item[0], item[1].ordinal))
        selected: list[tuple[float, DocumentChunk, Document]] = []
        seen_documents: set[str] = set()
        # 全局问题优先保证文档覆盖；关系问题优先保留图分数最高的证据。
        if mode == "global":
            for item in ranked:
                if item[2].id not in seen_documents:
                    selected.append(item)
                    seen_documents.add(item[2].id)
            selected.extend(item for item in ranked if item not in selected)
        else:
            selected = ranked
        selected = selected[: min(self.max_candidates, max(limit * 3, limit))]
        evidences = [
            Evidence(
                source_type="document_chunk",
                source_id=chunk.id,
                title=document.title,
                content=chunk.content,
                score=score,
                locator=f"document:{document.id}:chunk:{chunk.ordinal}",
                source_url=document.source_url,
                source_validation_state=document.source_validation_state,
                source_is_approved=document.source_is_approved,
            )
            for score, chunk, document in selected
        ]
        self.last_stats = GraphRetrievalStats(
            mode=mode,
            matched_entities=len(matched_ids),
            expanded_entities=max(0, len(expanded_ids) - len(matched_ids)),
            graph_candidates=len(evidences),
            covered_documents=len({document.id for _, _, document in selected}),
        )
        return evidences[:limit]


def fuse_graph_evidence(
    primary: list[Evidence], graph: list[Evidence], *, limit: int
) -> list[Evidence]:
    """以 RRF 融合普通 Hybrid 与图谱候选，避免不同评分量纲直接相加。"""

    ranked: dict[str, tuple[Evidence, float]] = {}
    for rank, evidence in enumerate(primary, start=1):
        ranked[evidence.locator] = (evidence, 1.0 / (60 + rank))
    for rank, evidence in enumerate(graph, start=1):
        current = ranked.get(evidence.locator)
        contribution = 1.0 / (60 + rank)
        if current is None:
            ranked[evidence.locator] = (evidence, contribution)
        else:
            ranked[evidence.locator] = (current[0], current[1] + contribution)
    ordered = sorted(ranked.values(), key=lambda item: (-item[1], item[0].locator))[:limit]
    maximum = max((score for _, score in ordered), default=1.0)
    return [replace(evidence, score=score / maximum) for evidence, score in ordered]
