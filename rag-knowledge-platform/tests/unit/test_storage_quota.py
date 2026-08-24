"""StorageQuotaExceededError shape and the quota arithmetic in isolation."""

import pytest

from rag_platform.exceptions import StorageQuotaExceededError


def test_error_is_413_with_named_reason():
    exc = StorageQuotaExceededError(used_bytes=450, limit_bytes=500, incoming_bytes=100)
    assert exc.status_code == 413
    assert exc.used_bytes == 450 and exc.limit_bytes == 500 and exc.incoming_bytes == 100
    # message names the real numbers so an operator can act on it
    assert "450" in str(exc) and "500" in str(exc) and "100" in str(exc)


@pytest.mark.parametrize(
    "used,incoming,limit,should_reject",
    [
        (0, 100, 500, False),      # first upload, well under
        (450, 40, 500, False),     # exactly at the edge but under
        (450, 51, 500, True),      # one byte over
        (500, 1, 500, True),       # already full
        (0, 100, 0, False),        # limit 0 = disabled, never rejects
    ],
)
def test_quota_threshold_logic(used, incoming, limit, should_reject):
    # Mirrors the check in IngestionService.register: reject iff a positive
    # limit is set and used + incoming exceeds it.
    rejected = limit > 0 and used + incoming > limit
    assert rejected is should_reject
