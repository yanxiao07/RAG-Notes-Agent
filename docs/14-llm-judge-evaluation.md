# 可选 LLM 判分评测

## 定位

DeepEval 适配器用于在已获授权、已脱敏的离线样本上补充观察回答相关性和证据忠实度。它不是 RAG 质量放行条件：引用存在性、无证据拒答、工作区隔离、Prompt/密钥保护和确定性检索指标仍是主门禁。

## 安全边界

- 仅安装可选依赖后启用：`uv sync --extra judge`。
- 评测样本不进入数据库、Git、日志、Trace 或 CI 工件；只允许在受控本地目录保存。
- 执行命令必须显式传入 `--allow-external-judge`，避免未确认就把内容发送给外部判分模型。
- 输出报告只保留用例 ID、分数、状态和稳定错误码，不保存问题、回答、检索正文或 DeepEval 的自然语言理由。
- 依赖未安装、判分模型不可用或单用例执行失败时，结果明确显示 `skipped`/`failed`，不会生成伪分数。

## 输入契约

本地 JSON 文件为数组，每项需要 `id`、`input`、`actualOutput` 与非空 `retrievalContext`。调用方应先完成数据授权与脱敏：

```json
[
  {
    "id": "deidentified-case-001",
    "input": "已脱敏问题",
    "actualOutput": "已脱敏回答",
    "retrievalContext": ["已脱敏检索证据"]
  }
]
```

## 执行

```powershell
cd backend
uv sync --extra judge
uv run python scripts/evaluate_llm_judge.py `
  --cases <受控本地样本.json> `
  --output artifacts/evaluations/llm-judge-report.json `
  --allow-external-judge `
  --require-judge
```

`--require-judge` 用于人工验收任务：依赖缺失、模型不可用或任一用例失败时返回非零状态。该命令不应放入默认 CI 质量门禁；真实业务基线须由人工标注、脱敏审核和确定性回归共同构成。
