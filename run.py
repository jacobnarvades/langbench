"""Agent loop: same exercises in six languages, two feedback conditions.

Every run gets the same first prompt (instructions + stub files + test files). After each attempt the
code is built and tested; if it fails the model gets feedback and another turn, up to --max-turns.

Conditions (only the feedback differs):
  tests  Test-runner output only. If the build step fails, the model is told only that it failed,
         with no compiler/syntax messages.
  diag   Full static diagnostics on every turn: compiler errors and warnings (rustc, go build + vet,
         javac -Xlint, g++ -Wall -Wextra), pyright for Python, tsc --strict for TypeScript; then the
         build errors or test output.

Turn 1 is identical in both conditions, so turn-1 results pooled across conditions are the
single-shot (no feedback) condition.

Usage:
  python run.py --model claude-sonnet-5-5 --samples 3 --workers 8
  python run.py --model mock:ref --langs python --slugs anagram          # harness self-test, no API
"""
import argparse
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from runners import RUNNERS, Outcome, evaluate

ROOT = Path(__file__).resolve().parent
TASKS = ROOT / "tasks"
CONDITIONS = ["tests", "diag"]
LANG_LABEL = {"python": "Python", "rust": "Rust", "go": "Go", "java": "Java",
              "typescript": "TypeScript", "cpp": "C++"}
FEEDBACK_LIMIT = 8000

SYSTEM = (
    "You are solving a programming exercise. Edit only the solution files. Do not change the tests. "
    "Reply with the complete new contents of every solution file you change, each wrapped as\n"
    '<file path="RELATIVE/PATH">\n...full file contents...\n</file>\n'
    "Do not use markdown code fences inside the file tags. You may add a short explanation outside the tags."
)


# ---------- prompts and parsing ----------

def first_prompt(task, ws):
    lang = LANG_LABEL[task["lang"]]
    parts = [f"Language: {lang}\n\n# Instructions\n\n{task['instructions']}\n\n# Solution files (edit these)\n"]
    for f in task["solution_files"]:
        p = ws / f
        body = p.read_text() if p.exists() else ""
        parts.append(f'<file path="{f}">\n{body}\n</file>\n')
    parts.append("\n# Test files (read-only; all tests will be run)\n")
    for f in task["test_files"]:
        parts.append(f'<test path="{f}">\n{(ws / f).read_text()}\n</test>\n')
    parts.append("\nWrite a complete solution that passes all the tests.")
    return "".join(parts)


FILE_RE = re.compile(r'<file\s+path="([^"]+)"\s*>\n?(.*?)\n?</file>', re.S)


def parse_files(text):
    files = {}
    for path, body in FILE_RE.findall(text):
        body = re.sub(r"^```[\w+-]*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)
        files[path.strip().lstrip("./")] = body + ("\n" if not body.endswith("\n") else "")
    return files


def allowed_path(task, path):
    """Solution files, or new files in a solution file's directory. Never tests or build config."""
    if ".." in Path(path).parts or path in task["test_files"]:
        return False
    if path in task["solution_files"]:
        return True
    sol_dirs = {str(Path(s).parent) for s in task["solution_files"]}
    return str(Path(path).parent) in sol_dirs and Path(path).suffix in {".py", ".rs", ".go", ".java", ".ts", ".cpp", ".h", ".hpp"}


def clip(s):
    s = s.strip()
    if len(s) <= FEEDBACK_LIMIT:
        return s
    head = FEEDBACK_LIMIT // 4
    return s[:head] + f"\n... [{len(s) - FEEDBACK_LIMIT} chars omitted] ...\n" + s[-(FEEDBACK_LIMIT - head):]


def feedback(condition, res, diag_out):
    if condition == "tests":
        if res["outcome"] == Outcome.BUILD_FAIL:
            return "The build failed. No further details are available. Fix the code and send the complete files again."
        return ("Some tests failed. Test output:\n\n" + clip(res["test_output"]) +
                "\n\nFix the code and send the complete files again.")
    msg = []
    if diag_out.strip():
        msg.append("Static checker / compiler output:\n\n" + clip(diag_out))
    if res["outcome"] == Outcome.BUILD_FAIL:
        msg.append("The build failed. Build output:\n\n" + clip(res["build_output"]))
    else:
        msg.append("Some tests failed. Test output:\n\n" + clip(res["test_output"]))
    return "\n\n".join(msg) + "\n\nFix the code and send the complete files again."


# ---------- models ----------

class Claude:
    def __init__(self, model, max_tokens):
        import anthropic
        self.client = anthropic.Anthropic(max_retries=8)
        self.model, self.max_tokens = model, max_tokens

    def __call__(self, messages, ctx):
        r = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                        system=SYSTEM, messages=messages)
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, {"in": r.usage.input_tokens, "out": r.usage.output_tokens, "stop": r.stop_reason}


class Mock:
    """Harness self-tests. mock:ref answers with the reference solution; mock:stub-then-ref resends
    the stub with a syntax error first, then the reference; mock:stub never fixes anything."""

    def __init__(self, mode):
        self.mode = mode

    def __call__(self, messages, ctx):
        tdir, task = ctx["tdir"], ctx["task"]
        turn = (len(messages) + 1) // 2
        ref = {f: (tdir / "reference" / f).read_text() for f in task["reference_files"]}
        if self.mode == "ref" or (self.mode == "stub-then-ref" and turn >= 2):
            files = ref
        else:
            f0 = task["solution_files"][0]
            files = {f0: (tdir / "workspace" / f0).read_text() + ("\n}}}(((\n" if self.mode == "stub-then-ref" else "")}
        text = "".join(f'<file path="{p}">\n{b}</file>\n' for p, b in files.items())
        return text, {"in": 0, "out": 0, "stop": "mock"}


