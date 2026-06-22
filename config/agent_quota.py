from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentQuota:
    """
    Tracks agent capacity for an organization/subscription.

    This is the foundation for the new pricing model with base quota + add-ons.
    """
    organization_id: str
    base_quota: int = 100          # Default high cap (Scale plan)
    purchased_addons: int = 0      # Extra agents bought
    active_agents: int = 0         # Currently deployed agents

    @property
    def total_quota(self) -> int:
        return self.base_quota + self.purchased_addons

    @property
    def remaining(self) -> int:
        return max(0, self.total_quota - self.active_agents)

    def can_deploy(self, count: int = 1) -> bool:
        return self.remaining >= count

    def add_addon(self, slots: int):
        self.purchased_addons += slots

    def remove_addon(self, slots: int):
        self.purchased_addons = max(0, self.purchased_addons - slots)

    def deploy_agents(self, count: int):
        if not self.can_deploy(count):
            raise ValueError("Not enough agent quota remaining")
        self.active_agents += count

    def release_agents(self, count: int):
        self.active_agents = max(0, self.active_agents - count)


# Example usage
# quota = AgentQuota(organization_id="org_123", base_quota=100)
# quota.add_addon(25)
# print(quota.total_quota)  # 125