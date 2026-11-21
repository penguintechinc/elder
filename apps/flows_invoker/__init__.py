"""Flows invoker — isolated CI/CD pipeline execution service.

Runs as its own container (separate image, restricted privileges, ephemeral
per-job workspace) because it executes arbitrary user-defined commands from
stage configuration (test runner, git operations). It consumes flow-execution
jobs from the Redis Streams job bus (group ``flows``) and never shares a process
with the main API or the consolidated worker.
"""