def make_model(name, max_tokens):
    return Mock(name.split(":", 1)[1]) if name.startswith("mock:") else Claude(name, max_tokens)


# ---------- one episode ----------

def episode(model, slug, lang, condition, sample, max_turns, worker, tdir_out):
    tdir = TASKS / slug / lang
    task = json.loads((tdir / "task.json").read_text())
    runner = RUNNERS[lang]
    turns, messages = [], []
    with tempfile.TemporaryDirectory(prefix=f"lb-{slug}-{lang}-") as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(tdir / "workspace", ws)
        messages.append({"role": "user", "content": first_prompt(task, ws)})
        final = None
        for turn in range(1, max_turns + 1):
            t0 = time.time()
            text, usage = model(messages, {"tdir": tdir, "task": task})
            messages.append({"role": "assistant", "content": text or "(empty)"})
            files = parse_files(text)
            rejected = [p for p in files if not allowed_path(task, p)]
            for p, body in files.items():
                if p in rejected:
                    continue
                dst = ws / p
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(body)
            if not files:
                res = {"outcome": "no_files", "build_output": "", "test_output": ""}
                diag_out = ""
            else:
                res = evaluate(lang, ws, task, worker)
                diag_out = runner.diagnostics(ws, task, worker) if condition == "diag" and res["outcome"] != Outcome.PASS else ""
            rec = {"turn": turn, "outcome": res["outcome"], "files": sorted(files), "rejected": rejected,
                   "tokens_in": usage["in"], "tokens_out": usage["out"], "stop": usage["stop"],
                   "diag_chars": len(diag_out), "seconds": round(time.time() - t0, 1)}
            turns.append(rec)
            final = res["outcome"]
            if final == Outcome.PASS or turn == max_turns:
                break
            if not files:
                fb = ('No files found. Reply with each solution file wrapped as <file path="...">...</file>.')
            else:
                fb = feedback(condition, res, diag_out)
            messages.append({"role": "user", "content": fb})
    key = f"{slug}|{lang}|{condition}|{sample}"
    (tdir_out / (key.replace("|", "__") + ".json")).write_text(json.dumps({"key": key, "messages": messages, "turns": turns}, indent=1))
    pass_turn = next((t["turn"] for t in turns if t["outcome"] == Outcome.PASS), None)
    return {"key": key, "slug": slug, "lang": lang, "condition": condition, "sample": sample,
            "final": final, "pass_turn": pass_turn, "n_turns": len(turns),
            "first_outcome": turns[0]["outcome"], "turns": turns}


# ---------- driver ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="Anthropic model id, or mock:ref / mock:stub-then-ref / mock:stub")
    ap.add_argument("--run-name", help="defaults to the model name")
    ap.add_argument("--langs", nargs="*")
    ap.add_argument("--slugs", nargs="*")
    ap.add_argument("--conditions", nargs="*", default=CONDITIONS)
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--allow-unvalidated", action="store_true", help="skip the valid.json filter (testing only)")
    args = ap.parse_args()

    man = json.loads((TASKS / "manifest.json").read_text())
    langs = args.langs or man["langs"]
    if args.slugs:
        slugs = args.slugs
    elif (TASKS / "valid.json").exists():
        slugs = json.loads((TASKS / "valid.json").read_text())["slugs"]
    elif args.allow_unvalidated:
        slugs = man["slugs"]
    else:
        raise SystemExit("tasks/valid.json not found: run validate.py first")
    if not args.model.startswith("mock:") and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY")

    name = args.run_name or args.model.replace(":", "-")
    out_dir = ROOT / "results" / name
    tdir_out = out_dir / "transcripts"
    tdir_out.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "episodes.jsonl"
    done = set()
    if out_file.exists():
        done = {json.loads(l)["key"] for l in out_file.read_text().splitlines() if l.strip()}

    jobs = [(s, l, c, k) for k in range(args.samples) for s in slugs for l in langs for c in args.conditions
            if f"{s}|{l}|{c}|{k}" not in done]
    print(f"{len(jobs)} episodes to run ({len(done)} already done) -> {out_file}")
    (out_dir / "config.json").write_text(json.dumps(vars(args), indent=2))

    model = make_model(args.model, args.max_tokens)
    workers = queue.Queue()
    for i in range(args.workers):
        workers.put(i)
    lock = threading.Lock()

    def job(s, l, c, k):
        w = workers.get()
        try:
            return episode(model, s, l, c, k, args.max_turns, w, tdir_out)
        finally:
            workers.put(w)

    n_pass = 0
    with ThreadPoolExecutor(args.workers) as ex, open(out_file, "a") as fh:
        futs = {ex.submit(job, *j): j for j in jobs}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:  # keep going; the job is retried on the next invocation
                print(f"[{n}/{len(jobs)}] ERROR {futs[f]}: {e!r}", flush=True)
                continue
            n_pass += r["final"] == Outcome.PASS
            with lock:
                fh.write(json.dumps(r) + "\n")
                fh.flush()
            print(f"[{n}/{len(jobs)}] {r['final']:10s} turn={r['pass_turn']} {r['key']}  (pass so far {n_pass}/{n})", flush=True)


if __name__ == "__main__":
    main()
