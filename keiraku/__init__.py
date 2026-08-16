"""Keiraku — attach any AI agent harness to a shared memory source."""

from .core import (
    DEFAULT_STALE_AFTER,
    PROVENANCE_PEER,
    SPEC_VERSION,
    KeirakuError,
    NotASource,
    UnknownRecipient,
    Vessel,
    attach,
    derive_vessel_id,
    init_source,
    new_msg_id,
    read_presence,
    try_claim,
)

__version__ = "0.1.0"

__all__ = [
    "attach",
    "init_source",
    "derive_vessel_id",
    "read_presence",
    "new_msg_id",
    "try_claim",
    "Vessel",
    "KeirakuError",
    "NotASource",
    "UnknownRecipient",
    "SPEC_VERSION",
    "DEFAULT_STALE_AFTER",
    "PROVENANCE_PEER",
    "__version__",
]
