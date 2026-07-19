# Task 1: LogConfig file handler

Create `vasp_sop/core/logging.py` and its test file as specified in the brief.

## Global Constraints (from plan)
- No new dependencies (pure stdlib)
- Non-loop mode unchanged
- Follow existing `logging.getLogger(__name__)` pattern

## Interface produced by this task
`LogConfig.setup_file_logging(root: Path, *, log_path: Path | None = None) -> None`

- Adds FileHandler at INFO → `{root}/batch_run.log`
- Lifts existing stderr handler to WARNING (terminal stays quiet)
- Idempotent on repeated calls

## Report contract
Write your completion report to `/home/duguex/vasp_sop/.superpowers/sdd/task-1-report.md`:
- Status (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED)
- Commits (git log --oneline for this task)
- Test summary: command run + output
- Any concerns

Read the brief first: `/home/duguex/vasp_sop/.superpowers/sdd/task-1-brief.md`