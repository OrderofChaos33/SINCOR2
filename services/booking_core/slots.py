from datetime import datetime, timedelta
from typing import List, Dict, Optional
import asyncpg
from zoneinfo import ZoneInfo

class SlotManager:
    def __init__(self, db_conn):
        self.db = db_conn
    
    async def generate_slots(self, resource_id: str, start_date: datetime, end_date: datetime, 
                           slot_duration_minutes: int = 60, timezone: str = 'America/Chicago') -> List[Dict]:
        """Generate available time slots for a resource"""
        
        # Get resource info
        resource = await self.db.fetchrow("SELECT * FROM resources WHERE id = $1", resource_id)
        if not resource:
            raise ValueError("Resource not found")
        
        tz = ZoneInfo(timezone)
        buffer_minutes = resource['buffer_minutes']
        
        # Generate slots every hour during business hours (9 AM - 6 PM)
        slots = []
        current = start_date.replace(tzinfo=tz, hour=9, minute=0, second=0, microsecond=0)
        end = end_date.replace(tzinfo=tz, hour=18, minute=0, second=0, microsecond=0)
        
        while current < end:
            slot_end = current + timedelta(minutes=slot_duration_minutes)
            
            # Skip weekends
            if current.weekday() < 5:  # Monday = 0, Friday = 4
                # Check if slot already exists
                existing = await self.db.fetchrow("""
                    SELECT id FROM slots 
                    WHERE resource_id = $1 AND start_ts = $2
                """, resource_id, current)
                
                if not existing:
                    # Check for conflicts with existing appointments
                    conflict = await self.db.fetchrow("""
                        SELECT id FROM appointments 
                        WHERE resource_id = $1 
                        AND status != 'cancelled'
                        AND (
                            (start_ts <= $2 AND end_ts > $2) OR
                            (start_ts < $3 AND end_ts >= $3) OR
                            (start_ts >= $2 AND end_ts <= $3)
                        )
                    """, resource_id, current, slot_end)
                    
                    status = 'booked' if conflict else 'available'
                    
                    # Insert slot
                    slot_id = await self.db.fetchval("""
                        INSERT INTO slots (resource_id, start_ts, end_ts, status)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                    """, resource_id, current, slot_end, status)
                    
                    slots.append({
                        'id': str(slot_id),
                        'resource_id': resource_id,
                        'start_ts': current.isoformat(),
                        'end_ts': slot_end.isoformat(),
                        'status': status,
                        'duration_minutes': slot_duration_minutes
                    })
            
            current += timedelta(minutes=slot_duration_minutes)
        
        return slots
    
    async def get_available_slots(self, resource_id: str, from_date: datetime, to_date: datetime) -> List[Dict]:
        """Get available slots for booking"""
        rows = await self.db.fetch("""
            SELECT id, start_ts, end_ts, status
            FROM slots
            WHERE resource_id = $1 
            AND start_ts >= $2 
            AND end_ts <= $3
            AND status = 'available'
            ORDER BY start_ts
        """, resource_id, from_date, to_date)
        
        return [
            {
                'id': str(row['id']),
                'resource_id': resource_id,
                'start_ts': row['start_ts'].isoformat(),
                'end_ts': row['end_ts'].isoformat(),
                'status': row['status']
            }
            for row in rows
        ]