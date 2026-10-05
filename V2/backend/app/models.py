from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.llm import create_model  # noqa: F401 - shared model factory import surface


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
    evidence_quote: str = Field(default="", description="从一个实际引用片段复制连续原文；保留标点和换行，JSON 中换行用转义字符，不用分号改写、拼接字段、加入片段编号或解释。原文仅供内部核查。")
    cited_chunk_ids: list[str] = Field(description="evidence_quote 所在片段的完整 UUID；不能选同文件或同主题的相邻片段。")
    text: str = Field(description="由本条原文支持的一个简短中文事实，回答对应子问题；独立状态、条件、限制和下一步各自分条。题设用按题设/如果限定，不能声称已读取后台。")


class CustomerGeneratedClaims(BaseModel):  # Customer Agent 根据检索资料第一次生成出来的 Claims
    subquestions: list[str] = Field(default_factory=list, description="用户真正询问的各个子问题，逐项列出后在 claims 中覆盖其结论、必要条件和下一步；不增加未询问的背景。")
    claims: list[AnswerClaim] = Field(description="仅依据提供资料生成的可核查结论，覆盖问题的各个部分。若资料不足，先说明具体缺失，再说明资料已确认的流程、可向谁确认和下一步；不要只重复限制或保证。")


class CustomerQuestionCheck(BaseModel):
    question: str = Field(description="当前用户的一个明确子问题，包括所问的业务对象或操作。")
    cited_chunk_ids: list[str] = Field(description="从全部检索片段中找到的直接相关定义/规则；未找到时为空。")
    evidence_quote: str = Field(description="上述片段中相关的短原文；资料未提供时为空。")
    draft_claim_indices: list[int] = Field(description="草稿中实际回答该问题的索引；不能用相近对象或阶段的答案代替。")
    complete: bool = Field(description="草稿是否已给出该子问题的具体结论、必要条件和正确引用；只有主题相同不算完整。")


class CustomerClaimReview(BaseModel):
    question_checks: list[CustomerQuestionCheck] = Field(default_factory=list, description="逐项核对用户的每个明确子问题，即使草稿完整也必须填写；依据全部相关资料，不能只检查草稿已引用的片段。")
    missing_answers: list[str] = Field(description="草稿尚未回答的明确子问题，或影响该回答的关键条件；只写简短问题，不输出推理。完整时为空。")
    remove_claim_indices: list[int] = Field(description="需要替换的错误或业务对象/阶段不符的草稿索引；正确事实保留。")
    claims: list[AnswerClaim] = Field(description="只补充遗漏或替换错误的原子事实，不能重写全部草稿或增加未问的背景。没有遗漏时为空。")


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
    product: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$", description="明确指定的单个产品标识，例如 merchant-console；公司/租户标识、错误码和版本号都不是产品。未明确指定产品时为 null")
    version: str | None = Field(default=None, pattern=r"^[0-9]+(?:\.[0-9]+)+$", description="当前请求适用的单个产品版本号，例如 2.0；不写解释、不拼多个版本，不把库存 version/source_version 或历史引用当作当前产品版本。未明确指定产品版本时为 null")
    customer_message: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_handoff_field(cls, value: object) -> object:
        # 只兼容模型已明确给出的 handoff，不补猜测出来的业务判断。
        if isinstance(value, dict) and value.get("action") == "handoff":
            if "decision" in value and value["decision"] != "handoff":
                raise ValueError("Conflicting Customer handoff fields")
            value = dict(value)
            value["decision"] = "handoff"
        return value


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
