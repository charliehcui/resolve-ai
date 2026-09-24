from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from backend.app.config import PROJECT_ROOT
from backend.app.database import get_connection
from backend.app.models import AnswerClaim, ClaimValidationOutput, RetrievedChunk, UserContext, create_groq_model


PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "citation_check.md"


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
def semantic_claim_checks(claims: list[AnswerClaim], chunks: list[RetrievedChunk]) -> tuple[ClaimValidationOutput, dict[str, int | None]]:
    prompt = PROMPT_FILE.read_text(encoding="utf-8")

    claim_lines = []

    for index, claim in enumerate(claims):
        cited_ids = ", ".join(claim.cited_chunk_ids)
        claim_lines.append(f"[{index}] {claim.text} | 引用: {cited_ids}")

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

    model = create_groq_model()

    structured_model = model.with_structured_output(
        ClaimValidationOutput,
        include_raw=True,
    )

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=(
                f"待检查结论：\n"
                f"{claim_text}\n\n"
                f"引用资料：\n"
                f"{evidence}"
            )
        ),
    ]

    result = structured_model.invoke(messages)

    if result.get("parsed") is None:
        raise RuntimeError("Citation checker did not return structured output")

    parsed_result = result["parsed"]
    token_usage = usage_from_message(result.get("raw"))

    return parsed_result, token_usage


# 完整检查每个结论
def validate_claims(claims: list[AnswerClaim], chunks: list[RetrievedChunk], user: UserContext, version: str | None) -> tuple[list[AnswerClaim], list[str], dict[str, int | None]]:
    # 当前真正检索出来的 chunk
    retrieved_ids = set()

    for chunk in chunks:
        retrieved_ids.add(chunk.chunk_id)

    # Agent 声称自己引用了哪些 chunk
    requested_ids = []

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
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
    try:
        result, usage = semantic_claim_checks(
            scoped_claims,
            chunks,
        )

    except Exception as error:
        for claim in scoped_claims:
            removed.append(
                f"{claim.text}（语义检查失败：{type(error).__name__}）"
            )

        return [], removed, {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }

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
