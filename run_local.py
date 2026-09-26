#!/usr/bin/env python3
"""Run scenarios against an agent and print the score.

    python run_local.py --scenario scenarios/pub_02_text_interrupt.json
    python run_local.py --all --time-scale 8 --quiet
    python run_local.py --all --agent agent.agent:ParticipantAgent
    python run_local.py --scenario scenarios/pub_01_text_simple.json --json out.json

Official runs use --time-scale 1. Higher scales speed up the scenario clock but
not your compute, so your think time looks slower. Same scorer as the hidden sets.
"""

from __future__ import annotations
import argparse
import glob
import importlib
import json
import sys

from harness.runner import run_scenario
from harness.scorer import score_scenario, format_report


def is_kit_scenario(scenario: dict) -> bool:
    """True when *scenario* matches the streaming-harness schema.

    The runner replays ``events[].timestamp_ms`` and the scorer grades
    ``ground_truth``; both index ``scenario_id``.  Other files in
    ``scenarios/`` (Member-4 evaluation traces, step-based fixtures) use
    different schemas and must be skipped by ``--all`` discovery.
    """
    return bool(scenario.get("scenario_id")) and isinstance(
        scenario.get("events"), list
    )


def kit_scenario_paths(pattern: str) -> tuple[list[str], list[str]]:
    """Split files matching *pattern* into (compatible, skipped) paths."""
    kept, skipped = [], []
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path) as f:
                scenario = json.load(f)
        except (OSError, ValueError):
            skipped.append(path)
            continue
        (kept if is_kit_scenario(scenario) else skipped).append(path)
    return kept, skipped


def load_agent_factory(spec: str):
    module_name, _, class_name = spec.partition(":")
    if not class_name:
        sys.exit(f"--agent must look like 'module.path:ClassName' (got {spec!r})")
    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return lambda in_q, out_q: cls(in_q, out_q)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", help="path to one scenario JSON")
    ap.add_argument("--all", action="store_true",
                    help="run every scenario in scenarios/")
    ap.add_argument("--agent", default="agent.agent:BaselineAgent",
                    help="module:Class of your agent (default: BaselineAgent)")
    ap.add_argument("--time-scale", type=float, default=1.0,
                    help="speed multiplier for local runs (official = 1.0)")
    ap.add_argument("--tail-ms", type=float, default=6000.0,
                    help="virtual ms to wait after the last event")
    ap.add_argument("--quiet", action="store_true", help="hide the live trace")
    ap.add_argument("--json", help="write {trace, score} JSON to this path")
    args = ap.parse_args()

    paths = []
    if args.scenario:
        paths.append(args.scenario)
    if args.all:
        kept, skipped = kit_scenario_paths("scenarios/*.json")
        paths.extend(kept)
        for path in skipped:
            print(f"skipping non-harness scenario file: {path}")
    if not paths:
        ap.error("provide --scenario PATH or --all")

    factory = load_agent_factory(args.agent)
    results, dumps = [], []

    for path in paths:
        with open(path) as f:
            scenario = json.load(f)
        if not args.quiet:
            print(f"\n########## {scenario['scenario_id']} "
                  f"({scenario.get('metadata', {}).get('modality')}, "
                  f"{scenario.get('metadata', {}).get('difficulty')}) ##########")
        trace = run_scenario(scenario, factory,
                             time_scale=args.time_scale,
                             verbose=not args.quiet,
                             tail_ms=args.tail_ms)
        result = score_scenario(scenario, trace)
        print(format_report(result))
        results.append(result)
        dumps.append({"scenario_id": result["scenario_id"],
                      "trace": trace, "score": result})

    if len(results) > 1:
        avg = sum(r["total"] for r in results) / len(results)
        print("\n" + "=" * 46)
        print(f"  OVERALL: {avg:.1f} / 100 across {len(results)} scenario(s)")
        print("=" * 46)
        for r in results:
            print(f"    {r['total']:>5.1f}  {r['scenario_id']}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(dumps, f, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
