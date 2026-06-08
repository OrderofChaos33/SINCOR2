from __future__ import annotations

"""Use case viability scoring for SINC ecosystem expansion."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List


class UseCaseVertical(Enum):
    """Target ecosystem verticals for SINC expansion."""

    DEFI = 'defi'
    PAYMENTS = 'payments'
    GAMING = 'gaming'
    DAO = 'dao'
    ENTERPRISE = 'enterprise'
    NFTS = 'nfts'
    RWA = 'rwa'


@dataclass
class UseCaseParameters:
    """Input parameters used to evaluate a candidate ecosystem use case."""

    vertical: UseCaseVertical
    name: str
    addressable_user_base: float
    transaction_frequency: float
    fee_savings: float
    integration_complexity: float
    regulatory_risk: float
    time_to_market_months: float
    description: str = ''


@dataclass
class UseCaseBrief:
    """Structured brief describing a scored ecosystem expansion opportunity."""

    vertical: UseCaseVertical
    name: str
    viability_score: float
    rank: int
    technical_requirements: List[str]
    smart_contract_templates: List[str]
    economic_model: Dict[str, float | int]
    competitive_advantages: List[str]
    go_to_market_steps: List[str]
    generated_at: str


class UseCaseViabilityEngine:
    """Scores SINC ecosystem opportunities and turns them into launch briefs."""

    _VERTICAL_DEFAULTS: Dict[UseCaseVertical, Dict[str, List[str]]] = {
        UseCaseVertical.DEFI: {
            'technical_requirements': ['ERC-20 approval flow', 'liquidity pool hooks', 'oracle integration'],
            'smart_contract_templates': ['staking_pool', 'liquidity_mining', 'yield_aggregator'],
        },
        UseCaseVertical.PAYMENTS: {
            'technical_requirements': ['payment SDK', 'fiat on-ramp API', 'webhook listener'],
            'smart_contract_templates': ['recurring_billing', 'escrow', 'multi-sig_treasury'],
        },
        UseCaseVertical.GAMING: {
            'technical_requirements': ['wallet SDK', 'NFT mint API', 'game loop hooks'],
            'smart_contract_templates': ['nft_marketplace', 'play_to_earn_rewards', 'asset_bridge'],
        },
        UseCaseVertical.DAO: {
            'technical_requirements': ['governance module', 'snapshot voting', 'treasury multi-sig'],
            'smart_contract_templates': ['governance_voting', 'contribution_tracking', 'token_vesting'],
        },
        UseCaseVertical.ENTERPRISE: {
            'technical_requirements': ['REST API', 'data provenance hooks', 'AML check'],
            'smart_contract_templates': ['supply_chain_provenance', 'identity_staking', 'settlement_layer'],
        },
        UseCaseVertical.NFTS: {
            'technical_requirements': ['ERC-721/1155', 'royalty registry', 'metadata API'],
            'smart_contract_templates': ['nft_marketplace', 'royalty_splitter', 'trait_staking'],
        },
        UseCaseVertical.RWA: {
            'technical_requirements': ['asset tokenization SDK', 'KYC/AML layer', 'legal wrapper'],
            'smart_contract_templates': ['rwa_vault', 'tokenized_asset', 'compliance_oracle'],
        },
    }

    _ECONOMIC_MODELS: Dict[UseCaseVertical, Dict[str, float | int]] = {
        UseCaseVertical.DEFI: {'fee_bps': 30, 'burn_pct': 0.01, 'staking_reward_pct': 0.12},
        UseCaseVertical.PAYMENTS: {'fee_bps': 20, 'burn_pct': 0.005, 'staking_reward_pct': 0.06},
        UseCaseVertical.GAMING: {'fee_bps': 25, 'burn_pct': 0.015, 'staking_reward_pct': 0.1},
        UseCaseVertical.DAO: {'fee_bps': 15, 'burn_pct': 0.005, 'staking_reward_pct': 0.08},
        UseCaseVertical.ENTERPRISE: {'fee_bps': 18, 'burn_pct': 0.004, 'staking_reward_pct': 0.05},
        UseCaseVertical.NFTS: {'fee_bps': 35, 'burn_pct': 0.02, 'staking_reward_pct': 0.09},
        UseCaseVertical.RWA: {'fee_bps': 12, 'burn_pct': 0.003, 'staking_reward_pct': 0.07},
    }

    def score(self, params: UseCaseParameters) -> float:
        """Compute the core viability score for a use case candidate."""

        denominator = max(
            params.integration_complexity * params.regulatory_risk * params.time_to_market_months,
            0.001,
        )
        numerator = params.addressable_user_base * params.transaction_frequency * params.fee_savings
        return round(numerator / denominator, 4)

    def generate_brief(self, params: UseCaseParameters) -> UseCaseBrief:
        """Generate a launch brief for a scored ecosystem expansion opportunity."""

        defaults = self._VERTICAL_DEFAULTS[params.vertical]
        viability_score = self.score(params)
        technical_requirements = list(defaults['technical_requirements'])
        smart_contract_templates = list(defaults['smart_contract_templates'])
        economic_model = dict(self._ECONOMIC_MODELS[params.vertical])
        competitive_advantages = [
            f"SINC can enter the {params.vertical.value} segment with reusable token infrastructure instead of a net-new stack.",
            f"{params.name} targets measurable fee compression and a viability score of {viability_score:.4f}.",
            'Treasury routing and staking incentives keep partner, holder, and protocol interests aligned.',
        ]
        go_to_market_steps = [
            f"Validate {params.name} with 3-5 design partners in the {params.vertical.value} vertical.",
            f"Ship an MVP using {smart_contract_templates[0]} and {smart_contract_templates[1]} templates.",
            'Launch incentive pilots tied to staking, referral, and liquidity activation goals.',
            'Track retention, fee savings, and treasury contribution before broader rollout.',
        ]
        if params.description:
            competitive_advantages.append(params.description)
        return UseCaseBrief(
            vertical=params.vertical,
            name=params.name,
            viability_score=viability_score,
            rank=0,
            technical_requirements=technical_requirements,
            smart_contract_templates=smart_contract_templates,
            economic_model=economic_model,
            competitive_advantages=competitive_advantages,
            go_to_market_steps=go_to_market_steps,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def rank_use_cases(self, params_list: List[UseCaseParameters]) -> List[UseCaseBrief]:
        """Rank use cases from highest viability to lowest viability."""

        briefs = [self.generate_brief(params) for params in params_list]
        ranked_briefs = sorted(briefs, key=lambda brief: brief.viability_score, reverse=True)
        for index, brief in enumerate(ranked_briefs, start=1):
            brief.rank = index
        return ranked_briefs


__all__ = ['UseCaseBrief', 'UseCaseParameters', 'UseCaseVertical', 'UseCaseViabilityEngine']
