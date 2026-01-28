"""
Event Normalization Engine - Day 1-2 Hardening
Normalizes all external events to internal schema with idempotency and DLQ
"""

import os
import json
import uuid
import hashlib
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
import time
import traceback
from flask import Blueprint, request, jsonify

# Configure structured logging (JSON format)
import logging.config

LOGGING_CONFIG = {
    'version': 1,
    'formatters': {
        'json': {
            'format': '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "service": "event_engine", "trace_id": "%(trace_id)s", "message": "%(message)s", "module": "%(name)s"}',
            'datefmt': '%Y-%m-%dT%H:%M:%S.%fZ'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
            'level': 'INFO'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console']
    }
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

class EventType(Enum):
    LEAD_GENERATED = "lead_generated"
    APPOINTMENT_BOOKED = "appointment_booked"
    PAYMENT_RECEIVED = "payment_received"
    PURCHASE_COMPLETED = "purchase_completed"
    SUBSCRIPTION_STARTED = "subscription_started"
    CUSTOMER_REGISTERED = "customer_registered"
    SERVICE_COMPLETED = "service_completed"
    REVIEW_RECEIVED = "review_received"
    EMAIL_OPENED = "email_opened"
    SMS_RESPONDED = "sms_responded"
    AD_CLICKED = "ad_clicked"
    CAMPAIGN_CONVERTED = "campaign_converted"

class EventSource(Enum):
    SQUARE = "square"
    PAYPAL = "paypal" 
    FACEBOOK = "facebook"
    GOOGLE_ADS = "google_ads"
    INSTAGRAM = "instagram"
    EMAIL_SYSTEM = "email_system"
    SMS_SYSTEM = "sms_system"
    WEBSITE = "website"
    CRM = "crm"
    MANUAL = "manual"

class EventStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DLQ = "dead_letter_queue"

@dataclass
class NormalizedEvent:
    """Normalized internal event schema"""
    event_id: str
    trace_id: str
    type: EventType
    source: EventSource
    timestamp: datetime
    amount: Optional[float] = None
    currency: str = "USD"
    customer_id: Optional[str] = None
    booking_id: Optional[str] = None
    campaign: Optional[str] = None
    meta: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.meta is None:
            self.meta = {}

class EventNormalizationEngine:
    def __init__(self):
        self.db_path = "clinton_auto_detailing_events.db"
        self.max_retries = 5
        self.retry_delays = [1, 2, 4, 8, 16]  # Exponential backoff
        self.processed_events_retention_days = 30
        
        self.init_database()
        self.cleanup_old_events()
        
    def init_database(self):
        """Initialize event processing database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Processed events table (for idempotency)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id TEXT PRIMARY KEY,
                dedupe_key TEXT,
                source TEXT,
                processed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(dedupe_key),
                INDEX(source),
                INDEX(processed_at)
            )
        ''')
        
        # Event queue table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                trace_id TEXT,
                type TEXT,
                source TEXT,
                status TEXT,
                payload TEXT,
                retry_count INTEGER DEFAULT 0,
                next_retry_at TIMESTAMP,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(status),
                INDEX(next_retry_at),
                INDEX(source)
            )
        ''')
        
        # Dead letter queue
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT,
                trace_id TEXT,
                original_payload TEXT,
                error_message TEXT,
                retry_count INTEGER,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(failed_at)
            )
        ''')
        
        # Event processing metrics
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS event_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE,
                source TEXT,
                type TEXT,
                total_events INTEGER DEFAULT 0,
                successful_events INTEGER DEFAULT 0,
                failed_events INTEGER DEFAULT 0,
                avg_processing_time_ms INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX(date, source)
            )
        ''')
        
        conn.commit()
        conn.close()
        
        logger.info("Event normalization database initialized", extra={'trace_id': 'init'})
    
    def generate_dedupe_key(self, source: str, external_id: str, event_type: str) -> str:
        """Generate deterministic deduplication key"""
        key_data = f"{source}:{external_id}:{event_type}"
        return hashlib.sha256(key_data.encode()).hexdigest()
    
    def is_duplicate_event(self, event_id: str, dedupe_key: str) -> bool:
        """Check if event has already been processed (idempotency)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM processed_events 
            WHERE event_id = ? OR dedupe_key = ?
        ''', (event_id, dedupe_key))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def mark_event_processed(self, event_id: str, dedupe_key: str, source: str):
        """Mark event as processed for idempotency"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO processed_events 
            (event_id, dedupe_key, source, processed_at)
            VALUES (?, ?, ?, ?)
        ''', (event_id, dedupe_key, source, datetime.now()))
        
        conn.commit()
        conn.close()
    
    def normalize_square_event(self, webhook_data: Dict) -> Optional[NormalizedEvent]:
        """Normalize Square webhook to internal schema"""
        event_type_map = {
            'payment.created': EventType.PAYMENT_RECEIVED,
            'booking.created': EventType.APPOINTMENT_BOOKED,
            'customer.created': EventType.CUSTOMER_REGISTERED,
            'order.fulfilled': EventType.PURCHASE_COMPLETED
        }
        
        square_event_type = webhook_data.get('type')
        if square_event_type not in event_type_map:
            return None
            
        data = webhook_data.get('data', {})
        object_data = data.get('object', {})
        
        # Extract common fields
        customer_id = object_data.get('customer_id')
        amount = None
        
        if 'amount_money' in object_data:
            amount = object_data['amount_money']['amount'] / 100  # Convert from cents
        elif 'base_price_money' in object_data:
            amount = object_data['base_price_money']['amount'] / 100
        
        return NormalizedEvent(
            event_id=str(uuid.uuid4()),
            trace_id=request.headers.get('X-Trace-ID', str(uuid.uuid4())),
            type=event_type_map[square_event_type],
            source=EventSource.SQUARE,
            timestamp=datetime.now(),
            amount=amount,
            customer_id=customer_id,
            booking_id=object_data.get('id'),
            meta={
                'square_event_type': square_event_type,
                'square_object_id': object_data.get('id'),
                'location_id': object_data.get('location_id'),
                'raw_webhook': webhook_data
            }
        )
    
    def normalize_facebook_event(self, webhook_data: Dict) -> Optional[NormalizedEvent]:
        """Normalize Facebook webhook to internal schema"""
        entry = webhook_data.get('entry', [{}])[0]
        changes = entry.get('changes', [{}])[0]
        value = changes.get('value', {})
        
        if changes.get('field') == 'leadgen':
            return NormalizedEvent(
                event_id=str(uuid.uuid4()),
                trace_id=request.headers.get('X-Trace-ID', str(uuid.uuid4())),
                type=EventType.LEAD_GENERATED,
                source=EventSource.FACEBOOK,
                timestamp=datetime.now(),
                campaign=value.get('ad_id'),
                meta={
                    'leadgen_id': value.get('leadgen_id'),
                    'form_id': value.get('form_id'),
                    'ad_id': value.get('ad_id'),
                    'raw_webhook': webhook_data
                }
            )
        
        return None
    
    def normalize_paypal_event(self, webhook_data: Dict) -> Optional[NormalizedEvent]:
        """Normalize PayPal webhook to internal schema"""
        event_type_map = {
            'PAYMENT.SALE.COMPLETED': EventType.PAYMENT_RECEIVED,
            'BILLING.SUBSCRIPTION.CREATED': EventType.SUBSCRIPTION_STARTED,
            'INVOICING.INVOICE.PAID': EventType.PURCHASE_COMPLETED
        }
        
        paypal_event_type = webhook_data.get('event_type')
        if paypal_event_type not in event_type_map:
            return None
            
        resource = webhook_data.get('resource', {})
        amount = None
        
        if 'amount' in resource:
            amount = float(resource['amount']['total'])
        
        return NormalizedEvent(
            event_id=str(uuid.uuid4()),
            trace_id=request.headers.get('X-Trace-ID', str(uuid.uuid4())),
            type=event_type_map[paypal_event_type],
            source=EventSource.PAYPAL,
            timestamp=datetime.now(),
            amount=amount,
            currency=resource.get('amount', {}).get('currency', 'USD'),
            meta={
                'paypal_event_type': paypal_event_type,
                'paypal_resource_id': resource.get('id'),
                'raw_webhook': webhook_data
            }
        )
    
    def enqueue_event(self, event: NormalizedEvent) -> bool:
        """Add event to processing queue"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                INSERT INTO event_queue 
                (event_id, trace_id, type, source, status, payload, next_retry_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                event.event_id,
                event.trace_id,
                event.type.value,
                event.source.value,
                EventStatus.PENDING.value,
                json.dumps(asdict(event), default=str),
                datetime.now()
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Event enqueued: {event.type.value}", extra={
                'trace_id': event.trace_id,
                'event_id': event.event_id,
                'source': event.source.value
            })
            
            return True
            
        except Exception as e:
            conn.rollback()
            conn.close()
            
            logger.error(f"Failed to enqueue event: {e}", extra={
                'trace_id': event.trace_id,
                'event_id': event.event_id,
                'error': str(e)
            })
            
            return False
    
    def process_event_queue(self) -> int:
        """Process pending events with retry logic"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get pending events ready for processing
        cursor.execute('''
            SELECT id, event_id, trace_id, type, source, payload, retry_count
            FROM event_queue 
            WHERE status IN (?, ?) 
            AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY created_at
            LIMIT 100
        ''', (EventStatus.PENDING.value, EventStatus.FAILED.value, datetime.now()))
        
        events = cursor.fetchall()
        processed_count = 0
        
        for event_row in events:
            event_id = event_row[1]
            trace_id = event_row[2]
            retry_count = event_row[6]
            
            try:
                # Mark as processing
                cursor.execute('''
                    UPDATE event_queue 
                    SET status = ?, updated_at = ?
                    WHERE event_id = ?
                ''', (EventStatus.PROCESSING.value, datetime.now(), event_id))
                conn.commit()
                
                # Process the event
                success = self._process_single_event(event_row)
                
                if success:
                    # Mark as processed
                    cursor.execute('''
                        UPDATE event_queue 
                        SET status = ?, updated_at = ?
                        WHERE event_id = ?
                    ''', (EventStatus.PROCESSED.value, datetime.now(), event_id))
                    
                    processed_count += 1
                    
                    logger.info(f"Event processed successfully", extra={
                        'trace_id': trace_id,
                        'event_id': event_id
                    })
                else:
                    # Handle retry or move to DLQ
                    if retry_count < self.max_retries:
                        next_retry = datetime.now() + timedelta(seconds=self.retry_delays[retry_count])
                        
                        cursor.execute('''
                            UPDATE event_queue 
                            SET status = ?, retry_count = retry_count + 1, 
                                next_retry_at = ?, updated_at = ?
                            WHERE event_id = ?
                        ''', (EventStatus.FAILED.value, next_retry, datetime.now(), event_id))
                    else:
                        # Move to dead letter queue
                        self._move_to_dlq(event_row)
                        
                        cursor.execute('''
                            UPDATE event_queue 
                            SET status = ?, updated_at = ?
                            WHERE event_id = ?
                        ''', (EventStatus.DLQ.value, datetime.now(), event_id))
                
                conn.commit()
                
            except Exception as e:
                conn.rollback()
                logger.error(f"Event processing failed: {e}", extra={
                    'trace_id': trace_id,
                    'event_id': event_id,
                    'error': str(e)
                })
        
        conn.close()
        return processed_count
    
    def _process_single_event(self, event_row) -> bool:
        """Process a single event (implement your business logic here)"""
        try:
            event_data = json.loads(event_row[5])  # payload
            event_type = event_row[3]
            source = event_row[4]
            
            # Emit to GA4
            self._emit_to_ga4(event_data)
            
            # Emit to Meta CAPI
            self._emit_to_meta_capi(event_data)
            
            # Emit to Google Ads
            self._emit_to_google_ads(event_data)
            
            # Update CRM
            self._sync_to_crm(event_data)
            
            # Trigger workflows
            self._trigger_workflows(event_data)
            
            return True
            
        except Exception as e:
            logger.error(f"Event processing error: {e}", extra={
                'event_id': event_row[1],
                'trace_id': event_row[2],
                'error': str(e),
                'traceback': traceback.format_exc()
            })
            return False
    
    def _emit_to_ga4(self, event_data: Dict):
        """Emit event to GA4 (implement GA4 Measurement Protocol)"""
        # TODO: Implement GA4 server-side events
        pass
    
    def _emit_to_meta_capi(self, event_data: Dict):
        """Emit event to Meta Conversion API"""
        # TODO: Implement Meta CAPI
        pass
    
    def _emit_to_google_ads(self, event_data: Dict):
        """Emit event to Google Ads API"""
        # TODO: Implement Google Ads conversion tracking
        pass
    
    def _sync_to_crm(self, event_data: Dict):
        """Sync event data to CRM"""
        # TODO: Implement CRM sync
        pass
    
    def _trigger_workflows(self, event_data: Dict):
        """Trigger automated workflows based on event"""
        # TODO: Implement workflow triggers
        pass
    
    def _move_to_dlq(self, event_row):
        """Move failed event to dead letter queue"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO dead_letter_queue 
            (event_id, trace_id, original_payload, error_message, retry_count)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            event_row[1],  # event_id
            event_row[2],  # trace_id
            event_row[5],  # payload
            "Max retries exceeded",
            event_row[6]   # retry_count
        ))
        
        conn.commit()
        conn.close()
        
        logger.warning(f"Event moved to DLQ", extra={
            'trace_id': event_row[2],
            'event_id': event_row[1]
        })
    
    def cleanup_old_events(self):
        """Clean up old processed events"""
        cutoff_date = datetime.now() - timedelta(days=self.processed_events_retention_days)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM processed_events WHERE processed_at < ?', (cutoff_date,))
        cursor.execute('DELETE FROM event_queue WHERE status = ? AND updated_at < ?', 
                      (EventStatus.PROCESSED.value, cutoff_date))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Cleaned up events older than {self.processed_events_retention_days} days")
    
    def get_queue_metrics(self) -> Dict:
        """Get event queue metrics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Queue depth by status
        cursor.execute('''
            SELECT status, COUNT(*) 
            FROM event_queue 
            GROUP BY status
        ''')
        queue_depth = dict(cursor.fetchall())
        
        # DLQ count
        cursor.execute('SELECT COUNT(*) FROM dead_letter_queue')
        dlq_count = cursor.fetchone()[0]
        
        # Processing metrics
        cursor.execute('''
            SELECT source, 
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) as successful,
                   SUM(CASE WHEN status = 'dead_letter_queue' THEN 1 ELSE 0 END) as failed
            FROM event_queue
            WHERE created_at >= date('now', '-1 day')
            GROUP BY source
        ''')
        source_metrics = cursor.fetchall()
        
        conn.close()
        
        return {
            'queue_depth': queue_depth,
            'dlq_count': dlq_count,
            'source_metrics': source_metrics,
            'timestamp': datetime.now().isoformat()
        }

# Flask Blueprint for webhook endpoints
event_bp = Blueprint('events', __name__)
engine = EventNormalizationEngine()

@event_bp.route('/webhook/square', methods=['POST'])
def square_webhook():
    """Square webhook endpoint with idempotency"""
    try:
        trace_id = request.headers.get('X-Trace-ID', str(uuid.uuid4()))
        webhook_data = request.json
        
        # Generate dedupe key
        square_event_id = webhook_data.get('data', {}).get('id', str(uuid.uuid4()))
        dedupe_key = engine.generate_dedupe_key('square', square_event_id, webhook_data.get('type', ''))
        
        # Check for duplicates
        if engine.is_duplicate_event(square_event_id, dedupe_key):
            logger.info("Duplicate Square event ignored", extra={
                'trace_id': trace_id,
                'event_id': square_event_id
            })
            return jsonify({'status': 'duplicate'}), 200
        
        # Normalize event
        normalized_event = engine.normalize_square_event(webhook_data)
        if not normalized_event:
            return jsonify({'status': 'ignored'}), 200
        
        normalized_event.trace_id = trace_id
        
        # Enqueue for processing
        if engine.enqueue_event(normalized_event):
            engine.mark_event_processed(square_event_id, dedupe_key, 'square')
            return jsonify({'status': 'accepted', 'event_id': normalized_event.event_id})
        else:
            return jsonify({'status': 'error'}), 500
            
    except Exception as e:
        logger.error(f"Square webhook error: {e}", extra={
            'trace_id': trace_id,
            'error': str(e)
        })
        return jsonify({'status': 'error'}), 500

@event_bp.route('/webhook/facebook', methods=['POST'])
def facebook_webhook():
    """Facebook webhook endpoint with idempotency"""
    try:
        trace_id = request.headers.get('X-Trace-ID', str(uuid.uuid4()))
        webhook_data = request.json
        
        # Handle Facebook verification
        if request.method == 'GET':
            return request.args.get('hub.challenge', '')
        
        # Generate dedupe key
        entry = webhook_data.get('entry', [{}])[0]
        fb_event_id = entry.get('id', str(uuid.uuid4()))
        dedupe_key = engine.generate_dedupe_key('facebook', fb_event_id, 'leadgen')
        
        # Check for duplicates
        if engine.is_duplicate_event(fb_event_id, dedupe_key):
            return jsonify({'status': 'duplicate'}), 200
        
        # Normalize event
        normalized_event = engine.normalize_facebook_event(webhook_data)
        if not normalized_event:
            return jsonify({'status': 'ignored'}), 200
        
        normalized_event.trace_id = trace_id
        
        # Enqueue for processing
        if engine.enqueue_event(normalized_event):
            engine.mark_event_processed(fb_event_id, dedupe_key, 'facebook')
            return jsonify({'status': 'accepted', 'event_id': normalized_event.event_id})
        else:
            return jsonify({'status': 'error'}), 500
            
    except Exception as e:
        logger.error(f"Facebook webhook error: {e}", extra={
            'trace_id': trace_id,
            'error': str(e)
        })
        return jsonify({'status': 'error'}), 500

@event_bp.route('/webhook/paypal', methods=['POST'])
def paypal_webhook():
    """PayPal webhook endpoint with idempotency"""
    try:
        trace_id = request.headers.get('X-Trace-ID', str(uuid.uuid4()))
        webhook_data = request.json
        
        # Generate dedupe key
        paypal_event_id = webhook_data.get('id', str(uuid.uuid4()))
        dedupe_key = engine.generate_dedupe_key('paypal', paypal_event_id, webhook_data.get('event_type', ''))
        
        # Check for duplicates
        if engine.is_duplicate_event(paypal_event_id, dedupe_key):
            return jsonify({'status': 'duplicate'}), 200
        
        # Normalize event
        normalized_event = engine.normalize_paypal_event(webhook_data)
        if not normalized_event:
            return jsonify({'status': 'ignored'}), 200
        
        normalized_event.trace_id = trace_id
        
        # Enqueue for processing
        if engine.enqueue_event(normalized_event):
            engine.mark_event_processed(paypal_event_id, dedupe_key, 'paypal')
            return jsonify({'status': 'accepted', 'event_id': normalized_event.event_id})
        else:
            return jsonify({'status': 'error'}), 500
            
    except Exception as e:
        logger.error(f"PayPal webhook error: {e}", extra={
            'trace_id': trace_id,
            'error': str(e)
        })
        return jsonify({'status': 'error'}), 500

@event_bp.route('/events/process', methods=['POST'])
def process_events():
    """Manually trigger event processing"""
    try:
        processed_count = engine.process_event_queue()
        return jsonify({
            'status': 'success',
            'processed_events': processed_count
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@event_bp.route('/events/metrics', methods=['GET'])
def event_metrics():
    """Get event processing metrics"""
    try:
        metrics = engine.get_queue_metrics()
        return jsonify(metrics)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == "__main__":
    # Test the event engine
    engine = EventNormalizationEngine()
    
    # Process any pending events
    processed = engine.process_event_queue()
    print(f"Processed {processed} events")
    
    # Show metrics
    metrics = engine.get_queue_metrics()
    print(f"Queue metrics: {json.dumps(metrics, indent=2)}")