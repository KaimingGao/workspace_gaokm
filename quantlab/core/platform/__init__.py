"""Platform archive: jobs, manifests, decisions, memory, feedback.

Prefer ``core.platform.<module>``; root ``core.<module>`` paths remain as
compat re-exports.
"""

from . import (
    decision_record,
    feedback_suggest,
    job_progress,
    memory_store,
    run_manifest,
    schedule_jobs,
)

__all__ = [
    "decision_record",
    "feedback_suggest",
    "job_progress",
    "memory_store",
    "run_manifest",
    "schedule_jobs",
]
