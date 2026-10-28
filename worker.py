from __future__ import annotations

import logging
import os
import signal
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from app import init_db
from judge_engine import SandboxRunner, _update_submission, claim_next_submission, judge_submission, now_iso, recover_interrupted_submissions

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOG = logging.getLogger("online_judge.worker")
STOP = False


def _stop(*_args):
    global STOP
    STOP = True


def _run_job(submission_id: int, runner: SandboxRunner) -> None:
    try:
        LOG.info("Judging submission %s", submission_id)
        judge_submission(submission_id, runner=runner)
        LOG.info("Finished submission %s", submission_id)
    except Exception:
        LOG.exception("Unhandled error while judging submission %s", submission_id)
        try:
            _update_submission(
                submission_id, status="RE", finished_at=now_iso(),
                error_message="Judge worker failed unexpectedly. Ask an administrator to check worker logs.",
            )
        except Exception:
            LOG.exception("Could not persist failure verdict for submission %s", submission_id)


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    init_db()
    recovered = recover_interrupted_submissions()
    if recovered:
        LOG.warning("Requeued %s submission(s) left running by an earlier worker", recovered)

    runner = SandboxRunner()
    while not STOP:
        try:
            runner.ensure_ready()
            break
        except Exception as exc:
            LOG.error("Execution engine not ready: %s. Retrying in 10 seconds.", exc)
            time.sleep(10)

    if STOP:
        return

    try:
        concurrency = int(os.environ.get("JUDGE_WORKERS", "2"))
    except ValueError:
        concurrency = 2
    concurrency = min(4, max(1, concurrency))
    LOG.info("Judge worker ready; polling SQLite queue with %s concurrent runner(s).", concurrency)
    active: dict[Future, int] = {}

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="judge") as pool:
        while not STOP or active:
            finished = [future for future in active if future.done()]
            for future in finished:
                submission_id = active.pop(future)
                try:
                    future.result()
                except Exception:
                    LOG.exception("Worker task crashed for submission %s", submission_id)

            claimed_any = False
            while not STOP and len(active) < concurrency:
                job = claim_next_submission()
                if job is None:
                    break
                future = pool.submit(_run_job, job["id"], runner)
                active[future] = job["id"]
                claimed_any = True

            if active and not claimed_any:
                wait(active.keys(), timeout=0.5, return_when=FIRST_COMPLETED)
            elif not active:
                time.sleep(0.5)


if __name__ == "__main__":
    main()
