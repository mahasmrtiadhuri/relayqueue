# Architecture and invariants

## Atomicity

Every state-changing operation begins an immediate SQLite transaction. The job row and associated event commit together or both roll back. Handler execution never holds a database transaction open.

## Ownership

A claim increments attempts and creates a random token with a lease deadline. Completion checks state, token, and deadline inside the write transaction. A stale worker cannot overwrite the result of a replacement worker.

## Recovery

Each claim first sweeps expired leases. Work with remaining attempts returns to the queue; exhausted work moves to `dead`. Recovery is lazy and requires an active polling worker.

## Idempotency

A unique key is stored with a SHA-256 fingerprint of canonical JSON, handler name, and retry limit. A matching request returns the persisted job. Conflicting reuse raises a conflict. This deduplicates submissions only; exactly-once external effects are not guaranteed.

## Storage tradeoff

SQLite is intentional for the single-host scope: reproducible setup, inspectable state, and no external service requirement. WAL permits concurrent readers, but writers serialize. The next backend should preserve these invariants under a process-level concurrency test before replacing SQLite.

## Failure matrix

| Failure | Result |
|---|---|
| API restarts after commit but before response | Client retries with the same idempotency key |
| Worker crashes before executing | Lease expires and another claim recovers work |
| Worker crashes after external side effect | Work can execute again; handler must deduplicate effects |
| Worker returns after lease expiration | Completion rejected |
| Handler fails repeatedly | Backoff then dead-letter state |
| Disk unavailable | API returns storage error; transaction rolls back where possible |

## Sources

- [SQLite transactions](https://www.sqlite.org/lang_transaction.html)
- [SQLite WAL](https://www.sqlite.org/wal.html)
- [Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html)
