# AGENTS.md — instructions for AI coding agents

Purpose
-------
This file gives concise, actionable guidance to AI coding agents working in this repository so they become productive quickly.

High-level facts
----------------
- Primary language: Python (many scripts live at `bsoft-py/` and `aws_labs/`).
- Key areas:
  - `aws_labs/` — lab automation, test runners, and IAM-related utilities. See `aws_labs/run_tc.py` and `aws_labs/nuke.py` for runners and cleanup logic.
  - `lab_permissions/` — example IAM policy JSON files (e.g. `lab_permissions/lab1.json`).
  - `leaky/` — contains an example app and `Dockerfile` for local builds.

How to operate (short)
----------------------
- Discover, don't assume: search the repo for scripts before editing. Link to existing docs rather than copying content.
- Minimal by default: only add content that is not discoverable automatically (e.g., conventions, entrypoints, sensitive steps).
- Run scripts with `python3 <script.py>`; inspect `Dockerfile` in `leaky/` for container builds.

Agent behavior guidelines
-------------------------
- Prefer creating `AGENTS.md` at repo root over `.github/copilot-instructions.md`.
- Follow the "link, don't embed" principle: point to existing README or scripts rather than duplicating.
- Keep suggestions small, specific, and verifiable. When making code changes, run or lint affected files where practical.

Quick links
-----------
- Lab permissions sample: [lab_permissions/lab1.json](lab_permissions/lab1.json#L1)
- Example runners: [aws_labs/run_tc.py](aws_labs/run_tc.py#L1) (if present)
- Dockerfile for the example app: [leaky/Dockerfile](leaky/Dockerfile#L1)

If you update this file
-----------------------
- Preserve existing content; add a short note at the top describing the change and why.
- For large areas (frontend/backend/tests), propose separate instruction files and link to them.

Questions for maintainers
-------------------------
- Are there any standard test or lint commands to include here?
- Is there a preferred Python version or virtualenv tooling (venv/poetry)?
