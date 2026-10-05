import json
import logging
from uuid import UUID

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langsmith import traceable
from openai import LengthFinishReasonError
from pydantic import ValidationError

from backend.app.database import get_connection
from backend.app.models import AnswerClaim, RetrievedChunk, UserContext

logger = logging.getLogger(__name__)


class StructuredOutputError(RuntimeError):
    def __init__(self, stage: str, events: list[dict], usage: dict):
        super().__init__(f"{stage} did not return valid structured output after one recovery")
        self.events = events
        self.usage = usage


@traceable(name="structured_generation", run_type="chain", process_inputs=lambda inputs: {"stage": inputs["stage"]})
def invoke_structured(model, schema: dict, output_type, messages: list, stage: str, expected_indices: list[int] | None = None, validate_output=None) -> tuple[object, dict, list[dict]]:
    structured_model = model.with_structured_output(schema, include_raw=True)
    events = []
    token_usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    for attempt in range(2):
        result = {}
        try:
            result = structured_model.invoke(messages)
            usage = usage_from_message(result.get("raw"))
            for key, value in usage.items():
                if value is not None:
                    token_usage[key] = (token_usage[key] or 0) + value
            parsed = result.get("parsed")
            if parsed is None:
                content = getattr(result.get("raw"), "content", "")
                if isinstance(content, str):
                    content = content.strip()
                    if content.startswith("```json") and content.endswith("```"):
                        content = content[7:-3].strip()
                    parsed = json.loads(content)
                    events.append({"stage": stage, "attempt": attempt + 1, "recovery": "raw_json"})
            parsed = output_type.model_validate(parsed)
            if expected_indices is not None and sorted(check.claim_index for check in parsed.checks) != expected_indices:
                raise ValueError("Citation checker must assess each claim exactly once")
            if validate_output is not None:
                validate_output(parsed)
            events.append({"stage": stage, "attempt": attempt + 1, "status": "valid"})
            return parsed, token_usage, events
        except (OutputParserException, ValidationError, ValueError, TypeError, LengthFinishReasonError) as error:
            events.append({"stage": stage, "attempt": attempt + 1, "status": "structured_output_error", "error_type": type(error).__name__})
            logger.warning("structured_output %s", json.dumps(events[-1]))
            if attempt == 0:
                correction = "上次输出不符合 schema。仅返回符合 schema 的 JSON 对象，保留真实证据编号；不要输出 Markdown 或解释。"
                if expected_indices is not None:
                    correction += f" checks 必须逐一覆盖索引 {expected_indices}，每个索引恰好一次。"
                if stage == "answer_coverage_judge":
                    correction += " part_checks 必须逐一覆盖每个 part_index。answer_ids 只能选提供的 answer_fragments 编号，不得编造编号；没有答案原文支持时 passed=false，answer_ids=[]。"
                if stage == "grounding_judge":
                    correction += " claim_checks 必须逐一覆盖每个 index。evidence_ids 只能选提供的 q 编号；supported=true 必须有真实证据；拒答问题必须返回布尔 no_answer_correct。"
                messages = [*messages, HumanMessage(content=correction)]
    error = StructuredOutputError(stage, events, token_usage)
    error.raw_output = result.get("parsed")
    raise error


# 检查这些 chunk_id 是否：
# 1. 真的存在
# 2. 属于当前公司
# 3. 没有过期
# 4. 如果指定 version，则版本也必须一致
def authorized_chunk_ids(chunk_ids: list[str], user: UserContext, version: str | None, product: str | None = None) -> set[str]:
    if not chunk_ids:
        return set()

    parameters: list[object] = [chunk_ids, user.company_id]

    version_filter = ""

    if version:
        version_filter = "AND d.version = %s"
        parameters.append(version)
    if product:
        version_filter += " AND d.product = %s"
        parameters.append(product)

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT c.chunk_id::text
            FROM support.document_chunks c
            JOIN support.product_documents d
                ON d.document_id = c.document_id
            WHERE c.chunk_id = ANY(%s::uuid[])
                AND c.company_id = %s
                AND d.effective_from <= CURRENT_DATE
                AND (
                    d.effective_to IS NULL
                    OR d.effective_to >= CURRENT_DATE
                )
                {version_filter}
            """,
            parameters,
        ).fetchall()

    allowed_ids = {row["chunk_id"] for row in rows}

    return allowed_ids


# 从 LLM 返回结果中读取 token 使用量
def usage_from_message(message: object) -> dict[str, int | None]:
    usage = getattr(message, "usage_metadata", None) or {}

    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }


# 只验证引用范围和真实原文锚点，不再调用语义检查模型。
@traceable(name="citation_validation", run_type="chain")
def validate_claims(claims: list[AnswerClaim], chunks: list[RetrievedChunk], user: UserContext, version: str | None, question: str = "", history: list[dict[str, object]] | None = None, product: str | None = None) -> tuple[list[AnswerClaim], list[str], dict[str, int | None]]:
    # 当前真正检索出来的 chunk
    retrieved_ids = set()

    for chunk in chunks:
        retrieved_ids.add(chunk.chunk_id)

    # Agent 声称自己引用了哪些 chunk
    requested_ids = []

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
            if chunk_id in retrieved_ids:
                try:
                    UUID(chunk_id)
                    requested_ids.append(chunk_id)
                except ValueError:
                    pass

    # 去数据库检查这些引用是否合法
    allowed_ids = authorized_chunk_ids(list(set(requested_ids)), user, version, product)

    scoped_claims: list[AnswerClaim] = []
    removed: list[str] = []

    # 第一轮：检查引用本身是否合法
    for claim in claims:
        has_no_citation = not claim.cited_chunk_ids

        has_invalid_citation = False

        for chunk_id in claim.cited_chunk_ids:
            if chunk_id not in retrieved_ids or chunk_id not in allowed_ids:
                has_invalid_citation = True

        quote = "".join(claim.evidence_quote.split())
        has_real_quote = bool(quote) and any(chunk.chunk_id in claim.cited_chunk_ids and quote in "".join(chunk.content.split()) for chunk in chunks)
        if has_no_citation or has_invalid_citation:
            removed.append(
                f"{claim.text}（引用不存在、越权、过期或版本不匹配）"
            )
        elif not has_real_quote:
            removed.append(f"{claim.text}（缺少真实连续的原文锚点）")
        else:
            scoped_claims.append(claim)

    return scoped_claims, removed, {}
