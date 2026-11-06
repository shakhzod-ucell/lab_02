# Lab 02 report

Student ID: [Your Student ID]
Submission PR/LMS link (record final SHA there): [Your Submission Link]
Python and package versions: Python 3.13.11, pandas 2.2.3, matplotlib 3.10.3

Write 800–1,100 words, excluding tables, captions, and code. Use short sentences.
Replace each TODO with your own result or explanation. Give numbers and file/table references.

## 1. Reading and checking records (T1–T2)

The system processes transaction records through a strict pipeline: JSON parsing, event validation, then duplicate checking. This order matters because invalid events must not reserve transaction IDs.

**Validation before duplicate checking:** When a line is read, the system first tries to parse it as JSON. Lines that fail JSON parsing (like line 18 with incomplete JSON `{"broken":`) are marked as `invalid_json`. Lines that parse but fail event validation (wrong currency, invalid amount, missing timezone) are marked as `invalid_event`. Only after passing all validation checks does the system check if the transaction ID already exists.

**First-valid-ID rule:** The system accepts only the first valid event for each ID. For example, if `T5` first appears with amount `0.00` (invalid - must be positive), that line is rejected but does NOT reserve the ID `T5`. When `T5` appears again with amount `13.00`, it can be accepted because the rejected line did not reserve the ID. However, `T4` appears first with amount `22.00` (valid, accepted), so when `T4` appears again with amount `999.00`, it is a duplicate and ignored.

**Metrics from transactions.jsonl:**
- Received: 20 lines (including blank line 21)
- Accepted: 11 valid transactions
- Duplicates: 2 (T4 and T5 retries after valid acceptance)
- Rejected: 7 (invalid JSON, invalid events)

Verification: 11 + 2 + 7 = 20 = received. The counts add up correctly.

**Examples:**
- Error (line 12): `{"tx_id": "N1", ..., "amount": true}` - Boolean amount rejected
- Duplicate (line 6): Second T4 with amount 999.00 ignored because T4 already accepted on line 5
- Accepted retry (line 8): T5 accepted with 13.00 because first T5 (line 7) was rejected for amount 0.00

## 2. Windows and data structures (T1–T3)

**Data structure choices:**

- **Frozen dataclass:** The `Transaction` class uses `frozen=True` to ensure immutability. Once created, a transaction cannot be modified, preventing accidental state corruption.

- **Generator (iter_jsonl):** Uses `yield` to lazily read one line at a time. This avoids loading the entire file into memory, enabling processing of arbitrarily large files.

- **Set (seen_ids):** Provides O(1) average-case lookup for duplicate detection. Using a list would be O(n) per lookup, making the total algorithm O(n^2).

- **defaultdict:** Automatically initializes missing keys for `account_totals` (Decimal zero) and `account_counts` (integer zero), simplifying the code.

- **Counter:** Efficiently tracks category frequencies per account to find the dominant category. Handles ties by selecting the smallest string.

- **deque with maxlen:** The `RollingWindow` uses a deque with fixed size for automatic eviction of old values. When the window is full, adding a new value automatically removes the oldest.

**Window total update:** The window maintains a running sum. When pushing a new value:
1. If the window is full, subtract the value being evicted from the total
2. Append the new value
3. Add the new value to the total

This gives O(1) arithmetic per event instead of O(W) for summing the entire window.

**Check before adding:** The anomaly check uses the **prior window mean** - the mean BEFORE adding the new amount. This is crucial because we compare the new payment against the historical baseline. If we added first, the large payment would inflate the mean and might not trigger the threshold.

**Exact threshold example:** Prior window `[10, 11, 12]` has mean 11. A new amount of 22 exactly equals `2 * 11 = 22`. Since `22 >= 22`, this triggers the anomaly flag. After adding 22, the window becomes `[11, 12, 22]` with mean 15.

**Decimal precision:** Amounts use Python's `Decimal` type with at least 28 digits of precision. This avoids floating-point errors in financial calculations. Division may produce repeating decimals (e.g., 33/3 = 11 exactly, but 10/3 rounds). We do not round the mean before comparing to preserve precision.

