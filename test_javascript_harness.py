
from pathlib import Path

from agent.javascript_test_harness import TestCase, check_tests

# Target JavaScript codebase.
codebase_path = Path(
    "/Users/ianayotte/Downloads/cli-table3-master"
)

# One passing test and one intentionally failing test.
cases = [
    TestCase(
        name="UT_1",
        imports=[
            'import { it, expect } from "vitest";',
            'import Table from "../index.js";',
        ],
        body="""
it("constructs a table with rows", () => {
    const table = new Table();
    table.push(["Alice", "25"]);
    table.push(["Bob", "30"]);

    expect(table.length).toBe(2);
});
""",
    ),
    TestCase(
        name="UT_2",
        imports=[
            'import { it, expect } from "vitest";',
            'import Table from "../index.js";',
        ],
        body="""
it("intentionally fails when the expected row count is incorrect", () => {
    const table = new Table();
    table.push(["Alice", "25"]);
    table.push(["Bob", "30"]);

    expect(table.length).toBe(3);
});
""",
    ),
]

# Store execution reports separately from the original project tests.
results_dir = codebase_path / "checkpoint_test_results"

# Execute through Checkpoint's JavaScript harness.
run = check_tests(
    kind="Unit",
    prefix="UT",
    cases=cases,
    rejected=[],
    codebase_path=str(codebase_path),
    codebase_name="cli-table3-master",
    results_dir=str(results_dir),
)

# Display results.
print("\n========== JAVASCRIPT HARNESS RESULTS ==========")
print(run.summary())

print("\nPassed:", run.passed)
print("Failed:", run.failed)
print("Skipped:", run.skipped)
print("Project error:", run.project_error)
print("Report path:", run.report_path)

if run.failed_tests:
    print("\nFailed test details:")
    for failure in run.failed_tests:
        print(failure)

# Verify the expected harness behavior.
print("\n========== HARNESS VERIFICATION ==========")

assert run.passed == 1, f"Expected 1 passed test, got {run.passed}"
assert run.failed == 1, f"Expected 1 failed test, got {run.failed}"
assert not run.project_error, (
    f"Unexpected project execution error: {run.project_error}"
)

print("SUCCESS: Harness correctly distinguishes test failures from execution errors.")
