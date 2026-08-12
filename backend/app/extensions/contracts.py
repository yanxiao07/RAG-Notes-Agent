"""扩展契约。第三方实现只能依赖这里的稳定类型。"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """解析后的标准文本表示，屏蔽 PDF、网页和 Markdown 的来源差异。"""

    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """尚未持久化的证据块。ordinal 保证文档内原始阅读顺序。"""

    ordinal: int
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


class DocumentParser(Protocol):
    name: str
    version: str

    def parse(self, *, title: str, content: str) -> ParsedDocument: ...


class FileDocumentParser(Protocol):
    """二进制文件解析协议，和文本 Parser 分开避免在接口中混入模糊的 bytes/string。"""

    name: str
    version: str

    def parse_bytes(self, *, title: str, content: bytes) -> ParsedDocument: ...


class Chunker(Protocol):
    name: str
    version: str

    def chunk(self, document: ParsedDocument) -> list[ChunkDraft]: ...


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class GroundingEvidence:
    """传给模型的最小证据表示，引用编号在整个回答流中保持稳定。"""

    citation_index: int
    title: str
    content: str
    locator: str
    source_url: str | None = None


class LLMProvider(Protocol):
    """支持流式输出的模型扩展契约。"""

    name: str
    model_name: str

    def stream_answer(
        self,
        *,
        conversation: list[ChatTurn],
        evidence: list[GroundingEvidence],
        response_mode: str,
        route_reason: str = "knowledge_request",
    ) -> Iterator[str]: ...


class EmbeddingProvider(Protocol):
    """文档和查询必须使用同一模型与维度，才可进行余弦相似度比较。"""

    name: str
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
