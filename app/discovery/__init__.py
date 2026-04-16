"""
Discovery domain — book metadata lookup, library sync, and source scanning.

Ported from AthenaScout. This package handles:
- Calibre library discovery and sync
- Multi-source metadata lookup (Goodreads, Hardcover, Kobo, Amazon, IBDB, Google Books)
- MyAnonamouse search for missing books
- Per-library database management (authors, books, series)
- Series suggestions via source consensus
"""
