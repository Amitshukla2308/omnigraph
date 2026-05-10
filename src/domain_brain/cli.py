#!/usr/bin/env python3
"""`omnigraph domain-brain` subcommand.

Usage:
    python src/domain_brain/cli.py audit --project-root ~/atelier/projects/MyProject
    python src/domain_brain/cli.py audit --project-root ~/atelier/projects/MyProject --json
    python src/domain_brain/cli.py tasks        # print researcher task specs
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .researcher import audit_project_domain, list_researcher_tasks


def _pp_report(report) -> str:
    lines = []
    lines.append(f"project: {report.project}")
    lines.append(f"domain_brain_root: {report.domain_brain_root}")
    lines.append(f"coverage_score: {report.coverage_score:.2f}")
    lines.append(f"next_action: {report.next_action}")
    lines.append("")
    lines.append("artifacts:")
    for a in report.artifacts:
        mark = "✓" if a.exists else "✗"
        size_str = f"{a.line_count} lines" if a.exists else "missing"
        stale_str = " [stale]" if a.stale else ""
        fauthored = " [founder]" if a.founder_authored else ""
        lines.append(f"  {mark} {a.kind:22s} {size_str}{stale_str}{fauthored}")
        if a.summary:
            lines.append(f"      → {a.summary}")
    if report.gaps:
        lines.append("")
        lines.append(f"gaps ({len(report.gaps)}):")
        for g in sorted(report.gaps, key=lambda x: {"blocker": 0, "high": 1, "medium": 2, "low": 3}[x.severity]):
            lines.append(f"  [{g.severity:7s}] {g.artifact:22s} {g.question}")
            lines.append(f"           plan: {g.proposed_research}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="omnigraph domain-brain")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="Scan atelier project domain_brain/ and report gaps")
    p_audit.add_argument("--project-root", required=True,
                         help="Path to ~/atelier/projects/<ProjectName>/")
    p_audit.add_argument("--json", action="store_true", help="Emit JSON report to stdout")

    sub.add_parser("tasks", help="Print researcher task specs")

    args = ap.parse_args(argv)

    if args.cmd == "audit":
        report = audit_project_domain(Path(args.project_root).expanduser())
        if args.json:
            print(json.dumps(report.to_json(), indent=2))
        else:
            print(_pp_report(report))
        return 0

    if args.cmd == "tasks":
        print(json.dumps(list_researcher_tasks(), indent=2))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
