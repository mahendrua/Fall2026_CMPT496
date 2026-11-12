from pathlib import Path

import pytest

from agent.gui_test_agent import (
    GuiTestAgent,
    SYSTEM_PROMPT,
    _normalize_test_case,
    _validate_test_case,
    collect_source_context,
)
from agent.structured_output.gui_test_output import (
    GeneratedGuiTestCase,
    GeneratedGuiTestSuite,
    GuiTestRepairResult,
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

    assert files == ["src/App.tsx", "package.json"]
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


def test_source_context_prioritizes_application_ui_and_extracts_real_control_evidence(tmp_path):
    (tmp_path / "README.md").write_text("Do not use guessed labels.", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Item.svelte").write_text(
        '<input placeholder="Task name" />\n'
        '<button aria-label="Remove task"></button>\n'
        '<label for="complete-all">Complete all tasks</label>\n'
        '<input id="complete-all" type="checkbox" />\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")

    context, files, _ = collect_source_context(tmp_path)

    assert files[0] == "src/Item.svelte"
    assert "README.md" not in files
    assert 'placeholder="Task name"' in context
    assert 'aria-label="Remove task"' in context
    assert "Complete all tasks" in context
    assert "Ground every locator in an actual accessible name" in SYSTEM_PROMPT
    assert "do not assume common conventions" in SYSTEM_PROMPT


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


def test_generated_test_javascript_syntax_is_validated():
    valid_case = GeneratedGuiTestCase(
        title="Valid syntax",
        purpose="Check the title.",
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "test('title', async ({ page }) => { "
            "await page.goto(process.env.BASE_URL); await expect(page).toBeTruthy(); });"
        ),
    )
    invalid_case = GeneratedGuiTestCase(
        title="Invalid syntax",
        purpose="Check syntax validation.",
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "await page.goto(process.env.BASE_URL);\n"
            "test('title', async ({ page }) => { await expect(page).toBeTruthy( });"
        ),
    )

    assert "test('title'" in _validate_test_case(valid_case)
    with pytest.raises(ValueError, match="invalid JavaScript syntax"):
        _validate_test_case(invalid_case)


def test_generated_test_normalizes_newlines_markdown_urls_and_module_format():
    case = GeneratedGuiTestCase(
        title="  Settings\n page  ",
        purpose="Checks settings navigation.",
        test_code=(
            "Here is the test:\n"
            "```javascript\n"
            'import { test, expect } from "@playwright/test";\n'
            "test('settings page', async ({ page }) => {\n"
            "  await page.goto('[http://localhost:3000/settings?tab=profile]"
            "(http://localhost:3000/settings?tab=profile)');\n"
            "  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible();\n"
            "});\n"
            "```\n"
        ),
    )

    normalized = _normalize_test_case(case)
    validated = _validate_test_case(normalized)

    assert normalized.title == "Settings page"
    assert 'const { test, expect } = require("@playwright/test");' in validated
    assert "new URL('/settings?tab=profile', process.env.BASE_URL).toString()" in validated
    assert "localhost" not in validated


def test_generated_test_assumptions_are_attached_as_playwright_annotations():
    case = GeneratedGuiTestCase(
        title="Boundary behavior",
        purpose="Checks the boundary value.",
        expected_result="The counter remains at zero.",
        assumptions=["The counter cannot go below zero."],
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "test('counter boundary', async ({ page }) => {\n"
            "  await page.goto(process.env.BASE_URL);\n"
            "  await expect(page.getByText('0')).toBeVisible();\n"
            "});"
        ),
    )

    validated = _validate_test_case(case)

    assert 'type: "assumption"' in validated
    assert 'type: "purpose"' in validated
    assert 'type: "expected"' in validated
    assert "The counter cannot go below zero." in validated


def test_generated_test_detects_newline_inside_playwright_title():
    case = GeneratedGuiTestCase(
        title="Malformed title",
        purpose="Checks syntax validation.",
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "test('unexpected\nnewline', async ({ page }) => {\n"
            "  await page.goto(process.env.BASE_URL);\n"
            "  await expect(page).toBeTruthy();\n"
            "});"
        ),
    )

    with pytest.raises(ValueError, match="invalid JavaScript syntax"):
        _validate_test_case(case)


