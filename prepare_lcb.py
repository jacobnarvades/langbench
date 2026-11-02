"""Build stdin/stdout tasks from LiveCodeBench (competitive programming) for the same harness.

Every problem gets the SAME statement and the SAME hidden tests in all six languages, so nothing
has to be translated per language. Output layout:
  <out>/<slug>/tests.json                 shared tests [{input, output, public}]
  <out>/<slug>/<lang>/task.json           kind=stdio, statement, solution file
  <out>/<slug>/<lang>/workspace/<file>    language stub
  <out>/manifest.json, valid.json

The dataset is streamed from Hugging Face and filtered on the fly (nothing large is stored).
Only stdin problems are kept (LeetCode-style function problems are dropped).

Usage:
  python prepare_lcb.py --after 2025-01-01 --difficulty medium hard --platform atcoder --out tasks_lcb
  python prepare_lcb.py --selftest --out tasks_selftest      # tiny set WITH reference solutions, to test the runners
"""
import argparse
import base64
import io
import json
import pickle
import random
import sys
import urllib.request
import zlib
from pathlib import Path

from stdio import SOLUTION_FILE, STUBS

LANGS = ["python", "rust", "go", "java", "typescript", "cpp"]
HF = "https://huggingface.co/datasets/livecodebench/code_generation_lite/resolve/main/{}"
FILES = ["test6.jsonl", "test5.jsonl"]   # newest first
IO_NOTE = "Read the input from standard input and write the answer to standard output."


class _StrOnly(pickle.Unpickler):
    def find_class(self, module, name):   # a pickled str needs no globals; refuse everything else
        raise pickle.UnpicklingError(f"refusing to load {module}.{name}")


def decode_private(blob: str):
    """LiveCodeBench stores private tests as base64(zlib(pickle(json-string)))."""
    try:
        return json.loads(_StrOnly(io.BytesIO(zlib.decompress(base64.b64decode(blob)))).load())
    except Exception:
        return json.loads(blob)


def stream(files):
    for f in files:
        with urllib.request.urlopen(HF.format(f), timeout=120) as r:
            for line in r:
                yield json.loads(line)


def pick_tests(q, n_private, seed):
    pub = [t for t in json.loads(q["public_test_cases"]) if t["testtype"] == "stdin"]
    priv = [t for t in decode_private(q["private_test_cases"]) if t["testtype"] == "stdin"]
    rng = random.Random(f"{seed}:{q['question_id']}")
    big = sorted(priv, key=lambda t: -len(t["input"]))[:max(1, n_private // 5)]   # keep the stress tests
    rest = [t for t in priv if t not in big]
    chosen = big + rng.sample(rest, min(len(rest), n_private - len(big)))
    out = [{"input": t["input"], "output": t["output"], "public": True} for t in pub]
    out += [{"input": t["input"], "output": t["output"], "public": False} for t in chosen]
    return out


def statement(q):
    return f"# {q['question_title']}\n\n{q['question_content'].strip()}\n\n{IO_NOTE}"


def write_task(out: Path, slug, text, tests, extra_ref=None):
    (out / slug).mkdir(parents=True, exist_ok=True)
    (out / slug / "tests.json").write_text(json.dumps(tests))
    for lang in LANGS:
        d = out / slug / lang
        (d / "workspace").mkdir(parents=True, exist_ok=True)
        f = SOLUTION_FILE[lang]
        (d / "workspace" / f).write_text(STUBS[lang])
        if extra_ref:
            (d / "reference").mkdir(exist_ok=True)
            (d / "reference" / f).write_text(extra_ref[lang])
        (d / "task.json").write_text(json.dumps({
            "kind": "stdio", "slug": slug, "lang": lang, "solution_files": [f], "test_files": [],
            "reference_files": [f] if extra_ref else [], "instructions": text}, indent=2))


def finish(out: Path, slugs):
    slugs = sorted(slugs)
    (out / "manifest.json").write_text(json.dumps({"langs": LANGS, "slugs": slugs}, indent=2))
    (out / "valid.json").write_text(json.dumps({"langs": LANGS, "slugs": slugs}, indent=2))
    print(f"wrote {len(slugs)} problems x {len(LANGS)} languages to {out}/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tasks_lcb")
    ap.add_argument("--after", default="2025-01-01", help="keep problems from contests on/after this date (YYYY-MM-DD)")
    ap.add_argument("--platform", nargs="*", default=["atcoder"])
    ap.add_argument("--difficulty", nargs="*", default=["medium", "hard"])
    ap.add_argument("--contest-prefix", nargs="*", help="keep only these contest families, e.g. arc agc (harder than abc)")
    ap.add_argument("--limit", type=int, help="cap the number of problems (random, seeded)")
    ap.add_argument("--private-tests", type=int, default=25, help="hidden tests per problem (all public examples are kept too)")
    ap.add_argument("--seed", default="langbench")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)

    if args.selftest:
        from selftest_problems import PROBLEMS
        for slug, p in PROBLEMS.items():
            write_task(out, slug, p["statement"] + "\n\n" + IO_NOTE, p["tests"], p["reference"])
        return finish(out, PROBLEMS)

    kept = []
    for q in stream(FILES):
        if q["platform"] not in args.platform or q["difficulty"] not in args.difficulty:
            continue
        if q["contest_date"][:10] < args.after:
            continue
        if args.contest_prefix and not q["contest_id"].startswith(tuple(args.contest_prefix)):
            continue
        if any(t["testtype"] != "stdin" for t in json.loads(q["public_test_cases"])):
            continue
        kept.append(q)
    print(f"{len(kept)} problems match (platform={args.platform} difficulty={args.difficulty} after={args.after})")
    if args.limit and len(kept) > args.limit:
        kept = random.Random(args.seed).sample(kept, args.limit)
    slugs = []
    for q in kept:
        tests = pick_tests(q, args.private_tests, args.seed)
        if not any(not t["public"] for t in tests) or not tests:
            continue
        slug = q["question_id"].lower().replace("_", "-")
        write_task(out, slug, statement(q), tests)
        slugs.append(slug)
    finish(out, slugs)


if __name__ == "__main__":
    main()
