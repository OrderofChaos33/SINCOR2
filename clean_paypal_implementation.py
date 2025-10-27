#!/usr/bin/env python3
"""
Clean PayPal implementation from scratch - following official docs exactly
Removes all complex polling/detection code, uses simple synchronous approach
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find the old PayPal script section (everything from SDK script tag to closing script tag)
old_script_start = '<script src="https://www.paypal.com/sdk/js?client-id='
old_script_section_start = content.find(old_script_start)
old_script_section_end = content.find('</script>', old_script_section_start + 1) + len('</script>')

# Extract everything before and after the PayPal scripts
before_scripts = content[:old_script_section_start]
after_scripts_marker = '</body>'
after_scripts_pos = content.find(after_scripts_marker, old_script_section_end)
after_scripts = content[after_scripts_pos:]

# Create completely new, clean PayPal implementation
clean_paypal_scripts = '''<script src="https://www.paypal.com/sdk/js?client-id=Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh&currency=USD&components=buttons"></script>
    <script>
    // Simple, clean PayPal button rendering - no complex detection needed

    // Button 1: Business Intelligence Report - $97
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '97.00' },
                    description: 'SINCOR Business Intelligence Report'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=bi-report&amount=97&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-bi-report');

    // Button 2: Competitive Analysis - $149
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '149.00' },
                    description: 'SINCOR Competitive Analysis Report'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=competitive-analysis&amount=149&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-competitive-analysis');

    // Button 3: Intelligence Hub - $2,500 (MOST POPULAR)
    paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '2500.00' },
                    description: 'SINCOR Intelligence Hub (90-day service)'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=intelligence-hub&amount=2500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-intelligence-hub');

    // Button 4: Content Package - Micro - $500
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '500.00' },
                    description: 'SINCOR Content Package - Micro'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=content-micro&amount=500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-content-micro');

    // Button 5: Content Package - Standard - $2,500 (BEST VALUE)
    paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '2500.00' },
                    description: 'SINCOR Content Package - Standard'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=content-standard&amount=2500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-content-standard');

    // Button 6: Content Package - Enterprise - $15,000
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '15000.00' },
                    description: 'SINCOR Content Package - Enterprise'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=content-enterprise&amount=15000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-content-enterprise');

    // Button 7: Creative Forge - $5,000
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '5000.00' },
                    description: 'SINCOR Creative Forge - Multi-channel Campaign'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=creative-forge&amount=5000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-creative-forge');

    // Button 8: Ops Core - $9,500
    paypal.Buttons({
        style: { layout: 'vertical', color: 'blue', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '9500.00' },
                    description: 'SINCOR Ops Core - Operations Automation'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=ops-core&amount=9500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-ops-core');

    // Button 9: Growth Engine - $15,000 (PREMIUM)
    paypal.Buttons({
        style: { layout: 'vertical', color: 'gold', shape: 'rect', label: 'paypal' },
        createOrder: function(data, actions) {
            return actions.order.create({
                purchase_units: [{
                    amount: { value: '15000.00' },
                    description: 'SINCOR Growth Engine - AI Sales System'
                }]
            });
        },
        onApprove: function(data, actions) {
            return actions.order.capture().then(function(details) {
                window.location.href = '/payment/success?product=growth-engine&amount=15000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error:', err);
            alert('Payment error occurred. Please try again.');
        }
    }).render('#pp-growth-engine');
    </script>

'''

# Reconstruct the file with clean PayPal scripts
new_content = before_scripts + clean_paypal_scripts + after_scripts

# Write the cleaned file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(new_content)

print('SUCCESS: Clean PayPal implementation applied!')
print('Changes:')
print('  - Removed ALL complex polling/detection code')
print('  - Simple synchronous button rendering')
print('  - 9 clean PayPal buttons following official docs')
print('  - Proper error handling on each button')
print('  - SDK with components=buttons parameter')
print(f'File size: {len(new_content)} characters')
print('\nAll 9 products:')
print('  1. Business Intelligence Report - $97')
print('  2. Competitive Analysis - $149')
print('  3. Intelligence Hub - $2,500 (MOST POPULAR)')
print('  4. Content Micro - $500')
print('  5. Content Standard - $2,500 (BEST VALUE)')
print('  6. Content Enterprise - $15,000')
print('  7. Creative Forge - $5,000')
print('  8. Ops Core - $9,500')
print('  9. Growth Engine - $15,000 (PREMIUM)')
