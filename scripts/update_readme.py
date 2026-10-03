"""Refresh the results table in README.md from evals/results.*.json (between the RESULTS markers)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- RESULTS:START -->", "<!-- RESULTS:END -->"


def row(path: Path) -> str | None:
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    s, m = d["summary"], d["meta"]
    return (f"| {m['mode']} ({m['llm']}) | {s['attack_cases']} | **{s['leak_rate']:.1%}** ({s['leaking_cases']} leaking) | "
            f"{s['legit_success_rate']:.1%} ({s['legit_passed']}/{s['legit_cases']}) | {s['denial_precision']:.0%} / {s['denial_recall']:.0%} | "
            f"{'PASS' if s['gate_ok'] else 'FAIL'} | {m['when']} (`{m['sha']}`) |")


def main() -> None:
    rows = [r for r in (row(ROOT / "evals" / "results.mock.json"), row(ROOT / "evals" / "results.real.json")) if r]
    table = ("| Run | Attacks | Leak rate | Legit success | Denial precision / recall | Gate | When |\n"
             "|---|---|---|---|---|---|---|\n" + "\n".join(rows))
    if not any("real" in r.split("|")[1] for r in rows):
        table += "\n| real provider | - | - | - | - | - | not run yet: `make eval-real` |"
    readme = ROOT / "README.md"
    text = readme.read_text()
    new = re.sub(re.escape(START) + r".*?" + re.escape(END), START + "\n" + table + "\n" + END, text, flags=re.S)
    readme.write_text(new)
    print(table)


if __name__ == "__main__":
    main()