def test_generated_test_rejects_hardcoded_urls_and_missing_base_url():
    case = GeneratedGuiTestCase(
        title="Hardcoded navigation",
        purpose="Checks navigation.",
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "test('home', async ({ page }) => {\n"
            "  await page.goto(process.env.BASE_URL || 'http://localhost:3000');\n"
            "  await expect(page).toBeTruthy();\n"
            "});"
        ),
    )

    with pytest.raises(ValueError, match="hardcoded absolute URL"):
        _validate_test_case(case)


def test_generated_test_requires_base_url_in_every_navigation_call():
    case = GeneratedGuiTestCase(
        title="Relative route",
        purpose="Checks a local route.",
        test_code=(
            'const { test, expect } = require("@playwright/test");\n'
            "test('home', async ({ page }) => {\n"
            "  const baseUrl = process.env.BASE_URL;\n"
            "  await page.goto('/home');\n"
            "  await expect(page).toBeTruthy();\n"
            "});"
        ),
    )

    with pytest.raises(ValueError, match="for every page.goto"):
        _validate_test_case(case)


def test_generation_retries_invalid_truncated_code_with_exact_validation_error(tmp_path):
    source_directory = tmp_path / "app"
    source_directory.mkdir()
    (source_directory / "index.html").write_text("<h1>Home</h1>", encoding="utf-8")
    output_directory = tmp_path / "generated-output"
    broken = GeneratedGuiTestSuite(
        test_cases=[
            GeneratedGuiTestCase(
                title="Home",
                purpose="Checks the home heading.",
                test_code=(
                    'const { test, expect } = require("@playwright/test");\n'
                    "test('home\nheading', async ({ page }) => {\n"
                    "  await page.goto(process.env.BASE_URL);\n"
                    "  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible();"
                ),
            )
        ]
    )
    repaired = GeneratedGuiTestSuite(
        test_cases=[
            GeneratedGuiTestCase(
                title="Home",
                purpose="Checks the home heading.",
                test_code=(
                    'const { test, expect } = require("@playwright/test");\n'
                    "test('home', async ({ page }) => {\n"
                    "  await page.goto(process.env.BASE_URL);\n"
                    "  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible();\n"
                    "});"
                ),
            )
        ]
    )

    class FakeStructuredModel:
        def __init__(self):
            self.messages = []

        def with_structured_output(self, schema):
            assert schema is GeneratedGuiTestSuite
            return self

        def invoke(self, messages):
            self.messages.append(messages)
            return broken if len(self.messages) == 1 else repaired

    model = FakeStructuredModel()
    agent = GuiTestAgent(model=model)
    result = agent.graph.invoke({
        "source_directory": str(source_directory),
        "output_directory": str(output_directory),
        "source_context": "",
        "source_files": [],
        "source_context_truncated": False,
        "test_cases": [],
        "generated_files": [],
    })

    assert len(model.messages) == 2
    repair_prompt = model.messages[1][1][1]
    assert "invalid JavaScript syntax" in repair_prompt
    assert "SyntaxError: Invalid or unexpected token" in repair_prompt
    assert len(result["generated_files"]) == 1
    generated_code = Path(result["generated_files"][0]).read_text(encoding="utf-8")
    assert "getByRole('heading', { name: 'Home' })" in generated_code
    assert "process.env.BASE_URL" in generated_code


def test_generation_stops_after_bounded_retries_without_saving_tests(tmp_path):
    source_directory = tmp_path / "app"
    source_directory.mkdir()
    (source_directory / "index.html").write_text("<h1>Home</h1>", encoding="utf-8")
    output_directory = tmp_path / "generated-output"
    truncated = GeneratedGuiTestSuite(
        test_cases=[
            GeneratedGuiTestCase(
                title="Truncated",
                purpose="Checks the home heading.",
                test_code=(
                    'const { test, expect } = require("@playwright/test");\n'
                    "test('home', async ({ page }) => {\n"
                    "  await page.goto(process.env.BASE_URL);\n"
                    "  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible();"
                ),
            )
        ]
    )

    class FakeStructuredModel:
        def __init__(self):
            self.calls = 0

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.calls += 1
            return truncated

    model = FakeStructuredModel()
    agent = GuiTestAgent(model=model)

    with pytest.raises(ValueError, match="No tests were saved.*invalid JavaScript syntax"):
        agent.graph.invoke({
            "source_directory": str(source_directory),
            "output_directory": str(output_directory),
            "source_context": "",
            "source_files": [],
            "source_context_truncated": False,
            "test_cases": [],
            "generated_files": [],
        })

    assert model.calls == 3
    assert not output_directory.exists()


