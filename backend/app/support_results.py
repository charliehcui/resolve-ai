from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conclusion: str = Field(min_length=1, description="由内部工具事实支持的简短中文结论")
    supporting_facts: list[str] = Field(description="支持结论的中文事实，每条事实必须来自内部工具结果")
    customer_explanation: str = Field(min_length=1, description="可以直接向客户展示的简单中文说明")
    outcome: Literal["resolution", "engineer_escalation"] = Field(description="问题已有结论，或需要工程师继续调查")
