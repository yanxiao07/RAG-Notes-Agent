"""知识库思维导图的生成、保存与版本冲突测试。"""

from typing import cast

from fastapi.testclient import TestClient

from app.application.mind_map_service import MindMapService


def test_semantic_mind_map_response_is_validated_before_rendering() -> None:
    themes = MindMapService._parse_themes(
        '{"themes":[{"title":"检索链路","points":["混合召回融合关键词和向量结果","重排仅处理已过滤的候选"]}]}'
    )

    graph = MindMapService._build_graph(root_label="RAG", themes=themes)
    nodes = cast(list[dict[str, object]], graph["nodes"])
    edges = cast(list[dict[str, str]], graph["edges"])

    assert nodes[1]["label"] == "检索链路"
    assert edges[0]["source"] == "root"


def test_generate_and_update_editable_mind_map(client: TestClient) -> None:
    knowledge_base = client.post(
        "/api/v1/knowledge-bases", json={"name": "图谱资料", "description": None}
    ).json()
    client.post(
        f"/api/v1/knowledge-bases/{knowledge_base['id']}/notes",
        json={"title": "缓存结论", "content": "Redis 缓存应设置 TTL。"},
    )
    generated = client.post(f"/api/v1/knowledge-bases/{knowledge_base['id']}/mind-maps/generate")
    assert generated.status_code == 201
    mind_map = generated.json()
    assert mind_map["graph"]["nodes"][0]["kind"] == "root"
    assert any(node["kind"] == "topic" for node in mind_map["graph"]["nodes"])

    graph = mind_map["graph"]
    graph["nodes"].append(
        {"id": "manual", "label": "人工补充", "kind": "manual", "position": {"x": 120, "y": 80}}
    )
    updated = client.put(
        f"/api/v1/mind-maps/{mind_map['id']}",
        json={"title": "已编辑图谱", "graph": graph, "version": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["graph"]["nodes"][-1]["id"] == "manual"

    conflict = client.put(
        f"/api/v1/mind-maps/{mind_map['id']}",
        json={"title": "冲突", "graph": graph, "version": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
