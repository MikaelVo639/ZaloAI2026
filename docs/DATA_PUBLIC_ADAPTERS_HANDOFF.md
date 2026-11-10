# Data public adapters — handoff

Branch: `codex/data-public-adapters`  
Base synced through: `main@fb76719` and customer coverage PR `#44`
Scope: Data & Schema assignment in `chia-viec-dot-3.md`

## Đợt 3 update — 10/11/2026

### Implemented

- Propagated documented product base units and groups from joined product tables into sales.
- Enriched otherwise-valid flat sales with the README product catalog by unique product code or
  unambiguous product name. The selected sales adapter and transaction values are unchanged.
- Added the reviewed SQLite POS debt profile selected by complete schema signatures plus the
  README's qualified debt references; no business ID is hard-coded.
- Tightened money-unit detection so ordinary text such as `Xử lý` or `đơn vị cơ bản` is not
  mistaken for a currency declaration. Explicit VND, thousand-VND and unsupported currencies
  keep their existing validation behavior.
- Incomplete product/debt mappings still fail closed instead of guessing.

### Labeled-train verification

| Check | Result |
|---|---:|
| Remaining base-unit failures (`biz_108`, `biz_115`) | **17/17 correct** |
| Debt failures (`biz_111`, `biz_123`) | **26/26 correct** |
| `biz_111` debt ledger | 66,469 orders / 66,195 payments / 0 reconciliation mismatch |
| `biz_123` debt ledger | 33,630 orders / 33,816 payments / 0 reconciliation mismatch |
| Full-train combined regression | **2,757 correct / 0 wrong / 243 abstain** |
| Python unit tests | **439 passed** |
| Ruff on changed Python files | **clean** |

The full-train combined regression uses the completed 3,000-question unit run and replaces only
the 26 previously abstained debt IDs with the separately rerun debt results. Artifacts are under
`outputs/runs/unit-mismatch-full-after-20261110/`,
`outputs/runs/debt-biz111-123-after-20261110/`, and
`outputs/runs/dot3-data-combined-20261110/` (gitignored).

## Original public-adapter result

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

## Original public verification

- `python -m unittest discover -s tests -v`: **383 tests passed**.
- Ruff on all changed Python files: **clean**.
- Final public run: **308 answered / 692 abstain**, up from **124 / 876**.
- Final run artifacts: `outputs/runs/public-data-adapters-final-20261108/` (gitignored).

This original public-only verification predates the labeled-train update reported at the top of
this handoff.
