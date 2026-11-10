# Round 3 public wording — Data handoff (groups 14–17, 20)

Date: 2026-11-10
Owner: `@longlth2297`
Baseline: `main@5ae1626`, **420 answered / 580 abstain** on the 1,000 public questions

Public has no labels. The counts below prove source coverage and join integrity only; they do not
claim that a public answer is correct. All implementation tests use synthetic fixtures.

## 14. Staff identity

- Existing code-based questions are safe: sales retain the README-described seller code in
  `Sale.staff_id`.
- Seven of the nine audited questions already answer by an explicit code on current `main`.
- The two name-based questions are `dev_006` and currently abstain in grammar. Its reviewed POS
  directory maps:

  | Code | Display name |
  |---|---|
  | `NV001` | Võ Trung Hoa |
  | `NV002` | Từ Hùng Hằng |
  | `NV003` | Đinh Đạt Đức |

- All three seller codes used by sales exist in the directory; there are no duplicate names.
- `load_staff_directory()` exposes a name only when it identifies exactly one code. Duplicate
  names are returned in `ambiguous_names` and are not aliases, so a question naming only “An”
  remains abstain when two codes share that name.
- Planner/Agent follow-up: resolve a reviewed unique name to its code before creating the existing
  `Filter("staff", code)`. Do not compare display names directly to `Sale.staff_id`.

## 15. Product name, size, brand and unit

The missing source mapping was a name-only product catalogue. The adapter previously required a
product code and silently left the catalogue unused. It now joins a name-only catalogue only when
the folded name is unique; duplicate names are deliberately removed from the join index.

Actual `dev_013` audit after the change:

| Attribute | Sale rows resolved | Missing | Distinct nonblank values |
|---|---:|---:|---:|
| Product catalogue join by unique name | 20,643 | 0 | — |
| Product group | 20,643 | 0 | 12 |
| Base unit | 20,643 | 0 | 13 |
| Brand | 18,683 | 1,960 | 36 |
| Milk number / diaper size | 9,868 | 10,775 | 11 |

The missing brand/size values are blank in the catalogue and remain `None`. Size values are the
documented labels `Size L/M/NB/XL/XXL` and `Số 0/1/2/3/4/9`; code must compare folded complete
labels, not infer a size from unrelated digits in a product name.

`Sale.product_brand` and `Sale.product_size` now carry this context. `vocabulary()` exposes both
sets for a future closed grammar. Existing base-unit conversion remains unchanged.

## 16. Product groups, customer groups and shares

- Product and customer groups remain separate fields: `Sale.product_group` and
  `Sale.customer_group`. A label must be resolved inside one namespace before planning.
- The name-only catalogue fix restores all 12 documented product groups for `dev_013`.
- On the 57-question `dev_013` subset, exactly three former abstentions now answer and no existing
  answer changes:

  | ID | Source filter | New answer type |
  |---|---|---|
  | `q_00c4e24fa228` | product group `đồ dùng` | number |
  | `q_3e36677e62a9` | product group `sữa bột`, whole-shop denominator | percent |
  | `q_b6d81403be69` | product group `bình sữa` | number |

- The share denominator is still whole-shop revenue for the same period, as defined by the current
  rulebook. The Data change does not alter the numerator/denominator calculation.
- Planner/Agent follow-up: order counts by product group must deduplicate `order_id`; two lines in
  the same group are one order. Brand/size quantity shares need an explicit approved filter before
  execution.

## 17. Payment method and channel

The source fields are independent and already preserved:

| Source shape | Payment field | Channel field |
|---|---|---|
| SQLite POS | `PhuongThucTT` | `KenhBanHang` |
| Combined sales export | `Hình thức thanh toán` | `Nguồn` |
| Restaurant ledger | — | `Nguồn` |
| Mother-and-baby ledger | — | `Kênh` |

Single-label questions already prove both paths on current main. Remaining audited questions are
grammar/composition cases (two labels in question order or a share), not missing data. A phone
sales channel must never be confused with a product whose name contains “điện thoại”.

Planner/Agent follow-up: emit one filter per requested label in question order. A payment share
uses whole-shop revenue in the same period; a channel filter must read only `Sale.channel`.

## 20. Credit sales and due dates

`load_credit_terms()` reads a complete README-described order/due-date source separately from the
debt payment ledger. Actual `dev_004` audit:

| Check | Result |
|---|---:|
| Credit-term records | 2,568 |
| Records joined to a sales order | 2,568 |
| Orphan records | 0 |
| Blank term or due date | 0 |
| Duplicate order IDs | 0 |

Each record preserves `order_id`, `due_date`, optional `term_days`, and row lineage. The adapter
rejects a partial source, invalid/negative term, invalid date, missing source, or duplicate order.

Planner/Agent follow-up: the six audited questions still abstain because no due-date intent or
contract exists. Count only documented credit orders, apply the observation date explicitly, and
keep “due after today” (`due_date > as_of_date`) separate from “due in month M”. Whether an order
already paid before the observation date is excluded must be defined by the debt contract before
execution is enabled.

## Verification

- Synthetic mapping tests cover name-only catalogue success and duplicate-name rejection.
- Staff tests cover unique names, duplicate-name abstention and conflicting duplicate codes.
- Credit-term tests cover a complete ledger plus incomplete, duplicate and invalid sources.
- Public subset artifact (gitignored): `outputs/runs/public-dev013-data-after-20261110/`.
- Baseline artifact (gitignored): `outputs/runs/public-r3-data-before-20261110/`.
- Combined 1,000-ID submission validates with **423 answered / 577 abstain**. Only the three
  documented `dev_013` group questions change from abstain to answered; artifact:
  `outputs/runs/public-r3-data-combined-20261110/`.
