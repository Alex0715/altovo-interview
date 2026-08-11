#!/usr/bin/env python3
"""Eval harness for the /ask endpoint (PLAN.md H5).

Runs every question in evals/questions.json against a live API, checks:
  - abstained  matches  expect_unanswerable
  - answer text contains each of expect_keywords (case-insensitive), when answerable
  - every citation is well-formed (non-empty chunk_id/document_id)

Prints a pass/fail table and writes evals/results.json. Non-zero exit if
anything fails, so it's usable as a gate.

This is also the retrieval-floor tuning loop: run it, look at which
unanswerable questions almost got an answer (or which answerable ones
abstained), adjust `min_similarity` in api/app/config.py, restart the API,
rerun.

Usage:
    python scripts/eval.py [--api-url http://localhost:8000] [--questions evals/questions.json]
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def parse_sse(raw: bytes) -> list[tuple[str, Any]]:
    """The response body is a complete, already-finished SSE stream (no real
    token streaming from the gateway — see PLAN.md H0) so it's safe to read
    it whole and split it, rather than parse incrementally."""
    events: list[tuple[str, Any]] = []
    for block in raw.decode("utf-8").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if event_name is None:
            continue
        data_raw = "\n".join(data_lines)
        try:
            data = json.loads(data_raw) if data_raw else None
        except json.JSONDecodeError:
            data = data_raw
        events.append((event_name, data))
    return events


def ask(api_url: str, question: str, timeout: float) -> dict:
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        f"{api_url}/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    wall_ms = int((time.perf_counter() - start) * 1000)

    events = parse_sse(raw)
    sources = next((d for e, d in events if e == "sources"), [])
    answer = next((d for e, d in events if e == "answer"), None)
    done = next((d for e, d in events if e == "done"), {})
    error = next((d for e, d in events if e == "error"), None)

    return {
        "sources": sources,
        "answer": answer,
        "done": done,
        "error": error,
        "wall_ms": wall_ms,
    }


def check_citations(answer: dict | None) -> list[str]:
    problems = []
    for c in (answer or {}).get("citations", []):
        if not c.get("chunk_id") or not c.get("document_id"):
            problems.append(f"malformed citation: {c}")
    return problems


def run(api_url: str, questions_path: Path, output_path: Path, timeout: float) -> bool:
    questions = json.loads(questions_path.read_text())
    results = []
    all_passed = True

    print(f"{'id':<24} {'ok':<4} {'abstained':<10} {'cites':<6} {'latency_ms':<10} notes")
    print("-" * 90)

    for q in questions:
        try:
            r = ask(api_url, q["question"], timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            all_passed = False
            print(f"{q['id']:<24} {'FAIL':<4} {'':<10} {'':<6} {'':<10} request failed: {exc}")
            results.append({"id": q["id"], "passed": False, "error": str(exc)})
            continue

        if r["error"]:
            all_passed = False
            print(f"{q['id']:<24} {'FAIL':<4} {'':<10} {'':<6} {'':<10} SSE error: {r['error']}")
            results.append({"id": q["id"], "passed": False, "error": r["error"]})
            continue

        answer = r["answer"] or {}
        abstained = bool(answer.get("abstained"))
        expect_unanswerable = bool(q.get("expect_unanswerable"))
        notes = []

        passed = abstained == expect_unanswerable
        if not passed:
            notes.append(
                f"expected abstained={expect_unanswerable}, got {abstained} "
                f"(reason={answer.get('abstain_reason')})"
            )

        if not expect_unanswerable and not abstained:
            text_lower = (answer.get("text") or "").lower()
            missing = [kw for kw in q.get("expect_keywords", []) if kw.lower() not in text_lower]
            if missing:
                passed = False
                notes.append(f"missing keywords: {missing}")

        cite_problems = check_citations(answer)
        if cite_problems:
            passed = False
            notes.extend(cite_problems)

        all_passed = all_passed and passed
        latency = r["done"].get("latency_ms", r["wall_ms"])
        print(
            f"{q['id']:<24} {'ok' if passed else 'FAIL':<4} {str(abstained):<10} "
            f"{len(answer.get('citations', [])):<6} {latency:<10} {'; '.join(notes)}"
        )

        results.append(
            {
                "id": q["id"],
                "question": q["question"],
                "passed": passed,
                "abstained": abstained,
                "expect_unanswerable": expect_unanswerable,
                "citations": len(answer.get("citations", [])),
                "sources_returned": len(r["sources"]),
                "top_source_score": r["sources"][0]["score"] if r["sources"] else None,
                "latency_ms": latency,
                "answer_text": answer.get("text"),
                "notes": notes,
            }
        )

    output_path.write_text(json.dumps(results, indent=2))
    print("-" * 90)
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed. Full detail: {output_path}")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--questions", type=Path, default=Path(__file__).resolve().parent.parent / "evals" / "questions.json"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent.parent / "evals" / "results.json"
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    ok = run(args.api_url, args.questions, args.output, args.timeout)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
