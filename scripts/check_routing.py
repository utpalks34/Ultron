"""Live check of the REAL CPU router (qwen2.5:1.5b-instruct) against the routing cases.

Needs Ollama running with the router model pulled. Run from backend-ai/ (config reads
.env from the working directory):

    python ..\\scripts\\check_routing.py [--mode {llm,hybrid}] [--set {main,heldout,both}]
                                        [--min 0.95] [--min-heldout 0.85]

hybrid (default) calls graph.supervisor.decide (keyword hits + LLM); llm calls the router
alone. main = the 30 cases in routing_cases.py, heldout = routing_cases_heldout.py.
Exit 1 if main accuracy < --min or held-out accuracy < --min-heldout, or if Ollama is
unreachable.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend-ai"))
sys.path.insert(0, str(ROOT / "backend-ai" / "tests"))

from routing_cases import CASES              # noqa: E402
from routing_cases_heldout import HELDOUT    # noqa: E402
from llm.router import route_segment         # noqa: E402
from graph.supervisor import decide          # noqa: E402


async def route(mode: str, segment: str) -> tuple[str | None, str]:
    if mode == "hybrid":
        got, source = await decide(segment, route_segment)
        return got, ("llm" if source.startswith("llm") else source)
    got, _reason, _secs = await route_segment(segment)
    return got, "llm"


async def run_set(name: str, cases, mode: str, min_acc: float) -> tuple[float, list[float]]:
    rows = []
    for utterance, expected in cases:
        t0 = time.perf_counter()
        try:
            got, source = await route(mode, utterance)
        except Exception as exc:
            got, source = f"ERROR({exc.__class__.__name__})", "-"
        ms = (time.perf_counter() - t0) * 1000
        rows.append((utterance, expected, got, source, ms))

    print(f"== {name} ({mode}) ==")
    print(f"{'utterance':<52} {'expected':<10} {'got':<10} {'source':<16} {'ms':>7}")
    print("-" * 99)
    for utterance, expected, got, source, ms in rows:
        mark = "" if got == expected else "  <-- MISMATCH"
        print(f"{utterance[:51]:<52} {str(expected):<10} {str(got):<10} {source:<16} {ms:7.0f}{mark}")

    mismatches = [r for r in rows if r[2] != r[1]]
    acc = (len(rows) - len(mismatches)) / len(rows)
    times = [r[4] for r in rows]
    print()
    print(f"{name} accuracy: {acc:.1%} ({len(rows) - len(mismatches)}/{len(rows)}), min {min_acc:.0%}")
    print(f"{name} latency:  mean {sum(times) / len(times):.0f} ms, max {max(times):.0f} ms")
    if mismatches:
        print("mismatches:")
        for utterance, expected, got, _source, _ms in mismatches:
            print(f"  {utterance!r}: expected {expected}, got {got}")
    print()
    return acc, times


async def main(mode: str, which: str, min_acc: float, min_heldout: float) -> int:
    try:
        await route_segment("open my browser")     # warm-up, excluded from timing
    except Exception as exc:
        print(f"Router unavailable ({exc.__class__.__name__}: {str(exc)[:200]}).")
        print("Is Ollama running, and is the router model pulled (scripts\\pull_models.py)?")
        return 1

    main_acc = heldout_acc = None
    if which in ("main", "both"):
        main_acc, _ = await run_set("main", CASES, mode, min_acc)
    if which in ("heldout", "both"):
        heldout_acc, _ = await run_set("heldout", HELDOUT, mode, min_heldout)

    failed = ((main_acc is not None and main_acc < min_acc)
              or (heldout_acc is not None and heldout_acc < min_heldout))
    fmt = lambda a: "n/a" if a is None else f"{a:.1%}"    # noqa: E731
    print(f"{'FAIL' if failed else 'PASS'}: main {fmt(main_acc)} (min {min_acc:.0%}), "
          f"heldout {fmt(heldout_acc)} (min {min_heldout:.0%})")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=["llm", "hybrid"], default="hybrid")
    ap.add_argument("--set", choices=["main", "heldout", "both"], default="both", dest="which")
    ap.add_argument("--min", type=float, default=0.95, dest="min_acc")
    ap.add_argument("--min-heldout", type=float, default=0.85, dest="min_heldout")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.mode, args.which, args.min_acc, args.min_heldout)))
