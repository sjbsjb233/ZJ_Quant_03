"""Async job system (doc §7.3): long maintenance/migration ops + progress.

The design names Dramatiq+Redis; this single-node implementation uses an
in-process thread pool with job state persisted in the audit DB, exposing the
identical Job API (submit -> poll job_id -> progress/result). It can be swapped
for a Dramatiq broker later without changing routes or the frontend.
"""
