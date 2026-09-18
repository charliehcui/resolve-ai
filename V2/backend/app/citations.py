from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from backend.app.config import PROJECT_ROOT
from backend.app.database import get_connection
from backend.app.models import AnswerClaim, AuthContext, CitationCheckOutput, RetrievedChunk, create_groq_model

PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "citation_check.md"


def authorized_chunk_ids(chunk_ids: list[str], auth: AuthContext, version: str | None) -> set[str]:
    if not chunk_ids:
        return set()
    parameters: list[object] = [chunk_ids, auth.company_id]
    version_filter = ""
    if version:
        version_filter = "AND d.version = %s"
        parameters.append(version)
    with get_connection() as connection:
        rows = connection.execute(
            f"""SELECT c.chunk_id::text FROM support.document_chunks c
            JOIN support.product_documents d ON d.document_id = c.document_id
            WHERE c.chunk_id = ANY(%s::uuid[]) AND c.company_id = %s
              AND d.effective_from <= CURRENT_DATE AND (d.effective_to IS NULL OR d.effective_to >= CURRENT_DATE)
              {version_filter}""",
            parameters,
        ).fetchall()
    return {row["chunk_id"] for row in rows}


def usage_from_message(message: object) -> dict[str, int | None]:
    usage = getattr(message, "usage_metadata", None) or {}
    return {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}


@traceable(name="citation_claim_support", run_type="llm")
def semantic_claim_checks(claims: list[AnswerClaim], chunks: list[RetrievedChunk]) -> tuple[CitationCheckOutput, dict[str, int | None]]:
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    claim_text = "\n".join(f"[{index}] {claim.text} | 引用: {', '.join(claim.cited_chunk_ids)}" for index, claim in enumerate(claims))
    evidence = "\n\n".join(f"[片段 {chunk.chunk_id}]\n{chunk.title}\n{chunk.content}" for chunk in chunks if chunk.chunk_id in {chunk_id for claim in claims for chunk_id in claim.cited_chunk_ids})
    result = create_groq_model().with_structured_output(CitationCheckOutput, include_raw=True).invoke([SystemMessage(content=prompt), HumanMessage(content=f"待检查结论：\n{claim_text}\n\n引用资料：\n{evidence}")])
    if result.get("parsed") is None:
        raise RuntimeError("Citation checker did not return structured output")
    return result["parsed"], usage_from_message(result.get("raw"))


def validate_claims(claims: list[AnswerClaim], chunks: list[RetrievedChunk], auth: AuthContext, version: str | None) -> tuple[list[AnswerClaim], list[str], dict[str, int | None]]:
    retrieved_ids = {chunk.chunk_id for chunk in chunks}
    requested_ids = [chunk_id for claim in claims for chunk_id in claim.cited_chunk_ids]
    allowed_ids = authorized_chunk_ids(requested_ids, auth, version)
    scoped_claims: list[AnswerClaim] = []
    removed = []
    for claim in claims:
        if not claim.cited_chunk_ids or any(chunk_id not in retrieved_ids or chunk_id not in allowed_ids for chunk_id in claim.cited_chunk_ids):
            removed.append(f"{claim.text}（引用不存在、越权、过期或版本不匹配）")
        else:
            scoped_claims.append(claim)
    if not scoped_claims:
        return [], removed, {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    try:
        result, usage = semantic_claim_checks(scoped_claims, chunks)
    except Exception as error:  # noqa: BLE001 - any checker failure must suppress unverified claims
        removed.extend(f"{claim.text}（语义检查失败：{type(error).__name__}）" for claim in scoped_claims)
        return [], removed, {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    checks = {check.claim_index: check for check in result.checks}
    supported = []
    for index, claim in enumerate(scoped_claims):
        check = checks.get(index)
        if check and check.supported:
            supported.append(claim)
        else:
            reason = check.reason if check else "检查结果缺失"
            removed.append(f"{claim.text}（{reason}）")
    return supported, removed, usage
