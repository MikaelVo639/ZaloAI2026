# Data public adapters — handoff

Branch: `codex/data-public-adapters`  
Base: `main@c5601e6`  
Scope: Data & Schema assignment in `chia-viec-dot-3.md`

## Result

All 10 assigned public shops now load through README/rulebook-driven adapters. Public has no
labels, so the table reports coverage only; it does not claim answer correctness.

| Shop | Original blocker | Answered before | Answered after | Newly answered |
|---|---|---:|---:|---:|
| `dev_002` | `SOURCE_MISSING` | 0 | 12 | 12 |
| `dev_003` | `README_COLUMNS_MISSING` | 0 | 30 | 30 |
| `dev_004` | `UNKNOWN_STATUS` | 0 | 33 | 33 |
| `dev_005` | `SOURCE_MISSING` | 0 | 35 | 35 |
| `dev_009` | `DUPLICATE_PARENT_KEY` | 0 | 12 | 12 |
| `dev_010` | `DUPLICATE_PARENT_KEY` | 0 | 24 | 24 |
| `dev_011` | `NO_SALES_LIST` | 0 | 11 | 11 |
| `dev_012` | `NO_SALES_LIST` | 0 | 6 | 6 |
| `dev_013` | `NO_SALES_LIST` | 0 | 14 | 14 |
| `dev_014` | `README_COLUMNS_MISSING` | 0 | 7 | 7 |
| **Total public** | 1,000 questions | **124** | **308** | **184** |

The four shops that already answered (`dev_001`, `dev_006`, `dev_007`, `dev_008`) have zero
lost answers and zero changed answers in the before/after comparison.

## Implemented

- Closed status mappings for the documented labels `đang ship` and `khách trả`.
- README filename templates such as `BanHang_T<tháng>_<năm>.xlsx` union only matching files.
- Repeated headers, daily total rows and exact duplicate rows are ignored only under the
  corresponding rulebook capability; conflicting duplicates still raise.
- Blank-first-row and merged two-row XLSX sheets remain discoverable without changing their raw
  files.
- Reviewed, evidence-gated profiles cover combined sales-software exports and handwritten phone
  ledgers. An incomplete shape still raises instead of guessing.
- Free-form `Ghi chú` is interpreted as status only when the split rulebook explicitly defines
  that convention. Text such as `kiểm tra lại` is not confused with returned goods.
- Monthly `STT` ledgers scope their local sequence by sheet, while actual order codes remain
  global so a return recorded in a later month still marks the original order.
- Repair tickets are available for revenue/order/staff calculations but are not exposed as
  rankable products because one cell contains several unpriced jobs.
- Documented Voucher / points / shipping adjustment rows are excluded from sales revenue and
  product rankings.

## Verification

- `python -m unittest discover -s tests -v`: **383 tests passed**.
- Ruff on all changed Python files: **clean**.
- Final public run: **308 answered / 692 abstain**, up from **124 / 876**.
- Final run artifacts: `outputs/runs/public-data-adapters-final-20261108/` (gitignored).

Per the team plan, Evaluation should run the labeled full-train benchmark on `main` after the
merge batch. This checkout does not contain `data/raw/train/questions.jsonl`, so no local labeled
train score is reported here.
