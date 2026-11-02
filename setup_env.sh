#!/usr/bin/env bash
# One-time setup. Needs: python3 (3.10+), node 18+, go, rustc/cargo, a JDK (17+), g++ (C++17).
# Optional: Boost (e.g. `brew install boost` / `apt install libboost-date-time-dev`) keeps the
# gigasecond exercise; without it the validator drops that exercise from every language.
set -euo pipefail
cd "$(dirname "$0")"

missing=()
for t in python3 node npm go cargo javac java g++; do command -v "$t" >/dev/null || missing+=("$t"); done
if ((${#missing[@]})); then echo "missing tools: ${missing[*]}"; exit 1; fi

echo "== python deps"
python3 -m pip install -q -r requirements.txt

echo "== TypeScript test deps (shared by all TS exercises)"
(cd env/ts && npm install --silent --no-audit --no-fund)

echo "== JUnit + AssertJ jars"
mkdir -p env/java
M=https://repo1.maven.org/maven2
for u in \
  org/junit/platform/junit-platform-console-standalone/1.10.0/junit-platform-console-standalone-1.10.0.jar \
  org/assertj/assertj-core/3.25.1/assertj-core-3.25.1.jar \
  net/bytebuddy/byte-buddy/1.14.11/byte-buddy-1.14.11.jar; do
  f="env/java/$(basename "$u")"
  [ -f "$f" ] || curl -sSfL -o "$f" "$M/$u"
done

if [ ! -d tasks ]; then
  echo "== cloning Exercism tracks and building tasks/"
  mkdir -p tracks
  # pinned to the commits the harness was validated against (tracks.lock)
  while read -r l sha; do
    [ -d "tracks/$l" ] && continue
    git init -q "tracks/$l"
    git -C "tracks/$l" fetch -q --depth 1 "https://github.com/exercism/$l.git" "$sha"
    git -C "tracks/$l" checkout -q FETCH_HEAD
  done < tracks.lock
  python3 prepare_tasks.py --tracks tracks --out tasks
fi

echo "done. next: python3 validate.py"
