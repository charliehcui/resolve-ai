import re

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import trace, traceable

from backend.app.citations import invoke_structured, validate_claims
from backend.app.config import PROJECT_ROOT
from backend.app.models import AnswerClaim, Citation, CustomerAnswer, CustomerGeneratedClaims, CustomerQueryDecision, RetrievedChunk, UserContext, create_model

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
        section = f"[片段 {chunk.chunk_id}]\n{chunk.content}\n[片段结束]"
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
def direct_customer_query(question: str, history: list[dict[str, object]]) -> CustomerQueryDecision | None:
    # 只有明确、独立的资料问题才绕过模型规划；真实对象、旧版和省略主语仍走原有路由。
    if history or len(question) >= 70 or question.count("？") + question.count("?") > 1:
        return None
    if re.search(r"\b(?:shop|order)-[A-Za-z0-9_-]+|\bO-\d+|旧版|老版|历史版本|后台|当前状态|实时|帮我|请查|执行|转人工", question, re.IGNORECASE):
        return None
    if any(word in question for word in ("分别", "以及", "哪些", "条件", "如果", "能否", "区别", "比较", "和", "与", "但", "却")):
        return None
    if not re.search(r"规则|资料|文档|定义|含义|计算|如何|什么|是否说明|\b(?:docs?|documentation|definition|calculate)\b", question, re.IGNORECASE):
        return None
    versions = set(re.findall(r"(?<![\d.])\d+\.\d+(?:\.\d+)*(?![\d.])", question))
    scope_free_question = re.sub(r"\bcompany-[A-Za-z0-9_-]+", "", question, flags=re.IGNORECASE)
    if len(versions) > 1 or re.search(r"(?:产品|\bproduct)\s*[:：=]|\b[A-Za-z]+-[A-Za-z0-9_-]+", scope_free_question, re.IGNORECASE):
        return None
    return CustomerQueryDecision(decision="search", search_query=question, version=next(iter(versions), None))


@traceable(name="customer_query_next_step", run_type="chain")
def decide_customer_query_next_step(question: str, history: list[dict[str, object]], company_id: str | None = None) -> tuple[CustomerQueryDecision, dict[str, int | None]]:
    direct = direct_customer_query(question, history)
    if direct is not None:
        return direct, {}
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

    model = create_model(reasoning_effort="none", max_output_tokens=1024)
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

def needs_query_understanding(question: str, history: list[dict[str, object]]) -> bool:
    if history or len(question) >= 70 or question.count("？") + question.count("?") > 1:
        return True
    complex_terms = ("分别", "除了", "以及", "哪些", "条件", "如果", "怎么办", "是否能", "能否", "有何", "区别", "比较", "和", "与", "但", "却")
    return any(term in question for term in complex_terms)


#根据搜索到的资料生成回答
@traceable(name="customer_answer_from_documents", run_type="chain")
def generate_answer_from_documents(question: str, chunks: list[RetrievedChunk], history: list[dict[str, object]], user: UserContext, version: str | None, product: str | None = None) -> CustomerAnswer:
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
        HumanMessage(content=f"可见产品资料：\n{context}\n\n最近会话：\n{recent_history}\n\n当前问题：{question}\n\n当前资料范围：company={user.company_id}, product={product or '未指定'}, version={version or '未指定'}"),
    ]

    model = create_model(temperature=0) if needs_query_understanding(question, history) else create_model(temperature=0, reasoning_effort="none", max_output_tokens=3072)
    schema = CustomerGeneratedClaims.model_json_schema()
    schema["$defs"]["AnswerClaim"]["properties"]["cited_chunk_ids"]["items"]["enum"] = [chunk.chunk_id for chunk in chunks]
    schema["$defs"]["AnswerClaim"]["required"] = ["text", "cited_chunk_ids", "evidence_quote"]
    schema["$defs"]["AnswerClaim"]["properties"]["evidence_quote"]["minLength"] = 1
    schema["required"] = ["subquestions", "claims"]
    parsed, draft_usage, _ = invoke_structured(model, schema, CustomerGeneratedClaims, messages, "answer_generation")
    claims = []
    for claim in parsed.claims:
        sentences = re.findall(r".+?(?:[。！？][”’」』]*|$)", claim.text, flags=re.DOTALL)
        for sentence in sentences:
            text = sentence.strip()
            if text:
                if text != claim.text and "按题设" in claim.text and "按题设" not in text:
                    text = "按题设，" + text
                claims.append(claim.model_copy(update={"text": text}))
    supported, removed, _check_usage = validate_claims(claims, chunks, user, version, question, history, product)

    with trace("final_composition", run_type="chain"):
        if len(supported) == 0:
            return CustomerAnswer(
                answer="引用检查未能证明关键结论，当前不能给出确定答案，需要进一步支持。",
                citations=[],
                removed_claims=removed,
                needs_support=True,
                usage=draft_usage,
            )

        citations = build_citations_from_claims(supported, chunks)

        answer_lines: list[str] = []

        for claim in supported:
            answer_lines.append(claim.text)

        answer_text = "\n".join(answer_lines)
        total_usage = draft_usage

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
