"""Compliance-fix regression tests (site audit 2026-10-28, items 1-8).

Asserts the DRAFT replacement copy from the compliance audit landed and
the blocklisted / contradicted claims are gone from the touched pages.
"""
from __future__ import annotations

import glob
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def prose(rel):
    """Visible prose: strip script/style bodies like the claim scanner."""
    text = read(rel)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    return text


class TestGenesisGate:
    def test_no_zero_humans_claim(self):
        assert "Zero humans" not in read("templates/genesis_gate.html")
        assert "zero humans" not in read("templates/genesis_gate.html").lower()

    def test_draft_copy_present(self):
        gate = read("templates/genesis_gate.html")
        assert "Named agents, sealed-bid auctions, settlement on Base" in gate
        assert "Autonomous agents. Sealed bids. Settlement on Base." in gate


class TestTerms:
    def test_token_access_not_investment_line(self):
        terms = prose("templates/terms.html")
        assert "SINC and AXM are utility tokens" in terms
        assert "Neither token is an investment, a security" in terms
        assert "no representation about any token's market price" in terms

    def test_section2_describes_marketplace(self):
        terms = prose("templates/terms.html")
        assert "execution layer for the autonomous machine economy" in terms
        assert "sealed-bid auctions" in terms
        assert "AI-powered business automation software delivered as a subscription service" not in terms
        assert "lead generation tools, content creation capabilities" not in terms

    def test_billing_matches_live_rail(self):
        terms = prose("templates/terms.html")
        assert "Checkout is in USDC on Base via x402 payment requests" in terms
        assert "Coinbase Onramp" in terms
        assert "Nothing is stored for recurring billing" in terms
        assert "subscriptions are paid in" not in terms
        assert "the standard subscription price will apply at the end of the promotional period" not in terms
        assert "a trial never converts to a charge automatically" in terms

    def test_fee_disclosure_no_burn(self):
        terms = prose("templates/terms.html")
        assert "5% platform fee" in terms
        assert "converted to USDC/WETH before deposit" in terms
        assert "No portion of the fee is burned" in terms

    def test_genesis_terms(self):
        terms = prose("templates/terms.html")
        assert "The genesis pass, any future airdrop, and the genesis SBT are free" in terms
        assert "non-transferable access credentials, carry no monetary value" in terms

    def test_sanctions_section(self):
        terms = prose("templates/terms.html")
        assert "Sanctions and Eligibility" in terms
        assert "OFAC Specially Designated Nationals" in terms
        assert "screened against sanctions lists before fulfillment" in terms

    def test_refund_mechanics_onchain(self):
        terms = prose("templates/terms.html")
        assert "refunded on-chain to the wallet that paid" in terms
        assert "via the original payment method" not in terms

    def test_dates_and_encoding(self):
        raw = read("templates/terms.html")
        assert "Last Updated:</strong> October 28, 2026" in raw
        assert "Γ" not in raw and "┬" not in raw


class TestEarnRefer:
    def test_earn_no_instant_no_burn_no_solicitation(self):
        earn = read("templates/earn.html")
        assert "instant" not in earn.lower()
        assert "LP burns" not in earn
        assert "attracts buyers" not in earn
        assert "Referral (3% ETH, paid by the contract on every curve buy)" in earn
        assert "liquidity milestone defined in the token spec" in earn

    def test_refer_no_audited_claim(self):
        refer = read("templates/refer.html")
        assert "audited" not in refer.lower()
        assert "instant" not in refer.lower()
        assert "SINC is the access token for the @sincor agent marketplace" in refer
        tagless = re.sub(r"<[^>]+>", " ", refer)
        assert "Bring a buyer.  Earn 3% — on-chain." in tagless


class TestPrivacy:
    def test_coinbase_onramp_disclosed(self):
        priv = prose("templates/privacy.html")
        assert "Coinbase (Onramp/CDP)" in priv
        assert "never your card number" in priv

    def test_sanctions_screening_disclosed(self):
        priv = prose("templates/privacy.html")
        assert "Sanctions screening" in priv
        assert "OFAC sanctions list (SDN)" in priv

    def test_ip_ration_disclosed(self):
        priv = prose("templates/privacy.html")
        assert "per-IP free-serve rations" in priv

    def test_no_billing_address_framing(self):
        priv = prose("templates/privacy.html")
        assert "billing address, and payment method details when you purchase" not in priv
        assert "We do not collect billing addresses or card numbers for wallet checkout" in priv

    def test_dates_and_encoding(self):
        raw = read("templates/privacy.html")
        assert "Last Updated:</strong> October 28, 2026" in raw
        assert "Γ" not in raw and "┬" not in raw


class TestUnlimited:
    def test_no_unlimited_on_plan_pages(self):
        for rel in ("templates/product_enterprise.html",
                    "templates/product_professional.html",
                    "templates/products_enterprise.html",
                    "templates/products_professional.html"):
            assert "nlimited" not in read(rel), rel

    def test_bounded_replacements_present(self):
        ent = prose("templates/product_enterprise.html")
        assert "Lead, email, and operation volumes set per enterprise agreement" in ent
        assert "Per agreement" in ent
        pro = prose("templates/product_professional.html")
        assert "Per agreement" in pro
        pe = prose("templates/products_enterprise.html")
        assert "nlimited" not in pe


class TestDefiPages:
    def test_p10_faq_corrected(self):
        p10 = prose("templates/products/p10-flash-arbitrage.html")
        assert "Is my principal at risk?</summary><div class=\"a\">No." not in p10
        assert "a bundle either completes profitably after gas or reverts" in p10
        assert "smart-contract, venue, and market risks remain" in p10
        assert "not live until it clears the platform's published go-live bar" in p10

    def test_yield_suffix_on_all_products(self):
        files = sorted(glob.glob(os.path.join(
            REPO_ROOT, "templates", "products", "p*.html")))
        assert len(files) == 26
        suffix = ("Design target for a product in development — not a live "
                  "return, not a projection of what you will earn.")
        numeric = 0
        for path in files:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            if re.search(r'Target yield</div><div class="v">[\d.]+%', text):
                numeric += 1
                assert suffix in text, os.path.basename(path)
        # 24 pages publish a numeric target yield; p20/p26 list the slot as
        # "Utility — infrastructure" with no figure to qualify.
        assert numeric == 24
