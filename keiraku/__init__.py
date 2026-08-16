"""Keiraku — attach any AI agent harness to a shared memory source."""

from .core import (
    DEFAULT_STALE_AFTER,
    PROVENANCE_PEER,
    SPEC_VERSION,
    KeirakuError,
    LockUnavailable,
    NotASource,
    UnknownRecipient,
    Vessel,
    attach,
    derive_vessel_id,
    init_source,
    new_msg_id,
    list_state,
    read_presence,
    read_state,
    state_filename,
    try_claim,
)

__version__ = "0.1.0"

__all__ = [
    "attach",
    "init_source",
    "derive_vessel_id",
    "read_presence",
    "read_state",
    "list_state",
    "state_filename",
    "new_msg_id",
    "try_claim",
    "Vessel",
    "KeirakuError",
    "NotASource",
    "LockUnavailable",
    "UnknownRecipient",
    "SPEC_VERSION",
    "DEFAULT_STALE_AFTER",
    "PROVENANCE_PEER",
    "__version__",
]
