"""已部署扩展目录：只读展示能力与版本，不允许经 API 加载可执行代码。"""

from dataclasses import dataclass

from app.extensions.builtin import build_builtin_registry
from app.extensions.registry import ExtensionRegistry


@dataclass(frozen=True, slots=True)
class ExtensionDescriptor:
    name: str
    version: str
    kind: str
    source_types: tuple[str, ...]


class ExtensionCatalogService:
    """将注册表投影为前端安全可见的版本目录。"""

    def __init__(self, registry: ExtensionRegistry | None = None) -> None:
        self.registry = registry or build_builtin_registry()

    def list_chunkers(self) -> list[ExtensionDescriptor]:
        return [
            ExtensionDescriptor(
                name=chunker.name,
                version=chunker.version,
                kind="chunker",
                source_types=("plain_text", "markdown", "pdf", "docx", "webpage"),
            )
            for chunker in self.registry.list_chunkers()
        ]

    def list_parsers(self) -> list[ExtensionDescriptor]:
        source_types = {
            "plain_text": ("plain_text", "webpage"),
            "markdown": ("markdown",),
            "utf8_text": ("plain_text",),
            "pdf": ("pdf",),
            "docx": ("docx",),
        }
        descriptors: dict[str, ExtensionDescriptor] = {}
        for parser in [*self.registry.list_parsers(), *self.registry.list_file_parsers()]:
            descriptors[parser.name] = ExtensionDescriptor(
                name=parser.name,
                version=parser.version,
                kind="parser",
                source_types=source_types.get(parser.name, ()),
            )
        return [descriptors[name] for name in sorted(descriptors)]
