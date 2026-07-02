"""Agentic common-schema alignment for cross-city residential EPC/APE datasets.

Stage 1 (this package, deterministic): profile each city's raw CSV into a
structured, privacy-safe `DatasetProfile`. The LLM agents downstream read only
these profiles -- never raw rows.
"""
