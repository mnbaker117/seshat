"""Seshat — MAM courier and Calibre ingest pipeline.

Sibling project to AthenaScout. The two share patterns deliberately
(config layer, supervised tasks, SQLite + WAL, FastAPI + APScheduler)
so the codebases feel like a coherent suite.

Seshat is MAM-only by design. Every module assumes MyAnonamouse as
the upstream — there are no tracker abstractions and there will be none.
"""
