"""SQLite-backed persistent queue (design §13).

`store` — enqueue / claim / finish / cancel / boost helpers (atomic).
`estimator` — duration + ETA estimation from task_duration_stat history.
`dispatcher` — long-running worker process: poll, claim, dispatch, finalize.
`scheduler` — optional daily cron — disabled by default per user request.
"""
