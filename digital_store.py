"""
Simple Digital Store endpoints for selling digital goods (ebooks, music, etc.)
Provides product listings and PayPal checkout link creation using existing PayPal integration.
"""

import json
import logging
from datetime import datetime
from flask import request, jsonify
from paypal_integration import SINCORPaymentProcessor, PaymentRequest, PaymentResult, PaymentStatus

logger = logging.getLogger(__name__)

# Simple product catalog (would be persisted in DB for production)
PRODUCT_CATALOG = [
    {
        "id": "book_auto_detailing",
        "title": "Complete Auto Detailing Guide",
        "description": "Pro techniques from Clinton Auto Detailing (Court). eBook + checklist.",
        "price": 19.99,
        "currency": "USD",
        "type": "ebook"
    },
    {
        "id": "album_ai_synth",
        "title": "AI Synth Album - Court",
        "description": "A compilation of AI-generated instrumental tracks for creators.",
        "price": 9.99,
        "currency": "USD",
        "type": "music"
    }
]

payment_processor = SINCORPaymentProcessor()


def list_products():
    """Return available products"""
    return jsonify({"success": True, "products": PRODUCT_CATALOG})


async def create_purchase():
    """Create a PayPal payment for a selected product and return approval URL"""
    data = request.get_json() or {}
    product_id = data.get('product_id')
    buyer_email = data.get('email', f"buyer-{int(datetime.now().timestamp())}@example.com")

    product = next((p for p in PRODUCT_CATALOG if p['id'] == product_id), None)
    if not product:
        return jsonify({"success": False, "error": "Product not found"}), 404

    amount = float(product['price'])

    # Use payment_processor to create a demo/sandbox payment
    try:
        payment_request = PaymentRequest(
            amount=amount,
            currency=product.get('currency', 'USD'),
            description=product['title'],
            customer_email=buyer_email,
            order_id=f"STORE-{product_id}-{int(datetime.now().timestamp())}"
        )

        result = await payment_processor.paypal.create_payment(payment_request)

        if result.success:
            return jsonify({
                "success": True,
                "approval_url": result.approval_url,
                "payment_id": result.payment_id
            })
        else:
            return jsonify({"success": False, "error": result.error_message}), 500

    except Exception as e:
        logger.error(f"Error creating purchase: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


def _send_download_email(to_email: str, product_id: str, download_link: str):
    """Send a download link email. If SMTP not configured, write a .eml draft to outbox."""
    import os
    import smtplib
    from email.message import EmailMessage

    subject = f"Your purchase: {product_id} - download link"
    body = f"Thanks for your purchase!\n\nDownload your product here: {download_link}\n\nThanks,\nSINCOR"

    smtp_host = os.getenv('SMTP_HOST', '')
    smtp_port = int(os.getenv('SMTP_PORT', '587') or 587)
    smtp_user = os.getenv('SMTP_USER', '')
    smtp_pass = os.getenv('SMTP_PASS', '')
    email_from = os.getenv('EMAIL_FROM', 'noreply@sincor.local')

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = email_from
    msg['To'] = to_email
    msg.set_content(body)

    try:
        if smtp_host and smtp_user and smtp_pass:
            with smtplib.SMTP(smtp_host, smtp_port) as s:
                s.starttls()
                s.login(smtp_user, smtp_pass)
                s.send_message(msg)
            logger.info(f"Sent download email to {to_email}")
            return True
        else:
            # Write to outbox for manual sending
            outbox_dir = 'outbox'
            os.makedirs(outbox_dir, exist_ok=True)
            filename = os.path.join(outbox_dir, f"purchase_{product_id}_{int(datetime.now().timestamp())}.eml")
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(msg.as_string())
            logger.info(f"Wrote email draft to {filename}")
            return True
    except Exception as e:
        logger.error(f"Failed to send/download email to {to_email}: {e}")
        return False


async def execute_purchase():
    """Execute a payment after approval (PayPal would redirect to your return URL with paymentId & PayerID)
    For demo mode, we accept payment_id and payer_id in JSON and execute immediately.
    """
    data = request.get_json() or {}
    payment_id = data.get('payment_id')
    payer_id = data.get('payer_id', 'DEMO-PAYER')
    buyer_email = data.get('email')

    if not payment_id:
        return jsonify({"success": False, "error": "Missing payment_id"}), 400

    try:
        result = await payment_processor.paypal.execute_payment(payment_id, payer_id)

        if result.success:
            # Record the purchase
            try:
                import os
                purchases_file = 'data/store_purchases.json'
                os.makedirs('data', exist_ok=True)

                purchase_record = {
                    'timestamp': datetime.now().isoformat(),
                    'payment_id': payment_id,
                    'product_payment': payment_id,
                    'product_id': data.get('product_id', 'unknown'),
                    'payer_id': payer_id,
                    'buyer_email': buyer_email,
                    'status': result.status.value
                }

                existing = []
                if os.path.exists(purchases_file):
                    with open(purchases_file, 'r', encoding='utf-8') as f:
                        try:
                            existing = json.load(f)
                        except Exception:
                            existing = []

                existing.append(purchase_record)
                with open(purchases_file, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to record purchase: {e}")

            # Deliver product: generate a simple download link (demo)
            product_id = data.get('product_id')
            download_link = f"/assets/downloads/{product_id}.zip" if product_id else None

            # Send email if provided
            if buyer_email and download_link:
                _send_download_email(buyer_email, product_id, download_link)

            return jsonify({"success": True, "payment_id": payment_id, "status": result.status.value, "download_link": download_link})
        else:
            return jsonify({"success": False, "error": result.error_message}), 400

    except Exception as e:
        logger.error(f"Error executing purchase: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# --- Webhook handlers (PayPal / legacy Stripe - deprecated) ---

def _update_purchase_status(payment_id: str, new_status: str, extra: dict | None = None) -> bool:
    """Update purchase record status for a given payment_id"""
    import os
    purchases_file = 'data/store_purchases.json'
    if not os.path.exists(purchases_file):
        return False

    try:
        with open(purchases_file, 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        return False

    changed = False
    for rec in existing:
        if rec.get('payment_id') == payment_id:
            rec['status'] = new_status
            if extra:
                rec.update(extra)
            changed = True

    if changed:
        try:
            with open(purchases_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2)
            logger.info(f"Updated purchase {payment_id} -> {new_status}")
            return True
        except Exception as e:
            logger.error(f"Failed to persist purchase update: {e}")
            return False

    return False


def handle_paypal_webhook(payload: dict) -> dict:
    """Handle a PayPal webhook payload.
    Expected minimal payload: {"payment_id": "DEMO-12345", "status": "COMPLETED"}
    In production, validate PayPal webhook signature and full event structure.
    """
    try:
        if not payload:
            return {"success": False, "error": "Empty payload"}

        payment_id = payload.get('payment_id') or payload.get('resource', {}).get('id')
        status = payload.get('status') or payload.get('resource', {}).get('status')

        if not payment_id:
            return {"success": False, "error": "Missing payment_id"}

        normalized_status = 'completed' if str(status).lower() in ('completed', 'approved') else str(status).lower()

        ok = _update_purchase_status(payment_id, normalized_status, extra={'paypal_webhook': payload})
        if ok:
            return {"success": True, "payment_id": payment_id, "status": normalized_status}
        else:
            return {"success": False, "error": "Purchase not found or update failed"}

    except Exception as e:
        logger.error(f"Error handling PayPal webhook: {e}")
        return {"success": False, "error": str(e)}


# Stripe support removed per project scope (Court uses only crypto and PayPal).
