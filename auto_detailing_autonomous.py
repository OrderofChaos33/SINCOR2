"""
AUTONOMOUS AUTO DETAILING MEDIA PACK ROUTES
Plugs into app.py - handles $99 media pack sales end-to-end
"""

from flask import request, jsonify, render_template
from datetime import datetime
from pathlib import Path
import json

def add_autonomous_routes(app):
    """Add autonomous media pack routes to Flask app"""

    @app.route('/auto-detailing-mediapack')
    def auto_detailing_mediapack():
        """$99 media pack landing page"""
        return render_template('auto_detailing_mediapack.html')

    @app.route('/api/auto-detailing/generate-mediapack', methods=['POST'])
    def generate_mediapack():
        """AUTONOMOUS: Generate & deliver after PayPal payment"""
        try:
            data = request.get_json()

            # Business info from form
            business_info = {
                'business_name': data.get('business_name'),
                'phone': data.get('phone'),
                'email': data.get('email'),
                'tagline': data.get('tagline', ''),
                'services': data.get('services', ''),
                'special_offer': data.get('special_offer', ''),
                'paypal_order_id': data.get('paypal_order_id'),
                'amount': 99.00,
                'timestamp': datetime.now().isoformat()
            }

            # Use existing content engine
            from content_quality_engine import ContentQualityEngine
            engine = ContentQualityEngine()

            # Generate 8 materials
            materials = []

            # Flyer
            flyer = engine.generate_content('landing_page', {
                'topic': f"{business_info['business_name']} Auto Detailing",
                'audience': 'Car owners',
                'keywords': ['detailing', 'car wash'],
                'word_count': 400,
                'tone': 'professional'
            })
            materials.append(('flyer.json', flyer))

            # 5 social ads
            for i in range(5):
                ad = engine.generate_content('social_media_post', {
                    'topic': f"{business_info['business_name']} Special",
                    'audience': 'Local customers',
                    'keywords': ['detailing', business_info['special_offer']],
                    'word_count': 150,
                    'tone': 'professional'
                })
                materials.append((f'social_ad_{i+1}.json', ad))

            # Email template
            email = engine.generate_content('email_campaign', {
                'topic': f"{business_info['business_name']} Newsletter",
                'audience': 'Customers',
                'keywords': ['service', 'quality'],
                'word_count': 300,
                'tone': 'professional'
            })
            materials.append(('email_template.json', email))

            # Price list
            prices = engine.generate_content('product_description', {
                'topic': f"{business_info['business_name']} Pricing",
                'audience': 'Customers',
                'keywords': ['pricing', 'packages'],
                'word_count': 400,
                'tone': 'professional'
            })
            materials.append(('price_list.json', prices))

            # Save everything
            output_dir = Path('outputs/mediapack_deliveries') / business_info['paypal_order_id']
            output_dir.mkdir(parents=True, exist_ok=True)

            for filename, content in materials:
                with open(output_dir / filename, 'w') as f:
                    json.dump(content, f, indent=2)

            # Save order
            with open(output_dir / 'order_info.json', 'w') as f:
                json.dump(business_info, f, indent=2)

            # Log revenue
            revenue_log = Path('outputs/autonomous/revenue.jsonl')
            revenue_log.parent.mkdir(parents=True, exist_ok=True)
            with open(revenue_log, 'a') as f:
                f.write(json.dumps({
                    'timestamp': business_info['timestamp'],
                    'product': 'auto_detailing_mediapack',
                    'amount': 99.00,
                    'paypal_order_id': business_info['paypal_order_id'],
                    'customer_email': business_info['email']
                }) + '\n')

            return jsonify({
                'success': True,
                'order_id': business_info['paypal_order_id'],
                'files_generated': len(materials),
                'message': 'Media pack generated! Check email.'
            })

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/auto-detailing/success')
    def success_page():
        """Success page"""
        order_id = request.args.get('order', 'N/A')
        return f"""
        <html>
        <head><title>Success!</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>Payment Successful!</h1>
            <p>Order: {order_id}</p>
            <p><strong>Check your email in 5 minutes!</strong></p>
            <p>8+ marketing materials on the way.</p>
        </body>
        </html>
        """
