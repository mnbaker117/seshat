"""
Database access functions for the workflow tables (`grabs`,
`announces`, author lists).

These modules are deliberately thin — each function maps to one
SQL statement, takes an `aiosqlite.Connection` explicitly, and
commits before returning. Connection lifecycle is the caller's
responsibility (matching the pattern in `app.rate_limit`).

There's no ORM and no query builder. The schema is small enough
that hand-written SQL is more readable than abstraction layers,
and the test suite can verify queries against the real schema
via the `temp_db` fixture without any mocking.
"""
