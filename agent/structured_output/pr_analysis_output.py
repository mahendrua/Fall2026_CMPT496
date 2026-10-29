"""Structured output for the AI analysis of a GitHub pull request."""

from pydantic import BaseModel, ConfigDict, Field


class PullRequestAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1, description="What the pull request does, in two to four sentences.")
    before: str = Field(..., min_length=1, description="How the affected parts of the project worked before the change.")
    after: str = Field(..., min_length=1, description="How the affected parts of the project work after the change.")
    added: list[str] = Field(default_factory=list, description="New behaviour, features, functions, files or tests the pull request adds.")
    changed: list[str] = Field(default_factory=list, description="Existing behaviour the pull request changes.")
    removed: list[str] = Field(default_factory=list, description="Behaviour, code or files the pull request removes.")
    risks: list[str] = Field(default_factory=list, description="Things a reviewer should check: possible bugs, missing tests, breaking changes.")
