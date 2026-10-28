from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

TEST_ROOT = Path(__file__).resolve().parent / ".test-instance"
shutil.rmtree(TEST_ROOT, ignore_errors=True)
TEST_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["JUDGE_DB_PATH"] = str(TEST_ROOT / f"judge-{os.getpid()}.db")
os.environ["SECRET_KEY"] = "test-secret-key"

from app import app as flask_app, connect_db, init_db  # noqa: E402
from judge_engine import ExecutionResult, judge_submission, normalize_output  # noqa: E402


@pytest.fixture(autouse=True)
def clean_submissions():
    init_db()
    conn = connect_db()
    try:
        conn.execute("DELETE FROM submissions")
        conn.commit()
    finally:
        conn.close()
    yield


@pytest.fixture
def client():
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.test_client() as client:
        yield client


def csrf_token(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', response.data)
    assert match, f"CSRF token not found on {path}"
    return match.group(1).decode()


def login(client, username="alice", password="Alice123!"):
    token = csrf_token(client, "/login")
    response = client.post("/login", data={"_csrf_token": token, "username": username, "password": password})
    assert response.status_code == 302


def insert_submission(problem_id=1, language="python", source="print(1)"):
    conn = connect_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()
        cur = conn.execute(
            "INSERT INTO submissions (user_id, problem_id, language, source_code, status) VALUES (?, ?, ?, ?, 'QUEUED')",
            (user["id"], problem_id, language, source),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def submission_row(submission_id):
    conn = connect_db()
    try:
        return dict(conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone())
    finally:
        conn.close()


class FakeRunner:
    def __init__(self, outputs=None, timeout_on=None, runtime_error_on=None, compile_error=False):
        self.outputs = outputs or {}
        self.timeout_on = timeout_on
        self.runtime_error_on = runtime_error_on
        self.compile_error = compile_error
        self.calls = []

    def compile_python(self, source_file):
        if self.compile_error:
            return ExecutionResult(1, "", "SyntaxError: invalid syntax", 2)
        return ExecutionResult(0, "", "", 2)

    def compile_cpp(self, source_file, build_dir):
        if self.compile_error:
            return ExecutionResult(1, "", "main.cpp: error: expected ';'", 5)
        build_dir.mkdir(parents=True, exist_ok=True)
        binary = build_dir / "main"
        binary.write_text("fake binary")
        return ExecutionResult(0, "", "", 5)

    def run_case(self, language, source_file, binary_file, input_data, time_limit_ms, memory_limit_mb):
        self.calls.append(input_data)
        if input_data == self.timeout_on:
            return ExecutionResult(124, "", "", 100, timed_out=True)
        if input_data == self.runtime_error_on:
            return ExecutionResult(2, "", "", 3)
        return ExecutionResult(0, self.outputs.get(input_data, ""), "", 2)


def test_home_page_shows_seeded_problem_library(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Sum of Two Numbers" in response.data
    assert b"Even or Odd" in response.data
    assert b"C++17" in response.data


def test_additional_practice_problems_are_seeded_with_hidden_tests(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Factorial" in response.data
    assert b"FizzBuzz" in response.data
    assert b"Second Largest Distinct" in response.data
    conn = connect_db()
    try:
        titles = {row[0] for row in conn.execute("SELECT title FROM problems")}
        assert len(titles) >= 14
        for title in ("Factorial", "FizzBuzz", "Prime Number", "Sum of an Array"):
            problem_id = conn.execute("SELECT id FROM problems WHERE title=?", (title,)).fetchone()[0]
            counts = conn.execute(
                "SELECT SUM(is_sample=1), SUM(is_sample=0) FROM test_cases WHERE problem_id=?",
                (problem_id,),
            ).fetchone()
            assert counts[0] == 1
            assert counts[1] >= 4
    finally:
        conn.close()


def test_participant_page_does_not_reveal_hidden_inputs(client):
    response = client.get("/problems/1")
    assert response.status_code == 200
    assert b"2 3" in response.data
    assert b"-1000000000 1" not in response.data
    assert b"10 20" not in response.data
    assert b"-4 9" not in response.data


def test_participant_can_queue_submission(client):
    login(client)
    token = csrf_token(client, "/problems/1")
    response = client.post(
        "/problems/1/submit",
        data={
            "_csrf_token": token,
            "language": "python",
            "source_code": "a, b = map(int, input().split())\nprint(a+b)",
        },
    )
    assert response.status_code == 302
    detail = client.get(response.headers["Location"])
    assert detail.status_code == 200
    assert b"QUEUED" in detail.data


def test_participant_cannot_open_admin_dashboard(client):
    login(client)
    response = client.get("/admin")
    assert response.status_code == 403


def test_hidden_tests_are_saved_on_admin_problem_creation(client):
    login(client, "admin", "Admin123!")
    token = csrf_token(client, "/admin/problems/new")
    response = client.post(
        "/admin/problems/new",
        data={
            "_csrf_token": token,
            "title": "Test Problem",
            "difficulty": "Easy",
            "statement": "Print the value.",
            "input_format": "One integer.",
            "output_format": "Print the integer.",
            "constraints_text": "0 <= n <= 9",
            "time_limit_ms": "1000",
            "memory_limit_mb": "128",
            "sample_input": "3\n",
            "sample_output": "3\n",
            "hidden_tests_json": '[{"input":"8\\n","output":"8\\n"}]',
        },
    )
    assert response.status_code == 302
    conn = connect_db()
    try:
        problem = conn.execute("SELECT id FROM problems WHERE title='Test Problem'").fetchone()
        tests = conn.execute("SELECT input_data, expected_output, is_sample FROM test_cases WHERE problem_id=? ORDER BY id", (problem["id"],)).fetchall()
    finally:
        conn.close()
    assert len(tests) == 2
    assert tests[1]["input_data"] == "8\n"
    assert tests[1]["is_sample"] == 0


def test_output_comparison_ignores_whitespace_only():
    assert normalize_output("  1\t2\n3  ") == normalize_output("1 2 3\n")
    assert normalize_output("1 2") != normalize_output("1 3")


def test_judge_marks_all_correct_cases_accepted():
    submission_id = insert_submission()
    runner = FakeRunner({"2 3\n": "5\n", "10 20\n": "30", "-4 9\n": "5", "0 0\n": "0", "-1000000000 1\n": "-999999999"})
    judge_submission(submission_id, runner)
    row = submission_row(submission_id)
    assert row["status"] == "AC"
    assert row["passed_tests"] == row["total_tests"] == 5


def test_judge_stops_at_first_wrong_answer_without_exposing_input():
    submission_id = insert_submission()
    runner = FakeRunner({"2 3\n": "5", "10 20\n": "31"})
    judge_submission(submission_id, runner)
    row = submission_row(submission_id)
    assert row["status"] == "WA"
    assert row["passed_tests"] == 1
    assert "10 20" not in row["error_message"]


def test_judge_marks_time_limit_exceeded():
    submission_id = insert_submission()
    runner = FakeRunner({"2 3\n": "5"}, timeout_on="2 3\n")
    judge_submission(submission_id, runner)
    assert submission_row(submission_id)["status"] == "TLE"


def test_judge_marks_compilation_error():
    submission_id = insert_submission(language="cpp", source="int main( {")
    runner = FakeRunner(compile_error=True)
    judge_submission(submission_id, runner)
    row = submission_row(submission_id)
    assert row["status"] == "CE"
    assert "expected" in row["error_message"]


def test_python_syntax_error_is_compilation_error():
    submission_id = insert_submission(language="python", source="print( ) else")
    runner = FakeRunner(compile_error=True)
    judge_submission(submission_id, runner)
    row = submission_row(submission_id)
    assert row["status"] == "CE"
    assert "SyntaxError" in row["error_message"]
