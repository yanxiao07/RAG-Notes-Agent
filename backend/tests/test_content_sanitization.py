"""知识入库敏感信息脱敏的确定性测试。"""

from app.security.content_sanitization import REDACTED_SECRET, sanitize_knowledge_content


def test_sanitizer_redacts_high_confidence_provider_tokens_only() -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    content = f'api_key="{secret}"\nplaceholder="sk-short"\n'

    result = sanitize_knowledge_content(content)

    assert secret not in result.content
    assert result.content == f'api_key="{REDACTED_SECRET}"\nplaceholder="sk-short"\n'
    assert result.redacted_count == 1


def test_sanitizer_handles_multiple_supported_token_prefixes() -> None:
    content = "sk-abcdefghijklmnopqrstuvwxyz123456 tvly-abcdefghijklmnopqrstuvwxyz123456"

    result = sanitize_knowledge_content(content)

    assert result.content == f"{REDACTED_SECRET} {REDACTED_SECRET}"
    assert result.redacted_count == 2
