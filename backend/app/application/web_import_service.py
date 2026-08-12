"""安全网页导入服务。

网页抓取属于不可信外部 I/O，只允许在 Worker 中执行。该模块负责 URL 规范化、
SSRF 防护、重定向校验、响应大小限制和 HTML 正文抽取，不把网页脚本或导航内容
直接送入 RAG 索引。
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx

from app.core.config import get_settings
from app.core.errors import ProcessingError
from app.rag.text_normalization import normalize_document_text

REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "nav",
    "footer",
    "header",
    "aside",
}
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}


@dataclass(frozen=True, slots=True)
class FetchedWebPage:
    url: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class WebSourceValidation:
    """网页来源的脱敏健康检查结果，不保存响应正文。"""

    state: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    error_code: str | None = None


def normalize_web_url(url: str, *, allow_http: bool | None = None) -> str:
    """校验并规范化 URL，同时阻断明显的内网/本机目标。"""

    settings = get_settings()
    parsed = urlsplit(url.strip())
    allow_insecure = settings.web_import_allow_http if allow_http is None else allow_http
    if parsed.scheme.lower() not in ({"https", "http"} if allow_insecure else {"https"}):
        raise ProcessingError(message="网页导入仅支持 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ProcessingError(message="网页地址不允许携带用户名或密码")
    if not parsed.hostname:
        raise ProcessingError(message="网页地址缺少有效主机名")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProcessingError(message="网页地址端口无效") from exc
    hostname = parsed.hostname.rstrip(".").lower()
    _reject_private_host(hostname, port or (443 if parsed.scheme.lower() == "https" else 80))
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def fetch_web_page(url: str) -> FetchedWebPage:
    """抓取网页并提取正文；所有网络异常都转换为稳定业务错误。"""

    settings = get_settings()
    current_url = normalize_web_url(url)
    client = httpx.Client(
        timeout=settings.web_import_timeout_seconds,
        follow_redirects=False,
        headers={"User-Agent": "RAG-Notes-Agent/1.0 (+web-import)"},
    )
    try:
        for redirect_count in range(settings.web_import_max_redirects + 1):
            with client.stream("GET", current_url) as response:
                if response.status_code in REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise ProcessingError(message="网页重定向缺少目标地址")
                    if redirect_count >= settings.web_import_max_redirects:
                        raise ProcessingError(message="网页重定向次数超过限制")
                    current_url = normalize_web_url(urljoin(current_url, location))
                    continue
                if response.status_code >= 400:
                    raise ProcessingError(
                        message="网页请求失败",
                        details={"statusCode": response.status_code},
                    )
                _ensure_html_content_type(response.headers.get("content-type", ""))
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > settings.web_import_max_bytes:
                            raise ProcessingError(message="网页响应超过大小限制")
                    except ValueError as exc:
                        raise ProcessingError(message="网页响应长度无效") from exc
                body = _read_limited_body(response, settings.web_import_max_bytes)
                encoding = response.encoding or "utf-8"
                try:
                    html = body.decode(encoding, errors="replace")
                except LookupError:
                    html = body.decode("utf-8", errors="replace")
                title, text = _extract_html(html)
                if not text:
                    raise ProcessingError(message="网页没有可索引的正文内容")
                return FetchedWebPage(
                    url=current_url,
                    title=title or _fallback_title(current_url),
                    text=text,
                )
        raise ProcessingError(message="网页请求未能完成")
    except httpx.HTTPError as exc:
        raise ProcessingError(message="网页连接失败") from exc
    finally:
        client.close()


def validate_web_source(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> WebSourceValidation:
    """校验网页来源当前是否仍可安全访问。

    校验只读取响应头。每次重定向都重新调用 ``normalize_web_url``，因此链接失效、
    协议降级或重定向到内网都不会绕过网页导入已有的 SSRF 边界。
    """

    settings = get_settings()
    try:
        current_url = normalize_web_url(url)
    except ProcessingError:
        return WebSourceValidation(
            state="unavailable",
            final_url=None,
            status_code=None,
            content_type=None,
            error_code="invalid_source_url",
        )

    client = httpx.Client(
        timeout=settings.source_validation_timeout_seconds,
        follow_redirects=False,
        headers={
            "User-Agent": "RAG-Notes-Agent/1.0 (+source-validator)",
            # 仅用于促使服务器尽早返回响应头，不能把网页正文带入校验任务。
            "Range": "bytes=0-0",
        },
        transport=transport,
    )
    try:
        for redirect_count in range(settings.web_import_max_redirects + 1):
            with client.stream("GET", current_url) as response:
                content_type = _normalize_content_type(response.headers.get("content-type", ""))
                if response.status_code in REDIRECT_STATUS_CODES:
                    location = response.headers.get("location")
                    if not location:
                        return WebSourceValidation(
                            state="unavailable",
                            final_url=current_url,
                            status_code=response.status_code,
                            content_type=content_type,
                            error_code="redirect_missing_location",
                        )
                    if redirect_count >= settings.web_import_max_redirects:
                        return WebSourceValidation(
                            state="unavailable",
                            final_url=current_url,
                            status_code=response.status_code,
                            content_type=content_type,
                            error_code="redirect_limit_exceeded",
                        )
                    try:
                        current_url = normalize_web_url(urljoin(current_url, location))
                    except ProcessingError:
                        return WebSourceValidation(
                            state="unavailable",
                            final_url=current_url,
                            status_code=response.status_code,
                            content_type=content_type,
                            error_code="redirect_target_rejected",
                        )
                    continue
                if not 200 <= response.status_code < 300:
                    return WebSourceValidation(
                        state="unavailable",
                        final_url=current_url,
                        status_code=response.status_code,
                        content_type=content_type,
                        error_code="http_status_unavailable",
                    )
                if not _is_html_content_type(content_type):
                    return WebSourceValidation(
                        state="unavailable",
                        final_url=current_url,
                        status_code=response.status_code,
                        content_type=content_type,
                        error_code="unsupported_content_type",
                    )
                return WebSourceValidation(
                    state="valid",
                    final_url=current_url,
                    status_code=response.status_code,
                    content_type=content_type,
                )
        return WebSourceValidation(
            state="unavailable",
            final_url=current_url,
            status_code=None,
            content_type=None,
            error_code="validation_incomplete",
        )
    except httpx.HTTPError:
        return WebSourceValidation(
            state="unavailable",
            final_url=current_url,
            status_code=None,
            content_type=None,
            error_code="network_error",
        )
    finally:
        client.close()


def _reject_private_host(hostname: str, port: int) -> None:
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ProcessingError(message="网页地址指向受限主机")
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            ]
        except (OSError, ValueError) as exc:
            raise ProcessingError(message="网页域名无法解析") from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ProcessingError(message="网页地址指向受限网络")


def _ensure_html_content_type(content_type: str) -> None:
    if not _is_html_content_type(content_type):
        raise ProcessingError(message="网页响应不是 HTML 文档")


def _normalize_content_type(content_type: str) -> str | None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type or None


def _is_html_content_type(content_type: str | None) -> bool:
    # 少数站点缺失 Content-Type，导入阶段已完成正文验证时允许保守通过；
    # 明确返回非 HTML 的站点必须标记为不可用，避免把下载页伪装成网页来源。
    return content_type in {None, "text/html", "application/xhtml+xml"}


def _read_limited_body(response: httpx.Response, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > maximum:
            raise ProcessingError(message="网页响应超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title" and self._skip_depth == 0:
            self._in_title = True
        if tag in BLOCK_TAGS and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in BLOCK_TAGS and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def _extract_html(html: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    title = normalize_document_text(" ".join(parser.title_parts))
    text = normalize_document_text(" ".join(parser.text_parts))
    return title, text


def _fallback_title(url: str) -> str:
    parsed: SplitResult = urlsplit(url)
    path_title = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return path_title or parsed.hostname or "网页文档"
