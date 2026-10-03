"""Verified TLS with public intermediates for an incomplete server chain."""
from __future__ import annotations

from pathlib import Path
import ssl

import certifi

_SUPPLEMENTAL_CHAIN = Path(__file__).with_name("certificates") / "letsencrypt-ye1-chain.pem"


def create_verified_context() -> ssl.SSLContext:
    """Require a valid hostname and a complete path to an existing trusted root.

    The supplemental certificates are cross-signed public intermediates, not
    new trust anchors. Disable partial-chain validation so they cannot replace
    a root already present in certifi. No network or session activity occurs.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    context.load_verify_locations(cafile=str(_SUPPLEMENTAL_CHAIN))
    return context
