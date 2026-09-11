"""Production Pydantic schemas for the auto detailing (CHROMA) vertical."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .config import DEFAULT_SHOP


class TaskInput(BaseModel):
    task_type: str
    payload: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class TaskOutput(BaseModel):
    status: str = Field(..., pattern="^(success|error|partial)$")
    result: Dict[str, Any]
    error: Optional[str] = None
    correlation_id: Optional[str] = None


class Vehicle(BaseModel):
    year: Optional[int] = Field(None, ge=1950, le=2035)
    make: Optional[str] = None
    model: Optional[str] = None
    body_style: Optional[str] = None
    color: Optional[str] = None
    condition: Optional[str] = Field(
        None, description="showroom | clean | daily | dirty | neglected"
    )
    size: Optional[str] = None


class LeadIngestRequest(BaseModel):
    source: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    message: str = ""
    vehicle: Optional[Vehicle] = None
    photo_urls: List[str] = Field(default_factory=list)
    zip_code: Optional[str] = None
    landing_page: Optional[str] = None
    utm: Dict[str, str] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def source_known(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "_")


class QuoteRequest(BaseModel):
    package_id: str
    vehicle: Vehicle = Field(default_factory=Vehicle)
    addons: List[str] = Field(default_factory=list)
    mobile: bool = False


class BookingHandoffRequest(BaseModel):
    lead_id: Optional[str] = None
    package_id: str
    vehicle: Vehicle = Field(default_factory=Vehicle)
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    calendly_handle: str = str(DEFAULT_SHOP["calendly_handle"])
    preferred_slot: Optional[str] = None
    weather_precip_pct: Optional[int] = None


class CopyRequest(BaseModel):
    channel: str = Field(..., description="google_rsa | meta | gbp | sms | email | landing")
    package_id: Optional[str] = None
    city: str = str(DEFAULT_SHOP["city"])
    offer: Optional[str] = None
    tone: str = "direct"


class PresenceRequest(BaseModel):
    shop_name: str = str(DEFAULT_SHOP["shop_name"])
    city: str = str(DEFAULT_SHOP["city"])
    region: str = str(DEFAULT_SHOP["region"])
    phone: str = str(DEFAULT_SHOP["phone"])
    url: str = str(DEFAULT_SHOP["url"])
    services: List[str] = Field(default_factory=list)
    reviews: List[Dict[str, Any]] = Field(default_factory=list)
    gallery: List[Dict[str, Any]] = Field(default_factory=list)


class SocialScheduleRequest(BaseModel):
    platforms: List[str] = Field(default_factory=lambda: ["instagram", "facebook", "gbp"])
    cadence_per_week: int = Field(5, ge=1, le=21)
    city: str = str(DEFAULT_SHOP["city"])
    season: Optional[str] = None


class OutreachRequest(BaseModel):
    kind: str = Field(..., description="quote_followup | membership_nudge | review_ask | winback")
    name: str
    package_id: Optional[str] = None
    last_service_days: Optional[int] = None
    channel: str = "email"


class EngageRequest(BaseModel):
    visitor_message: str
    page: str = "/"
    vehicle: Optional[Vehicle] = None
    known_name: Optional[str] = None
