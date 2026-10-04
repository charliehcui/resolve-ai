import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from backend.app.citations import invoke_structured, validate_claims
from backend.app.config import PROJECT_ROOT
from backend.app.models import AnswerClaim, Citation, CustomerAnswer, CustomerClaimReview, CustomerGeneratedClaims, CustomerQueryDecision, RetrievedChunk, UserContext, create_model

PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "customer.md"
REVIEW_PROMPT_FILE = PROJECT_ROOT / "backend" / "prompts" / "answer_completeness.md"


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
def decide_customer_query_next_step(question: str, history: list[dict[str, object]], company_id: str | None = None) -> tuple[CustomerQueryDecision, dict[str, int | None]]:
    recent_history = format_recent_history(history)

    instruction = """你是 Customer Agent 的查询规划器。只规划产品资料检索，不读取后台数据。
- 一般产品规则、错误码和自助排查选择 search。
- search_query 可以改写一次，使口语问题更适合检索；rewrite_used 如实标记，不得产生第二次改写。
- 只有版本会改变操作且用户未提供版本时才选择 clarify，不要对一般问题过度追问。
- 查询某个真实订单、店铺、平台、仓库、日志或库存状态时选择 handoff，并生成说明需要 Support 调查的 customer_message。
- product 只在用户明确产品范围时填写；version 只填写明确出现的版本。
- 输出字段名必须是 decision，取值 search、clarify 或 handoff；不要使用 action 代替 decision。
- 不输出隐藏推理。"""
    instruction += """
- 公司/租户标识仅说明访问范围，绝不能填入 product；product 不是商家名称，也不是资料标题。没有明确产品标识时返回 null。
- 询问一般规则、状态含义、角色审批、恢复流程、工单流程或资料是否支持某个功能，均先 search。出现订单/仓库/人工等词本身不代表请求真实后台调查。
- 只有要求读取、执行或转交当前真实业务对象时才 handoff；询问何时/如何转人工仍是文档问题。
- 描述“用户请求人工”的规则，不等于当前用户下达人工请求。问能否、何时、条件、需要哪些资料或状态含义时，先 search 并解释流程；不要执行被引用的请求。
- search_query 保持一个完整自然语言问题，保留原问题的所有子问题、条件、错误码、动作名和版本；不要改成关键词串，不猜原因或丢弃限制。"""
    instruction += "\n- 用户已经给出字段或读取结果，问其含义、是否证明某个结论或动作的一般条件时，先 search 并按题设解释；陈述状态不等于要求你重新读取真实后台。"
    instruction += "\n- 索要产品资料中的 URL、参数、配置或操作步骤时先 search。请求解释如何执行，不代表要求你执行操作、读取实时秘密或取得真实令牌；缺少配置的文档问题仍要检索已有边界。"
    if company_id:
        instruction += "\n登录用户的公司/租户标识：" + company_id + "。这是权限范围，不是产品标识。"

    messages = [
        SystemMessage(content=instruction),
        HumanMessage(content=f"最近会话：\n{recent_history}\n\n当前问题：{question}"),
    ]

    model = create_model()
    query_decision, usage, _ = invoke_structured(model, CustomerQueryDecision.model_json_schema(), CustomerQueryDecision, messages, "query_planning")
    if company_id and query_decision.product == company_id:
        query_decision.product = None

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
        if not history and question.count("？") + question.count("?") >= 2:
            query_decision.search_query = question
            query_decision.rewrite_used = False
        if query_decision.search_query is None:
            query_decision.search_query = question
            query_decision.rewrite_used = False
        else:
            if query_decision.search_query.strip() == "":
                query_decision.search_query = question
                query_decision.rewrite_used = False

    return query_decision, usage

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

