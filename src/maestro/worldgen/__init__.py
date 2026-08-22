"""World builds alternate llm/image/mesh queues many times on a box with only one GPU, so a
job legitimately waits behind a model swap (~1-2min) plus the other queue draining — longer
than `workqueue.job_timeout_seconds` (900s default) is built to tolerate."""

WORLD_JOB_TIMEOUT = 3600
