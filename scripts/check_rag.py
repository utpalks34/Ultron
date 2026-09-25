"""Live, in-process evaluation of the real RAG pipeline against tests/rag_cases.py. Run from
backend-ai/ with the venv active, Ollama and PostgreSQL up, the SAMPLE corpus ingested, and the
backend stopped or idle (this script uses its own model manager and the GPU):

    python ..\\scripts\\check_rag.py
    python ..\\scripts\\check_rag.py --no-generate      # retrieval + grading only, no GPU
    python ..\\scripts\\check_rag.py --only warranty    # cases whose question contains the text

Each case is judged on kind, expected substrings, source label, collection, a visible rewrite
step and corpus contamination. A failed hard-negative case (e.g. the toaster warranty) is
informative rather than a bug: the 1.5B grader is lenient and may accept the laptop warranty.
"""
import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Devanagari on Windows consoles
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend-ai"))
sys.path.insert(0, str(ROOT / "backend-ai" / "tests"))

import asyncpg  # noqa: E402

from config import settings  # noqa: E402
from graph.agents.rag_worker import build_deps  # noqa: E402
from graph.rag import rag_answer  # noqa: E402
from llm.ollama_manager import OllamaManager  # noqa: E402
from memory.db import close_pool, init_pool  # noqa: E402
from rag_cases import RAG_CASES  # noqa: E402

_FILE_LABEL = re.compile(r"\.\w+ #\d+$")     # e.g. "ultron_notes.md #3"


class PrintBus:
    """Records every published event; the RAG worker and the manager only call publish()."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish(self, event: dict) -> None:
        self.events.append(event)


def _trace(events: list[dict]) -> list[str]:
    return [f"{e['payload'].get('tool')}: {e['payload'].get('args_preview', '')}"
            for e in events if e.get("type") == "tool_call"]


def _judge(case: dict, result, events: list[dict], generated: bool) -> list[str]:
    fails: list[str] = []
    if result.kind != case["kind"]:
        fails.append(f"kind {result.kind!r}, expected {case['kind']!r}")
    if generated or case["kind"] != "answer":
        low = result.text.lower()
        for s in case.get("contains", []):
            if s.lower() not in low:
                fails.append(f"answer lacks {s!r}")
    if "source" in case and not any(case["source"].lower() in s.lower() for s in result.sources):
        fails.append(f"no source containing {case['source']!r} (got {result.sources})")
    if "collection" in case and result.collection != case["collection"]:
        fails.append(f"collection {result.collection!r}, expected {case['collection']!r}")
    if case.get("rewrites") and not any(
            e.get("type") == "tool_call" and e["payload"].get("tool") == "rewrite" for e in events):
        fails.append("no rewrite step in the trace")
    if result.collection == "personal" and any(s.startswith("Gita") for s in result.sources):
        fails.append(f"contamination: scripture label in a personal result {result.sources}")
    if result.collection == "scripture" and any(_FILE_LABEL.search(s) for s in result.sources):
        fails.append(f"contamination: file label in a scripture result {result.sources}")
    return fails


async def run(args: argparse.Namespace) -> int:
    cases = [c for c in RAG_CASES if args.only.lower() in c["q"].lower()]
    if not cases:
        print(f"no case contains {args.only!r}")
        return 1

    pool = await init_pool(settings.pg_dsn)
    bus = PrintBus()
    ollama = OllamaManager(bus)
    try:
        if not await ollama.warm_resident():
            print(f"model manager startup failed: {ollama.startup_error}")
            return 1

        async def skipped(question, chunks, collection) -> str:
            return "(generation skipped)"

        print(f"{'question':<48} {'expect':<7} {'got':<7} {'sec':>5}  result  first source")
        failed, answer_secs = 0, []
        for case in cases:
            bus.events.clear()
            tokens: list[str] = []

            async def token(text: str) -> None:
                tokens.append(text)

            deps = build_deps(pool, bus, ollama, None, token)
            if args.no_generate:
                deps.generate = skipped
            t0 = time.perf_counter()
            try:
                result = await rag_answer(case["q"], deps)
                secs = time.perf_counter() - t0
                fails = _judge(case, result, bus.events, not args.no_generate)
                got, first = result.kind, (result.sources[0] if result.sources else "-")
            except Exception as exc:          # fail visible: a crash is a FAIL, not a stop
                secs = time.perf_counter() - t0
                result, got, first = None, "error", "-"
                fails = [f"{type(exc).__name__}: {exc}"]
            if case["kind"] == "answer" and result is not None:
                answer_secs.append(secs)
            failed += bool(fails)
            print(f"{case['q'][:47]:<48} {case['kind']:<7} {got:<7} {secs:>5.1f}  "
                  f"{'FAIL' if fails else 'PASS':<6}  {first}")
            if fails:
                for reason in fails:
                    print(f"    - {reason}")
                for step in _trace(bus.events):
                    print(f"      . {step}")
                if result is not None:
                    print(f"      text: {' '.join(result.text.split())[:200]}")

        print(f"\n{len(cases) - failed}/{len(cases)} passed, {failed} failed")
        if answer_secs:
            print(f"mean seconds for answer cases: {sum(answer_secs) / len(answer_secs):.1f}")
        return 1 if failed else 0
    finally:
        await ollama.release()
        await ollama.shutdown()
        await close_pool(pool)


def main() -> int:
    ap = argparse.ArgumentParser(description="Live evaluation of the RAG pipeline.")
    ap.add_argument("--no-generate", action="store_true",
                    help="stub the generator (no GPU); skips 'contains' checks for answers")
    ap.add_argument("--only", default="", metavar="SUBSTRING",
                    help="run only cases whose question contains this text")
    try:
        return asyncio.run(run(ap.parse_args()))
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        print(f"database error: {exc}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
