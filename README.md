# CodeForge — Online Coding Judge

A full-stack practice-platform MVP built around the PS4 brief. Participants solve problems in **Python 3** or **C++17**; submissions are checked against public samples and private hidden test cases; verdicts and progress update live; submission history and a leaderboard are included.

## Features

- Participant registration, login, and session-based access control.
- Admin dashboard to publish problems with input/output descriptions, constraints, resource limits, a public sample, and 1–49 hidden tests.
- Practice library with difficulty filters and search.
- Python 3 and C++17 solution submission.
- Queue-backed judge worker with live `QUEUED` → `RUNNING` → verdict status updates.
- Verdicts: **AC**, **WA**, **TLE**, **RE**, and **CE**.
- Per-test time limit; Docker memory, CPU and process limits; capped stdout/stderr; whitespace-tolerant output comparison.
- A user's submission history, source snapshot, verdict feedback, and a participant leaderboard.
- SQLite data model and pytest tests using a fake runner, so the judge logic can be tested without Docker.

The project intentionally implements the **practice platform** variant, rather than a timed contest.

## Quick start

Requirements: Python 3.10+ and a working Docker Engine / Docker Desktop installation. The Python web app and worker run on the same host so the worker can mount each temporary source file into a Docker sandbox.

```bash
# 1. Create and activate an environment
python -m venv .venv
source .venv/bin/activate             # Windows PowerShell: .venv\Scripts\Activate.ps1

# 2. Install Python dependencies
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt  # optional; only for tests

# 3. Set a real secret before sharing the app
export SECRET_KEY="replace-with-a-long-random-value"
export JUDGE_ADMIN_PASSWORD="set-a-unique-admin-password-before-first-run"

# 4. Start both the web server and judge worker
./start.sh
```

On Windows, use a WSL2 Linux environment (recommended) with Docker Desktop integration, install dependencies inside WSL, and run the commands above there; `start.sh` is a Bash helper. The worker checks Docker availability and downloads `python:3.12-slim` and `gcc:13` if they are not already present. Alternatively, pre-pull them first:

```bash
docker pull python:3.12-slim
docker pull gcc:13
```

Open **http://127.0.0.1:5000**.

### Demo accounts

On a fresh database, the application seeds these accounts:

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `Admin123!` by default |
| Participant | `alice` | `Alice123!` |

Set `JUDGE_ADMIN_PASSWORD` **before the first launch** to override the initial admin password. The value is only used while seeding a new database. For a shared demo, change the seeded admin password and `SECRET_KEY`, and remove or change demo credentials before exposing the app to a network.

Fourteen practice problems are seeded: the original three plus eleven additional Easy/Medium problems. When you update an existing installation, restarting the app and worker adds any missing practice problems without deleting your existing users, submissions, or authored problems. The database is created at `instance/judge.db`; deleting that file resets the local demo and removes all users and submissions. On problem pages, the editor now has more space and an Expand/Collapse focus mode (Escape also exits focus mode).

## Tests

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

The tests exercise routing, participant/admin access, hidden-test visibility, queue creation, whitespace-tolerant comparison, and AC/WA/TLE/CE verdict paths using a fake runner. They do not require Docker or compile submitted code.

## Architecture

```text
Browser
  │ HTML forms / JSON polling
  ▼
Flask web app ────── SQLite (users, problems, test_cases, submissions)
  │                         ▲
  │ creates QUEUED row      │ worker writes progress / verdict
  ▼                         │
SQLite queue ───────── Judge worker (worker.py)
                            │
                            ├─ Python: python:3.12-slim sandbox
                            └─ C++17: gcc:13 compile sandbox + runtime sandbox
```

The web app never executes submitted code in a request handler. The separate worker claims queued submissions, compiles C++ in a constrained container, runs each test in a fresh container, compares tokenized output, and persists progress. A worker restart requeues submissions left in `RUNNING` state. The worker runs a configurable, bounded pool of isolated runner jobs (`JUDGE_WORKERS`, default `2`, maximum `4`). When 20 people submit at once, the web app stores all 20 quickly; up to the configured number execute in parallel and the rest remain queued, with live status polling. SQLite transactions serialize queue claims. Run only one worker process because startup recovery treats any previously `RUNNING` row as interrupted work.

### Main files

- `app.py` — Flask routes, authentication, SQLite schema/seed data, admin problem creation and status endpoints.
- `judge_engine.py` — queue helpers, output comparison, Docker sandbox lifecycle, compilation and verdict logic.
- `worker.py` — background queue polling process.
- `templates/` and `static/style.css` — responsive interface.
- `tests/test_app_and_judge.py` — route and judge logic tests.

## Verdict rules

- **AC (Accepted):** every sample and hidden test passes.
- **WA (Wrong Answer):** the program exits normally but its whitespace-tokenized output differs from expected output.
- **TLE (Time Limit Exceeded):** the in-container timeout fires for a test case.
- **RE (Runtime Error):** the program exits unsuccessfully or exceeds the output cap.
- **CE (Compilation Error):** C++ source fails compilation or the compile phase hits its resource/time limit.

The configured time limit applies separately to each run. The `Judge wall time` shown in the UI includes container startup overhead and should not be interpreted as a precise benchmark of only the submitted program's CPU time. Memory is enforced by Docker; in-container peak memory measurement is not currently displayed.

## Security notes and limitations

This is an educational MVP, **not a hardened public multi-tenant judge**. Docker containers use disabled networking, a read-only root filesystem, a non-root UID, dropped Linux capabilities, `no-new-privileges`, memory/swap/CPU/PID/file-descriptor limits, timeouts, and bounded captured output. Only the submission's source file or binary is mounted into a runner. Hidden test data is sent through stdin and is never mounted in the sandbox. The web server does not reveal hidden test inputs or expected outputs.

However, Docker shares the host kernel and is not a perfect boundary against a determined attacker. Anyone who can submit code can consume the worker queue, and anyone who can control the worker can indirectly ask Docker to create containers. **Do not expose this app to the public internet or untrusted users without additional isolation and operational controls.** For a production or open competition service, use a hardened runner such as Judge0/Piston or microVM/gVisor-based isolation, run the worker on a separate machine, apply authentication/rate limits/queue quotas, configure TLS and secure cookies, audit problem authoring, and monitor the host. Keep Docker patched. The Docker socket should never be mounted into a sandbox container.

Other scope choices: no contest clock, plagiarism detector, per-test partial scoring, multi-worker distributed queue, or in-browser IDE syntax highlighting. The textarea editor is intentionally dependency-free.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | dev-only fallback | Flask session signing key; replace before sharing |
| `JUDGE_ADMIN_PASSWORD` | `Admin123!` | Password for admin seed user on a new database only |
| `JUDGE_DB_PATH` | `instance/judge.db` | SQLite database file path |
| `HOST` | `127.0.0.1` | Web server bind host |
| `PORT` | `5000` | Web server port |
| `DOCKER_BIN` | `docker` | Docker CLI executable name/path |
| `JUDGE_WORKERS` | `2` | Concurrent sandbox jobs in the worker (1–4; lower it on small machines) |
| `LOG_LEVEL` | `INFO` | Worker log verbosity |
