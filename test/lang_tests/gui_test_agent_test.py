from pathlib import Path

import pytest

from agent.gui_test_agent import (
    GuiTestAgent,
    _validate_test_case,
    collect_source_context,
)
from agent.structured_output.gui_test_output import (
    GeneratedGuiTestCase,
    GeneratedGuiTestSuite,
)


def test_collect_source_context_includes_ui_source_and_skips_dependencies_and_secrets(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules" / "library").mkdir(parents=True)
    (tmp_path / "Dockerfile").write_text("FROM nginx", encoding="utf-8")
    (tmp_path / "src" / "App.tsx").write_text("export function App() {}", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"scripts":{"start":"vite"}}', encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=value", encoding="utf-8")
    (tmp_path / "node_modules" / "library" / "vendor.js").write_text(
        "vendor source", encoding="utf-8"
    )

    context, files, was_truncated = collect_source_context(tmp_path)

    assert files == ["package.json", "src/App.tsx"]
    assert not was_truncated
    assert "export function App()" in context
    assert "SECRET=value" not in context
    assert "vendor source" not in context


def test_collect_source_context_rejects_missing_source_folder(tmp_path):
    with pytest.raises(ValueError, match="Select an app source folder"):
        collect_source_context("")
    with pytest.raises(ValueError, match="does not exist"):
        collect_source_context(tmp_path / "missing")


def test_agent_run_does_not_require_dockerfile(tmp_path):
    app_directory = tmp_path / "app"
    app_directory.mkdir()
    generated_file = tmp_path / "generated.spec.js"

    class FakeGraph:
        def invoke(self, state):
            return {
                **state,
                "source_files": ["package.json"],
                "source_context_truncated": False,
                "generated_files": [str(generated_file)],
            }

    agent = GuiTestAgent(model=object())
    agent.graph = FakeGraph()

    result = agent.run(app_directory)

    assert result["generated_files"] == [str(generated_file)]


def test_collect_source_context_is_bounded_to_forty_files(tmp_path):
    for index in range(41):
        (tmp_path / f"screen-{index:02d}.html").write_text(
            f"<main>Screen {index}</main>", encoding="utf-8"
        )

    context, files, was_truncated = collect_source_context(tmp_path)

    assert len(files) == 40
    assert was_truncated
    assert "Source context was bounded" in context


def test_large_source_file_does_not_hide_later_files(tmp_path):
    (tmp_path / "a-large.html").write_text("x" * 12000, encoding="utf-8")
    (tmp_path / "b-screen.html").write_text("<main>Later screen</main>", encoding="utf-8")

    context, files, was_truncated = collect_source_context(tmp_path)

    assert files == ["a-large.html", "b-screen.html"]
    assert was_truncated
    assert "Later screen" in context


def test_generated_test_requires_test_and_expect():
    case = GeneratedGuiTestCase(
        title="Homepage",
        purpose="Check that the homepage is visible.",
        test_code="console.log('not a test');",
    )
    with pytest.raises(ValueError, match="does not contain a Playwright test"):
        _validate_test_case(case)


def test_agent_writes_generated_specs_to_separate_output(tmp_path):
    source_directory = tmp_path / "my app"
    source_directory.mkdir()
    (source_directory / "index.html").write_text(
        "<button>Continue</button>", encoding="utf-8"
    )
    output_directory = tmp_path / "generated-output"

    class FakeStructuredModel:
        def with_structured_output(self, schema):
            assert schema is GeneratedGuiTestSuite
            return self

        def invoke(self, messages):
            return GeneratedGuiTestSuite(
                test_cases=[
                    GeneratedGuiTestCase(
                        title="Homepage Continue button",
                        purpose="Verify the homepage action is available.",
                        test_code=(
                            'import { test, expect } from "@playwright/test";\n'
                            "test('continue button is visible', async ({ page }) => {\n"
                            "  await page.goto(process.env.BASE_URL || 'http://localhost:3000');\n"
                            "  await expect(page.getByRole('button', { name: 'Continue' })).toBeVisible();\n"
                            "});"
                        ),
                    )
                ]
            )

    agent = GuiTestAgent(model=FakeStructuredModel())
    state = agent.graph.invoke({
        "source_directory": str(source_directory),
        "output_directory": str(output_directory),
        "source_context": "",
        "source_files": [],
        "source_context_truncated": False,
        "test_cases": [],
        "generated_files": [],
    })

    generated_path = Path(state["generated_files"][0])
    assert generated_path.parent.parent.name == "my-app"
    assert generated_path.name.endswith(".spec.js")
    assert generated_path.is_file()
    assert "getByRole('button'" in generated_path.read_text(encoding="utf-8")
