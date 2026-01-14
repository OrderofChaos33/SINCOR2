from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Dict, Any
from uuid import UUID

class Consent(BaseModel):
    tcpa: bool = False
    timestamp: Optional[str] = None
    proof_uri: Optional[str] = None

class Contact(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    ip: Optional[str] = None
    zip: Optional[str] = None
    state: Optional[str] = None

class Lead(BaseModel):
    schema: str = "lead.v1"
    lead_id: UUID
    vertical: str
    ts: str
    contact: Contact
    meta: Dict[str, Any] = {}
    attributes: Dict[str, Any] = {}
    consent: Consent = Field(default_factory=Consent)