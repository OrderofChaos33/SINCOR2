"""Shared fixtures: an in-process transport wiring VerifiedBuyer straight
into a real VerifiedEndpoint — the full loop with mocked platform +
mocked facilitator, no network anywhere.

Plain helper functions/constants live in x402pkg_helpers.py and are
imported by the test modules directly; only fixtures live here.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sincor_x402 import (LocalFacilitator, LocalReferencePlatform,
                         PaymentRequirement, VerifiedEndpoint)
from sincor_x402.types import USDC_BASE_ADDRESS

from x402pkg_helpers import AMOUNT_ATOMIC, TREASURY, CountingSigner, good_work


@pytest.fixture()
def requirement():
    return PaymentRequirement(
        scheme="exact", network="base", max_amount_required=AMOUNT_ATOMIC,
        asset=USDC_BASE_ADDRESS, pay_to=TREASURY, max_timeout_seconds=60,
        resource="/summarize", description="Summarize a document")


@pytest.fixture()
def platform():
    return LocalReferencePlatform()


@pytest.fixture()
def facilitator():
    return LocalFacilitator()


@pytest.fixture()
def spec():
    return {"required_keys": ["answer"]}


@pytest.fixture()
def endpoint(requirement, platform, facilitator, spec):
    return VerifiedEndpoint(good_work, requirement=requirement, spec=spec,
                            platform=platform, facilitator=facilitator)


@pytest.fixture()
def signer():
    return CountingSigner()
