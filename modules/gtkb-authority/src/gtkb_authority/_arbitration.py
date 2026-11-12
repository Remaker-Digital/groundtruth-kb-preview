from __future__ import annotations
from typing import Any
PostgresTransaction = Any

ARBITRATION_KEYS = (2_147_483_000, 1)


def arbitrate(tx: PostgresTransaction) -> None:
    """First lock of every admission, manual claim and live DN control writer."""
    tx.cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", ARBITRATION_KEYS)
