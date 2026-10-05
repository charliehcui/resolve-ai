"""Evaluation datasets and runner for ResolveAI V2."""

import os

# 批量 Evaluation 不上传远端 Trace；正常应用继续使用 .env 开关。
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