**Amount limits:** Maximum amount is 10^12 (one trillion). Account totals can reach 10^24 within Decimal precision. Amounts must be whole cents (no fractions like 12.501).

## 3. Time and memory experiment (T4)

**Timing benchmark:** I compared list vs set membership lookup time with three collection sizes (100, 1000, 10000) and 7 repeats per size. The timer measures only the lookup phase, NOT collection creation.

| Size | Queries | List median (ms) | Set median (ms) | Ratio |
|------|---------|------------------|-----------------|-------|
| 100 | ~600 | ~0.15 | ~0.03 | ~5x |
| 1000 | ~1500 | ~1.5 | ~0.05 | ~30x |
| 10000 | ~10500 | ~25 | ~0.07 | ~350x |

The ratio grows with size because list lookup is O(n) while set lookup is O(1). See `outputs/timing_benchmark.csv` and `outputs/benchmark_chart.png`.

**Platform:** Python 3.13.11 on macOS (darwin).

**Memory comparison:** Using `tracemalloc`, I compared eager loading (all lines into a list first) versus lazy loading (line-by-line generator). For the small test file, the difference is minimal (~1-5 KB). For large files, lazy loading would use significantly less memory since it holds only one line at a time.

**tracemalloc limitations:** It tracks only Python object allocations on the heap. It does NOT measure:
- Memory-mapped files
- C extension allocations
- Operating system buffers
- Stack memory

**Memory complexity:**
- U (unique IDs): O(U) - the seen_ids set grows with each unique valid transaction
- A (accounts): O(A * W) - each account keeps W values in its window (bounded)
- W (window size): Small W doesn't help because U and O grow unboundedly
- C (categories): O(A * C) - Counter per account tracks category frequencies
- O (output rows): O(anomalies + rejected) - both DataFrames can grow without limit

The seen-ID set and output DataFrames are the main sources of unbounded memory growth.

## 4. Limits and copying (T5)

**Copy demonstration:**
- Reference: `ref = original` - same object, changes visible everywhere
- Shallow copy: `copy.copy(original)` - new dict, but nested objects are shared
- Deep copy: `copy.deepcopy(original)` - fully independent, nested objects cloned

When modifying nested data in the original, shallow copies see the change (shared reference), but deep copies remain unchanged.

**Early payments cannot be flagged:** The anomaly rule requires at least 3 prior values in the window. Until an account has 3 accepted payments, no comparison baseline exists. This is a limitation of any window-based approach.

**Large payments raise the baseline:** When a flagged payment (22) enters window `[11, 12, 22]`, the new mean is 15. Future payments need `>= 30` to trigger, higher than before. This can cause "threshold drift" where detecting one anomaly makes the next harder to detect.

**Arrival vs event time:** We process lines in file order (arrival), not by `occurred_at` timestamp. This matches real-time stream processing but may miss patterns visible in chronological order.

**Alternative baseline:** Use median instead of mean. This is more robust to outliers but still influenced by the same window contents and has the same warm-up problem.

**Old state deletion:** Could expire IDs after a time window (e.g., 24 hours). Trade-off: An expired duplicate ID might be accepted again, breaking the uniqueness guarantee. This is acceptable if the business rule allows reprocessing old transactions.

## 5. Run and check (T6)

**Commands:**
```bash
# Run tests
python -m pytest tests/test_public.py -v

# Run notebook
jupyter nbconvert --execute notebooks/lab.ipynb --to html
```

**Extra tests:** The notebook demonstrates:
- Immutability check (FrozenInstanceError)
- Five invalid input cases (Boolean, NaN, EUR, no timezone, fractional cents)
- Lazy iterator behavior (single-pass)
- Exact threshold example (22 >= 2*11)
- Copy behavior (reference, shallow, deep)

**Outputs:** All files saved to `outputs/`:
- metrics.json, summary.json, anomalies.json, rejected.json
- timing_benchmark.csv, memory_benchmark.csv, benchmark_chart.png

**Status:** All 6 public tests pass. The notebook runs from a fresh kernel without errors.

## AI-use note

Complete AI_USE.md. State the help used, what you changed, and how you checked it.
