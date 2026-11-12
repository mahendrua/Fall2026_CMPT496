import pytest

import agent.pr_analysis_agent as pr_analysis_agent
from agent.pr_analysis_agent import PullRequestAnalysisAgent, build_diff_context
from agent.structured_output.pr_analysis_output import PullRequestAnalysis


PULL_REQUEST = {
    "number": 12,
    "title": "Add password reset",
    "body": "Lets users reset a forgotten password.",
    "author": "contributor",
    "state": "open",
    "baseBranch": "main",
    "headBranch": "feature/reset",
    "commits": [{"sha": "a" * 40, "message": "Add reset endpoint\n\nDetails"}],
}


class FakeModel:
    def __init__(self):
        self.messages = None

    def with_structured_output(self, schema):
        assert schema is PullRequestAnalysis
        return self

    def invoke(self, messages):
        self.messages = messages
        return PullRequestAnalysis(
            summary="Adds a password reset flow.",
            before="Users could not reset a password.",
            after="Users can request a reset link.",
            added=["A reset endpoint."],
        )


def _file(filename, patch="@@ -1 +1 @@\n-old\n+new", **extra):
    return {"filename": filename, "status": "modified", "additions": 1, "deletions": 1, "patch": patch, **extra}


def test_run_sends_pull_request_and_diff_and_returns_the_analysis():
    model = FakeModel()

    result = PullRequestAnalysisAgent(model=model).run(PULL_REQUEST, [_file("src/reset.py")])

    prompt = model.messages[1][1]
    assert "Pull request #12: Add password reset" in prompt
    assert "Merges feature/reset into main" in prompt
    assert "aaaaaaa Add reset endpoint" in prompt
    assert "Details" not in prompt
    assert "+new" in prompt
    assert result["summary"] == "Adds a password reset flow."
    assert result["added"] == ["A reset endpoint."]
    assert result["pull_request_number"] == 12
    assert result["files_analyzed"] == ["src/reset.py"]
    assert result["files_omitted"] == []


def test_sensitive_files_are_listed_but_their_diff_is_not_sent():
    secret_line = "+API_TOKEN=do-not-send"
    files = [
        _file(".env.production", patch=secret_line),
        _file("certs/server.pem", patch=secret_line),
        _file("src/app.js"),
    ]

    context, included, omitted = build_diff_context(files)

    assert secret_line not in context
    assert ".env.production" in context
    assert included == ["src/app.js"]
    assert [item["reason"] for item in omitted] == ["may hold secrets", "may hold secrets"]


def test_files_without_a_diff_and_over_the_limits_are_reported(monkeypatch):
    monkeypatch.setattr(pr_analysis_agent, "MAX_PATCH_CHARS", 10)
    monkeypatch.setattr(pr_analysis_agent, "MAX_DIFF_CHARS", 30)
    monkeypatch.setattr(pr_analysis_agent, "MAX_FILES", 3)
    files = [
        _file("image.png", patch=None),
        _file("a.py", patch="x" * 50),
        _file("b.py", patch="y" * 50),
        _file("c.py"),
    ]

    context, included, omitted = build_diff_context(files)

    # a.py is cut at the per-file limit, b.py at what is left of the total.
    assert "x" * 10 + "\n(diff truncated)" in context
    assert "x" * 11 not in context
    assert "yyy\n(diff truncated)" in context
    assert "y" * 4 not in context
    assert included == ["a.py", "b.py"]
    assert omitted == [
        {"filename": "image.png", "reason": "no diff from GitHub"},
        {"filename": "c.py", "reason": "over the file limit"},
    ]


def test_size_limit_leaves_out_later_diffs_once_full(monkeypatch):
    monkeypatch.setattr(pr_analysis_agent, "MAX_DIFF_CHARS", 10)
    files = [_file("a.py", patch="x" * 10), _file("b.py", patch="y")]

    _, included, omitted = build_diff_context(files)

    assert included == ["a.py"]
    assert omitted == [{"filename": "b.py", "reason": "over the size limit"}]


@pytest.mark.parametrize(
    ("pull_request", "files", "message"),
    [
        ({}, [_file("a.py")], "pull request to analyze is missing"),
        (PULL_REQUEST, [], "no changed files"),
        (PULL_REQUEST, [_file("logo.png", patch=None)], "did not provide a diff for any file"),
    ],
)
def test_run_rejects_input_with_nothing_to_analyze(pull_request, files, message):
    model = FakeModel()

    with pytest.raises(ValueError, match=message):
        PullRequestAnalysisAgent(model=model).run(pull_request, files)
    assert model.messages is None
