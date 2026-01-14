from datetime import datetime
from typing import Dict, Optional
import asyncpg
from uuid import uuid4

class CalendarManager:
    def __init__(self, db_conn):
        self.db = db_conn
    
    async def book_appointment(self, resource_id: str, slot_id: str, lead_data: Dict) -> Dict:
        """Book an appointment in a specific slot"""
        
        async with self.db.transaction():
            # Get slot details
            slot = await self.db.fetchrow("""
                SELECT * FROM slots WHERE id = $1 AND resource_id = $2
            """, slot_id, resource_id)
            
            if not slot:
                raise ValueError("Slot not found")
            
            if slot['status'] != 'available':
                raise ValueError("Slot not available")
            
            # Create appointment
            appointment_id = await self.db.fetchval("""
                INSERT INTO appointments (
                    resource_id, lead_id, contact_name, contact_email, 
                    contact_phone, start_ts, end_ts, notes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, 
                resource_id,
                lead_data.get('lead_id'),
                lead_data.get('name'),
                lead_data.get('email'),
                lead_data.get('phone'),
                slot['start_ts'],
                slot['end_ts'],
                lead_data.get('notes', '')
            )
            
            # Mark slot as booked
            await self.db.execute("""
                UPDATE slots SET status = 'booked' WHERE id = $1
            """, slot_id)
            
            return {
                'appointment_id': str(appointment_id),
                'resource_id': resource_id,
                'slot_id': slot_id,
                'start_ts': slot['start_ts'].isoformat(),
                'end_ts': slot['end_ts'].isoformat(),
                'contact': {
                    'name': lead_data.get('name'),
                    'email': lead_data.get('email'),
                    'phone': lead_data.get('phone')
                },
                'status': 'confirmed'
            }
    
    async def cancel_appointment(self, appointment_id: str) -> Dict:
        """Cancel an appointment and free up the slot"""
        
        async with self.db.transaction():
            # Get appointment details
            appointment = await self.db.fetchrow("""
                SELECT * FROM appointments WHERE id = $1
            """, appointment_id)
            
            if not appointment:
                raise ValueError("Appointment not found")
            
            # Update appointment status
            await self.db.execute("""
                UPDATE appointments SET status = 'cancelled' WHERE id = $1
            """, appointment_id)
            
            # Free up the slot
            await self.db.execute("""
                UPDATE slots 
                SET status = 'available' 
                WHERE resource_id = $1 
                AND start_ts = $2 
                AND end_ts = $3
            """, appointment['resource_id'], appointment['start_ts'], appointment['end_ts'])
            
            return {
                'appointment_id': str(appointment_id),
                'status': 'cancelled',
                'start_ts': appointment['start_ts'].isoformat(),
                'end_ts': appointment['end_ts'].isoformat()
            }
    
    async def get_appointments(self, resource_id: str, from_date: datetime, to_date: datetime) -> list:
        """Get appointments for a resource in date range"""
        rows = await self.db.fetch("""
            SELECT * FROM appointments
            WHERE resource_id = $1
            AND start_ts >= $2
            AND end_ts <= $3
            AND status != 'cancelled'
            ORDER BY start_ts
        """, resource_id, from_date, to_date)
        
        return [
            {
                'id': str(row['id']),
                'resource_id': str(row['resource_id']),
                'lead_id': str(row['lead_id']) if row['lead_id'] else None,
                'contact': {
                    'name': row['contact_name'],
                    'email': row['contact_email'],
                    'phone': row['contact_phone']
                },
                'start_ts': row['start_ts'].isoformat(),
                'end_ts': row['end_ts'].isoformat(),
                'status': row['status'],
                'notes': row['notes']
            }
            for row in rows
        ]