"""Structured output for AI-generated Playwright GUI test cases."""

from pydantic import BaseModel, ConfigDict, Field


class GeneratedGuiTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=160)
    purpose: str = Field(..., min_length=1, max_length=500)
    test_code: str = Field(..., min_length=1, description="A complete JavaScript Playwright test file.")


class GeneratedGuiTestSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_cases: list[GeneratedGuiTestCase] = Field(..., min_length=1, max_length=8)
