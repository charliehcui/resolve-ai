import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from backend.app.citations import validate_claims
from backend.app.config import PROJECT_ROOT
from backend.app.models import AnswerClaim, AuthContext, Citation, CustomerAnswer, CustomerModelOutput, CustomerQueryPlan, RetrievedChunk, create_groq_model

PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "customer.md"


def token_usage(raw_message: object) -> dict[str, int | None]:
    usage_metadata = getattr(raw_message, "usage_metadata", None)
    if usage_metadata is None:
        usage_metadata = {}

    return {
        "input_tokens": usage_metadata.get("input_tokens"),
        "output_tokens": usage_metadata.get("output_tokens"),
        "total_tokens": usage_metadata.get("total_tokens"),
    }


def add_usage(*items: dict[str, int | None]) -> dict[str, int | None]:
    combined_usage: dict[str, int | None] = {}

    for token_name in ("input_tokens", "output_tokens", "total_tokens"):
        token_total = 0
        has_value = False

        for item in items:
            value = item.get(token_name)
            if value is not None:
                token_total += value
                has_value = True

        if has_value:
            combined_usage[token_name] = token_total
        else:
            combined_usage[token_name] = None

    return combined_usage


def format_recent_history(history: list[dict[str, object]]) -> str:
    history_lines: list[str] = []

    for message in history[-6:]:
        role = message["role"]
        content = message["content"]
        history_lines.append(f"{role}: {content}")

    if not history_lines:
        return "无"

    return "\n".join(history_lines)


def format_customer_documents(chunks: list[RetrievedChunk]) -> str:
    document_sections: list[str] = []

    for chunk in chunks:
        section = f"[片段 {chunk.chunk_id}]\n标题：{chunk.title}\n来源：{chunk.source_uri}\n版本：{chunk.version}\n内容：{chunk.content}"
        document_sections.append(section)

    return "\n\n".join(document_sections)


def build_customer_citations(claims: list[AnswerClaim], chunks: list[RetrievedChunk]) -> list[Citation]:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    used_chunk_ids: list[str] = []

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
            if chunk_id not in used_chunk_ids:
                used_chunk_ids.append(chunk_id)

    citations: list[Citation] = []
    for chunk_id in used_chunk_ids:
        chunk = chunks_by_id[chunk_id]
        citation = Citation(chunk_id=chunk_id, title=chunk.title, source_uri=chunk.source_uri)
        citations.append(citation)

    return citations


@traceable(name="customer_query_plan", run_type="llm")
def plan_customer_query(question: str, history: list[dict[str, object]]) -> tuple[CustomerQueryPlan, dict[str, int | None]]:
    recent_history = format_recent_history(history)
    instruction = """你是 Customer Agent 的查询规划器。只规划产品资料检索，不读取后台数据。
- 一般产品规则、错误码和自助排查选择 search。
- search_query 可以改写一次，使口语问题更适合检索；rewrite_used 如实标记，不得产生第二次改写。
- 只有版本会改变操作且用户未提供版本时才选择 clarify，不要对一般问题过度追问。
- 查询某个真实订单、店铺、平台、仓库、日志或库存状态时选择 handoff，并生成说明需要 Support 调查的 customer_message。
- product 只在用户明确产品范围时填写；version 只填写明确出现的版本。
- 不输出隐藏推理。"""
    messages = [
        SystemMessage(content=instruction),
        HumanMessage(content=f"最近会话：\n{recent_history}\n\n当前问题：{question}"),
    ]
    model = create_groq_model()
    structured_model = model.with_structured_output(CustomerQueryPlan, include_raw=True)
    result = structured_model.invoke(messages)

    if result.get("parsed") is None:
        raise RuntimeError("Query planner did not return structured output")

    plan = result["parsed"]
    if re.search(r"旧版|老版|历史版本", question) and not re.search(r"\b\d+\.\d+\b", question):
        plan = CustomerQueryPlan(decision="clarify", customer_message="请提供具体产品版本号，例如 1.0 或 2.0，我再选择对应资料。")
    if plan.decision == "handoff":
        plan.customer_message = "这个问题涉及真实订单、店铺或平台状态，已转交 Support Agent。Customer Agent 没有读取后台数据。"
    if plan.decision == "search" and not plan.search_query.strip():
        plan.search_query = question
        plan.rewrite_used = False
    return plan, token_usage(result.get("raw"))


def no_search_answer(plan: CustomerQueryPlan, usage: dict[str, int | None]) -> CustomerAnswer:
    message = plan.customer_message.strip()
    if not message:
        if plan.decision == "clarify":
            message = "请补充会影响产品资料选择的版本信息。"
        else:
            message = "这个问题需要 Support Agent 查询真实业务状态。"
    return CustomerAnswer(answer=message, citations=[], needs_support=plan.decision == "handoff", usage=usage)


@traceable(name="customer_rag_answer", run_type="llm")
def answer_customer_question(question: str, chunks: list[RetrievedChunk], history: list[dict[str, object]], auth: AuthContext, version: str | None) -> CustomerAnswer:
    if not chunks:
        return CustomerAnswer(answer="当前可见产品资料不足以回答这个问题，需要交给 Support 进一步处理。", citations=[], needs_support=True, usage={"input_tokens": None, "output_tokens": None, "total_tokens": None})
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    context = format_customer_documents(chunks)
    recent_history = format_recent_history(history)
    messages = [SystemMessage(content=prompt), HumanMessage(content=f"最近会话：\n{recent_history}\n\n可见产品资料：\n{context}\n\n当前问题：{question}")]

    model = create_groq_model(temperature=0)
    structured_model = model.with_structured_output(CustomerModelOutput, include_raw=True)
    result = structured_model.invoke(messages)
    parsed = result.get("parsed")
    if parsed is None:
        raise RuntimeError("Groq did not return the required structured answer")
    supported, removed, check_usage = validate_claims(parsed.claims, chunks, auth, version)
    if not supported:
        return CustomerAnswer(answer="引用检查未能证明关键结论，当前不能给出确定答案，需要进一步支持。", citations=[], removed_claims=removed, needs_support=True, usage=add_usage(token_usage(result.get("raw")), check_usage))
    citations = build_customer_citations(supported, chunks)

    answer_lines: list[str] = []
    for claim in supported:
        answer_lines.append(claim.text)

    answer_text = "\n".join(answer_lines)
    total_usage = add_usage(token_usage(result.get("raw")), check_usage)
    return CustomerAnswer(answer=answer_text, citations=citations, claims=supported, removed_claims=removed, needs_support=False, usage=total_usage)