def test_generation_retries_a_truncated_structured_model_response(tmp_path):
    source_directory = tmp_path / "app"
    source_directory.mkdir()
    (source_directory / "index.html").write_text("<h1>Home</h1>", encoding="utf-8")
    output_directory = tmp_path / "generated-output"
    valid_suite = GeneratedGuiTestSuite(
        test_cases=[
            GeneratedGuiTestCase(
                title="Home",
                purpose="Checks the home heading.",
                test_code=(
                    'const { test, expect } = require("@playwright/test");\n'
                    "test('home', async ({ page }) => {\n"
                    "  await page.goto(process.env.BASE_URL);\n"
                    "  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible();\n"
                    "});"
                ),
            )
        ]
    )

    class FakeStructuredModel:
        def __init__(self):
            self.messages = []

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.messages.append(messages)
            if len(self.messages) == 1:
                raise ValueError("Truncated structured model response: unterminated JSON")
            return valid_suite

    model = FakeStructuredModel()
    result = GuiTestAgent(model=model).graph.invoke({
        "source_directory": str(source_directory),
        "output_directory": str(output_directory),
        "source_context": "",
        "source_files": [],
        "source_context_truncated": False,
        "test_cases": [],
        "generated_files": [],
    })

    assert len(model.messages) == 2
    assert "Truncated structured model response: unterminated JSON" in model.messages[1][1][1]
    assert Path(result["generated_files"][0]).is_file()


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
                            "  await page.goto(process.env.BASE_URL);\n"
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
    generated_code = generated_path.read_text(encoding="utf-8")
    assert "APP_PAGE_ERROR:" in generated_code
    assert "APP_CONSOLE_ERROR:" in generated_code


def test_selector_repair_refuses_unconfirmed_failure_without_calling_model(tmp_path):
    app_directory = tmp_path / "app"
    app_directory.mkdir()

    class FakeModel:
        def with_structured_output(self, schema):
            raise AssertionError("The model must not be called for an unconfirmed failure.")

    agent = GuiTestAgent(model=FakeModel())

    result = agent.repair_failed_test(
        app_directory,
        tmp_path / "missing.spec.js",
        {"category": "locator_timeout", "diagnostics": {}, "errors": [], "output": []},
    )

    assert result["repairable"] is False
    assert "lacks a failing locator" in result["reason"]


def test_selector_repair_writes_syntax_validated_spec_separately(tmp_path, monkeypatch):
    import agent.gui_test_agent as gui_test_agent_module

    app_directory = tmp_path / "app"
    app_directory.mkdir()
    (app_directory / "index.html").write_text("<button>Continue</button>", encoding="utf-8")
    output_root = tmp_path / "generated-output"
    failed_file = output_root / "app" / "run-1" / "case.spec.js"
    failed_file.parent.mkdir(parents=True)
    failed_file.write_text(
        'import { test, expect } from "@playwright/test";\n'
        "test('continue', async ({ page }) => { await page.goto(process.env.BASE_URL); "
        "await expect(page.getByLabel('Continue')).toBeVisible(); });\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gui_test_agent_module, "_gui_output_root", lambda: output_root)

    class FakeModel:
        def with_structured_output(self, schema):
            assert schema is GuiTestRepairResult
            return self

        def invoke(self, messages):
            assert "Failing locator: getByLabel('Continue')" in messages[1][1]
            assert "<button>Continue</button>" in messages[1][1]
            return GuiTestRepairResult(
                repairable=True,
                reason="The source button has the accessible name Continue; use its button role.",
                test_code=(
                    'import { test, expect } from "@playwright/test";\n'
                    "test('continue', async ({ page }) => { await page.goto(process.env.BASE_URL); "
                    "await expect(page.getByRole('button', { name: 'Continue' })).toBeVisible(); });"
                ),
            )

    result = GuiTestAgent(model=FakeModel()).repair_failed_test(
        app_directory,
        failed_file,
        {
            "category": "locator_timeout",
            "diagnostics": {"locator": "getByLabel('Continue')"},
            "errors": [{"message": "Timed out waiting for locator"}],
            "output": [],
        },
    )

    repaired_file = Path(result["generated_file"])
    assert result["repairable"] is True
    assert repaired_file.parent.parent.parent.name == "_repairs"
    assert repaired_file != failed_file
    repaired_code = repaired_file.read_text(encoding="utf-8")
    assert "getByRole('button'" in repaired_code
    assert "APP_PAGE_ERROR:" in repaired_code
    assert "APP_CONSOLE_ERROR:" in repaired_code
