from typing import Literal

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from backend.app.config import get_settings

ProviderName = Literal["groq", "google"]
TaskName = Literal["customer_answer", "simple_intent", "query_rewrite", "simple_structured", "support_investigation", "complex_tool_calling", "parallel_tool_calling", "evidence_conflict", "complex_action_proposal"]


class AuthContext(BaseModel):  #当前是谁在使用系统
    company_id: str
    user_id: str
    role: str


class RetrievedChunk(BaseModel):   #RAG 搜索出来的一小段资料
    chunk_id: str
    title: str
    source_uri: str
    version: str
    content: str
    score: float


class Citation(BaseModel):
    chunk_id: str
    title: str
    source_uri: str


class AnswerClaim(BaseModel):   #AI 回答中的一个结论
    text: str = Field(description="一个可独立核查的简短中文结论")
    cited_chunk_ids: list[str] = Field(description="回答实际使用的资料片段编号")


class CustomerModelOutput(BaseModel):  #这是 Customer AI 第一次生成出来的原始结果，AI 刚生成的东西
    claims: list[AnswerClaim] = Field(description="仅依据提供资料生成的可核查结论")


class CustomerAnswer(BaseModel):  #Customer Agent 完整处理结束后，最终交出去的结果
    answer: str
    citations: list[Citation]
    claims: list[AnswerClaim] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    needs_support: bool = False
    usage: dict[str, int | None]


class CustomerQueryPlan(BaseModel):  #Customer Agent 在正式做事以前，先决定下一步
    decision: Literal["search", "clarify", "handoff"]
    search_query: str = ""
    rewrite_used: bool = False
    product: str | None = None
    version: str | None = None
    customer_message: str = ""


class ClaimCheck(BaseModel):   #检查某个 Claim
    claim_index: int
    supported: bool
    reason: str


class CitationCheckOutput(BaseModel):   #一次可能检查很多 Claim
    checks: list[ClaimCheck]


#用于检查系统不同能力是否正常
class DoctorStructuredResult(BaseModel):     
    status: Literal["ok"]


class DoctorLookup(BaseModel):
    """Request the Phase 1 doctor capability probe."""

    value: str = Field(description="The probe value")


class DoctorShopLookup(BaseModel):
    """Read a test shop status without changing data."""

    shop_id: str = Field(description="The test shop ID")


class DoctorPlatformLookup(BaseModel):
    """Read a test platform status without changing data."""

    platform_id: str = Field(description="The test platform ID")


def provider_for_task(task: TaskName) -> ProviderName:
    if task in {"customer_answer", "simple_intent", "query_rewrite", "simple_structured"}:
        return "groq"
    return "google"


def create_groq_model(temperature: float = 0) -> ChatGroq:  #创建一个可以调用 Groq 模型的对象
    settings = get_settings()
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=temperature,
        timeout=30,
        max_retries=2,
    )


def create_google_model(temperature: float = 0, model_name: str | None = None, max_retries: int = 2) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    return ChatGoogleGenerativeAI(
        model=model_name or settings.google_model,
        google_api_key=settings.google_api_key,
        temperature=temperature,
        timeout=30,
        max_retries=max_retries,
    )