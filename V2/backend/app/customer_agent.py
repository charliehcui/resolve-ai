import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from backend.app.citations import validate_claims
from backend.app.config import PROJECT_ROOT
from backend.app.models import AnswerClaim, Citation, CustomerAnswer, CustomerGeneratedClaims, CustomerQueryDecision, RetrievedChunk, UserContext, create_groq_model

PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "customer.md"


def get_token_usage(raw_message: object) -> dict[str, int | None]:
    usage_metadata = getattr(raw_message, "usage_metadata", None)

    if usage_metadata is None:
        usage_metadata = {}

    return {
        "input_tokens": usage_metadata.get("input_tokens"),
        "output_tokens": usage_metadata.get("output_tokens"),
        "total_tokens": usage_metadata.get("total_tokens"),
    }


def sum_token_usage(*items: dict[str, int | None]) -> dict[str, int | None]:
    combined_usage: dict[str, int | None] = {}

    for token_name in ("input_tokens", "output_tokens", "total_tokens"):
        token_total = 0
        has_value = False

        for item in items:
            value = item.get(token_name)

            if value is not None:
                token_total += value
                has_value = True

        if has_value is True:
            combined_usage[token_name] = token_total
        else:
            combined_usage[token_name] = None

    return combined_usage

#整理最近聊天记录
def format_recent_history(history: list[dict[str, object]]) -> str:
    history_lines: list[str] = []

    for message in history[-6:]:
        role = message["role"]
        content = message["content"]
        history_lines.append(f"{role}: {content}")

    if len(history_lines) == 0:
        return "无"

    return "\n".join(history_lines)

#把搜索到的资料整理成给 AI 阅读的格式
def format_customer_documents(chunks: list[RetrievedChunk]) -> str:
    document_sections: list[str] = []

    for chunk in chunks:
        section = f"[片段 {chunk.chunk_id}]\n标题：{chunk.title}\n来源：{chunk.source_uri}\n版本：{chunk.version}\n内容：{chunk.content}"
        document_sections.append(section)

    return "\n\n".join(document_sections)


def build_citations_from_claims(claims: list[AnswerClaim], chunks: list[RetrievedChunk]) -> list[Citation]:
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    used_chunk_ids: list[str] = []

    for claim in claims:
        for chunk_id in claim.cited_chunk_ids:
            if chunk_id in used_chunk_ids:
                continue

            used_chunk_ids.append(chunk_id)

    citations: list[Citation] = []

    for chunk_id in used_chunk_ids:
        chunk = chunks_by_id.get(chunk_id)

        if chunk is None:
            continue

        citation = Citation(chunk_id=chunk_id, title=chunk.title, source_uri=chunk.source_uri)
        citations.append(citation)

    return citations

#判断下一步是 search、clarify 还是 handoff
@traceable(name="customer_query_next_step", run_type="llm")
def decide_customer_query_next_step(question: str, history: list[dict[str, object]]) -> tuple[CustomerQueryDecision, dict[str, int | None]]:
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
    structured_model = model.with_structured_output(CustomerQueryDecision, include_raw=True)  # 用于 debug，会返回 raw、parsed 和 parsing_error
    result = structured_model.invoke(messages)

    if result.get("parsed") is None:  # parsed 是 Groq 解析出来的结构化结果，如果没有，说明 Groq 没有按要求输出
        raise RuntimeError("Query planner did not return structured output")

    query_decision = result["parsed"]

    old_version_match = re.search(r"旧版|老版|历史版本", question)
    version_number_match = re.search(r"\b\d+\.\d+\b", question)

    if old_version_match is None:
        pass
    else:
        if version_number_match is None:
            query_decision = CustomerQueryDecision(
                decision="clarify",
                customer_message="请提供具体产品版本号，例如 1.0 或 2.0，我再选择对应资料。",
            )

    if query_decision.decision == "handoff":
        query_decision.customer_message = "这个问题涉及真实订单、店铺或平台状态，已转交 Support Agent。Customer Agent 没有读取后台数据。"

    if query_decision.decision == "search":
        if query_decision.search_query is None:
            query_decision.search_query = question
            query_decision.rewrite_used = False
        else:
            if query_decision.search_query.strip() == "":
                query_decision.search_query = question
                query_decision.rewrite_used = False

    return query_decision, get_token_usage(result.get("raw"))