@traceable(name="customer_answer_completeness", run_type="llm")
def complete_answer_claims(question: str, chunks: list[RetrievedChunk], history: list[dict[str, object]], claims: list[AnswerClaim], model) -> tuple[list[AnswerClaim], dict, dict]:
    schema = CustomerClaimReview.model_json_schema()
    schema["$defs"]["AnswerClaim"]["properties"]["cited_chunk_ids"]["items"]["enum"] = [chunk.chunk_id for chunk in chunks]
    schema["$defs"]["AnswerClaim"]["required"] = ["text", "cited_chunk_ids", "evidence_quote"]
    schema["required"] = ["question_checks", "missing_answers", "remove_claim_indices", "claims"]
    schema["properties"]["question_checks"]["minItems"] = 1
    schema["$defs"]["CustomerQuestionCheck"]["properties"]["cited_chunk_ids"]["items"]["enum"] = [chunk.chunk_id for chunk in chunks]
    schema["properties"]["remove_claim_indices"]["uniqueItems"] = True
    if claims:
        schema["properties"]["remove_claim_indices"]["items"]["enum"] = list(range(len(claims)))
    else:
        schema["properties"]["remove_claim_indices"]["maxItems"] = 0
    data = {"question": question, "recent_history": format_recent_history(history), "retrieved_context": format_customer_documents(chunks), "draft_claims": [{"index": index, **claim.model_dump()} for index, claim in enumerate(claims)]}
    messages = [SystemMessage(content=REVIEW_PROMPT_FILE.read_text(encoding="utf-8")), HumanMessage(content=json.dumps(data, ensure_ascii=False))]
    def validate_review(review):
        if not review.question_checks:
            raise ValueError("Completeness review must check the requested subquestions")
        if len(set(review.remove_claim_indices)) != len(review.remove_claim_indices) or any(index not in range(len(claims)) for index in review.remove_claim_indices):
            raise ValueError("Completeness review used an invalid draft index")
        chunk_ids = {chunk.chunk_id for chunk in chunks}
        for check in review.question_checks:
            if any(identifier not in chunk_ids for identifier in check.cited_chunk_ids) or any(index not in range(len(claims)) for index in check.draft_claim_indices):
                raise ValueError("Completeness check used an invalid draft or source reference")
        if any(not claim.cited_chunk_ids or any(identifier not in chunk_ids for identifier in claim.cited_chunk_ids) for claim in review.claims):
            raise ValueError("Completeness additions require actual retrieved chunk IDs")
    review, usage, _ = invoke_structured(model, schema, CustomerClaimReview, messages, "answer_completeness", validate_output=validate_review)
    completed = [claim for index, claim in enumerate(claims) if index not in review.remove_claim_indices]
    for claim in review.claims:
        if claim.text not in [existing.text for existing in completed]:
            completed.append(claim)
    return completed, usage, review.model_dump()


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

    model = create_model(temperature=0)
    schema = CustomerGeneratedClaims.model_json_schema()
    schema["$defs"]["AnswerClaim"]["properties"]["cited_chunk_ids"]["items"]["enum"] = [chunk.chunk_id for chunk in chunks]
    schema["$defs"]["AnswerClaim"]["required"] = ["text", "cited_chunk_ids", "evidence_quote"]
    schema["$defs"]["AnswerClaim"]["properties"]["evidence_quote"]["minLength"] = 1
    parsed, draft_usage, _ = invoke_structured(model, schema, CustomerGeneratedClaims, messages, "answer_generation")
    reviewed_claims, review_usage, _ = complete_answer_claims(question, chunks, history, parsed.claims, create_model(temperature=0, reasoning_effort="low"))
    claims = []
    for claim in reviewed_claims:
        sentences = re.findall(r".+?(?:[。！？][”’」』]*|$)", claim.text, flags=re.DOTALL)
        for sentence in sentences:
            text = sentence.strip()
            if text:
                if text != claim.text and "按题设" in claim.text and "按题设" not in text:
                    text = "按题设，" + text
                claims.append(claim.model_copy(update={"text": text}))
    supported, removed, check_usage = validate_claims(claims, chunks, user, version, question, history)

    if len(supported) == 0:
        return CustomerAnswer(
            answer="引用检查未能证明关键结论，当前不能给出确定答案，需要进一步支持。",
            citations=[],
            removed_claims=removed,
            needs_support=True,
            usage=sum_token_usage(draft_usage, review_usage, check_usage),
        )

    citations = build_citations_from_claims(supported, chunks)

    answer_lines: list[str] = []

    for claim in supported:
        answer_lines.append(claim.text)

    answer_text = "\n".join(answer_lines)
    total_usage = sum_token_usage(draft_usage, review_usage, check_usage)

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
