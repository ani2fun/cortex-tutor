"""The gate eval runner (``evals/README.md`` §3-4).

Replays every labelled case N times through the **production** gate — the same ``gate.evaluate``,
the same ``tutor.skills`` prompt files, the same provider ``factory`` + env (``FORCE_LOCAL`` picks
Ollama vs Claude) — and reports three independent axes:

1. schema health (valid / coerced / failsafe rates + a failure-shape histogram over the raw output),
2. decision quality vs labels (false-pass-weighted; a probe ``pass`` = step-hallucination),
3. stability (modal agreement, pass↔non-pass flip rate, score spread) + latency.

    FORCE_LOCAL=false uv run python -m evals.gate_runner \
        evals/datasets/two_sum_pilot.jsonl evals/datasets/two_sum_probes.jsonl --replays 5

``--baseline evals/baselines/<name>.json`` compares against frozen thresholds and exits non-zero on
regression (the CI hook). ``--write-baseline`` freezes this run's metrics + margined thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from tutor.config import get_settings
from tutor.domain.verdict import SCORE_VALUES, Verdict
from tutor.models.factory import make_gate_provider
from tutor.orchestration import gate
from tutor.skills import loader

from evals.schema import Case, load_cases

#: A false pass (unearned advance) is weighted this much vs. a false retry (friction).
FALSE_PASS_WEIGHT = 5

#: Margins added to a run's metrics when freezing it as a baseline threshold.
BASELINE_MARGINS = {"false_pass_rate": 0.02, "flip_rate": 0.05, "schema_valid_rate": -0.05}

_LIST_FIELDS = ("rubric_hits", "missing")
_KNOWN_KEYS = {"verdict", "score", "rubric_hits", "missing", "hint", "next_hint_level"}


def failure_shapes(raw: dict | None) -> list[str]:
    """Classify HOW a raw verdict missed the schema — the histogram a prompt/schema fix targets."""
    if raw is None:
        return ["provider_error"]
    shapes: list[str] = []
    if raw.get("verdict") not in {v.value for v in Verdict}:
        shapes.append("unknown_verdict" if "verdict" in raw else "missing_verdict")
    score = raw.get("score")
    if score is not None and (isinstance(score, bool) or score not in SCORE_VALUES):
        shapes.append("score_out_of_enum")
    for f in _LIST_FIELDS:
        if f in raw and not isinstance(raw[f], list):
            kind = "null" if raw[f] is None else "string" if isinstance(raw[f], str) else "other"
            shapes.append(f"{f}_as_{kind}")
    level = raw.get("next_hint_level")
    if level is not None and (isinstance(level, bool) or level not in (0, 1, 2, 3)):
        shapes.append("hint_level_out_of_range")
    if set(raw) - _KNOWN_KEYS:
        shapes.append("extra_keys")
    return shapes


@dataclass(frozen=True)
class Replay:
    case_id: str
    evaluation: gate.GateEvaluation


async def run_replays(cases: list[Case], n: int, concurrency: int) -> list[Replay]:
    provider = make_gate_provider(get_settings())
    sem = asyncio.Semaphore(concurrency)
    total = len(cases) * n
    done = 0

    async def one(case: Case) -> Replay:
        nonlocal done
        async with sem:
            ev = await gate.evaluate(
                provider,
                step=case.step,
                problem_context=case.problem_context,
                transcript=list(case.transcript),
                # The same evidence folding production applies — implement/test cases without code
                # replay with the explicit no-code marker, exactly as a live claim-only turn would.
                answer=gate.compose_answer(
                    case.answer,
                    step=case.step,
                    code=case.code,
                    language=case.language,
                    run_result=case.run_result,
                ),
            )
        done += 1
        print(
            f"  [{done}/{total}] {case.case_id}: {ev.verdict.verdict.value}/{ev.verdict.score} ({ev.outcome})"
        )
        return Replay(case.case_id, ev)

    return await asyncio.gather(*(one(c) for c in cases for _ in range(n)))


def _pct(xs: list[int], p: float) -> int:
    return sorted(xs)[min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))] if xs else 0


def compute_metrics(cases: list[Case], replays: list[Replay], n: int) -> dict:
    by_case: dict[str, list[Replay]] = {}
    for r in replays:
        by_case.setdefault(r.case_id, []).append(r)
    case_by_id = {c.case_id: c for c in cases}

    outcomes = Counter(r.evaluation.outcome for r in replays)
    shape_hist: Counter[str] = Counter()
    for r in replays:
        if r.evaluation.outcome != "valid":
            shape_hist.update(failure_shapes(r.evaluation.raw))

    confusion: Counter[str] = Counter()  # "expected→emitted"
    fp = fr = ok = underscored = failsafe_denied_pass = 0
    for r in replays:
        c = case_by_id[r.case_id]
        assert c.expected is not None
        v = r.evaluation.verdict.verdict
        expected_label = (
            c.expected.allowed_verdicts[0].value if len(c.expected.allowed_verdicts) == 1 else "not_pass"
        )
        confusion[f"{expected_label}→{v.value}"] += 1
        if c.expected.allows(v):
            ok += 1
            if (
                v is Verdict.PASS
                and c.expected.min_score
                and r.evaluation.verdict.score < c.expected.min_score
            ):
                underscored += 1
        elif v is Verdict.PASS:
            fp += 1
        elif c.expected.allowed_verdicts == [Verdict.PASS]:
            fr += 1
            if r.evaluation.outcome.startswith("failsafe"):
                failsafe_denied_pass += 1

    per_case = {}
    flips = 0
    modal_fracs: list[float] = []
    spreads: list[int] = []
    for case_id, rs in by_case.items():
        verdicts = [r.evaluation.verdict.verdict.value for r in rs]
        scores = [r.evaluation.verdict.score for r in rs]
        modal = Counter(verdicts).most_common(1)[0]
        passish = {v == "pass" for v in verdicts}
        flipped = len(passish) > 1
        flips += flipped
        modal_fracs.append(modal[1] / len(rs))
        spreads.append(max(scores) - min(scores))
        per_case[case_id] = {
            "verdicts": verdicts,
            "scores": scores,
            "outcomes": [r.evaluation.outcome for r in rs],
            "modal_verdict": modal[0],
            "flipped": flipped,
        }

    latencies = [r.evaluation.latency_ms for r in replays]
    total = len(replays)
    probes = [r for r in replays if case_by_id[r.case_id].kind == "cross_step_probe"]
    probe_passes = sum(r.evaluation.verdict.verdict is Verdict.PASS for r in probes)
    return {
        "n_cases": len(cases),
        "n_replays_per_case": n,
        "n_total": total,
        "schema": {
            "valid_rate": outcomes["valid"] / total,
            "coerced_rate": outcomes["coerced"] / total,
            "failsafe_schema_rate": outcomes["failsafe_schema"] / total,
            "failsafe_provider_rate": outcomes["failsafe_provider"] / total,
            "failure_shapes": dict(shape_hist),
        },
        "decision": {
            "accuracy": ok / total,
            "false_pass_rate": fp / total,
            "false_retry_rate": fr / total,
            "weighted_error": (FALSE_PASS_WEIGHT * fp + fr) / total,
            "underscored_pass": underscored,
            "failsafe_denied_pass": failsafe_denied_pass,
            "step_hallucination_rate": (probe_passes / len(probes)) if probes else None,
            "confusion": dict(confusion),
        },
        "stability": {
            "flip_rate": flips / len(by_case),
            "mean_modal_agreement": statistics.mean(modal_fracs),
            "mean_score_spread": statistics.mean(spreads),
            "max_score_spread": max(spreads),
        },
        "latency_ms": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95)},
        "per_case": per_case,
    }


def render_report(meta: dict, m: dict, cases: list[Case]) -> str:
    case_by_id = {c.case_id: c for c in cases}
    s, d, st = m["schema"], m["decision"], m["stability"]
    lines = [
        "# Gate eval report",
        "",
        " | ".join(f"**{k}**: {v}" for k, v in meta.items()),
        "",
        f"{m['n_cases']} cases x {m['n_replays_per_case']} replays = {m['n_total']} gate calls; "
        f"latency p50 {m['latency_ms']['p50']} ms, p95 {m['latency_ms']['p95']} ms.",
        "",
        "## Schema health",
        "",
        f"- valid first-try: **{s['valid_rate']:.1%}**, coerced: {s['coerced_rate']:.1%}, "
        f"fail-safe (schema): {s['failsafe_schema_rate']:.1%}, "
        f"fail-safe (provider): {s['failsafe_provider_rate']:.1%}",
        f"- failure shapes: {s['failure_shapes'] or '—'}",
        "",
        "## Decision quality (vs labels)",
        "",
        f"- accuracy: **{d['accuracy']:.1%}**, false-pass: **{d['false_pass_rate']:.1%}**, "
        f"false-retry: {d['false_retry_rate']:.1%}, "
        f"weighted error (FPx{FALSE_PASS_WEIGHT}): {d['weighted_error']:.2f}",
        "- step-hallucination rate (probe passes): "
        + (f"**{d['step_hallucination_rate']:.1%}**" if d["step_hallucination_rate"] is not None else "n/a"),
        f"- pass below min_score: {d['underscored_pass']}; fail-safes that denied a deserved pass: "
        f"**{d['failsafe_denied_pass']}**",
        f"- confusion: {d['confusion']}",
        "",
        "## Stability",
        "",
        f"- pass↔non-pass flip rate: **{st['flip_rate']:.1%}** of cases; modal agreement "
        f"{st['mean_modal_agreement']:.1%}; score spread mean {st['mean_score_spread']:.0f} "
        f"/ max {st['max_score_spread']}",
        "",
        "## Per case",
        "",
        "| case | kind | expected | verdicts (replays) | scores | outcomes |",
        "|---|---|---|---|---|---|",
    ]
    for cid, pc in m["per_case"].items():
        c = case_by_id[cid]
        assert c.expected is not None
        exp = "/".join(v.value for v in c.expected.allowed_verdicts)
        flag = " ⚠" if pc["flipped"] else ""
        lines.append(
            f"| {cid}{flag} | {c.kind} | {exp} | {' '.join(pc['verdicts'])} "
            f"| {' '.join(map(str, pc['scores']))} | {' '.join(pc['outcomes'])} |"
        )
    return "\n".join(lines) + "\n"


def compare_baseline(m: dict, baseline: dict) -> list[str]:
    t = baseline["thresholds"]
    failures = []
    if m["decision"]["false_pass_rate"] > t["false_pass_rate"]:
        failures.append(
            f"false_pass_rate {m['decision']['false_pass_rate']:.3f} > {t['false_pass_rate']:.3f}"
        )
    if m["stability"]["flip_rate"] > t["flip_rate"]:
        failures.append(f"flip_rate {m['stability']['flip_rate']:.3f} > {t['flip_rate']:.3f}")
    if m["schema"]["valid_rate"] < t["schema_valid_rate"]:
        failures.append(f"schema_valid_rate {m['schema']['valid_rate']:.3f} < {t['schema_valid_rate']:.3f}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+", type=Path)
    parser.add_argument("--replays", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None, help="output dir (default evals/out/run-<utc>)")
    parser.add_argument("--baseline", type=Path, help="frozen baseline to compare against (CI mode)")
    parser.add_argument("--write-baseline", type=Path, help="freeze this run's metrics as a baseline")
    args = parser.parse_args()

    cases: list[Case] = []
    for ds in args.datasets:
        cases.extend(load_cases(ds))
    unlabelled = [c.case_id for c in cases if c.expected is None]
    if unlabelled:
        raise SystemExit(f"refusing to run: {len(unlabelled)} unlabelled case(s), e.g. {unlabelled[:3]}")

    replays = asyncio.run(run_replays(cases, args.replays, args.concurrency))
    metrics = compute_metrics(cases, replays, args.replays)

    first = replays[0].evaluation
    meta = {
        "rubric_version": loader.rubric_version(),
        "provider": first.provider_kind,
        "model": first.model,
        "git": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "datasets": ", ".join(d.name for d in args.datasets),
        "run_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }

    out = args.out or Path("evals/out") / f"run-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps({"meta": meta, **metrics}, indent=2) + "\n")
    (out / "report.md").write_text(render_report(meta, metrics, cases))
    print(f"\nwrote {out}/metrics.json + report.md")

    if args.write_baseline:
        thresholds = {
            "false_pass_rate": metrics["decision"]["false_pass_rate"] + BASELINE_MARGINS["false_pass_rate"],
            "flip_rate": metrics["stability"]["flip_rate"] + BASELINE_MARGINS["flip_rate"],
            "schema_valid_rate": metrics["schema"]["valid_rate"] + BASELINE_MARGINS["schema_valid_rate"],
        }
        args.write_baseline.parent.mkdir(parents=True, exist_ok=True)
        args.write_baseline.write_text(
            json.dumps({"meta": meta, "thresholds": thresholds, "metrics": metrics}, indent=2) + "\n"
        )
        print(f"baseline frozen → {args.write_baseline}")

    if args.baseline:
        failures = compare_baseline(metrics, json.loads(args.baseline.read_text()))
        if failures:
            print("\nREGRESSION vs baseline:")
            for f in failures:
                print(f"  ✗ {f}")
            raise SystemExit(1)
        print("baseline check: OK")


if __name__ == "__main__":
    main()
