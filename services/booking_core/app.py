from fastapi import FastAPI, HTTPException, Response, Query
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncpg
import os
from typing import Optional
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from slots import SlotManager
from calendar import CalendarManager

app = FastAPI()
DB_DSN = os.getenv("DB_DSN")
BOOKING_TIMEZONE = os.getenv("BOOKING_TIMEZONE", "America/Chicago")
BOOKING_BUFFER_MIN = int(os.getenv("BOOKING_BUFFER_MIN", "15"))

# Metrics
BOOKINGS = Counter("booking_appointments_total", "Total appointments booked")
CANCELLATIONS = Counter("booking_cancellations_total", "Total appointment cancellations")

class AppointmentRequest(BaseModel):
    resource_id: str
    slot_id: str
    lead_id: Optional[str] = None
    name: str
    email: str
    phone: str
    notes: Optional[str] = ""

class ResourceRequest(BaseModel):
    tenant_id: str
    name: str
    timezone: Optional[str] = "America/Chicago"
    buffer_minutes: Optional[int] = 15

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)
    app.state.slots = SlotManager(app.state.db)
    app.state.calendar = CalendarManager(app.state.db)

@app.get("/health")
async def health():
    return {"ok": True, "service": "booking_core"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Resource Management
@app.post("/resources")
async def create_resource(resource: ResourceRequest):
    """Create a new bookable resource (staff member, room, etc.)"""
    resource_id = await app.state.db.fetchval("""
        INSERT INTO resources (tenant_id, name, timezone, buffer_minutes)
        VALUES ($1, $2, $3, $4)
        RETURNING id
    """, resource.tenant_id, resource.name, resource.timezone, resource.buffer_minutes)
    
    return {"resource_id": str(resource_id), "name": resource.name}

@app.get("/resources")
async def list_resources(tenant_id: Optional[str] = None):
    """List all resources for a tenant"""
    if tenant_id:
        rows = await app.state.db.fetch("""
            SELECT * FROM resources WHERE tenant_id = $1 AND active = true
        """, tenant_id)
    else:
        rows = await app.state.db.fetch("SELECT * FROM resources WHERE active = true")
    
    return [
        {
            "id": str(row['id']),
            "tenant_id": row['tenant_id'],
            "name": row['name'],
            "timezone": row['timezone'],
            "buffer_minutes": row['buffer_minutes']
        }
        for row in rows
    ]

# Slot Management
@app.post("/resources/{resource_id}/generate-slots")
async def generate_slots(resource_id: str, 
                        days_ahead: int = Query(14, description="Generate slots for next N days"),
                        slot_duration: int = Query(60, description="Slot duration in minutes")):
    """Generate available time slots for a resource"""
    start_date = datetime.now()
    end_date = start_date + timedelta(days=days_ahead)
    
    slots = await app.state.slots.generate_slots(
        resource_id, start_date, end_date, slot_duration, BOOKING_TIMEZONE
    )
    
    return {"slots_generated": len(slots), "slots": slots[:20]}  # Return first 20

@app.get("/slots")
async def get_slots(resource_id: str = Query(...), 
                   from_date: str = Query(...),
                   to_date: str = Query(...)):
    """Get available slots for booking"""
    from_dt = datetime.fromisoformat(from_date)
    to_dt = datetime.fromisoformat(to_date)
    
    slots = await app.state.slots.get_available_slots(resource_id, from_dt, to_dt)
    return {"available_slots": slots}

# Appointment Management
@app.post("/appointments")
async def book_appointment(appointment: AppointmentRequest):
    """Book a new appointment"""
    try:
        result = await app.state.calendar.book_appointment(
            appointment.resource_id,
            appointment.slot_id,
            {
                'lead_id': appointment.lead_id,
                'name': appointment.name,
                'email': appointment.email,
                'phone': appointment.phone,
                'notes': appointment.notes
            }
        )
        BOOKINGS.inc()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/appointments/{appointment_id}/cancel")
async def cancel_appointment(appointment_id: str):
    """Cancel an appointment"""
    try:
        result = await app.state.calendar.cancel_appointment(appointment_id)
        CANCELLATIONS.inc()
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/appointments")
async def get_appointments(resource_id: str = Query(...),
                          from_date: str = Query(...),
                          to_date: str = Query(...)):
    """Get appointments for a resource"""
    from_dt = datetime.fromisoformat(from_date)
    to_dt = datetime.fromisoformat(to_date)
    
    appointments = await app.state.calendar.get_appointments(resource_id, from_dt, to_dt)
    return {"appointments": appointments}