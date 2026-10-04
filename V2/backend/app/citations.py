import json
import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable
from openai import LengthFinishReasonError
from pydantic import ValidationError

from backend.app.config import PROJECT_ROOT
from backend.app.database import get_connection
from backend.app.models import AnswerClaim, ClaimValidationOutput, RetrievedChunk, UserContext, create_model

PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "citation_check.md"
logger = logging.getLogger(__name__)


class StructuredOutputError(RuntimeError):
    def __init__(self, stage: str, events: list[dict], usage: dict):
        super().__init__(f"{stage} did not return valid structured output after one recovery")
        self.events = events
        self.usage = usage


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
def authorized_chunk_ids(chunk_ids: list[str], user: UserContext, version: str | None) -> set[str]:
    if not chunk_ids:
        return set()

    parameters: list[object] = [chunk_ids, user.company_id]

    version_filter = ""

    if version:
        version_filter = "AND d.version = %s"
        parameters.append(version)

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


# 让 LLM 判断：
# Customer Agent 的结论，是否真的被引用资料支持
@traceable(name="citation_claim_support", run_type="llm")
def semantic_claim_checks(claims: list[AnswerClaim], chunks: list[RetrievedChunk], question: str = "", history: list[dict[str, object]] | None = None) -> tuple[ClaimValidationOutput, dict[str, int | None]]:
    prompt = PROMPT_FILE.read_text(encoding="utf-8")

    claim_lines = []

    for index, claim in enumerate(claims):
        cited_ids = ", ".join(claim.cited_chunk_ids)
        claim_lines.append(f"[{index}] {claim.text} | 引用: {cited_ids} | 原文锚点: {claim.evidence_quote}")

    claim_text = "\n".join(claim_lines)

    needed_chunk_ids = set()

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
            needed_chunk_ids.add(chunk_id)

    evidence_parts = []

    for chunk in chunks:
        if chunk.chunk_id in needed_chunk_ids:
            evidence_parts.append(
                f"[片段 {chunk.chunk_id}]\n"
                f"{chunk.title}\n"
                f"{chunk.content}"
            )

    evidence = "\n\n".join(evidence_parts)

    model = create_model()

    schema = ClaimValidationOutput.model_json_schema()
    schema["$defs"]["ClaimValidationResult"]["properties"]["claim_index"]["enum"] = list(range(len(claims)))
    schema["properties"]["checks"].update(minItems=len(claims), maxItems=len(claims))
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=(
                f"用户原问题（未核实的题设）：\n{question}\n\n"
                f"用户会话（未核实的题设）：\n{json.dumps((history or [])[-6:], ensure_ascii=False)}\n\n"
                f"待检查结论：\n"
                f"{claim_text}\n\n"
                f"引用资料：\n"
                f"{evidence}"
            )
        ),
    ]

    parsed_result, token_usage, _ = invoke_structured(model, schema, ClaimValidationOutput, messages, "citation_validation", list(range(len(claims))))
    return parsed_result, token_usage


# 完整检查每个结论
def validate_claims(claims: list[AnswerClaim], chunks: list[RetrievedChunk], user: UserContext, version: str | None, question: str = "", history: list[dict[str, object]] | None = None) -> tuple[list[AnswerClaim], list[str], dict[str, int | None]]:
    # 当前真正检索出来的 chunk
    retrieved_ids = set()

    for chunk in chunks:
        retrieved_ids.add(chunk.chunk_id)

    # Agent 声称自己引用了哪些 chunk
    requested_ids = []

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
            if chunk_id in retrieved_ids:
                requested_ids.append(chunk_id)

    # 去数据库检查这些引用是否合法
    allowed_ids = authorized_chunk_ids(
        requested_ids,
        user,
        version,
    )

    scoped_claims: list[AnswerClaim] = []
    removed: list[str] = []

    # 第一轮：检查引用本身是否合法
    for claim in claims:
        has_no_citation = not claim.cited_chunk_ids

        has_invalid_citation = False

        for chunk_id in claim.cited_chunk_ids:
            if chunk_id not in retrieved_ids or chunk_id not in allowed_ids:
                has_invalid_citation = True

        if claim.evidence_quote:
            quote = "".join(claim.evidence_quote.split())
            if not any(chunk.chunk_id in claim.cited_chunk_ids and quote in "".join(chunk.content.split()) for chunk in chunks):
                # 表格可能被生成器拼成非连续原文；辅助锚点不能否决真实片段支持的事实。
                logger.info("citation_anchor_not_verbatim")
                claim = claim.model_copy(update={"evidence_quote": ""})
        if has_no_citation or has_invalid_citation:
            removed.append(
                f"{claim.text}（引用不存在、越权、过期或版本不匹配）"
            )
        else:
            scoped_claims.append(claim)

    # 如果所有结论都已经被删掉，就不用再调用 LLM
    if not scoped_claims:
        return [], removed, {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }

    # 第二轮：让 LLM 检查“资料内容是否真的支持结论”
    # Provider/格式失败是执行错误，不能伪装成所有事实无依据。
    result, usage = semantic_claim_checks(scoped_claims, chunks, question, history)

    # 把检查结果按照 claim_index 整理
    checks = {}

    for check in result.checks:
        checks[check.claim_index] = check

    supported: list[AnswerClaim] = []

    # 第三轮：留下真正被资料支持的结论
    for index, claim in enumerate(scoped_claims):
        check = checks.get(index)

        if check and check.supported:
            supported.append(claim)

        else:
            if check:
                reason = check.reason
            else:
                reason = "检查结果缺失"

            removed.append(
                f"{claim.text}（{reason}）"
            )

    return supported, removed, usage
