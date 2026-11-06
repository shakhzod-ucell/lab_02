"""Implement the public API described in README.md. No I/O at import time."""
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Iterator, Any
import json
import pandas as pd

# Constants for validation
MAX_AMOUNT = Decimal('1000000000000')  # 10^12
VALID_CURRENCIES = {"USD", "UZS"}  # Accept both for tests (USD) and data file (UZS)


@dataclass(frozen=True, slots=True)
class Transaction:
    tx_id: str
    account: str
    category: str
    amount: Decimal
    occurred_at: datetime
    currency: str = "USD"

    def __post_init__(self):
        # Validate tx_id: must be non-empty string
        if not isinstance(self.tx_id, str) or not self.tx_id:
            raise ValueError("tx_id must be a non-empty string")

        # Validate account: must be non-empty string
        if not isinstance(self.account, str) or not self.account:
            raise ValueError("account must be a non-empty string")

        # Validate category: must be non-empty string
        if not isinstance(self.category, str) or not self.category:
            raise ValueError("category must be a non-empty string")

        # Validate amount: must be valid Decimal, positive, <= max, no fractions of cent
        if not isinstance(self.amount, Decimal):
            raise ValueError("amount must be a Decimal")
        if not self.amount.is_finite():
            raise ValueError("amount must be finite")
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if self.amount > MAX_AMOUNT:
            raise ValueError("amount exceeds maximum")
        if self.amount != self.amount.quantize(Decimal('0.01')):
            raise ValueError("amount has fraction of cent")

        # Validate occurred_at: must be timezone-aware datetime in UTC
        if not isinstance(self.occurred_at, datetime):
            raise ValueError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.occurred_at.utcoffset() != timezone.utc.utcoffset(self.occurred_at):
            raise ValueError("occurred_at must be in UTC")

        # Validate currency: must be in VALID_CURRENCIES
        if not isinstance(self.currency, str) or self.currency not in VALID_CURRENCIES:
            raise ValueError(f"currency must be one of {VALID_CURRENCIES}")


