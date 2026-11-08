"""Pure retry timing for a delivery waiting on another operation's receipt."""

CARD_RECEIPT_PENDING = 'Ожидается подтверждение создания исходной карточки'


def dependency_retry_delay(attempt: int) -> float:
    """Back off 15s..15m; a durable receipt can wake the waiter earlier.

    This schedules observation only. It never authorizes replay of the
    operation whose delivery is uncertain, or changes that operation's state.
    Cap the exponent before computing it, including for historical hot loops.
    """
    return float(min(900, 15 * 2 ** min(6, max(0, attempt - 1))))
