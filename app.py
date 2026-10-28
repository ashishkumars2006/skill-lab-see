from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

from flask import (
    Flask, abort, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("JUDGE_DB_PATH", str(BASE_DIR / "instance" / "judge.db")))
SUPPORTED_LANGUAGES = {
    "python": {"label": "Python 3", "image": "python:3.12-slim"},
    "cpp": {"label": "C++17", "image": "gcc:13"},
}
MAX_SOURCE_BYTES = 64 * 1024
MAX_TESTS_PER_PROBLEM = 50
STARTER_CODES = {
    "Sum of Two Numbers": {
        "python": 'def solve():\n    a, b = map(int, input().split())\n    print(a + b)\n\nif __name__ == "__main__":\n    solve()',
        "cpp": '#include <iostream>\nusing namespace std;\n\nint main() {\n    long long a, b;\n    cin >> a >> b;\n    cout << a + b << "\\n";\n    return 0;\n}',
    },
    "Even or Odd": {
        "python": 'n = int(input())\nprint("Even" if n % 2 == 0 else "Odd")',
        "cpp": '#include <iostream>\nusing namespace std;\n\nint main() {\n    long long n;\n    cin >> n;\n    cout << (n % 2 == 0 ? "Even" : "Odd") << "\\n";\n    return 0;\n}',
    },
    "Largest of Three": {
        "python": 'a, b, c = map(int, input().split())\nprint(max(a, b, c))',
        "cpp": '#include <algorithm>\n#include <iostream>\nusing namespace std;\n\nint main() {\n    long long a, b, c;\n    cin >> a >> b >> c;\n    cout << max({a, b, c}) << "\\n";\n    return 0;\n}',
    },

    "Positive, Negative or Zero": {
        "python": 'n = int(input())\nprint("Positive" if n > 0 else "Negative" if n < 0 else "Zero")',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { long long n; cin >> n; cout << (n > 0 ? "Positive" : n < 0 ? "Negative" : "Zero") << "\\n"; }',
    },
    "Count Digits": {
        "python": 'n = int(input())\nprint(len(str(n)))',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { unsigned long long n; cin >> n; int digits = 1; while (n >= 10) { n /= 10; ++digits; } cout << digits << "\\n"; }',
    },
    "Factorial": {
        "python": 'import math\nn = int(input())\nprint(math.factorial(n))',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; unsigned long long result = 1; for (int i = 2; i <= n; ++i) result *= i; cout << result << "\\n"; }',
    },
    "GCD of Two Numbers": {
        "python": 'import math\na, b = map(int, input().split())\nprint(math.gcd(a, b))',
        "cpp": '#include <iostream>\n#include <numeric>\nusing namespace std;\nint main() { long long a, b; cin >> a >> b; cout << gcd(a, b) << "\\n"; }',
    },
    "Palindrome Number": {
        "python": 'n = input().strip()\nprint("Palindrome" if n == n[::-1] else "Not Palindrome")',
        "cpp": '#include <algorithm>\n#include <iostream>\n#include <string>\nusing namespace std;\nint main() { string n; cin >> n; string r = n; reverse(r.begin(), r.end()); cout << (n == r ? "Palindrome" : "Not Palindrome") << "\\n"; }',
    },
    "Reverse a String": {
        "python": 'word = input().strip()\nprint(word[::-1])',
        "cpp": '#include <algorithm>\n#include <iostream>\n#include <string>\nusing namespace std;\nint main() { string word; cin >> word; reverse(word.begin(), word.end()); cout << word << "\\n"; }',
    },
    "Leap Year": {
        "python": 'year = int(input())\nleap = year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)\nprint("Leap Year" if leap else "Not Leap Year")',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { int year; cin >> year; bool leap = year % 400 == 0 || (year % 4 == 0 && year % 100 != 0); cout << (leap ? "Leap Year" : "Not Leap Year") << "\\n"; }',
    },
    "FizzBuzz": {
        "python": 'n = int(input())\nfor i in range(1, n + 1):\n    if i % 15 == 0: print("FizzBuzz")\n    elif i % 3 == 0: print("Fizz")\n    elif i % 5 == 0: print("Buzz")\n    else: print(i)',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; for (int i = 1; i <= n; ++i) { if (i % 15 == 0) cout << "FizzBuzz"; else if (i % 3 == 0) cout << "Fizz"; else if (i % 5 == 0) cout << "Buzz"; else cout << i; cout << "\\n"; } }',
    },
    "Sum of an Array": {
        "python": 'n = int(input())\nvalues = list(map(int, input().split()))\nprint(sum(values))',
        "cpp": '#include <iostream>\nusing namespace std;\nint main() { int n; cin >> n; long long sum = 0, x; for (int i = 0; i < n; ++i) { cin >> x; sum += x; } cout << sum << "\\n"; }',
    },
    "Second Largest Distinct": {
        "python": 'n = int(input())\nvalues = list(map(int, input().split()))\nunique = sorted(set(values), reverse=True)\nprint(unique[1] if len(unique) >= 2 else "NO")',
        "cpp": '#include <algorithm>\n#include <iostream>\n#include <set>\nusing namespace std;\nint main() { int n; cin >> n; set<long long> values; for (int i=0; i<n; ++i) { long long x; cin >> x; values.insert(x); } if (values.size() < 2) cout << "NO\\n"; else { auto it = values.rbegin(); ++it; cout << *it << "\\n"; } }',
    },
    "Prime Number": {
        "python": 'import math\nn = int(input())\nif n < 2:\n    print("Not Prime")\nelse:\n    print("Prime" if all(n % d for d in range(2, math.isqrt(n) + 1)) else "Not Prime")',
        "cpp": '#include <cmath>\n#include <iostream>\nusing namespace std;\nint main() { long long n; cin >> n; bool prime = n >= 2; for (long long d = 2; prime && d <= sqrt((long double)n); ++d) if (n % d == 0) prime = false; cout << (prime ? "Prime" : "Not Prime") << "\\n"; }',
    },
}
GENERIC_STARTERS = {
    "python": '# Read input from stdin, then print the answer.',
    "cpp": '#include <iostream>\nusing namespace std;\n\nint main() {\n    // Read input and print your answer.\n    return 0;\n}',
}
_init_lock = threading.Lock()
_initialized = False

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=256 * 1024,
)


def connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed_demo_data(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("admin", generate_password_hash(os.environ.get("JUDGE_ADMIN_PASSWORD", "Admin123!")), "admin"),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            ("alice", generate_password_hash("Alice123!"), "participant"),
        )

    if conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 0:
        demo_problems = [
            {
                "title": "Sum of Two Numbers", "difficulty": "Easy",
                "statement": "Read two integers and print their sum.\n\nYour program should print only the resulting integer.",
                "input_format": "Two integers a and b, separated by whitespace.",
                "output_format": "Print a + b.",
                "constraints": "-10^9 ≤ a, b ≤ 10^9",
                "time_limit_ms": 1000, "memory_limit_mb": 128,
                "sample": ("2 3\n", "5\n"),
                "hidden": [("10 20\n", "30\n"), ("-4 9\n", "5\n"), ("0 0\n", "0\n"), ("-1000000000 1\n", "-999999999\n")],
            },
            {
                "title": "Even or Odd", "difficulty": "Easy",
                "statement": "Given an integer n, print `Even` if it is divisible by 2; otherwise print `Odd`.",
                "input_format": "A single integer n.", "output_format": "Print exactly `Even` or `Odd`.",
                "constraints": "-10^9 ≤ n ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
                "sample": ("7\n", "Odd\n"),
                "hidden": [("2\n", "Even\n"), ("0\n", "Even\n"), ("-11\n", "Odd\n"), ("-8\n", "Even\n")],
            },
            {
                "title": "Largest of Three", "difficulty": "Medium",
                "statement": "Read three integers and print the largest value. Values may be equal or negative.",
                "input_format": "Three integers a, b and c, separated by whitespace.",
                "output_format": "Print the maximum of the three integers.",
                "constraints": "-10^9 ≤ a, b, c ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
                "sample": ("4 9 2\n", "9\n"),
                "hidden": [("-1 -5 -3\n", "-1\n"), ("6 6 2\n", "6\n"), ("0 0 0\n", "0\n"), ("100 99 101\n", "101\n")],
            },
        ]
        for p in demo_problems:
            cur = conn.execute(
                """INSERT INTO problems
                   (title, difficulty, statement, input_format, output_format, constraints_text,
                    time_limit_ms, memory_limit_mb, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (p["title"], p["difficulty"], p["statement"], p["input_format"], p["output_format"],
                 p["constraints"], p["time_limit_ms"], p["memory_limit_mb"]),
            )
            problem_id = cur.lastrowid
            conn.execute(
                "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 1, 0)",
                (problem_id, p["sample"][0], p["sample"][1]),
            )
            for idx, (input_data, expected_output) in enumerate(p["hidden"], start=1):
                conn.execute(
                    "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 0, ?)",
                    (problem_id, input_data, expected_output, idx),
                )



def _seed_additional_problems(conn: sqlite3.Connection) -> None:
    """Add the extra practice set idempotently, including for existing databases."""
    additional = [
        {
            "title": "Positive, Negative or Zero", "difficulty": "Easy",
            "statement": "Given an integer n, print `Positive` if n is greater than zero, `Negative` if it is less than zero, or `Zero` otherwise.",
            "input_format": "A single integer n.", "output_format": "Print exactly Positive, Negative, or Zero.",
            "constraints": "-10^9 ≤ n ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("0\n", "Zero\n"),
            "hidden": [("8\n", "Positive\n"), ("-3\n", "Negative\n"), ("1\n", "Positive\n"), ("-999999\n", "Negative\n")],
        },
        {
            "title": "Count Digits", "difficulty": "Easy",
            "statement": "Given a non-negative integer n, print how many decimal digits it contains. The number 0 has one digit.",
            "input_format": "A single non-negative integer n.", "output_format": "Print the number of decimal digits in n.",
            "constraints": "0 ≤ n ≤ 10^18", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("2026\n", "4\n"),
            "hidden": [("0\n", "1\n"), ("7\n", "1\n"), ("100000\n", "6\n"), ("999999999999999999\n", "18\n")],
        },
        {
            "title": "Factorial", "difficulty": "Easy",
            "statement": "Given an integer n, print n factorial. By definition, 0! = 1.",
            "input_format": "A single integer n.", "output_format": "Print n! as an integer.",
            "constraints": "0 ≤ n ≤ 20", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("5\n", "120\n"),
            "hidden": [("0\n", "1\n"), ("1\n", "1\n"), ("10\n", "3628800\n"), ("20\n", "2432902008176640000\n"), ("3\n", "6\n")],
        },
        {
            "title": "GCD of Two Numbers", "difficulty": "Easy",
            "statement": "Given two integers a and b, print their greatest common divisor. The result must be non-negative; define gcd(0, 0) as 0 for this problem.",
            "input_format": "Two integers a and b separated by whitespace.", "output_format": "Print the non-negative greatest common divisor of a and b.",
            "constraints": "-10^9 ≤ a, b ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("12 18\n", "6\n"),
            "hidden": [("7 0\n", "7\n"), ("-24 18\n", "6\n"), ("0 0\n", "0\n"), ("1000000000 250000000\n", "250000000\n")],
        },
        {
            "title": "Palindrome Number", "difficulty": "Easy",
            "statement": "Given a non-negative integer n, print `Palindrome` if its decimal representation reads the same forward and backward; otherwise print `Not Palindrome`.",
            "input_format": "A single non-negative integer n.", "output_format": "Print exactly Palindrome or Not Palindrome.",
            "constraints": "0 ≤ n ≤ 10^18", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("121\n", "Palindrome\n"),
            "hidden": [("10\n", "Not Palindrome\n"), ("0\n", "Palindrome\n"), ("1221\n", "Palindrome\n"), ("1001\n", "Palindrome\n"), ("123\n", "Not Palindrome\n")],
        },
        {
            "title": "Reverse a String", "difficulty": "Easy",
            "statement": "Given a single word, print its characters in reverse order. The input contains only lowercase English letters and has no spaces.",
            "input_format": "A single lowercase word.", "output_format": "Print the reversed word.",
            "constraints": "1 ≤ length of word ≤ 1000", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("code\n", "edoc\n"),
            "hidden": [("a\n", "a\n"), ("hello\n", "olleh\n"), ("racecar\n", "racecar\n"), ("abc\n", "cba\n")],
        },
        {
            "title": "Leap Year", "difficulty": "Easy",
            "statement": "Given a year, print `Leap Year` if it is a leap year in the Gregorian calendar; otherwise print `Not Leap Year`. A year is a leap year if it is divisible by 400, or divisible by 4 but not by 100.",
            "input_format": "A single positive integer year.", "output_format": "Print exactly Leap Year or Not Leap Year.",
            "constraints": "1 ≤ year ≤ 9999", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("2024\n", "Leap Year\n"),
            "hidden": [("1900\n", "Not Leap Year\n"), ("2000\n", "Leap Year\n"), ("2023\n", "Not Leap Year\n"), ("2400\n", "Leap Year\n")],
        },
        {
            "title": "FizzBuzz", "difficulty": "Medium",
            "statement": "For every integer from 1 through n, print `FizzBuzz` if it is divisible by both 3 and 5, `Fizz` if only divisible by 3, `Buzz` if only divisible by 5, and the number otherwise. Print one item per line.",
            "input_format": "A single integer n.", "output_format": "Print the FizzBuzz sequence from 1 to n, one item per line.",
            "constraints": "1 ≤ n ≤ 100", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("5\n", "1\n2\nFizz\n4\nBuzz\n"),
            "hidden": [("1\n", "1\n"), ("3\n", "1\n2\nFizz\n"), ("15\n", "1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz\n"), ("7\n", "1\n2\nFizz\n4\nBuzz\nFizz\n7\n")],
        },
        {
            "title": "Sum of an Array", "difficulty": "Easy",
            "statement": "Read n integers and print their sum.",
            "input_format": "The first input is n. The next line contains n integers.", "output_format": "Print the sum of all n integers.",
            "constraints": "1 ≤ n ≤ 1000; -10^9 ≤ each element ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("5\n1 2 3 4 5\n", "15\n"),
            "hidden": [("1\n42\n", "42\n"), ("4\n-1 -2 -3 -4\n", "-10\n"), ("3\n0 0 0\n", "0\n"), ("6\n10 -10 20 -20 5 5\n", "10\n")],
        },
        {
            "title": "Second Largest Distinct", "difficulty": "Medium",
            "statement": "Given an array, print its second-largest distinct value. If the array contains fewer than two distinct values, print `NO`.",
            "input_format": "The first integer is n. The next line contains n integers.", "output_format": "Print the second-largest distinct value, or NO if it does not exist.",
            "constraints": "1 ≤ n ≤ 1000; -10^9 ≤ each element ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("5\n3 1 4 4 2\n", "3\n"),
            "hidden": [("1\n7\n", "NO\n"), ("3\n5 5 5\n", "NO\n"), ("2\n1 2\n", "1\n"), ("3\n-1 -3 -2\n", "-2\n"), ("5\n9 8 7 9 8\n", "8\n")],
        },
        {
            "title": "Prime Number", "difficulty": "Easy",
            "statement": "Given an integer n, print `Prime` if n is a prime number; otherwise print `Not Prime`. Numbers less than 2 are not prime.",
            "input_format": "A single integer n.", "output_format": "Print exactly Prime or Not Prime.",
            "constraints": "0 ≤ n ≤ 10^9", "time_limit_ms": 1000, "memory_limit_mb": 128,
            "sample": ("7\n", "Prime\n"),
            "hidden": [("2\n", "Prime\n"), ("1\n", "Not Prime\n"), ("0\n", "Not Prime\n"), ("97\n", "Prime\n"), ("100\n", "Not Prime\n"), ("99991\n", "Prime\n")],
        },
    ]
    for problem in additional:
        exists = conn.execute("SELECT 1 FROM problems WHERE title = ? LIMIT 1", (problem["title"],)).fetchone()
        if exists:
            continue
        cur = conn.execute(
            """INSERT INTO problems
               (title, difficulty, statement, input_format, output_format, constraints_text,
                time_limit_ms, memory_limit_mb, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (problem["title"], problem["difficulty"], problem["statement"], problem["input_format"],
             problem["output_format"], problem["constraints"], problem["time_limit_ms"], problem["memory_limit_mb"]),
        )
        problem_id = cur.lastrowid
        conn.execute(
            "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 1, 0)",
            (problem_id, problem["sample"][0], problem["sample"][1]),
        )
        for idx, (input_data, expected_output) in enumerate(problem["hidden"], start=1):
            conn.execute(
                "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 0, ?)",
                (problem_id, input_data, expected_output, idx),
            )


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        conn = connect_db()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'participant' CHECK(role IN ('admin','participant')),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE TABLE IF NOT EXISTS problems (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    difficulty TEXT NOT NULL DEFAULT 'Easy' CHECK(difficulty IN ('Easy','Medium','Hard')),
                    statement TEXT NOT NULL,
                    input_format TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    constraints_text TEXT NOT NULL DEFAULT '',
                    time_limit_ms INTEGER NOT NULL DEFAULT 1000,
                    memory_limit_mb INTEGER NOT NULL DEFAULT 128,
                    is_published INTEGER NOT NULL DEFAULT 1,
                    created_by INTEGER REFERENCES users(id),
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE TABLE IF NOT EXISTS test_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
                    input_data TEXT NOT NULL,
                    expected_output TEXT NOT NULL,
                    is_sample INTEGER NOT NULL DEFAULT 0,
                    sequence_no INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    problem_id INTEGER NOT NULL REFERENCES problems(id),
                    language TEXT NOT NULL CHECK(language IN ('python','cpp')),
                    source_code TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    started_at TEXT,
                    finished_at TEXT,
                    execution_time_ms INTEGER,
                    memory_used_mb REAL,
                    passed_tests INTEGER NOT NULL DEFAULT 0,
                    total_tests INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_submissions_status_created ON submissions(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_submissions_user_created ON submissions(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tests_problem_order ON test_cases(problem_id, is_sample DESC, sequence_no);
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            _seed_demo_data(conn)
            _seed_additional_problems(conn)
            conn.commit()
            _initialized = True
        finally:
            conn.close()


@app.before_request
def load_user_and_check_csrf() -> None:
    init_db()
    user_id = session.get("user_id")
    g.user = None
    if user_id:
        conn = connect_db()
        try:
            g.user = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            conn.close()
        if g.user is None:
            session.clear()

    if request.method == "POST":
        supplied = request.form.get("_csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            abort(400, description="Your form session expired. Refresh the page and try again.")


@app.context_processor
def inject_helpers() -> dict[str, Any]:
    def csrf_input() -> Markup:
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return Markup(f'<input type="hidden" name="_csrf_token" value="{escape(token)}">')

    return {
        "csrf_input": csrf_input,
        "language_labels": {key: value["label"] for key, value in SUPPORTED_LANGUAGES.items()},
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


@app.get("/")
def index():
    conn = connect_db()
    try:
        problems = conn.execute(
            """SELECT p.*, (SELECT COUNT(*) FROM test_cases t WHERE t.problem_id=p.id AND t.is_sample=1) AS sample_count,
                      (SELECT COUNT(*) FROM submissions s WHERE s.problem_id=p.id AND s.status='AC') AS accepted_count
               FROM problems p WHERE p.is_published=1 ORDER BY p.id"""
        ).fetchall()
        stats = {
            "problems": conn.execute("SELECT COUNT(*) FROM problems WHERE is_published=1").fetchone()[0],
            "submissions": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
            "solved": conn.execute("SELECT COUNT(DISTINCT user_id || ':' || problem_id) FROM submissions WHERE status='AC'").fetchone()[0],
        }
    finally:
        conn.close()
    return render_template("index.html", problems=problems, stats=stats)


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "online-coding-judge"})


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = connect_db()
        try:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        finally:
            conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["_csrf_token"] = secrets.token_urlsafe(32)
            flash(f"Welcome back, {user['username']}.", "success")
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("index"))
        flash("Username or password is incorrect.", "error")
    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if g.user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_]{3,24}", username):
            flash("Username must be 3–24 characters and use only letters, numbers, or underscores.", "error")
        elif len(password) < 8:
            flash("Password must contain at least 8 characters.", "error")
        else:
            conn = connect_db()
            try:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'participant')",
                    (username, generate_password_hash(password)),
                )
                conn.commit()
                user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
            else:
                session.clear()
                session["user_id"] = user["id"]
                session["_csrf_token"] = secrets.token_urlsafe(32)
                flash("Your participant account is ready.", "success")
                return redirect(url_for("index"))
            finally:
                conn.close()
    return render_template("register.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "info")
    return redirect(url_for("index"))


@app.get("/problems/<int:problem_id>")
def problem_detail(problem_id: int):
    conn = connect_db()
    try:
        problem = conn.execute("SELECT * FROM problems WHERE id=? AND is_published=1", (problem_id,)).fetchone()
        if not problem:
            abort(404)
        samples = conn.execute(
            "SELECT input_data, expected_output FROM test_cases WHERE problem_id=? AND is_sample=1 ORDER BY sequence_no, id",
            (problem_id,),
        ).fetchall()
        accepted = conn.execute("SELECT COUNT(DISTINCT user_id) FROM submissions WHERE problem_id=? AND status='AC'", (problem_id,)).fetchone()[0]
        my_latest = None
        if g.user:
            my_latest = conn.execute(
                "SELECT id, status, created_at FROM submissions WHERE user_id=? AND problem_id=? ORDER BY id DESC LIMIT 1",
                (g.user["id"], problem_id),
            ).fetchone()
    finally:
        conn.close()
    starter_codes = STARTER_CODES.get(problem["title"], GENERIC_STARTERS)
    return render_template("problem.html", problem=problem, samples=samples, accepted=accepted,
                           my_latest=my_latest, starter_codes=starter_codes)


@app.post("/problems/<int:problem_id>/submit")
@login_required
def submit_problem(problem_id: int):
    language = request.form.get("language", "")
    source_code = request.form.get("source_code", "")
    if language not in SUPPORTED_LANGUAGES:
        flash("Choose one of the supported languages.", "error")
        return redirect(url_for("problem_detail", problem_id=problem_id))
    if not source_code.strip():
        flash("Paste your solution before submitting.", "error")
        return redirect(url_for("problem_detail", problem_id=problem_id))
    if len(source_code.encode("utf-8")) > MAX_SOURCE_BYTES:
        flash("Source code must be 64 KB or smaller.", "error")
        return redirect(url_for("problem_detail", problem_id=problem_id))

    conn = connect_db()
    try:
        exists = conn.execute("SELECT id FROM problems WHERE id=? AND is_published=1", (problem_id,)).fetchone()
        if not exists:
            abort(404)
        cur = conn.execute(
            "INSERT INTO submissions (user_id, problem_id, language, source_code, status) VALUES (?, ?, ?, ?, 'QUEUED')",
            (g.user["id"], problem_id, language, source_code),
        )
        conn.commit()
        submission_id = cur.lastrowid
    finally:
        conn.close()
    flash("Submission queued. The judge will update its status automatically.", "success")
    return redirect(url_for("submission_detail", submission_id=submission_id))


@app.get("/submissions")
@login_required
def submissions():
    conn = connect_db()
    try:
        if g.user["role"] == "admin" and request.args.get("all") == "1":
            rows = conn.execute(
                """SELECT s.*, u.username, p.title AS problem_title FROM submissions s
                   JOIN users u ON u.id=s.user_id JOIN problems p ON p.id=s.problem_id
                   ORDER BY s.id DESC LIMIT 150"""
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.*, u.username, p.title AS problem_title FROM submissions s
                   JOIN users u ON u.id=s.user_id JOIN problems p ON p.id=s.problem_id
                   WHERE s.user_id=? ORDER BY s.id DESC LIMIT 100""", (g.user["id"],)
            ).fetchall()
    finally:
        conn.close()
    return render_template("submissions.html", submissions=rows, show_all=(g.user["role"] == "admin" and request.args.get("all") == "1"))


@app.get("/submissions/<int:submission_id>")
@login_required
def submission_detail(submission_id: int):
    conn = connect_db()
    try:
        submission = conn.execute(
            """SELECT s.*, u.username, p.title AS problem_title, p.time_limit_ms, p.memory_limit_mb
               FROM submissions s JOIN users u ON u.id=s.user_id JOIN problems p ON p.id=s.problem_id
               WHERE s.id=?""", (submission_id,)
        ).fetchone()
        if not submission:
            abort(404)
        if submission["user_id"] != g.user["id"] and g.user["role"] != "admin":
            abort(403)
    finally:
        conn.close()
    return render_template("submission_detail.html", submission=submission, is_terminal=submission["status"] not in ("QUEUED", "RUNNING"))


@app.get("/api/submissions/<int:submission_id>/status")
@login_required
def submission_status(submission_id: int):
    conn = connect_db()
    try:
        submission = conn.execute(
            "SELECT id, user_id, status, passed_tests, total_tests, execution_time_ms, memory_used_mb, error_message, finished_at FROM submissions WHERE id=?",
            (submission_id,),
        ).fetchone()
        if not submission:
            abort(404)
        if submission["user_id"] != g.user["id"] and g.user["role"] != "admin":
            abort(403)
        return jsonify(dict(submission))
    finally:
        conn.close()


@app.get("/leaderboard")
def leaderboard():
    conn = connect_db()
    try:
        rows = conn.execute(
            """SELECT u.id, u.username,
                      COUNT(DISTINCT CASE WHEN s.status='AC' THEN s.problem_id END) AS solved,
                      COUNT(CASE WHEN s.status='AC' THEN 1 END) AS accepted_submissions,
                      MAX(CASE WHEN s.status='AC' THEN s.finished_at END) AS last_accepted
               FROM users u LEFT JOIN submissions s ON s.user_id=u.id
               WHERE u.role='participant'
               GROUP BY u.id, u.username
               ORDER BY solved DESC, last_accepted ASC, u.username COLLATE NOCASE ASC"""
        ).fetchall()
    finally:
        conn.close()
    return render_template("leaderboard.html", rows=rows)


@app.get("/admin")
@admin_required
def admin_dashboard():
    conn = connect_db()
    try:
        stats = {
            "problems": conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0],
            "submissions": conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0],
            "users": conn.execute("SELECT COUNT(*) FROM users WHERE role='participant'").fetchone()[0],
            "queued": conn.execute("SELECT COUNT(*) FROM submissions WHERE status='QUEUED'").fetchone()[0],
        }
        problems = conn.execute(
            """SELECT p.*, COUNT(DISTINCT t.id) AS test_count,
                      COUNT(DISTINCT CASE WHEN s.status='AC' THEN s.user_id END) AS solved_by
               FROM problems p LEFT JOIN test_cases t ON t.problem_id=p.id
               LEFT JOIN submissions s ON s.problem_id=p.id
               GROUP BY p.id ORDER BY p.id DESC"""
        ).fetchall()
        recent = conn.execute(
            """SELECT s.id, s.status, s.language, s.created_at, u.username, p.title AS problem_title
               FROM submissions s JOIN users u ON u.id=s.user_id JOIN problems p ON p.id=s.problem_id
               ORDER BY s.id DESC LIMIT 8"""
        ).fetchall()
    finally:
        conn.close()
    return render_template("admin.html", stats=stats, problems=problems, recent=recent)


@app.route("/admin/problems/new", methods=["GET", "POST"])
@admin_required
def create_problem():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        difficulty = request.form.get("difficulty", "Easy")
        statement = request.form.get("statement", "").strip()
        input_format = request.form.get("input_format", "").strip()
        output_format = request.form.get("output_format", "").strip()
        constraints_text = request.form.get("constraints_text", "").strip()
        raw_hidden = request.form.get("hidden_tests_json", "").strip()
        try:
            time_limit_ms = int(request.form.get("time_limit_ms", "1000"))
            memory_limit_mb = int(request.form.get("memory_limit_mb", "128"))
            hidden_tests = json.loads(raw_hidden)
            sample_input = request.form.get("sample_input", "")
            sample_output = request.form.get("sample_output", "")
            if not isinstance(hidden_tests, list):
                raise ValueError("Hidden tests must be a JSON array.")
            if not 1 <= len(hidden_tests) <= MAX_TESTS_PER_PROBLEM - 1:
                raise ValueError(f"Add between 1 and {MAX_TESTS_PER_PROBLEM - 1} hidden tests.")
            for case in hidden_tests:
                if not isinstance(case, dict) or not isinstance(case.get("input"), str) or not isinstance(case.get("output"), str):
                    raise ValueError('Each hidden test must look like {"input": "...", "output": "..."}.')
                if len(case["input"]) > 16_384 or len(case["output"]) > 16_384:
                    raise ValueError("Each test input/output must be 16 KB or smaller.")
            if not title or len(title) > 120 or not statement or not input_format or not output_format:
                raise ValueError("Fill in the title, statement, input format, and output format.")
            if difficulty not in ("Easy", "Medium", "Hard"):
                raise ValueError("Choose a valid difficulty.")
            if not 100 <= time_limit_ms <= 5000:
                raise ValueError("Time limit must be between 100 and 5000 milliseconds.")
            if not 32 <= memory_limit_mb <= 512:
                raise ValueError("Memory limit must be between 32 and 512 MB.")
            if len(sample_input) > 16_384 or len(sample_output) > 16_384:
                raise ValueError("Sample input/output must be 16 KB or smaller.")
        except (ValueError, json.JSONDecodeError) as exc:
            flash(f"Problem not created: {exc}", "error")
        else:
            conn = connect_db()
            try:
                cur = conn.execute(
                    """INSERT INTO problems (title, difficulty, statement, input_format, output_format,
                       constraints_text, time_limit_ms, memory_limit_mb, created_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (title, difficulty, statement, input_format, output_format, constraints_text,
                     time_limit_ms, memory_limit_mb, g.user["id"]),
                )
                problem_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 1, 0)",
                    (problem_id, sample_input, sample_output),
                )
                for idx, case in enumerate(hidden_tests, start=1):
                    conn.execute(
                        "INSERT INTO test_cases (problem_id, input_data, expected_output, is_sample, sequence_no) VALUES (?, ?, ?, 0, ?)",
                        (problem_id, case["input"], case["output"], idx),
                    )
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()
            flash("Problem published with hidden tests. It is now visible to participants.", "success")
            return redirect(url_for("problem_detail", problem_id=problem_id))
    return render_template("problem_form.html")


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "Bad request.")), 400


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403, message="You do not have permission to access this page."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="The page you are looking for does not exist."), 404


@app.errorhandler(413)
def too_large(_error):
    return render_template("error.html", code=413, message="This request is too large."), 413


# Kept import-safe so the worker can reuse the app module.
if __name__ == "__main__":
    init_db()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=False)
