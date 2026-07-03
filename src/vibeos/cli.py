from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from .broker import CapabilityBroker
from .doctor import SessionDoctor
from .models import CommandRequest
from .planner import plan_payload
from .runtime import LocalRuntime, RuntimeSelectionError, build_runtime
from .task_trace import TaskTraceStore, bind_trace_session, make_trace_run_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibe", description="VibeOS command line client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask = subparsers.add_parser("ask", help="run one natural-language command")
    ask.add_argument("utterance")
    ask.add_argument("--dry-run", action="store_true", help="parse and resolve without executing capabilities")
    ask.add_argument("--json", action="store_true", help="print machine-readable JSON")
    ask.add_argument("--debug", action="store_true", help="include raw provider payloads in debug_trace")

    plan = subparsers.add_parser("plan", help="build a v0.3 task plan without executing it")
    plan.add_argument("utterance")
    plan.add_argument("--json", action="store_true", help="print machine-readable JSON")
    plan.add_argument("--debug", action="store_true", help="include raw provider payloads in debug_trace")

    approve = subparsers.add_parser("approve", help="approve and execute a pending L2 review request")
    approve.add_argument("review_id")
    approve.add_argument("--dry-run", action="store_true", help="resolve the stored review without executing capabilities")
    approve.add_argument("--json", action="store_true", help="print machine-readable JSON")
    approve.add_argument("--debug", action="store_true", help="include raw provider payloads in debug_trace when available")

    subparsers.add_parser("repl", help="start an interactive natural-language REPL")
    subparsers.add_parser("apps", help="list applications")
    subparsers.add_parser("windows", help="list windows")
    capabilities = subparsers.add_parser("capabilities", help="list registered capabilities and permission policy")
    capabilities.add_argument("--json", action="store_true", help="print machine-readable JSON")

    reviews = subparsers.add_parser("reviews", help="inspect pending permission reviews")
    reviews_sub = reviews.add_subparsers(dest="reviews_command", required=True)
    reviews_pending = reviews_sub.add_parser("pending", help="list pending L2 review requests")
    reviews_pending.add_argument("--json", action="store_true", help="print machine-readable JSON")
    reviews_provide = reviews_sub.add_parser("provide", help="provide supplemental input for a pending user-input review")
    reviews_provide.add_argument("review_id")
    reviews_provide.add_argument("supplemental_input", nargs="+")
    reviews_provide.add_argument("--json", action="store_true", help="print machine-readable JSON")
    reviews_reject = reviews_sub.add_parser("reject", help="reject a pending L2 review request")
    reviews_reject.add_argument("review_id")
    reviews_reject.add_argument("--json", action="store_true", help="print machine-readable JSON")

    doctor = subparsers.add_parser("doctor", help="diagnose Linux session integration readiness")
    doctor.add_argument("--json", action="store_true", help="print machine-readable JSON")

    audit = subparsers.add_parser("audit", help="inspect audit log")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    tail = audit_sub.add_parser("tail", help="print recent audit entries")
    tail.add_argument("-n", "--count", type=int, default=20)

    trace = subparsers.add_parser("trace", help="inspect unified task traces")
    trace_sub = trace.add_subparsers(dest="trace_command", required=True)
    trace_latest = trace_sub.add_parser("latest", help="list recent task traces")
    trace_latest.add_argument("-n", "--count", type=int, default=10)
    trace_latest.add_argument("--json", action="store_true", help="print machine-readable JSON")
    trace_show = trace_sub.add_parser("show", help="show one task trace summary")
    trace_show.add_argument("run_id")
    trace_show.add_argument("--json", action="store_true", help="print machine-readable JSON")
    trace_events = trace_sub.add_parser("events", help="show one task trace event stream")
    trace_events.add_argument("run_id")
    trace_events.add_argument("--json", action="store_true", help="print machine-readable JSON")
    trace_model = trace_sub.add_parser("model", help="show one task trace model I/O")
    trace_model.add_argument("run_id")
    trace_model.add_argument("--json", action="store_true", help="print machine-readable JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        report = SessionDoctor().run()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print_doctor(report)
        return 0 if report["summary"]["overall"] in {"ok", "warn"} else 1

    if args.command == "trace":
        store = TaskTraceStore()
        if args.trace_command == "latest":
            payload = store.latest_runs(args.count)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print_traces(payload)
            return 0
        if args.trace_command == "show":
            payload = {
                "manifest": store.manifest(args.run_id),
                "summary": store.summary(args.run_id),
            }
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print_trace_summary(payload)
            return 0 if payload["summary"] else 1
        if args.trace_command == "events":
            payload = store.events(args.run_id)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print_trace_events(payload)
            return 0 if payload else 1
        if args.trace_command == "model":
            payload = store.model_io(args.run_id)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print_trace_model(payload)
            return 0 if payload else 1

    if args.command == "plan":
        store = TaskTraceStore()
        trace_session = store.start_run(
            run_id=make_trace_run_id(args.utterance),
            command_name="plan",
            utterance=args.utterance,
            mode="plan",
            transport=None,
            dry_run=True,
            debug=args.debug,
        )
        with bind_trace_session(trace_session):
            payload = plan_payload(args.utterance, debug=args.debug)
        goal_synthesis = payload.get("goal_synthesis") if isinstance(payload.get("goal_synthesis"), dict) else {}
        goal_spec = goal_synthesis.get("goal_spec") if isinstance(goal_synthesis.get("goal_spec"), dict) else {}
        trace_session.finalize(
            status=str(payload.get("status", "failed")),
            goal_id=str(goal_spec.get("goal_id")) if goal_spec.get("goal_id") is not None else None,
            message=str(payload.get("message", "")),
            overall_status=str(payload.get("overall_status", payload.get("status", "failed"))),
            plan_id=str(payload.get("plan", {}).get("plan_id")) if isinstance(payload.get("plan"), dict) and payload.get("plan", {}).get("plan_id") is not None else None,
        )
        print_plan_payload(payload, json_output=args.json)
        return 0 if payload["status"] == "validated" else 1

    try:
        runtime = build_runtime()
    except RuntimeSelectionError as exc:
        return print_runtime_error(args, exc)

    if args.command == "audit" and args.audit_command == "tail":
        print(json.dumps(runtime.audit_tail(args.count), ensure_ascii=False, indent=2))
        return 0

    if args.command == "ask":
        result = runtime.handle(CommandRequest(args.utterance, dry_run=args.dry_run, debug=args.debug))
        print_result(result, json_output=args.json)
        return 0 if result.overall_status in {"completed", "dry_run"} else 1

    if args.command == "approve":
        result = runtime.handle(CommandRequest("", review_id=args.review_id, dry_run=args.dry_run, approve=True, debug=args.debug))
        print_result(result, json_output=args.json)
        return 0 if result.overall_status in {"completed", "dry_run"} else 1

    if args.command == "repl":
        return repl(runtime)

    if args.command == "apps":
        print(json.dumps(runtime.list_apps(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "windows":
        print(json.dumps(runtime.list_windows(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "capabilities":
        payload = runtime.capabilities()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print_capabilities(payload)
        return 0

    if args.command == "reviews" and args.reviews_command == "pending":
        payload = runtime.pending_reviews()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print_pending_reviews(payload)
        return 0

    if args.command == "reviews" and args.reviews_command == "provide":
        result = runtime.handle(CommandRequest("", review_id=args.review_id, supplemental_input=" ".join(args.supplemental_input)))
        print_result(result, json_output=args.json)
        return 0 if result.overall_status in {"completed", "dry_run"} else 1

    if args.command == "reviews" and args.reviews_command == "reject":
        result = runtime.reject_review(args.review_id)
        print_result(result, json_output=args.json)
        return 0 if result.message == "review request rejected by user" else 1

    return 2


def repl(runtime) -> int:
    print("VibeOS REPL. Type 'exit' to quit.")
    while True:
        try:
            utterance = input("vibe> ").strip()
        except EOFError:
            print()
            return 0
        if utterance in {"exit", "quit"}:
            return 0
        if not utterance:
            continue
        result = runtime.handle(CommandRequest(utterance))
        if result.status == "review_required":
            print_result(result, json_output=False)
            answer = input("approve this L2 action? [y/N] ").strip().lower()
            if answer in {"y", "yes"}:
                result = runtime.handle(CommandRequest("", review_id=result.review_id, approve=True))
        print_result(result, json_output=False)


def print_result(result, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return
    print(f"status: {result.status}")
    print(f"intent: {result.intent.action}")
    if result.intent.target:
        print(f"target: {json.dumps(result.intent.target, ensure_ascii=False)}")
    if result.selected_target:
        print(f"selected: {result.selected_target}")
    if result.transport:
        print(f"transport: {result.transport}")
    if result.review_id:
        print(f"review_id: {result.review_id}")
    if result.trace_run_id:
        print(f"trace_run_id: {result.trace_run_id}")
    if result.review:
        print(f"risk: {result.review.risk_level}")
        print(f"review_required: {result.review.review_required}")
        print(f"review_reason: {result.review.reason}")
        if result.review.effects:
            print(f"effects: {', '.join(result.review.effects)}")
    if result.message:
        print(f"message: {result.message}")
    print(f"execution_status: {result.execution_status}")
    print(f"acceptance_status: {result.acceptance_status}")
    print(f"overall_status: {result.overall_status}")
    if result.result is not None:
        print(json.dumps(result.result, ensure_ascii=False, indent=2))
    if result.audit_id:
        print(f"audit: {result.audit_id}")


def print_plan_payload(payload: dict[str, object], json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"status: {payload['status']}")
    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        print(f"analysis_type: {analysis.get('type')}")
        if analysis.get("domains"):
            print(f"domains: {', '.join(str(item) for item in analysis['domains'])}")
        if analysis.get("explanation"):
            print(f"analysis: {analysis['explanation']}")
    plan = payload.get("plan")
    if isinstance(plan, dict):
        print(f"plan_id: {plan.get('plan_id')}")
        print(f"route: {plan.get('selected_route_id')}")
        print(f"steps: {len(plan.get('steps', []))}")
    validation = payload.get("validation")
    if isinstance(validation, dict):
        print(f"valid: {validation.get('ok')}")
        for error in validation.get("errors", []):
            print(f"error: {error}")


def print_runtime_error(args, exc: RuntimeSelectionError) -> int:
    payload = exc.to_payload()
    if runtime_error_uses_json(args):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"runtime error: {payload['message']}", file=sys.stderr)
    return 1


def runtime_error_uses_json(args) -> bool:
    if args.command in {"apps", "windows"}:
        return True
    if args.command in {"ask", "approve", "capabilities", "doctor"}:
        return bool(getattr(args, "json", False))
    if args.command == "reviews":
        return bool(getattr(args, "json", False))
    return False


def print_doctor(report: dict[str, object]) -> None:
    summary = report["summary"]
    print(f"overall: {summary['overall']}  ok={summary['ok']} warn={summary['warn']} fail={summary['fail']}")
    for check in report["checks"]:
        print(f"{check['status']:>4}  {check['name']}: {check['message']}")


def print_capabilities(payload: dict[str, object]) -> None:
    for item in payload["capability_details"]:
        review = "review" if item["review_required"] else "auto"
        print(f"{item['action']:<18} {item['risk_level']:<2} {review:<6} {item['reason']}")


def print_pending_reviews(payload: list[dict[str, object]]) -> None:
    if not payload:
        print("no pending reviews")
        return
    for item in payload:
        intent = item["intent"]
        review = item["review"]
        print(f"{item['review_id']}  {review['risk_level']}  {intent['action']}  {item['utterance']}")


def print_traces(payload: list[dict[str, object]]) -> None:
    if not payload:
        print("no traces")
        return
    for item in payload:
        print(f"{item.get('run_id')}  {item.get('status')}  {item.get('started_at')}  {item.get('message', '')}")


def print_trace_summary(payload: dict[str, object]) -> None:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        print("trace not found")
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_trace_events(payload: list[dict[str, object]]) -> None:
    if not payload:
        print("no events")
        return
    for item in payload:
        print(f"{item.get('ts')}  {item.get('phase')}  {item.get('event_type')}  {item.get('status')}")


def print_trace_model(payload: list[dict[str, object]]) -> None:
    if not payload:
        print("no model io")
        return
    for item in payload:
        print(f"{item.get('ts')}  {item.get('phase')}  {item.get('provider')}  {item.get('model')}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