#处理不需要搜索的情况，比如追问用户或提示已转交 Support
def build_non_search_answer(query_decision: CustomerQueryDecision, usage: dict[str, int | None]) -> CustomerAnswer:
    message = query_decision.customer_message

    if message is None:
        message = ""
    else:
        message = message.strip()

    if message == "":
        if query_decision.decision == "clarify":
            message = "请补充会影响产品资料选择的版本信息。"
        else:
            message = "这个问题需要 Support Agent 查询真实业务状态。"

    return CustomerAnswer(
        answer=message,
        citations=[],
        needs_support=query_decision.decision == "handoff",
        usage=usage,
    )

#根据搜索到的资料生成回答
@traceable(name="customer_answer_from_documents", run_type="llm")
def generate_answer_from_documents(question: str, chunks: list[RetrievedChunk], history: list[dict[str, object]], user: UserContext, version: str | None) -> CustomerAnswer:
    if len(chunks) == 0:
        return CustomerAnswer(
            answer="当前可见产品资料不足以回答这个问题，需要交给 Support 进一步处理。",
            citations=[],
            needs_support=True,
            usage={
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            },
        )

    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    context = format_customer_documents(chunks)
    recent_history = format_recent_history(history)

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(content=f"最近会话：\n{recent_history}\n\n可见产品资料：\n{context}\n\n当前问题：{question}"),
    ]

    model = create_groq_model(temperature=0)
    structured_model = model.with_structured_output(CustomerGeneratedClaims, include_raw=True)
    result = structured_model.invoke(messages)

    parsed = result.get("parsed")

    if parsed is None:
        raise RuntimeError("Groq did not return the required structured answer")

    supported, removed, check_usage = validate_claims(parsed.claims, chunks, user, version)

    if len(supported) == 0:
        return CustomerAnswer(
            answer="引用检查未能证明关键结论，当前不能给出确定答案，需要进一步支持。",
            citations=[],
            removed_claims=removed,
            needs_support=True,
            usage=sum_token_usage(get_token_usage(result.get("raw")), check_usage),
        )

    citations = build_citations_from_claims(supported, chunks)

    answer_lines: list[str] = []

    for claim in supported:
        answer_lines.append(claim.text)

    answer_text = "\n".join(answer_lines)
    total_usage = sum_token_usage(get_token_usage(result.get("raw")), check_usage)

    return CustomerAnswer(
        answer=answer_text,
        citations=citations,
        claims=supported,
        removed_claims=removed,
        needs_support=False,
        usage=total_usage,
    )



# 用户问题 question + 最近对话 history
#         ↓
# decide_customer_query_next_step()
#         ↓
# CustomerQueryDecision
#         ↓
# ┌─────────────┬─────────────┬─────────────┐
# │ search      │ clarify     │ handoff     │
# │             │             │             │
# │ ↓           │ ↓           │ ↓           │
# │ 检索资料    │ build_non_  │ build_non_  │
# │ ↓           │ search_     │ search_     │
# │ chunks      │ answer()    │ answer()    │
# │ ↓           │ ↓           │ ↓           │
# │ generate_   │ Customer    │ Customer    │
# │ answer_     │ Answer      │ Answer      │
# │ from_       │             │             │
# │ documents() │             │             │
# │ ↓           │             │             │
# │ CustomerGeneratedClaims   │             │
# │ ↓                         │             │
# │ validate_claims()         │             │
# │ ↓                         │             │
# │ ClaimValidationOutput     │             │
# │ ↓                         │             │
# │ build_citations_from_     │             │
# │ claims()                  │             │
# │ ↓                         │             │
# │ CustomerAnswer            │             │
# └───────────────────────────┴─────────────┘
