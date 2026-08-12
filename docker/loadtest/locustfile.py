"""隔离环境下的 RAG 检索压测。

只调用不触发回答生成的 ``/api/v1/retrieval/search``，避免压测消耗 LLM Token 或产生
会话消息。查询来自版本化的合成评测集；运行方必须显式传入独立的知识库 ID。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from random import choice

from locust import HttpUser, between, task

CASES_PATH = Path("/evaluations/synthetic-enterprise-rag-cases.json")


def load_queries() -> list[str]:
    raw_cases = json.loads(CASES_PATH.read_text(encoding="utf-8-sig"))
    queries = [item.get("query") for item in raw_cases if isinstance(item, dict)]
    valid_queries = [query.strip() for query in queries if isinstance(query, str) and query.strip()]
    if not valid_queries:
        raise RuntimeError("合成评测集没有可用于压测的查询。")
    return valid_queries


KNOWLEDGE_BASE_ID = os.environ.get("RAG_LOAD_KNOWLEDGE_BASE_ID", "").strip()
if not KNOWLEDGE_BASE_ID:
    raise RuntimeError("必须显式设置 RAG_LOAD_KNOWLEDGE_BASE_ID，禁止压测默认知识库。")
QUERIES = load_queries()


class RetrievalLoadUser(HttpUser):
    """单次任务只验证 HTTP 结果和证据字段，不保存响应正文。"""

    wait_time = between(0.05, 0.25)

    @task
    def retrieve(self) -> None:
        payload = {
            "knowledgeBaseId": KNOWLEDGE_BASE_ID,
            "query": choice(QUERIES),
            "limit": 5,
        }
        with self.client.post("/api/v1/retrieval/search", json=payload, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"unexpected HTTP status: {response.status_code}")
                return
            try:
                body = response.json()
            except ValueError:
                response.failure("response is not JSON")
                return
            if not isinstance(body.get("evidences"), list):
                response.failure("response has no evidence list")