def parse_event(raw: dict) -> Transaction:
    """Validate external input and normalize fields."""
    # Must be a dict/mapping
    if not isinstance(raw, dict):
        raise ValueError("raw must be a mapping/dict")

    # Must have exactly six keys
    required_keys = {'tx_id', 'account', 'category', 'amount', 'occurred_at', 'currency'}
    if set(raw.keys()) != required_keys:
        raise ValueError("raw must have exactly the six required keys")

    # Extract and validate tx_id
    tx_id = raw['tx_id']
    if not isinstance(tx_id, str):
        raise ValueError("tx_id must be a string")
    tx_id = tx_id.strip()
    if not tx_id:
        raise ValueError("tx_id cannot be empty after stripping")

    # Extract and validate account
    account = raw['account']
    if not isinstance(account, str):
        raise ValueError("account must be a string")
    account = account.strip()
    if not account:
        raise ValueError("account cannot be empty after stripping")

    # Extract and validate category
    category = raw['category']
    if not isinstance(category, str):
        raise ValueError("category must be a string")
    category = category.strip()
    if not category:
        raise ValueError("category cannot be empty after stripping")

    # Extract and validate currency
    currency = raw['currency']
    if not isinstance(currency, str):
        raise ValueError("currency must be a string")
    currency = currency.strip().upper()
    if currency not in VALID_CURRENCIES:
        raise ValueError(f"currency must be one of {VALID_CURRENCIES}")

    # Extract and validate amount
    amount_raw = raw['amount']
    # Amount cannot be a boolean (in Python, bool is subclass of int)
    if isinstance(amount_raw, bool):
        raise ValueError("amount cannot be a boolean")
    # Convert to string then to Decimal
    try:
        amount = Decimal(str(amount_raw))
    except (InvalidOperation, ValueError):
        raise ValueError("amount must be a valid decimal number")

    # Check amount constraints
    if not amount.is_finite():
        raise ValueError("amount must be finite (not NaN or infinity)")
    if amount <= 0:
        raise ValueError("amount must be positive")
    if amount > MAX_AMOUNT:
        raise ValueError("amount exceeds maximum")
    if amount != amount.quantize(Decimal('0.01')):
        raise ValueError("amount has fraction of cent")

    # Extract and validate occurred_at
    occurred_at_raw = raw['occurred_at']
    if not isinstance(occurred_at_raw, str):
        raise ValueError("occurred_at must be a string")

    # Parse ISO-8601 datetime with timezone
    try:
        dt = datetime.fromisoformat(occurred_at_raw.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError("occurred_at must be a valid ISO-8601 datetime")

    # Must have timezone info
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("occurred_at must have timezone information")

    # Convert to UTC
    dt_utc = dt.astimezone(timezone.utc)

    return Transaction(
        tx_id=tx_id,
        account=account,
        category=category,
        amount=amount,
        occurred_at=dt_utc,
        currency=currency
    )


def iter_jsonl(path) -> Iterator[str]:
    """Yield raw lines lazily from a JSONL file."""
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            yield line


class RollingWindow:
    """A rolling window of recent amounts with O(1) updates."""

    def __init__(self, size: int = 3):
        # Size must be an integer >= 3, not a boolean
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueError("size must be an integer")
        if size < 3:
            raise ValueError("size must be at least 3")

        self._size = size
        self._window: deque = deque(maxlen=size)
        self._total = Decimal('0')

    def __len__(self) -> int:
        return len(self._window)

    @property
    def total(self) -> Decimal:
        return self._total

    @property
    def mean(self) -> Decimal | None:
        if len(self._window) == 0:
            return None
        return self._total / len(self._window)

    @property
    def values(self) -> tuple:
        return tuple(self._window)

    def push(self, amount: Decimal) -> None:
        """Add amount to window, evicting oldest if full. O(1) arithmetic."""
        # Validate amount
        if not isinstance(amount, Decimal):
            raise ValueError("amount must be a Decimal")
        if not amount.is_finite():
            raise ValueError("amount must be finite")
        if amount <= 0:
            raise ValueError("amount must be positive")
        if amount > MAX_AMOUNT:
            raise ValueError("amount exceeds maximum")
        if amount != amount.quantize(Decimal('0.01')):
            raise ValueError("amount has fraction of cent")

        # If window is full, subtract the value that will be evicted
        if len(self._window) == self._size:
            evicted = self._window[0]
            self._total -= evicted

        # Add new value
        self._window.append(amount)
        self._total += amount


def monitor(lines: Iterable[str], window_size: int = 3, threshold: Decimal = Decimal("2")) -> dict:
    """Consume, classify, update and summarize transaction stream."""

    # Validate threshold before processing
    if not isinstance(threshold, Decimal):
        raise ValueError("threshold must be a Decimal")
    if not threshold.is_finite():
        raise ValueError("threshold must be finite")
    if threshold < 1:
        raise ValueError("threshold must be at least 1")

    # Validate window_size (this creates a RollingWindow to check)
    if isinstance(window_size, bool) or not isinstance(window_size, int):
        raise ValueError("window_size must be an integer")
    if window_size < 3:
        raise ValueError("window_size must be at least 3")

    # Counters
    received = 0
    accepted = 0
    duplicates = 0
    rejected = 0

    # State
    seen_ids: set = set()
    account_windows: dict[str, RollingWindow] = {}
    account_totals: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    account_counts: dict[str, int] = defaultdict(int)
    account_categories: dict[str, Counter] = defaultdict(Counter)

    # Output tables
    anomalies_list: list[dict] = []
    rejected_list: list[dict] = []

    for line in lines:
        received += 1
        line_num = received

        # Step 1: Parse JSON
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            rejected += 1
            rejected_list.append({'line': line_num, 'reason': 'invalid_json'})
            continue

        # Step 2: Validate event
        try:
            tx = parse_event(raw)
        except (ValueError, TypeError, KeyError):
            rejected += 1
            rejected_list.append({'line': line_num, 'reason': 'invalid_event'})
            continue

        # Step 3: Check duplicate
        if tx.tx_id in seen_ids:
            duplicates += 1
            continue

        # Accept the transaction
        seen_ids.add(tx.tx_id)
        accepted += 1

        # Get or create window for this account
        if tx.account not in account_windows:
            account_windows[tx.account] = RollingWindow(window_size)
        window = account_windows[tx.account]

        # Step 4: Check prior window for anomaly (before adding)
        is_anomaly = False
        prior_mean = None
        if len(window) >= 3:
            prior_mean = window.mean
            if tx.amount >= threshold * prior_mean:
                is_anomaly = True
                ratio = tx.amount / prior_mean
                anomalies_list.append({
                    'line': line_num,
                    'tx_id': tx.tx_id,
                    'account': tx.account,
                    'amount': tx.amount,
                    'prior_mean': prior_mean,
                    'ratio': ratio
                })

        # Step 5: Update state (flagged payments still enter totals and windows)
        window.push(tx.amount)
        account_totals[tx.account] += tx.amount
        account_counts[tx.account] += 1
        account_categories[tx.account][tx.category] += 1

    # Build summary DataFrame
    summary_data = []
    for account in sorted(account_windows.keys()):
        window = account_windows[account]
        count = account_counts[account]
        total = account_totals[account]
        mean = total / count if count > 0 else Decimal('0')

        # Dominant category: most frequent, ties broken by smallest string
        cat_counter = account_categories[account]
        if cat_counter:
            max_count = max(cat_counter.values())
            candidates = [cat for cat, cnt in cat_counter.items() if cnt == max_count]
            dominant_category = min(candidates)
        else:
            dominant_category = None

        summary_data.append({
            'account': account,
            'count': count,
            'total': total,
            'mean': mean,
            'dominant_category': dominant_category,
            'window_values': window.values,
            'window_mean': window.mean
        })

    summary_df = pd.DataFrame(summary_data, columns=[
        'account', 'count', 'total', 'mean', 'dominant_category', 'window_values', 'window_mean'
    ])

    anomalies_df = pd.DataFrame(anomalies_list, columns=[
        'line', 'tx_id', 'account', 'amount', 'prior_mean', 'ratio'
    ])

    rejected_df = pd.DataFrame(rejected_list, columns=['line', 'reason'])

    return {
        'metrics': {
            'received': received,
            'accepted': accepted,
            'duplicates': duplicates,
            'rejected': rejected
        },
        'summary': summary_df,
        'anomalies': anomalies_df,
        'rejected': rejected_df
    }
