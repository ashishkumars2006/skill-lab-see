"""Docker-backed, resource-limited runner for participant submissions.

Hidden test inputs are passed through stdin and are never mounted into a sandbox.
Only the participant's source file (or compiled binary) is mounted into a sandbox.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app import DB_PATH, SUPPORTED_LANGUAGES, connect_db, init_db

LOG = logging.getLogger("online_judge.worker")
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 32 * 1024
COMPILE_STDERR_BYTES = 8 * 1024
TERMINAL_STATUSES = {"AC", "WA", "TLE", "RE", "CE"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_output(text: str) -> list[str]:
    """Typical online-judge comparison: ignore whitespace differences only."""
    return text.split()


@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: int
    timed_out: bool = False
    output_limited: bool = False


@dataclass
class CappedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    elapsed_ms: int
    timed_out: bool = False
    output_limited: bool = False


class CaseRunner(Protocol):
    def compile_python(self, source_file: Path) -> ExecutionResult: ...

    def compile_cpp(self, source_file: Path, build_dir: Path) -> ExecutionResult: ...

    def run_case(
        self, language: str, source_file: Path, binary_file: Path | None,
        input_data: str, time_limit_ms: int, memory_limit_mb: int,
    ) -> ExecutionResult: ...


class SandboxRunner:
    """Executes code in fresh Docker containers with conservative defaults."""

    def __init__(self) -> None:
        self.docker = os.environ.get("DOCKER_BIN", "docker")

    @staticmethod
    def _container_name() -> str:
        return f"ocj-{uuid.uuid4().hex[:12]}"

    def _base_args(self, name: str, memory_mb: int) -> list[str]:
        memory = f"{max(32, int(memory_mb))}m"
        return [
            self.docker, "run", "--rm", "--init", "--interactive", "--name", name,
            "--network", "none",
            "--memory", memory, "--memory-swap", memory,
            "--cpus", "1.0", "--pids-limit", "64",
            "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m,mode=1777",
            "--user", "65534:65534", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--ulimit", "nofile=64:64", "--ulimit", "core=0",
            "--log-driver", "none",
        ]

    def ensure_ready(self) -> None:
        """Check Docker and pull required images before the queue is consumed."""
        try:
            result = subprocess.run(
                [self.docker, "info"], stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Docker is not available: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace")[-1000:]
            raise RuntimeError(f"Docker daemon is unavailable: {detail.strip()}")
        images = sorted({definition["image"] for definition in SUPPORTED_LANGUAGES.values()})
        for image in images:
            inspected = subprocess.run(
                [self.docker, "image", "inspect", image],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
            )
            if inspected.returncode != 0:
                LOG.info("Pulling runner image %s", image)
                try:
                    pulled = subprocess.run([self.docker, "pull", image], timeout=300, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError(f"Could not pull Docker image {image}: {exc}") from exc
                if pulled.returncode != 0:
                    raise RuntimeError(f"Could not pull Docker image {image}; check network access and Docker permissions.")

    def _run_capped(
        self, command: list[str], input_data: bytes, timeout_seconds: float,
        container_name: str, stdout_limit: int = MAX_STDOUT_BYTES,
        stderr_limit: int = MAX_STDERR_BYTES,
    ) -> CappedProcessResult:
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True,
            )
        except OSError as exc:
            return CappedProcessResult(127, b"", str(exc).encode(), 0)

        out = bytearray()
        err = bytearray()
        output_limited = threading.Event()

        def drain(stream, target: bytearray, limit: int) -> None:
            total = 0
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    remaining = limit - total
                    if remaining > 0:
                        target.extend(chunk[:remaining])
                    total += len(chunk)
                    if total > limit:
                        output_limited.set()
                        try:
                            proc.kill()
                        except OSError:
                            pass
            except (OSError, ValueError):
                pass

        def feed_stdin() -> None:
            try:
                assert proc.stdin is not None
                proc.stdin.write(input_data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except OSError:
                    pass

        assert proc.stdout is not None and proc.stderr is not None
        out_thread = threading.Thread(target=drain, args=(proc.stdout, out, stdout_limit), daemon=True)
        err_thread = threading.Thread(target=drain, args=(proc.stderr, err, stderr_limit), daemon=True)
        in_thread = threading.Thread(target=feed_stdin, daemon=True)
        out_thread.start()
        err_thread.start()
        in_thread.start()
        timed_out = False
        try:
            proc.wait(timeout=max(0.1, timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        in_thread.join(timeout=1)
        out_thread.join(timeout=2)
        err_thread.join(timeout=2)
        if timed_out or output_limited.is_set():
            self._remove_container(container_name)
        try:
            proc.stdout.close()
            proc.stderr.close()
        except OSError:
            pass
        code = proc.returncode if proc.returncode is not None else 137
        return CappedProcessResult(
            code, bytes(out), bytes(err), int((time.monotonic() - started) * 1000),
            timed_out=timed_out, output_limited=output_limited.is_set(),
        )

    def _remove_container(self, name: str) -> None:
        try:
            subprocess.run(
                [self.docker, "rm", "--force", name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=4, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            LOG.warning("Could not remove timed-out container %s", name)


    def compile_python(self, source_file: Path) -> ExecutionResult:
        """Syntax-check Python in a sandbox so syntax errors receive CE, not RE."""
        name = self._container_name()
        image = SUPPORTED_LANGUAGES["python"]["image"]
        command = self._base_args(name, 128)
        command.extend([
            "--mount", f"type=bind,src={source_file.resolve()},dst=/main.py,readonly",
            "--workdir", "/tmp", "--entrypoint", "/usr/bin/timeout", image,
            "--signal=KILL", "--kill-after=0.1s", "5.000s", "python3", "-B", "-c",
            "import pathlib; compile(pathlib.Path('/main.py').read_bytes(), '/main.py', 'exec')",
        ])
        raw = self._run_capped(command, b"", timeout_seconds=20, container_name=name,
                               stdout_limit=COMPILE_STDERR_BYTES, stderr_limit=COMPILE_STDERR_BYTES)
        return ExecutionResult(
            raw.returncode, raw.stdout.decode("utf-8", errors="replace"),
            raw.stderr.decode("utf-8", errors="replace"), raw.elapsed_ms,
            timed_out=raw.timed_out or raw.returncode == 124, output_limited=raw.output_limited,
        )

    def compile_cpp(self, source_file: Path, build_dir: Path) -> ExecutionResult:
        name = self._container_name()
        build_dir.mkdir(mode=0o777, parents=True, exist_ok=True)
        try:
            build_dir.chmod(0o777)
        except OSError:
            pass
        command = [
            self.docker, "run", "--rm", "--init", "--interactive", "--name", name,
            "--network", "none", "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1.0", "--pids-limit", "64", "--read-only",
            "--tmpfs", "/tmp:rw,nosuid,size=128m,mode=1777", "--user", "65534:65534",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--ulimit", "nofile=64:64", "--ulimit", "core=0", "--log-driver", "none",
            "--mount", f"type=bind,src={source_file.resolve()},dst=/main.cpp,readonly",
            "--mount", f"type=bind,src={build_dir.resolve()},dst=/build",
            "--workdir", "/tmp", "--entrypoint", "/bin/sh", "gcc:13",
            "-lc", "g++ -std=c++17 -O2 -pipe /main.cpp -o /build/main",
        ]
        raw = self._run_capped(command, b"", timeout_seconds=25, container_name=name,
                               stdout_limit=COMPILE_STDERR_BYTES, stderr_limit=COMPILE_STDERR_BYTES)
        return ExecutionResult(
            raw.returncode, raw.stdout.decode("utf-8", errors="replace"),
            raw.stderr.decode("utf-8", errors="replace"), raw.elapsed_ms,
            timed_out=raw.timed_out, output_limited=raw.output_limited,
        )

    def run_case(
        self, language: str, source_file: Path, binary_file: Path | None,
        input_data: str, time_limit_ms: int, memory_limit_mb: int,
    ) -> ExecutionResult:
        name = self._container_name()
        image = SUPPORTED_LANGUAGES[language]["image"]
        command = self._base_args(name, memory_limit_mb)
        seconds = max(0.1, time_limit_ms / 1000.0)
        command.extend(["--workdir", "/tmp"])
        if language == "python":
            command.extend([
                "--mount", f"type=bind,src={source_file.resolve()},dst=/main.py,readonly",
                "--entrypoint", "/usr/bin/timeout", image,
                "--signal=KILL", "--kill-after=0.1s", f"{seconds:.3f}s",
                "python3", "-B", "/main.py",
            ])
        else:
            if binary_file is None:
                return ExecutionResult(127, "", "Compiled binary is missing.", 0)
            command.extend([
                "--mount", f"type=bind,src={binary_file.resolve()},dst=/program,readonly",
                "--entrypoint", "/usr/bin/timeout", image,
                "--signal=KILL", "--kill-after=0.1s", f"{seconds:.3f}s", "/program",
            ])
        # The timeout utility enforces the per-test limit inside the container;
        # this outer timeout is a backstop for a stuck Docker client/daemon.
        raw = self._run_capped(command, input_data.encode("utf-8"),
                               timeout_seconds=max(15.0, seconds + 12.0), container_name=name)
        stdout = raw.stdout.decode("utf-8", errors="replace")
        stderr = raw.stderr.decode("utf-8", errors="replace")
        timed_out = raw.timed_out or raw.returncode == 124
        return ExecutionResult(raw.returncode, stdout, stderr, raw.elapsed_ms, timed_out, raw.output_limited)


def _update_submission(submission_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {
        "status", "started_at", "finished_at", "execution_time_ms", "memory_used_mb",
        "passed_tests", "total_tests", "error_message",
    }
    if any(key not in allowed for key in fields):
        raise ValueError("Attempted to update an unsupported submission field")
    setters = ", ".join(f"{key} = ?" for key in fields)
    conn = connect_db()
    try:
        conn.execute(
            f"UPDATE submissions SET {setters} WHERE id = ?",
            (*fields.values(), submission_id),
        )
        conn.commit()
    finally:
        conn.close()


def judge_submission(submission_id: int, runner: CaseRunner | None = None) -> None:
    """Evaluate one queued submission and persist a standard online-judge verdict."""
    init_db()
    runner = runner or SandboxRunner()
    conn = connect_db()
    try:
        submission = conn.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not submission:
            LOG.error("Submission %s disappeared before judging", submission_id)
            return
        problem = conn.execute("SELECT * FROM problems WHERE id=?", (submission["problem_id"],)).fetchone()
        tests = conn.execute(
            "SELECT input_data, expected_output, is_sample FROM test_cases WHERE problem_id=? ORDER BY is_sample DESC, sequence_no, id",
            (submission["problem_id"],),
        ).fetchall()
    finally:
        conn.close()

    if not problem or not tests:
        _update_submission(submission_id, status="RE", finished_at=now_iso(), error_message="Problem has no test cases.")
        return

    _update_submission(
        submission_id, status="RUNNING", started_at=now_iso(), total_tests=len(tests),
        passed_tests=0, error_message=None,
    )
    elapsed_total = 0
    passed = 0
    try:
        with tempfile.TemporaryDirectory(prefix="online-judge-") as temp_name:
            workspace = Path(temp_name)
            language = submission["language"]
            source_file = workspace / ("main.py" if language == "python" else "main.cpp")
            source_file.write_text(submission["source_code"], encoding="utf-8")
            source_file.chmod(0o644)
            binary_file: Path | None = None
            if language == "cpp":
                build_dir = workspace / "build"
                compile_result = runner.compile_cpp(source_file, build_dir)
                elapsed_total += compile_result.elapsed_ms
                if compile_result.timed_out:
                    _update_submission(
                        submission_id, status="CE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        error_message="Compilation exceeded the compiler resource limit.",
                    )
                    return
                if compile_result.returncode != 0:
                    message = (compile_result.stderr or compile_result.stdout or "Compilation failed.").strip()
                    _update_submission(
                        submission_id, status="CE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        error_message=message[:COMPILE_STDERR_BYTES],
                    )
                    return
                binary_file = build_dir / "main"
                if not binary_file.exists():
                    _update_submission(
                        submission_id, status="CE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        error_message="Compiler finished but did not produce an executable.",
                    )
                    return
            elif language == "python":
                compile_result = runner.compile_python(source_file)
                elapsed_total += compile_result.elapsed_ms
                if compile_result.timed_out:
                    _update_submission(
                        submission_id, status="CE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        error_message="Python syntax validation exceeded its resource limit.",
                    )
                    return
                if compile_result.returncode != 0:
                    message = (compile_result.stderr or compile_result.stdout or "Python syntax validation failed.").strip()
                    _update_submission(
                        submission_id, status="CE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        error_message=message[:COMPILE_STDERR_BYTES],
                    )
                    return

            for index, case in enumerate(tests, start=1):
                result = runner.run_case(
                    language, source_file, binary_file, case["input_data"],
                    int(problem["time_limit_ms"]), int(problem["memory_limit_mb"]),
                )
                elapsed_total += result.elapsed_ms
                if result.timed_out:
                    _update_submission(
                        submission_id, status="TLE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        passed_tests=passed, error_message=f"Time limit exceeded on test case {index}.",
                    )
                    return
                if result.output_limited:
                    _update_submission(
                        submission_id, status="RE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        passed_tests=passed, error_message=f"Output limit exceeded on test case {index}.",
                    )
                    return
                if result.returncode != 0:
                    _update_submission(
                        submission_id, status="RE", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        passed_tests=passed, error_message=f"Runtime error on test case {index}.",
                    )
                    return
                if normalize_output(result.stdout) != normalize_output(case["expected_output"]):
                    _update_submission(
                        submission_id, status="WA", finished_at=now_iso(), execution_time_ms=elapsed_total,
                        passed_tests=passed, error_message=f"Wrong answer on test case {index}.",
                    )
                    return
                passed += 1
                _update_submission(submission_id, passed_tests=passed, execution_time_ms=elapsed_total)

        _update_submission(
            submission_id, status="AC", finished_at=now_iso(), execution_time_ms=elapsed_total,
            passed_tests=passed, total_tests=len(tests), error_message=None,
        )
    except Exception:
        LOG.exception("Unexpected judge failure for submission %s", submission_id)
        _update_submission(
            submission_id, status="RE", finished_at=now_iso(), execution_time_ms=elapsed_total,
            passed_tests=passed, error_message="Judge internal error. Ask an administrator to check worker logs.",
        )


def claim_next_submission() -> dict | None:
    """Atomically lease one queued submission; safe for a single worker process."""
    conn = connect_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM submissions WHERE status='QUEUED' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        cur = conn.execute(
            "UPDATE submissions SET status='RUNNING', started_at=? WHERE id=? AND status='QUEUED'",
            (now_iso(), row["id"]),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        return {"id": row["id"]}
    finally:
        conn.close()


def recover_interrupted_submissions() -> int:
    """Requeue work left RUNNING after an unclean worker exit."""
    conn = connect_db()
    try:
        cur = conn.execute(
            "UPDATE submissions SET status='QUEUED', started_at=NULL, error_message=NULL WHERE status='RUNNING'"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
