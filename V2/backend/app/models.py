from typing import Literal

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from backend.app.config import get_settings

ProviderName = Literal["groq", "google"]

TaskName = Literal[
    "customer_answer",
    "simple_intent",
    "query_rewrite",
    "simple_structured",
    "support_investigation",
    "complex_tool_calling",
    "parallel_tool_calling",
    "evidence_conflict",
    "complex_action_proposal",
]


class UserContext(BaseModel):  # 当前是谁在使用系统，以及属于哪家公司
    company_id: str
    user_id: str
    role: str


class RetrievedChunk(BaseModel):  # RAG 检索出来的一小段资料
    chunk_id: str
    title: str
    source_uri: str
    version: str
    content: str
    score: float


class Citation(BaseModel):  # 最终返回给用户的资料引用
    chunk_id: str
    title: str
    source_uri: str


class AnswerClaim(BaseModel):  # AI 回答中的一个可以单独检查的结论
    text: str = Field(description="一个可独立核查的简短中文结论")
    cited_chunk_ids: list[str] = Field(description="回答实际使用的资料片段编号")


class CustomerGeneratedClaims(BaseModel):  # Customer Agent 根据检索资料第一次生成出来的 Claims
    claims: list[AnswerClaim] = Field(description="仅依据提供资料生成的可核查结论")


class CustomerAnswer(BaseModel):  # Customer Agent 完整处理结束后最终返回的结果
    answer: str
    citations: list[Citation]
    claims: list[AnswerClaim] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    needs_support: bool = False
    usage: dict[str, int | None]


class CustomerQueryDecision(BaseModel):  # Customer Agent 正式处理问题以前决定下一步做什么
    decision: Literal["search", "clarify", "handoff"]
    search_query: str = ""
    rewrite_used: bool = False
    product: str | None = None
    version: str | None = None
    customer_message: str = ""


class ClaimValidationResult(BaseModel):  # 一个 Claim 的检查结果
    claim_index: int
    supported: bool
    reason: str


class ClaimValidationOutput(BaseModel):  # 一次 Claim Validation 可以检查多个 Claims
    checks: list[ClaimValidationResult]


# 以下 Doctor Models 用于检查系统不同能力是否正常


class DoctorStatusResult(BaseModel):  # Doctor 检查完成后返回的状态
    status: Literal["ok"]


class DoctorProbeRequest(BaseModel):  # 请求执行一个基础 Doctor 能力测试
    value: str = Field(description="The probe value")


class DoctorShopStatusRequest(BaseModel):  # 请求读取测试店铺的状态，不修改数据
    shop_id: str = Field(description="The test shop ID")


class DoctorPlatformStatusRequest(BaseModel):  # 请求读取测试平台的状态，不修改数据
    platform_id: str = Field(description="The test platform ID")


def provider_for_task(task: TaskName) -> ProviderName:  # 根据任务类型决定使用 Groq 还是 Google
    if task in {"customer_answer", "simple_intent", "query_rewrite", "simple_structured"}:
        return "groq"

    return "google"


def create_groq_model(temperature: float = 0) -> ChatGroq:  # 创建一个可以调用 Groq 模型的对象
    settings = get_settings()

    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=temperature,
        timeout=30,
        max_retries=2,
    )


def create_google_model(temperature: float = 0, model_name: str | None = None, max_retries: int = 2) -> ChatGoogleGenerativeAI:  # 创建一个可以调用 Google 模型的对象
    settings = get_settings()

    return ChatGoogleGenerativeAI(
        model=model_name or settings.google_model,
        google_api_key=settings.google_api_key,
        temperature=temperature,
        timeout=30,
        max_retries=max_retries,
    )
