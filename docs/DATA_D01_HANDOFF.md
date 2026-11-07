# DATA-D01 — debt ledger adapter handoff

**Owner:** Data & Schema (`@longlth2297`)

**Reviewer:** Agent (`@tqhuy0602`)

**Branch:** `codex/data-d01-debt-ledger`

**Contract dependency:** C01-fixes `9c4e726`; aligned with the Data notes in C01 PR #26

**Status:** review fixes, synthetic regression and reviewed development-source
verification complete; awaiting team re-review.

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
- reviewed structural profiles for the KiotViet-style POS and BanHang exports;
- strict detection of incomplete sibling files/sheets before role selection;
- VND/thousand-VND unit handling with evidence and unsupported-unit rejection;
- zero-payment retention/counting and non-negative receivable reconciliation.
- per-`file::sheet` completeness before `absent`, file-family role propagation, and
  paid-to-date reconciliation.

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
| Missing capability | Partial/ambiguous source → `partial`/typed abstention error; only a fully documented shop with no numeric debt signal receives `absent`. Deadline-only tables are not numeric debt signals. |
| Coverage | README `Dữ liệu có đến hết …` first; otherwise latest observed order/payment date with lineage source `observed`. A README date earlier than a record is `reconcile`. |
| Closing debt | For every non-cancelled, non-excluded order, a confirmed non-negative receivable is reconciled as `max(total - payments_through_last_day, 0)`. |
| Paid-to-date | When the order source exposes `Khách đã trả`/`Đã thu`, every order is reconciled against payments through `last_day`; mismatch → `reconcile`. |
| Money sign | Negative totals/payments → `amount`; zero payments are retained and counted in lineage. |
| Money unit | Default source currency is VND. Global VND/đồng → ×1; nghìn/ngàn VND/đồng → ×1,000. Numeric factors, bare `k`/nghìn, foreign, scoped or conflicting declarations → `amount`. |
| Missing channel | Remains `partial`; D01 does not infer `Store` or absence of marketplace orders. |

## 3. Files changed

| File | Purpose |
|---|---|
| `src/datamate/ingestion/debt.py` | Debt source discovery, parsing, validation, reconciliation and lineage. |
| `tests/test_ingestion_debt.py` | Synthetic D01 regression for valid and rejected source cases. |
| `scripts/verify_debt_train.py` | Development-only audit runner with validation-business guard and source checksums. |
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
- [x] Incomplete second payment file is rejected instead of silently skipped.
- [x] Deadline-only source is `absent`; paid/closing amount hints are partial.
- [x] VND, thousand-VND conversion and unsupported money unit.
- [x] Coded customer keeps its code when the display name is `Khách lẻ`.
- [x] Zero payment is retained/countable; overpayment reconciles to zero receivable.
- [x] Reviewed POS profile resolves report/source roles without business-id dispatch.
- [x] Plain/bold debt convention without a usable schema mapping is `partial`, never `absent`.
- [x] POS profile rejects an incomplete sibling payment file and unions it when complete.
- [x] Foreign, conflicting or per-source money-unit declarations are `amount` errors.
- [x] Evidence runner resolves relative paths and binds revision to adapter source/manifest SHA.
- [x] Markdown `### Công nợ` sections are debt hints even when the mapping is in later lines.
- [x] POS payment siblings missing date, order id, or amount are all rejected as `partial`.
- [x] Global `nghìn VND` is converted; unknown declarations such as JPY are `amount`.
- [x] `absent` requires every physical `file::sheet` to match a reviewed mapping/profile.
- [x] A recognised `SoQuy_*` family rejects siblings missing two or all required fields.
- [x] Numeric/bare money factors (`1.000`, `1000`, `x1000`, bare nghìn/k) are `amount`.
- [x] `paid_to_date` mismatch is `reconcile`; successful lineage records checks/mismatches.

## 5. Verification run

Run from the repository root:

```sh
python -m datamate smoke
python -m unittest tests.test_ingestion_debt tests.test_contract_c01 -v
python -m unittest discover -s tests
git diff --check
```

Local Windows checkout results at the review-fix revision:

| Check | Result |
|---|---|
| D01 + evidence-runner focused tests | 21 passed |
| Reviewed raw development sources | `biz_109`: 33,065 orders / 36,974 payments; `biz_124`: 51,044 / 51,238 |
| Closing reconciliation | `biz_124`: 47,852 applicable orders checked, 0 mismatch |
| Paid-to-date reconciliation | `biz_109`: 33,065 checked; `biz_124`: 51,044 checked; 0 mismatch |
| Missing-channel capability | `biz_106`, `biz_129`: typed `partial`, no inferred channel |
| Deadline-only capability | `biz_116`, `biz_118`: `absent` |
| Smoke | 24/24 records; local proxy 21 correct, 0 wrong, 3 abstain |
| Full suite | 276 passed in 33.302s |
| `git diff --check` / compile | Pass |

The proxy score is local and not an official competition score.

## 6. Development-source evidence

Generate the ignored, non-raw evidence artifact with:

```sh
python scripts/verify_debt_train.py --train-root <train> \
  --manifest outputs/agent-integration-holdout-20261104-162921.json \
  --output outputs/d01_train_evidence.json
```

The artifact records the script checkout revision, adapter/manifest/rulebook/source
SHA256, status, counts and full safe lineage. The script resolves relative paths, verifies
the selected businesses belong to the manifest's train split and rejects its validation
businesses. Current reviewed result:

- `biz_109`: supported POS workbook/multi-sheet source;
- `biz_124`: supported BanHang orders/payments/customers and exact closing reconciliation;
- `biz_106`, `biz_129`: unsupported until C01 decides the no-channel capability.

The full 24-business manifest audit remains **2 supported ledgers, 6 `partial`, 16
`absent`**. It is written to the ignored `outputs/d01_train_all_evidence.json` artifact.

External C01 acceptance artifacts still belong to the C01 owner:

- `C01/verify/verify_c01_train.py`;
- `C01/verify/logs/verify_c01_train_20261106.txt`;
- the remaining C01 verification log and script referenced by that contract;
- explicit diagnostics for the report-style shop whose five table questions were not
  included in the manual `7/7` result.

Do not use validation/public/private businesses to add aliases or tune this adapter.

## 7. Team review requested

### Data / C01 owner

- [ ] Confirm the payment/channel allowlist covers only labels reviewed in train README/data.
- [x] Zero payment is retained; negative payment is rejected with `amount` per C01 PR #26.
- [ ] Confirm fallback coverage to latest order/payment record when README has no date.
- [x] Use the fixed group holdout manifest and record its checksum in D01 evidence.
- [x] Run adapter on the four reviewed development debt sources and attach lineage/error summary.

### Huy / Agent reviewer

- [ ] Confirm `DebtSourceError.code` handling: only `absent` may support `unanswerable`;
  `partial`, `amount`, `customer_mode`, `rules_missing`, `column_missing`, `key`,
  `orphan`, `label` and `reconcile` must abstain.
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
