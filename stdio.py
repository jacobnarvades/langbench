"""stdin/stdout task runners (competitive programming).

A stdio task is one problem with one hidden test set shared by every language: the program reads
stdin and its stdout is compared with the expected output. The same three stages as runners.py:
  build(ws, task, worker)        -> (ok, output)   compile / syntax check, no warnings
  diagnostics(ws, task, worker)  -> output         compiler warnings + pyright / tsc --strict
  test(ws, task, worker)         -> (rc, output)   run every test; rc 0 pass, 1 fail, None timeout

Feedback on failure shows only the verdict of the FIRST failing test. For a public example it also
shows input / expected / got; for a hidden test it shows the verdict (wrong answer, runtime error + stderr,
time limit) but not the test data. Same in both feedback conditions.
"""
import json
import os
import subprocess
import time
from pathlib import Path

from runners import ENV, ROOT, sh, BUILD_TIMEOUT

TEST_TIME_LIMIT = float(os.environ.get("LANGBENCH_TIME_LIMIT", 10))   # seconds per test, every language
TOTAL_TIME_LIMIT = float(os.environ.get("LANGBENCH_TOTAL_LIMIT", 180))
SHIM = str(ROOT / "support" / "cpp")
GO_ENV = {"GOTOOLCHAIN": "local", "GO111MODULE": "off"}


def tokens_equal(got: str, want: str) -> bool:
    """Token-wise comparison; numeric tokens containing '.' or 'e' match within 1e-6 relative/absolute."""
    a, b = got.split(), want.split()
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False
        if not (any(c in y for c in ".eE") and abs(fx - fy) <= 1e-6 * max(1.0, abs(fy))):
            return False
    return True


def clip(s, n=600):
    s = s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"
    return s


class Stdio:
    name = ""
    src = ""          # solution file name inside the workspace

    def compile_cmd(self, warn):   # -> (cmd, env) ; cmd is None when there is nothing to compile
        raise NotImplementedError

    def run_cmd(self, ws):
        raise NotImplementedError

    def extra_diag(self, ws):
        return ""

    def build(self, ws, task, worker):
        cmd, env = self.compile_cmd(False)
        if cmd is None:
            return True, ""
        rc, out = sh(cmd, ws, BUILD_TIMEOUT, env)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        cmd, env = self.compile_cmd(True)
        out = sh(cmd, ws, BUILD_TIMEOUT, env)[1] if cmd else ""
        return (out + "\n" + self.extra_diag(ws)).strip()

    def test(self, ws, task, worker):
        tests = json.loads(Path(task["tests_path"]).read_text())
        cmd = self.run_cmd(ws)
        start = time.time()
        for i, t in enumerate(tests, 1):
            label = f"public example {i}" if t["public"] else f"hidden test {i}"
            if time.time() - start > TOTAL_TIME_LIMIT:
                return None, f"Time limit exceeded (total budget) before {label}."
            try:
                p = subprocess.run(cmd, cwd=ws, input=t["input"], capture_output=True, text=True,
                                   timeout=TEST_TIME_LIMIT, errors="replace", env={**os.environ, "LC_ALL": "C.UTF-8"})
            except subprocess.TimeoutExpired:
                return None, f"Time limit exceeded ({TEST_TIME_LIMIT:g}s) on {label}."
            detail = f"\nInput:\n{clip(t['input'])}\nExpected:\n{clip(t['output'])}\nGot:\n{clip(p.stdout)}" if t["public"] else ""
            if p.returncode != 0:
                return 1, f"Runtime error (exit {p.returncode}) on {label}.\nstderr:\n{clip(p.stderr, 1500)}{detail}"
            if not tokens_equal(p.stdout, t["output"]):
                return 1, f"Wrong answer on {label}.{detail}"
        return 0, f"All {len(tests)} tests passed."


class Python(Stdio):
    name, src = "python", "solution.py"

    def compile_cmd(self, warn):
        return ["python3", "-m", "py_compile", self.src], {}

    def run_cmd(self, ws):
        return ["python3", self.src]

    def extra_diag(self, ws):
        return sh(["pyright", self.src], ws, BUILD_TIMEOUT)[1]


class Rust(Stdio):
    name, src = "rust", "solution.rs"

    def compile_cmd(self, warn):
        return ["rustc", "--edition", "2021", "-O", self.src, "-o", "prog"], {}

    def run_cmd(self, ws):
        return ["./prog"]


class Go(Stdio):
    name, src = "go", "solution.go"

    def compile_cmd(self, warn):
        return ["go", "build", "-o", "prog", self.src], GO_ENV

    def run_cmd(self, ws):
        return ["./prog"]

    def extra_diag(self, ws):
        return sh(["go", "vet", self.src], ws, BUILD_TIMEOUT, GO_ENV)[1]


class Java(Stdio):
    name, src = "java", "Main.java"

    def compile_cmd(self, warn):
        return ["javac", *(["-Xlint:all"] if warn else ["-nowarn"]), "-d", "out", self.src], {}

    def run_cmd(self, ws):
        return ["java", "-cp", "out", "Main"]


class TypeScript(Stdio):
    name, src = "typescript", "solution.ts"
    NM = ENV / "ts" / "node_modules"

    def _link(self, ws):
        nm = Path(ws) / "node_modules"
        if not nm.exists():
            nm.symlink_to(self.NM)

    def build(self, ws, task, worker):
        self._link(ws)
        rc, out = sh(["node", str(ENV / "ts" / "ts_build.mjs"), self.src], ws, BUILD_TIMEOUT)
        return rc == 0, out

    def diagnostics(self, ws, task, worker):
        self._link(ws)
        return sh([str(self.NM / ".bin" / "tsc"), "--noEmit", "--strict", "--target", "ES2020", "--module", "commonjs",
                   "--esModuleInterop", "--skipLibCheck", "--types", "node", self.src], ws, BUILD_TIMEOUT)[1]

    def run_cmd(self, ws):
        return ["node", "--no-warnings", "out/solution.js"]


class Cpp(Stdio):
    name, src = "cpp", "solution.cpp"

    def compile_cmd(self, warn):
        return ["g++", "-std=c++17", "-O2", f"-I{SHIM}", *(["-Wall", "-Wextra"] if warn else []),
                "-o", "prog", self.src], {}

    def run_cmd(self, ws):
        return ["./prog"]


STDIO_RUNNERS = {r.name: r for r in [Python(), Rust(), Go(), Java(), TypeScript(), Cpp()]}
STUBS = {
    "python": "# Read from standard input, write to standard output.\n",
    "rust": "use std::io::{self, Read};\n\nfn main() {\n    let mut input = String::new();\n    io::stdin().read_to_string(&mut input).unwrap();\n}\n",
    "go": "package main\n\nfunc main() {\n}\n",
    "java": "public class Main {\n    public static void main(String[] args) throws Exception {\n    }\n}\n",
    "typescript": "// Read from standard input (e.g. require(\"fs\").readFileSync(0, \"utf8\")), write to standard output.\n",
    "cpp": "#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    return 0;\n}\n",
}
SOLUTION_FILE = {l: r.src for l, r in STDIO_RUNNERS.items()}
