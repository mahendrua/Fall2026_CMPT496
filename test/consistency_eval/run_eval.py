"""
@file run_eval.py
@brief Runs the Consistency Review evaluation cases and reports how the checker did.

Offline by default: the model is replaced by the expectation-driven oracle, so
nothing is sent anywhere and nothing is paid for. --real uses the key and model
selected in Manage API Keys and makes one model call per target per repeat
(plus at most one re-ask if an answer cannot be read).

    .venv\\Scripts\\python.exe test\\consistency_eval\\run_eval.py --list
    .venv\\Scripts\\python.exe test\\consistency_eval\\run_eval.py
    .venv\\Scripts\\python.exe test\\consistency_eval\\run_eval.py --real --cases inclusive_boundary,summary_injection --repeat 2 --max-calls 6

Each run gets its own codebase and app folder under the output directory
(default test/consistency_eval/runs/<time>-<mode>/, ignored by git); the
checker's saved result, the raw structured answers and the run log are kept
there. report.json holds every per-run detail and the totals.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))

from agent.consistency_checker import run_consistency_check  # noqa: E402
from backend.token_usage import record_usage, track_command  # noqa: E402
from eval_cases import CASES, CASES_BY_NAME  # noqa: E402
from eval_fakes import OracleLLM, RecordingLLM  # noqa: E402
from eval_fixtures import build_case  # noqa: E402
from eval_scoring import score_run, summarize  # noqa: E402

# Hard ceiling on model calls in one invocation, whatever --max-calls says.
CALL_CEILING = 60


def targets_of(case):
    return [{"path": t.path, "symbol": t.symbol} if t.symbol else t.path for t in case.targets]


def run_one(case, repeat, out_dir, llm_factory):
    base = out_dir / case.name / f"run{repeat}"
    root, app = build_case(case, base)
    llm = RecordingLLM(llm_factory(case))

    started = time.perf_counter()
    with track_command(app, case.name, "consistency_eval"):
        with record_usage("consistency_eval") as usage:
            result = run_consistency_check(
                root, run_id=f"eval-{case.name}-{repeat}", targets=targets_of(case),
                llm=llm, app_dir=app, save=True,
            )
    seconds = time.perf_counter() - started

    # The run log is only written for runs that called a model.
    log_file = app / "last_run_log.json"
    log = json.loads(log_file.read_text(encoding="utf-8")) if log_file.is_file() else {}
    tokens = usage.summary()
    stats = llm.stats()
    calls = dict(
        case=case.name, repeat=repeat, seconds=seconds, calls=stats["calls"], unreadable=stats["unreadable"],
        proposed_findings=stats["proposed_findings"], waits=len(log.get("waits") or []),
        input_tokens=tokens["input_tokens"], output_tokens=tokens["output_tokens"],
        thinking_tokens=tokens.get("thinking_tokens", 0), total_tokens=tokens["total_tokens"],
    )
    (base / "answers.json").write_text(json.dumps(llm.calls, indent=2, default=str), encoding="utf-8")
    scored = score_run(case, result)
    scored.update(repeat=repeat, verdicts=[
        dict(target=a.scope.path + (f"#{a.scope.symbol}" if a.scope.symbol else ""), artifact=a.artifact.kind,
             rule_id=a.artifact.rule_id, verdict=a.verdict, accepted=a.accepted, reason=a.reason, problem=a.problem)
        for a in result.assessments
    ])
    return scored, calls


def print_report(scored, totals, model):
    print(f"\nModel: {model}")
    for run in scored:
        print(f"\n== {run['case']} (run {run['repeat']}): status={run['status']}"
              f"{'' if run['status_as_expected'] else '  [UNEXPECTED STATUS]'}"
              f"{'  [tuning case]' if run['tuning'] else ''}")
        for row in run["outcomes"]:
            verdict = row["verdict"] or "-"
            print(f"   {row['outcome']:<22} {row['target']:<34} {row['handle']:<8} expected={row['expected']:<22} verdict={verdict}")
            if row.get("reason"):
                print(f"   {'':<22} reason: {row['reason']}")
        for finding in run["findings"]:
            print(f"   finding [{finding['category']}] {finding['title']}  {finding['handles']} lines {finding['source_lines']}")
        for error in run["errors"]:
            print(f"   error: {error}")
        for item in run["undetermined"]:
            print(f"   undetermined {item['handle']}: {item['reason']}")
    print("\n== Totals")
    print(json.dumps(totals, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="List the cases and their expectations, then stop.")
    parser.add_argument("--cases", help="Comma-separated case names (default: all).")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case (default 1).")
    parser.add_argument("--real", action="store_true", help="Use the configured real model. Makes paid calls.")
    parser.add_argument("--max-calls", type=int, default=0,
                        help=f"Required with --real: refuse to start if the batch needs more calls (ceiling {CALL_CEILING}).")
    parser.add_argument("--out", help="Output directory (default: test/consistency_eval/runs/<time>-<mode>).")
    args = parser.parse_args(argv)

    names = args.cases.split(",") if args.cases else [case.name for case in CASES]
    unknown = [n for n in names if n not in CASES_BY_NAME]
    if unknown:
        parser.error(f"Unknown case(s): {', '.join(unknown)}")
    cases = [CASES_BY_NAME[n] for n in names]

    if args.list:
        for case in cases:
            print(f"\n{case.name}{'  [tuning]' if case.tuning else ''}: {case.topic}")
            print(f"  behaviour: {case.behaviour}")
            if case.cannot_check:
                print(f"  cannot check: {case.cannot_check}")
            print(f"  expected status: {' or '.join(case.expected_status)}")
            for target in case.targets:
                for e in target.expects:
                    print(f"    {target.label:<34} {e.handle:<8} {e.expected:<22} {e.category or '':<15} {e.claim}")
        return 0

    if args.repeat < 1:
        parser.error("--repeat must be at least 1.")
    planned = sum(len(case.targets) for case in cases) * args.repeat

    if args.real:
        if not args.max_calls:
            parser.error("--real needs --max-calls: the most model calls you allow for this batch.")
        if planned > min(args.max_calls, CALL_CEILING):
            parser.error(f"This batch needs {planned} model call(s) (up to {planned * 2} if answers must be re-asked); "
                         f"--max-calls is {args.max_calls} and the ceiling is {CALL_CEILING}.")
        from agent.llm import get_active_config, make_llm
        config = get_active_config()
        model = f"{config.provider} {config.model}"
        shared = make_llm()
        factory = lambda case: shared                    # noqa: E731
        print(f"Real model {model}: {planned} planned call(s) across {len(cases)} case(s) x {args.repeat}.")
    else:
        model = "offline oracle (no model calls)"
        factory = OracleLLM

    mode = "real" if args.real else "offline"
    out_dir = Path(args.out) if args.out else HERE / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{mode}"
    out_dir.mkdir(parents=True, exist_ok=True)

    scored, calls = [], []
    for case in cases:
        for repeat in range(1, args.repeat + 1):
            run, call = run_one(case, repeat, out_dir, factory)
            scored.append(run)
            calls.append(call)

    totals = summarize(scored, calls)
    (out_dir / "report.json").write_text(
        json.dumps(dict(model=model, mode=mode, runs=scored, calls=calls, totals=totals), indent=2), encoding="utf-8",
    )
    print_report(scored, totals, model)
    print(f"\nSaved: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
