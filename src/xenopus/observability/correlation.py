"""Correlation IDs: end-to-end trace identifiers for every boundary.

Every event, request, and tool call carries a correlation id propagated
across agents, tools, and channels (Protocol v9 Section 12.4, addendum 44).
"""

from uuid import uuid4


def new_correlation_id() -> str:
    """Generate a fresh correlation id (UUID4, hex, prefixed for greppability)."""
    return f"corr-{uuid4().hex}"
