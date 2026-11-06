# DATA-D01 — debt ledger adapter handoff

**Owner:** Data & Schema (`@longlth2297`)

**Reviewer:** Agent (`@tqhuy0602`)

**Branch:** `codex/data-d01-debt-ledger`

**Contract dependency:** C01-fixes `9c4e726`

**Status:** implementation and synthetic regression complete; awaiting team review and
train-artifact verification.

## 1. Scope delivered

D01 implements the Data side of the C01 debt contract without changing Semantics or
Agent execution:

- `DebtSourceError(code, message, **diagnostics)` with stable source-error codes;
- `load_debt_ledger(catalog, rulebook) -> DebtLedger`;
- README-driven order/payment/customer column mapping;
- explicit `Công nợ` convention validation, including `table.column` references;
- closed mappings for order status, payment method and sales channel;
- order-key uniqueness, payment orphan and required-value checks;
- one-to-many order/payment representation without multiplying order totals;
- optional closing-debt reconciliation at the documented coverage date;
- `uses_customer_codes` derived from README/schema, never from observed row values;
- `customer_mode` failure when the README/schema cannot determine the shop mode;
- source, column, README, rule, label, coverage and check lineage.

The adapter reuses the existing catalog/read-only table readers and README record helpers,
so CSV, XLSX, SQLite and XLSX report headers follow the same source handling as DATA-03.

## 2. Contract decisions implemented

| Contract item | D01 behavior |
|---|---|
| Order total | Uses the README column described as the amount the customer must pay for the whole order; never line revenue. |
| Payments | Keeps one `DebtPayment` per source row; several payments for one order do not duplicate the `DebtOrder`. |
| Customer mode | Order schema has a documented customer-code column → `uses_customer_codes=True`; schema has customer name but no code → `False`; inconsistent/unknown → `customer_mode`. |
| Missing code in coded shop | Keeps `customer_id=None`; the name remains display/lineage data and must not become the customer key. |
| Walk-in | Reviewed walk-in labels become no customer key. |
| Status | Uses the shared four-state `order_state`; unknown labels → `label`. |
| COD/marketplace | Stores `excluded="cod"` or `excluded="marketplace"`; no debt calculation is performed in D01. |
| Missing capability | Partial/ambiguous source → abstention error; only a fully documented shop with no debt source receives `absent`. |
| Coverage | README `Dữ liệu có đến hết …` first; otherwise latest observed order/payment date. A README date earlier than a record is `reconcile`. |
| Closing debt | For every non-cancelled, non-excluded order: `total - payments_through_last_day` must exactly equal the documented closing value. |
| Money sign | Negative order totals and non-positive payment rows are rejected unless a later reviewed contract explicitly supports reversal/refund rows. |

## 3. Files changed

| File | Purpose |
|---|---|
| `src/datamate/ingestion/debt.py` | Debt source discovery, parsing, validation, reconciliation and lineage. |
| `tests/test_ingestion_debt.py` | Synthetic D01 regression for valid and rejected source cases. |
| `docs/DATA_D01_HANDOFF.md` | This review handoff. |

No changes were made to `contracts.py`, `semantics/`, Agent executor/validation,
pipeline, scorer or model code beyond inheriting the C01-fixes base commit.

## 4. Regression covered

- [x] Valid ledger with several payments for one order.
- [x] Decimal totals and exact closing-debt reconciliation.
- [x] Duplicate/missing order key.
- [x] Payment orphan.
- [x] Unknown order/payment/channel labels.
- [x] Negative payment value.
- [x] README coverage earlier than an observed record.
- [x] Reconciliation mismatch and missing closing balance.
- [x] Missing debt rule rows.
- [x] Partial source is not misreported as `absent`.
- [x] `absent` requires a complete README with no debt source/hint.
- [x] Plain column references and qualified `table.column` references.
- [x] Marketplace label with reviewed `Sàn <marketplace>` prefix.
- [x] Shop with codes even when every observed order code is blank.
- [x] Shop without codes, identified by customer-name schema.
- [x] Ambiguous customer mode → `DebtSourceError("customer_mode")`.
- [x] Lineage records sources, fields, rules, checks, coverage and customer-mode basis.

