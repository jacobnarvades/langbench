"""Check every task instance before spending money on it.

A task (exercise x language) is kept only if
  1. the track's reference solution builds and passes the full un-skipped test suite, and
  2. the untouched stub does NOT pass (so the tests really test something).
An exercise is kept only if it is valid in EVERY language, so the comparison stays matched.

Usage: python validate.py [--langs python rust ...] [--workers 8]
Writes tasks/valid.json.
"""
import argparse
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from runners import Outcome, evaluate

TASKS = Path(__file__).resolve().parent / "tasks"


def check(slug, lang, worker):
    tdir = TASKS / slug / lang
    task = json.loads((tdir / "task.json").read_text())
    res = {}
    for variant in ["reference", "stub"]:
        with tempfile.TemporaryDirectory(prefix=f"lb-{slug}-{lang}-") as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(tdir / "workspace", ws)
            if variant == "reference":
                for f in task["reference_files"]:
                    dst = ws / f
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tdir / "reference" / f, dst)
            r = evaluate(lang, ws, task, worker)
            res[variant] = r["outcome"]
            if variant == "reference" and r["outcome"] != Outcome.PASS:
                res["reference_log"] = (r["build_output"] + r["test_output"])[-1500:]
    res["valid"] = res["reference"] == Outcome.PASS and res["stub"] != Outcome.PASS
    return slug, lang, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", nargs="*")
    ap.add_argument("--slugs", nargs="*")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    man = json.loads((TASKS / "manifest.json").read_text())
    langs = args.langs or man["langs"]
    slugs = args.slugs or man["slugs"]

    out_path = TASKS / "validation.json"
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    jobs = [(s, l) for s in slugs for l in langs]
    with ThreadPoolExecutor(args.workers) as ex:
        futs = [ex.submit(check, s, l, i % args.workers) for i, (s, l) in enumerate(jobs)]
        for n, f in enumerate(as_completed(futs), 1):
            s, l, r = f.result()
            results.setdefault(s, {})[l] = r
            flag = "ok " if r["valid"] else "BAD"
            print(f"[{n}/{len(jobs)}] {flag} {s:28s} {l:10s} ref={r['reference']:10s} stub={r['stub']}", flush=True)
    out_path.write_text(json.dumps(results, indent=2))

    all_langs = man["langs"]
    valid = sorted(s for s, per in results.items()
                   if all(per.get(l, {}).get("valid") for l in all_langs))
    (TASKS / "valid.json").write_text(json.dumps({"langs": all_langs, "slugs": valid}, indent=2))
    missing = [l for l in all_langs if not all(l in per for per in results.values())]
    print(f"\n{len(valid)} exercises valid in all of {all_langs}")
    if missing:
        print(f"not yet validated: {missing} (run with --langs {' '.join(missing)})")


if __name__ == "__main__":
    main()
