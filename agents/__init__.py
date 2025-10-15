from .leadgen_agent import LeadGenAgent
from .compliance_agent import ComplianceAgent
from .pricing_engine import PricingEngine
from .media_agent import MediaAgent
from .booking_agent import BookingAgent

# Registry of coroutine-capable agents
registry = {
    "leadgen": LeadGenAgent(),
    "compliance": ComplianceAgent(),
    "pricing": PricingEngine(),
    "media": MediaAgent(),
    "booking": BookingAgent(),
}
