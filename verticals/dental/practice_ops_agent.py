from __future__ import annotations

"""Operational automation for dental practice scheduling and billing."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List
from uuid import uuid4

# Slots valued at or above this threshold are prioritised for scheduling.
HIGH_VALUE_SLOT_FLOOR = Decimal('750.00')


@dataclass
class AppointmentSlot:
    """Represents a schedulable chair-time window."""

    start_time: datetime
    end_time: datetime
    provider: str
    operatory: str
    production_value: Decimal
    patient_id: str = ''
    status: str = 'open'


@dataclass
class TreatmentPlan:
    """Represents a pending treatment plan for a dental patient."""

    patient_id: str
    plan_id: str
    procedures: List[Dict[str, str]]
    due_date: datetime
    estimated_value: Decimal
    status: str = 'proposed'
    notes: str = ''


class DentalPracticeAgent:
    """Optimizes daily operations for a dental practice."""

    def optimize_schedule(self, slots: List[AppointmentSlot]) -> Dict[str, object]:
        """Prioritize high-value treatment windows while minimizing idle gaps."""
        ordered = sorted(slots, key=lambda slot: (slot.status != 'open', -slot.production_value, slot.start_time))
        gaps = []
        for current, nxt in zip(ordered, ordered[1:]):
            gap_minutes = max(int((nxt.start_time - current.end_time).total_seconds() // 60), 0)
            if gap_minutes:
                gaps.append(gap_minutes)
        utilization = 1.0 if not slots else round(sum(1 for slot in slots if slot.status != 'open') / len(slots), 2)
        return {
            'optimized_order': [slot.provider + ':' + slot.start_time.isoformat() for slot in ordered],
            'average_gap_minutes': round(sum(gaps) / len(gaps), 2) if gaps else 0,
            'utilization_ratio': utilization,
            'high_value_slots': [slot.start_time.isoformat() for slot in ordered if slot.production_value >= HIGH_VALUE_SLOT_FLOOR],
        }

    def send_recall(self, patient_id: str, last_visit: datetime, interval_days: int = 180) -> Dict[str, str]:
        """Generate a compliant patient recall message."""
        due_date = last_visit + timedelta(days=interval_days)
        return {
            'patient_id': patient_id,
            'recall_due_date': due_date.date().isoformat(),
            'channel': 'sms+email',
            'message': f'Hi {patient_id}, your preventive dental recall is due on {due_date.date().isoformat()}. Reply to schedule.',
        }

    def generate_billing(self, treatment_plan: TreatmentPlan) -> Dict[str, object]:
        """Create a mock patient billing summary from a treatment plan."""
        line_items = []
        running_total = Decimal('0.00')
        for procedure in treatment_plan.procedures:
            fee = Decimal(str(procedure.get('fee', '0.00')))
            running_total += fee
            line_items.append({
                'code': procedure.get('code', 'UNSPECIFIED'),
                'description': procedure.get('description', 'Procedure'),
                'fee': str(fee.quantize(Decimal('0.01'))),
            })
        billing_id = f"DB-{uuid4().hex[:10].upper()}"
        return {
            'billing_id': billing_id,
            'patient_id': treatment_plan.patient_id,
            'plan_id': treatment_plan.plan_id,
            'line_items': line_items,
            'total_due': str(running_total.quantize(Decimal('0.01'))),
            'estimated_value': str(treatment_plan.estimated_value.quantize(Decimal('0.01'))),
            'statement_note': 'Insurance estimates are non-binding until adjudication is complete.',
        }
