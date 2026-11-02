# langbench: matched-task language comparison for LLM coding agents

Question: holding the problem fixed, how does a model's success rate in Rust, Go, Java, TypeScript
and C++ compare to Python, and how much of any difference comes from compiler / type-checker feedback?

Public benchmarks can't answer this because each language gets different tasks (e.g. Python
from SWE-bench Verified, everything else from SWE-bench Multilingual), so language and task difficulty
are confounded. Here every language gets the **same Exercism exercises** (the 66 practice exercises
shared by all six tracks), each with tests written idiomatically by that track's maintainers.

## Design

| Variable | Levels |
|---|---|
| Language | python, rust, go, java, typescript, cpp |
| Exercise | ~60 shared exercises (after validation) |
| Feedback condition | `tests`: test output only; if the build fails the model is told just "the build failed" |
|  | `diag`: also gets all static diagnostics every turn: rustc, go build + vet, javac -Xlint, g++ -Wall -Wextra, pyright, tsc --strict |
| Turns | up to 4 (1 attempt + 3 repairs) |
| Samples | 3 per cell (default) |

Turn 1 is the same prompt in both conditions, so turn-1 results pooled across conditions give the
single-shot (no feedback) number.

The model sees the instructions, the stub files, and the full un-skipped test files, and replies with
complete files in `<file path="...">` tags. It can only write solution files (or new source files next
to them), never tests or build config.

What the compiler-feedback hypothesis predicts:
- single shot: no special advantage for compiled languages
- `diag` minus `tests` (report section 3): clearly positive for Rust/Java/TS/C++/Go, near zero for Python
- the `language x condition` interaction terms in the mixed model (section 5) carry the formal test

## Run it

```bash
./setup_env.sh                      # deps, JUnit jars, pinned Exercism tracks -> tasks/
python3 validate.py --workers 8     # reference solutions must pass, stubs must fail, in ALL languages
export ANTHROPIC_API_KEY=...
python3 run.py --model claude-haiku-5-5 --slugs anagram clock --samples 1   # pilot: ~24 episodes
python3 run.py --model claude-haiku-5-5 --samples 3 --workers 8             # full sweep, resumable
python3 analyze.py results/claude-haiku-5-5
```

`run.py` appends to `results/<model>/episodes.jsonl` and skips finished episodes, so it's safe to
interrupt and re-run. Full transcripts (every prompt and reply) go to `results/<model>/transcripts/`.
Use `--run-name` to keep variants apart (e.g. a `--max-turns 8` run).

Harness self-test without an API key:

```bash
python3 run.py --model mock:ref --slugs anagram --samples 1            # everything passes on turn 1
python3 run.py --model mock:stub-then-ref --slugs anagram --samples 1  # broken first, passes on turn 2
```

## Harder tasks: competitive programming (stdin/stdout)

The Exercism set saturates on strong models (Haiku passes ~97-99% single-shot in every language), which leaves no
room to see language effects. The harness also runs **stdin/stdout problems** from
[LiveCodeBench](https://livecodebench.github.io) (AtCoder). One problem, one statement, and one hidden test set are shared
by all six languages, so nothing is translated per language.

```bash
python3 prepare_lcb.py --selftest --out tasks_selftest        # 2 tiny problems WITH reference solutions in all 6 languages
python3 validate.py --tasks-dir tasks_selftest                # verifies the six stdin/stdout runners
python3 prepare_lcb.py --after 2025-01-01 --difficulty medium hard --contest-prefix arc agc --out tasks_lcb_arc
python3 validate.py --tasks-dir tasks_lcb_arc
python3 run.py --model claude-haiku-5-5 --tasks-dir tasks_lcb_arc --samples 3 --workers 8
python3 analyze.py results/claude-haiku-5-5-lcb_arc
```

Design notes:
- Tests: every public example plus 25 hidden tests per problem (the 5 largest inputs, so slow languages hit the time
  limit, plus 20 random). Output is compared token by token; numbers containing `.`/`e` match within 1e-6.
- Time limit: 10 s per test for every language (`LANGBENCH_TIME_LIMIT`). No memory limit. Speed differences between
  languages therefore count as part of "language effect" on problems with large inputs.
- Feedback (both conditions): the verdict of the first failing test. Public examples also show input / expected / got;
  hidden tests show only the verdict (wrong answer, runtime error + stderr, time limit).
- Build: python `py_compile`, TypeScript transpile (syntax only), `rustc -O`, `go build`, `javac`, `g++ -O2 -std=c++17`.
  Diagnostics: warnings + pyright / `tsc --strict` / `go vet`. C++ gets a `bits/stdc++.h` shim (`support/cpp`) because
  macOS g++ has none.
- Replies may be long on hard problems: `--max-tokens` defaults to 32000, and a truncated reply counts as a failure.
- LiveCodeBench ships no reference solutions, so `validate.py` only checks that the stub fails. The runners are verified
  by the selftest set instead. Problems no language ever solves carry no information for the comparison.

**Contamination caveat.** The newest public LiveCodeBench release (`test6`) covers contests up to April 2025. A model
whose training data extends past that has probably seen these problems. The `--after` filter does not fix this unless
newer problems are added to the loader. Treat results as "hard problems, possibly seen", and prefer comparisons between
languages over absolute pass rates.

## Size and cost

~60 exercises x 6 languages x 2 conditions x 3 samples = ~2,200 episodes per model, ~1.5 model calls
each on average. Inputs are 2-10k tokens per call (test files dominate), so a full sweep is roughly
5-10M input tokens: cheap on a Haiku-class model, a few hundred dollars on a frontier model.

Power: with ~60 exercises, a single language's `diag - tests` effect has a 95% CI of about +/-9 points
(checked on synthetic data). Raise `--samples` or pool languages by type if you need tighter intervals.

## Known limitations

- Exercism exercises are small, single-file problems that likely appear in training data. This
  measures language effects on well-known problems, not repo-scale engineering.
- `diag` adds information, but also tokens. If `diag` helps, check transcripts to see whether the fixes
  addressed the diagnostics.
- "Build" means different things per language (syntax-only for Python/TS, full compile elsewhere),
  so turn-1 `build_fail` rates aren't directly comparable. That difference is part of what is measured.
- Exercises that need external libraries (e.g. Boost for C++ `gigasecond`) and exercises whose upstream
  reference solution fails its own tests are dropped from every language by `validate.py`.

## Files

- `prepare_tasks.py`: builds `tasks/<exercise>/<lang>/` from the pinned tracks (un-skips all tests)
- `runners.py`: build / diagnostics / test per language
- `validate.py`: writes `tasks/valid.json`, the matched exercise set
- `stdio.py`, `prepare_lcb.py`, `selftest_problems.py`, `support/`: stdin/stdout tasks (LiveCodeBench)
- `run.py`: agent loop, both conditions
- `analyze.py`: Python-normalized indices with bootstrap CIs, diagnostics effect, mixed model -> `report.md`