## 5. Verification run

Run from the repository root:

```sh
python -m datamate smoke
python -m unittest tests.test_ingestion_debt tests.test_contract_c01 -v
python -m unittest discover -s tests
git diff --check
```

Local Windows checkout results after rebasing onto C01-fixes:

| Check | Result |
|---|---|
| C01 + D01 focused tests | 27 passed |
| Smoke | 24/24 records; local proxy 21 correct, 0 wrong, 3 abstain |
| Full suite | 267 passed in 29.150s |
| `git diff --check` | Pass |

The proxy score is local and not an official competition score.

## 6. Evidence still required before calling D01 complete on train

The repository checkout does not contain raw train data or the C01 external verification
folder. Before merge/acceptance, the team should provide or run:

- `C01/verify/verify_c01_train.py`;
- `C01/verify/logs/verify_c01_train_20261106.txt`;
- the fixed `business_split.json` manifest and its checksum;
- a run of `load_debt_ledger` on the train businesses selected by C01;
- per-business source/README checksums and adapter lineage;
- confirmation that closing-debt reconciliation has zero mismatched applicable orders;
- explicit diagnostics for the report-style shop whose five table questions were not
  included in the manual `7/7` result.

Do not use validation/public/private businesses to add aliases or tune this adapter.

## 7. Team review requested

### Data / C01 owner

- [ ] Confirm the payment/channel allowlist covers only labels reviewed in train README/data.
- [ ] Confirm rejecting payment `<= 0` is correct, or provide a reviewed reversal/refund rule.
- [ ] Confirm fallback coverage to latest order/payment record when README has no date.
- [ ] Provide the C01 external verification folder, manifest and checksums.
- [ ] Run the adapter on the C01 development businesses and attach lineage/error summary.

### Huy / Agent reviewer

- [ ] Confirm `DebtSourceError.code` handling: only `absent` may support `unanswerable`;
  `customer_mode`, `rules_missing`, `column_missing`, `key`, `orphan`, `label` and
  `reconcile` must abstain.
- [ ] Confirm `uses_customer_codes` and retained display name are sufficient for A01
  customer-key/evidence behavior.
- [ ] Confirm `prepare_debt` will cache one ledger per business/rulebook in the worker.
- [ ] Confirm A01 does not mutate/reinterpret D01 totals, labels or customer mode.
- [ ] Review lineage fields needed for executor evidence and validation.

### Kurosuz / Semantics

- [ ] Confirm S01 relies on C01 intent/rule gates and does not infer debt capability from
  a column name alone.
- [ ] Confirm unsupported filters/combinations continue to abstain.

### Integration / Evaluation

- [ ] Rebase/merge order is C01 → D01/S01 → A01 → integrated benchmark.
- [ ] Run CI on the final head and paired benchmark on the fixed holdout.
- [ ] Report every newly wrong ID; do not accept only an aggregate score increase.

## 8. Known boundaries

D01 intentionally does **not**:

- parse debt questions or create `Plan` objects (S01);
- calculate `revenue_increasing`, debt totals, rankings or flows (A01);
- turn source failures into answer types (A01 validation);
- add payment filters to `Sale`;
- implement debt-age bins or empty-table semantics;
- enable new intents in the local model backend;
- claim the manual C01 train counts have been independently reproduced.

## 9. Suggested PR summary

> Implements DATA-D01 on top of C01-fixes: a README-driven, read-only debt ledger
> adapter with explicit customer-code mode, typed source failures, key/orphan checks,
> closed label handling, coverage validation, closing-balance reconciliation and
> audit lineage. It does not change Plan grammar or Agent execution. Synthetic C01/D01
> regression and the full repository suite pass; train acceptance remains pending the
> external C01 verification artifacts and reviewer checks listed in this handoff.
