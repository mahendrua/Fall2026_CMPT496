"""Structured output for AI-generated Playwright GUI test cases."""

from pydantic import BaseModel, ConfigDict, Field


class GeneratedGuiTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=160)
    purpose: str = Field(..., min_length=1, max_length=500)
    expected_result: str | None = Field(
        default=None,
        max_length=500,
        description="Observable expected result, or null when the intended behavior is uncertain.",
    )
    assumptions: list[str] = Field(default_factory=list, max_length=8)
    test_code: str = Field(..., min_length=1, description="A complete JavaScript Playwright test file.")


class GeneratedGuiTestSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_cases: list[GeneratedGuiTestCase] = Field(..., min_length=1, max_length=8)


class GuiTestRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repairable: bool
    reason: str = Field(..., min_length=1, max_length=500)
    test_code: str | None = Field(
        default=None,
        description="Complete corrected JavaScript test file only when repairable is true.",
    )
