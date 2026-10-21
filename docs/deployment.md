# Deployment and operating scope

## Supported topology

One host, API and worker processes, and one local durable volume. The included Compose configuration illustrates this topology. No managed cloud resources are provisioned by the repository.

## Cloud deployment path

For a single virtual machine, place the database on a persistent block-storage volume, run API and worker containers on the same host, and terminate HTTPS at a reverse proxy. Restrict network access to the API and inject the bearer token through the host's secret-management mechanism. Do not place the SQLite WAL database on a network filesystem or scale independent container replicas across hosts with separate databases.

## Operational checks

- Inspect `/health` counts and job event history.
- Alert on a growing dead-job count or sustained queued backlog.
- Back up with SQLite's online backup API; copying only the main database while WAL writes continue is insufficient.
- Exercise restoration on a separate volume before relying on a backup.
- Preserve the volume across deployments; deleting it deletes queue state.

## Limits

The HTTP server has a body-size cap and socket timeout, but no rate limiting, tenant isolation, or production TLS handling. Queue history can grow without retention. The demo has no published throughput or availability measurements. Load testing, hardened ingress, and restore drills are required before exposing a deployment to real users.
