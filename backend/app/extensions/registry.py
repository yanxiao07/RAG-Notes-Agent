"""扩展注册表，集中处理名称冲突和按名称解析。"""

from app.extensions.contracts import Chunker, DocumentParser, FileDocumentParser, LLMProvider


class ExtensionNotFoundError(LookupError):
    """配置请求未启用的扩展时抛出。"""


class ExtensionRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}
        self._file_parsers: dict[str, FileDocumentParser] = {}
        self._chunkers: dict[str, Chunker] = {}
        self._llm_providers: dict[str, LLMProvider] = {}

    def register_parser(self, parser: DocumentParser) -> None:
        if parser.name in self._parsers:
            raise ValueError(f"解析器已注册: {parser.name}")
        self._parsers[parser.name] = parser

    def register_chunker(self, chunker: Chunker) -> None:
        if chunker.name in self._chunkers:
            raise ValueError(f"切块器已注册: {chunker.name}")
        self._chunkers[chunker.name] = chunker

    def register_file_parser(self, parser: FileDocumentParser) -> None:
        if parser.name in self._file_parsers:
            raise ValueError(f"文件解析器已注册: {parser.name}")
        self._file_parsers[parser.name] = parser

    def register_llm_provider(self, provider: LLMProvider) -> None:
        if provider.name in self._llm_providers:
            raise ValueError(f"模型 Provider 已注册: {provider.name}")
        self._llm_providers[provider.name] = provider

    def list_parsers(self) -> list[DocumentParser]:
        """返回稳定排序的已注册文本解析器，供只读目录展示。"""

        return [self._parsers[name] for name in sorted(self._parsers)]

    def list_file_parsers(self) -> list[FileDocumentParser]:
        """返回稳定排序的已注册二进制文件解析器。"""

        return [self._file_parsers[name] for name in sorted(self._file_parsers)]

    def list_chunkers(self) -> list[Chunker]:
        """返回当前部署白名单实际启用的切分器。"""

        return [self._chunkers[name] for name in sorted(self._chunkers)]

    def get_parser(self, name: str) -> DocumentParser:
        try:
            return self._parsers[name]
        except KeyError as exc:
            raise ExtensionNotFoundError(f"未找到解析器: {name}") from exc

    def get_chunker(self, name: str) -> Chunker:
        try:
            return self._chunkers[name]
        except KeyError as exc:
            raise ExtensionNotFoundError(f"未找到切块器: {name}") from exc

    def get_file_parser(self, name: str) -> FileDocumentParser:
        try:
            return self._file_parsers[name]
        except KeyError as exc:
            raise ExtensionNotFoundError(f"未找到文件解析器: {name}") from exc

    def get_llm_provider(self, name: str) -> LLMProvider:
        try:
            return self._llm_providers[name]
        except KeyError as exc:
            raise ExtensionNotFoundError(f"未找到模型 Provider: {name}") from exc
