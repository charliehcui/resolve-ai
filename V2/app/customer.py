import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from app.citations import validate_claims
from app.config import PROJECT_ROOT
from app.models import AuthContext, Citation, CustomerAnswer, CustomerModelOutput, CustomerQueryPlan, RetrievedChunk, create_groq_model

PROMPT_FILE = PROJECT_ROOT / "prompts" / "customer.md"


def token_usage(raw_message: object) -> dict[str, int | None]:
    usage = getattr(raw_message, "usage_metadata", None) or {}
    return {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}


def add_usage(*items: dict[str, int | None]) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        values = [item.get(name) for item in items]
        result[name] = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
    return result


@traceable(name="customer_query_plan", run_type="llm")
def plan_customer_query(question: str, history: list[dict[str, object]]) -> tuple[CustomerQueryPlan, dict[str, int | None]]:
    recent_history = "\n".join(f"{message['role']}: {message['content']}" for message in history[-6:]) or "无"
    instruction = """你是 Customer Agent 的查询规划器。只规划产品资料检索，不读取后台数据。
- 一般产品规则、错误码和自助排查选择 search。
- search_query 可以改写一次，使口语问题更适合检索；rewrite_used 如实标记，不得产生第二次改写。
- 只有版本会改变操作且用户未提供版本时才选择 clarify，不要对一般问题过度追问。
- 查询某个真实订单、店铺、平台、仓库、日志或库存状态时选择 handoff，并生成说明需要 Support 调查的 customer_message。
- product 只在用户明确产品范围时填写；version 只填写明确出现的版本。
- 不输出隐藏推理。"""
    result = create_groq_model().with_structured_output(CustomerQueryPlan, include_raw=True).invoke([SystemMessage(content=instruction), HumanMessage(content=f"最近会话：\n{recent_history}\n\n当前问题：{question}")])
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
        message = "请补充会影响产品资料选择的版本信息。" if plan.decision == "clarify" else "这个问题需要 Support Agent 查询真实业务状态。"
    return CustomerAnswer(answer=message, citations=[], needs_support=plan.decision == "handoff", usage=usage)


@traceable(name="customer_rag_answer", run_type="llm")
def answer_customer_question(question: str, chunks: list[RetrievedChunk], history: list[dict[str, object]], auth: AuthContext, version: str | None) -> CustomerAnswer:
    if not chunks:
        return CustomerAnswer(answer="当前可见产品资料不足以回答这个问题，需要交给 Support 进一步处理。", citations=[], needs_support=True, usage={"input_tokens": None, "output_tokens": None, "total_tokens": None})
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    context = "\n\n".join(f"[片段 {chunk.chunk_id}]\n标题：{chunk.title}\n来源：{chunk.source_uri}\n版本：{chunk.version}\n内容：{chunk.content}" for chunk in chunks)
    recent_history = "\n".join(f"{message['role']}: {message['content']}" for message in history[-6:]) or "无"
    messages = [SystemMessage(content=prompt), HumanMessage(content=f"最近会话：\n{recent_history}\n\n可见产品资料：\n{context}\n\n当前问题：{question}")]
    result = create_groq_model(temperature=0).with_structured_output(CustomerModelOutput, include_raw=True).invoke(messages)
    parsed = result.get("parsed")
    if parsed is None:
        raise RuntimeError("Groq did not return the required structured answer")
    supported, removed, check_usage = validate_claims(parsed.claims, chunks, auth, version)
    if not supported:
        return CustomerAnswer(answer="引用检查未能证明关键结论，当前不能给出确定答案，需要进一步支持。", citations=[], removed_claims=removed, needs_support=True, usage=add_usage(token_usage(result.get("raw")), check_usage))
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    used_ids = list(dict.fromkeys(chunk_id for claim in supported for chunk_id in claim.cited_chunk_ids))
    citations = [Citation(chunk_id=chunk_id, title=chunk_by_id[chunk_id].title, source_uri=chunk_by_id[chunk_id].source_uri) for chunk_id in used_ids]
    return CustomerAnswer(answer="\n".join(claim.text for claim in supported), citations=citations, claims=supported, removed_claims=removed, needs_support=False, usage=add_usage(token_usage(result.get("raw")), check_usage))
