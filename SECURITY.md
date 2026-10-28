# Security overview

The judge is designed for a supervised local-course demo. It deliberately separates HTTP handling from execution and does not promise strong isolation against a skilled adversary.

Implemented controls:

- participant/admin role check for administrative routes;
- password hashing, session cookies marked HttpOnly and SameSite=Lax, and form CSRF tokens;
- source size and test case size/count limits;
- per-case inner timeout plus an outer Docker client timeout;
- Docker networking disabled, read-only root filesystem, non-root UID, dropped capabilities, `no-new-privileges`, no core dumps, constrained PID/file descriptors, memory/swap and CPU limits;
- bounded stdout/stderr collection and force-removal attempts for timed-out containers;
- hidden test inputs are supplied through stdin instead of being mounted or written to the sandbox filesystem;
- only the participant's source file or compiled binary is mounted into runtime containers.

Before using beyond a classroom demo, replace the fallback Flask secret, change seeded credentials, run behind TLS, add rate limits/quotas and operational monitoring, keep the host and Docker patched, and move the execution worker to a dedicated hardened environment. Docker shares the host kernel and a container escape could compromise the machine. Treat the web/worker host as a security-sensitive system.
