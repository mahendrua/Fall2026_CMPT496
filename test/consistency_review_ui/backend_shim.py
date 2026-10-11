"""
@file backend_shim.py
@brief Starts the real backend (main.py) against an isolated app folder, with no real model.

    python backend_shim.py <app_dir> [main.py arguments...]

Used by verify_screen.js. APP_DIR is pointed at <app_dir>, so saved results,
reviews and the error log are read from and written to that folder only.

No real model can be built. By default every way of building one raises and
leaves a MODEL_REQUESTED file in <app_dir>. With CR_SHIM_MODELS=oracle, a
consistency check gets an offline stand-in that answers from the tracked
evaluation expectations (test/consistency_eval) for the codebase named in the
prompt; each model created and each call is appended to MODEL_LOG.jsonl in
<app_dir>, so a test can count them. Every other model request still raises.

An optional <app_dir>/SHIM_CONTROL.json, read on every call, can slow calls
down ({"delay": seconds}), announce a provider wait for some codebases
({"wait": [names]}) or fail them with a daily-limit error ({"fail": [names]}).

Generation agents and database builds are replaced with functions that log a
"generation" event to MODEL_LOG.jsonl and raise, so any attempt is caught. In
oracle mode the full pipeline's steps are replaced too: each keeps the real
stage machinery but only rewrites the codebase's existing outputs (logged as
"pipeline_step"); {"pipeline_fail": step} in SHIM_CONTROL.json fails one.
"""

import json
import runpy
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "test" / "consistency_eval"))

app_dir = Path(sys.argv.pop(1)).resolve()

import os  # noqa: E402
import re  # noqa: E402
from dataclasses import replace  # noqa: E402

import agent.llm as llm  # noqa: E402
from eval_cases import CASES_BY_NAME  # noqa: E402
from eval_fakes import OracleLLM  # noqa: E402


def refuse(*args, **kwargs):
    (app_dir / "MODEL_REQUESTED").write_text("A model was requested.", encoding="utf-8")
    raise RuntimeError("Model creation is disabled while verifying the Consistency Review screen.")


def read_control():
    """Optional behaviour for a verification run, from SHIM_CONTROL.json (read each time)."""
    path = app_dir / "SHIM_CONTROL.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def log(event, **fields):
    with open(app_dir / "MODEL_LOG.jsonl", "a", encoding="utf-8") as file:
        file.write(json.dumps(dict(event=event, time=time.time(), **fields)) + "\n")


class TolerantOracle(OracleLLM):
    """The oracle, but a whole-file check of a method-level case uses that file's expectations."""

    def _answer(self, messages):
        user = messages[-1][1]
        path = re.search(r"^File: (.+)$", user, re.M).group(1)
        scope = re.search(r"^Code in scope: (.+)$", user, re.M).group(1)
        symbol = re.match(r"lines \d+-\d+ \(([^)]+)\)", scope)
        symbol = symbol.group(1) if symbol else None
        if not any(t.path == path and t.symbol == symbol for t in self.case.targets):
            same_file = [t for t in self.case.targets if t.path == path]
            if same_file:
                self.case = replace(self.case, targets=(replace(same_file[0], symbol=symbol),))
        return super()._answer(messages)


class RoutingOracle:
    """Answers each prompt from the expectations of the case it names (offline)."""

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        user = messages[-1][1]
        name = re.search(r"^Codebase: (.+)$", user, re.M).group(1)
        log("call", codebase=name, file=re.search(r"^File: (.+)$", user, re.M).group(1))

        # Optional behaviour for a verification run, read on every call.
        control = read_control()
        if name in control.get("wait", []):
            # The message agent/llm.py announces before retrying a rate-limited call.
            from backend.progress_logging import progress
            progress("Google rate limit reached, waiting 1 s before retrying...")
            time.sleep(1)
        if control.get("delay"):
            time.sleep(float(control["delay"]))
        if name in control.get("fail", []):
            raise llm.DailyLimitError(
                "This Google API key has hit its daily limit or is out of credit. Try again later, check your "
                "plan and billing, or select a different key."
            )
        return TolerantOracle(CASES_BY_NAME[name])._answer(messages)


def oracle_model(*args, **kwargs):
    log("created")
    return RoutingOracle()


# Patched before the agents are imported: they bind make_llm by name.
llm.make_llm = refuse
llm.build_chat_model = refuse

import backend.commands as commands  # noqa: E402
commands.APP_DIR = app_dir


def no_generation(name):
    def refuse_generation(*args, **kwargs):
        log("generation", name=name)
        raise RuntimeError(f"{name} must not run while verifying the Consistency Review screen.")
    return refuse_generation


# A consistency check must never regenerate artifacts or run pipeline steps;
# any attempt is logged (event "generation") and refused.
for _name in ("FileSummaryAgent", "DirectoryAgent", "BRAgent", "UTAgent", "UTVAgent", "ITAgent",
              "build_database", "build_summary_database"):
    setattr(commands, _name, no_generation(_name))

# Full pipeline steps that rewrite this folder of the codebase's outputs.
PIPELINE_OUTPUTS = {
    "generate_file_summaries": "file_summary_agent_output",
    "validate_business_rules": "BR_agent_output",
    "generate_unit_tests": "UT_agent_output",
}


def fake_step(name):
    """
    A full-pipeline step that keeps the real stage machinery (_run_command:
    run log, usage scope) but generates nothing: it rewrites the codebase's
    existing outputs for its folder, as a real run writes them again. Logged
    as a "pipeline_step" event; SHIM_CONTROL {"pipeline_fail": name} fails it.
    """
    def step(self, *args, **kwargs):
        target = str(args[0]) if args else str(kwargs.get("codebase", ""))

        def task():
            log("pipeline_step", name=name)
            if read_control().get("pipeline_fail") == name:
                raise RuntimeError(f"{name} failed (verification run)")
            folder = PIPELINE_OUTPUTS.get(name)
            if folder:
                base = app_dir / "agent" / folder / Path(target).name
                for file in base.rglob("*") if base.is_dir() else []:
                    if file.is_file():
                        file.write_bytes(file.read_bytes())
            return {"message": f"{name} done (verification run)"}

        return self._run_command(name, task, individualStep=False)
    return step


if os.environ.get("CR_SHIM_MODELS") == "oracle":
    import agent.consistency_checker as checker  # noqa: E402
    checker.make_llm = oracle_model
    for _step in ("build_database", "generate_file_summaries", "build_summary_database", "generate_directory_summaries",
                  "validate_business_rules", "generate_unit_tests", "generate_integration_tests", "generate_all_uml"):
        setattr(commands.Commands, _step, fake_step(_step))

runpy.run_path(str(REPO / "main.py"), run_name="__main__")
