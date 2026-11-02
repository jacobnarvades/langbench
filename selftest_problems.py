"""Tiny problems with reference solutions in all six languages. Used only to check the stdin/stdout
runners (python prepare_lcb.py --selftest --out tasks_selftest; python validate.py --tasks-dir tasks_selftest)."""
import random

_r = random.Random(1)


def _sum_tests():
    t = [{"input": "3\n1 2 3\n", "output": "6\n", "public": True},
         {"input": "1\n-5\n", "output": "-5\n", "public": False}]
    big = [_r.randint(-10**9, 10**9) for _ in range(200000)]
    t.append({"input": f"{len(big)}\n{' '.join(map(str, big))}\n", "output": f"{sum(big)}\n", "public": False})
    return t


def _avg_tests():
    return [{"input": "3\n1 2 4\n", "output": "2.333333\n", "public": True},
            {"input": "2\n10 20\n", "output": "15.000000\n", "public": False}]


PROBLEMS = {
    "selftest-sum": {
        "statement": "# Sum\n\nGiven N and N integers A_1..A_N (|A_i| <= 10^9, N <= 2*10^5), print their sum.\n\nInput: N on the first line, then the N integers on the second line.",
        "tests": _sum_tests(),
        "reference": {
            "python": "import sys\nd = sys.stdin.read().split()\nprint(sum(map(int, d[1:1 + int(d[0])])))\n",
            "rust": "use std::io::*;\nfn main() {\n    let mut s = String::new();\n    stdin().read_to_string(&mut s).unwrap();\n    let mut it = s.split_whitespace();\n    let n: usize = it.next().unwrap().parse().unwrap();\n    let t: i64 = it.take(n).map(|x| x.parse::<i64>().unwrap()).sum();\n    println!(\"{}\", t);\n}\n",
            "go": "package main\n\nimport (\n\t\"bufio\"\n\t\"fmt\"\n\t\"os\"\n)\n\nfunc main() {\n\tr := bufio.NewReader(os.Stdin)\n\tvar n int\n\tfmt.Fscan(r, &n)\n\tvar t, x int64\n\tfor i := 0; i < n; i++ {\n\t\tfmt.Fscan(r, &x)\n\t\tt += x\n\t}\n\tfmt.Println(t)\n}\n",
            "java": "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n    public static void main(String[] args) throws Exception {\n        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));\n        int n = Integer.parseInt(br.readLine().trim());\n        StringTokenizer st = new StringTokenizer(br.readLine());\n        long t = 0;\n        for (int i = 0; i < n; i++) t += Long.parseLong(st.nextToken());\n        System.out.println(t);\n    }\n}\n",
            "typescript": "const d = (require(\"fs\").readFileSync(0, \"utf8\") as string).split(/\\s+/).filter(Boolean).map(Number);\nlet t = 0;\nfor (let i = 1; i <= d[0]; i++) t += d[i];\nconsole.log(String(t));\n",
            "cpp": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    int n;\n    scanf(\"%d\", &n);\n    long long t = 0, x;\n    while (n--) { scanf(\"%lld\", &x); t += x; }\n    printf(\"%lld\\n\", t);\n}\n",
        },
    },
    "selftest-average": {
        "statement": "# Average\n\nGiven N and N integers, print their average with exactly 6 digits after the decimal point.\n\nInput: N on the first line, then the N integers on the second line.",
        "tests": _avg_tests(),
        "reference": {
            "python": "import sys\nd = sys.stdin.read().split()\nn = int(d[0])\nprint(f\"{sum(map(int, d[1:1 + n])) / n:.6f}\")\n",
            "rust": "use std::io::*;\nfn main() {\n    let mut s = String::new();\n    stdin().read_to_string(&mut s).unwrap();\n    let mut it = s.split_whitespace();\n    let n: usize = it.next().unwrap().parse().unwrap();\n    let t: f64 = it.take(n).map(|x| x.parse::<f64>().unwrap()).sum();\n    println!(\"{:.6}\", t / n as f64);\n}\n",
            "go": "package main\n\nimport \"fmt\"\n\nfunc main() {\n\tvar n int\n\tfmt.Scan(&n)\n\tt := 0.0\n\tfor i := 0; i < n; i++ {\n\t\tvar x float64\n\t\tfmt.Scan(&x)\n\t\tt += x\n\t}\n\tfmt.Printf(\"%.6f\\n\", t/float64(n))\n}\n",
            "java": "import java.util.*;\n\npublic class Main {\n    public static void main(String[] args) {\n        Scanner sc = new Scanner(System.in);\n        int n = sc.nextInt();\n        double t = 0;\n        for (int i = 0; i < n; i++) t += sc.nextInt();\n        System.out.printf(\"%.6f%n\", t / n);\n    }\n}\n",
            "typescript": "const d = (require(\"fs\").readFileSync(0, \"utf8\") as string).split(/\\s+/).filter(Boolean).map(Number);\nlet t = 0;\nfor (let i = 1; i <= d[0]; i++) t += d[i];\nconsole.log((t / d[0]).toFixed(6));\n",
            "cpp": "#include <bits/stdc++.h>\nusing namespace std;\nint main() {\n    int n;\n    cin >> n;\n    double t = 0;\n    for (int i = 0, x; i < n; i++) { cin >> x; t += x; }\n    printf(\"%.6f\\n\", t / n);\n}\n",
        },
    },
}
