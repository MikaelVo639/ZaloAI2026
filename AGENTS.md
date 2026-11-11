# Working in this repository

- This is an offline Zalo AI Challenge 2026 DataMate starter, Python >=3.11.
- Run `uv sync --locked`, `uv run datamate smoke`, then `uv run python -m unittest discover -s tests -v`.
- Keep inference free of network calls, downloads and reference-answer reads.
- Never key answer logic on question/business ids. Business ids are routing metadata only.
- Never copy public/private test text (questions, rulebook rows, README content) into code,
  tests or docs, and never tune rules on public coverage. Build and measure on train only;
  run `uv run python scripts/check_no_public_text.py` before a PR.
- Read rulebook, business README and calendar from the split being answered.
- Keep unknown/unsupported behavior as `abstain`; execution errors do not prove `unanswerable`.
- Do not overwrite original competition data. Preserve source columns, units and identifiers.
- Shared interfaces are in `src/datamate/contracts.py`; coordinate incompatible changes.
- Add regression cases for changed calculations, joins, rule interpretations and formats.
- The scorer here is a local proxy; never describe its scores as official leaderboard scores.
- Do not add raw data, outputs, credentials or model binaries to Git.
- Shared docs/configs must work after a fresh clone without personal paths or pre-downloaded data.
- Keep checkout-specific results in ignored run artifacts; use docs/EXPERIMENTS.md for reporting.
- Document model release date, licence, total parameter count and offline loading procedure
  before adding any inference model. Current deterministic baseline uses no model weights.
