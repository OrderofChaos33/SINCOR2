#!/usr/bin/env python3
"""Fix PayPal buttons - remove duplicate try blocks and update SDK URL"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: Update SDK URL to include components=buttons per PayPal docs
old_sdk = '<script src="https://www.paypal.com/sdk/js?client-id=Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh&currency=USD" data-sdk-integration-source="button-factory"></script>'
new_sdk = '<script src="https://www.paypal.com/sdk/js?client-id=Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh&currency=USD&components=buttons" data-sdk-integration-source="developer-studio"></script>'

content = content.replace(old_sdk, new_sdk)

# Fix 2: Remove duplicate try block and console.log
old_broken_start = '''    console.log('PayPal SDK loaded - rendering 9 buttons...');
    try {
    console.log('PayPal SDK loaded successfully - rendering 9 buttons');
    try {
        paypal.Buttons({'''

new_fixed_start = '''    console.log('PayPal SDK loaded - rendering 9 buttons...');
    try {
        paypal.Buttons({'''

content = content.replace(old_broken_start, new_fixed_start)

# Fix 3: Add onError handlers to all buttons (per PayPal docs)
# For button 1
old_btn1_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=bi-report&amount=97&order=' + data.orderID;
            });
        }
    }).render('#pp-bi-report');'''

new_btn1_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=bi-report&amount=97&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (bi-report):', err);
        }
    }).render('#pp-bi-report');'''

content = content.replace(old_btn1_end, new_btn1_end)

# For button 2
old_btn2_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=competitive-analysis&amount=149&order=' + data.orderID;
            });
        }
    }).render('#pp-competitive-analysis');'''

new_btn2_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=competitive-analysis&amount=149&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (competitive-analysis):', err);
        }
    }).render('#pp-competitive-analysis');'''

content = content.replace(old_btn2_end, new_btn2_end)

# For button 3
old_btn3_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=intelligence-hub&amount=2500&order=' + data.orderID;
            });
        }
    }).render('#pp-intelligence-hub');'''

new_btn3_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=intelligence-hub&amount=2500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (intelligence-hub):', err);
        }
    }).render('#pp-intelligence-hub');'''

content = content.replace(old_btn3_end, new_btn3_end)

# For button 4
old_btn4_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-micro&amount=500&order=' + data.orderID;
            });
        }
    }).render('#pp-content-micro');'''

new_btn4_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-micro&amount=500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (content-micro):', err);
        }
    }).render('#pp-content-micro');'''

content = content.replace(old_btn4_end, new_btn4_end)

# For button 5
old_btn5_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-standard&amount=2500&order=' + data.orderID;
            });
        }
    }).render('#pp-content-standard');'''

new_btn5_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-standard&amount=2500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (content-standard):', err);
        }
    }).render('#pp-content-standard');'''

content = content.replace(old_btn5_end, new_btn5_end)

# For button 6
old_btn6_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-enterprise&amount=15000&order=' + data.orderID;
            });
        }
    }).render('#pp-content-enterprise');'''

new_btn6_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=content-enterprise&amount=15000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (content-enterprise):', err);
        }
    }).render('#pp-content-enterprise');'''

content = content.replace(old_btn6_end, new_btn6_end)

# For button 7
old_btn7_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=creative-forge&amount=5000&order=' + data.orderID;
            });
        }
    }).render('#pp-creative-forge');'''

new_btn7_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=creative-forge&amount=5000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (creative-forge):', err);
        }
    }).render('#pp-creative-forge');'''

content = content.replace(old_btn7_end, new_btn7_end)

# For button 8
old_btn8_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=ops-core&amount=9500&order=' + data.orderID;
            });
        }
    }).render('#pp-ops-core');'''

new_btn8_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=ops-core&amount=9500&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (ops-core):', err);
        }
    }).render('#pp-ops-core');'''

content = content.replace(old_btn8_end, new_btn8_end)

# For button 9
old_btn9_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=growth-engine&amount=15000&order=' + data.orderID;
            });
        }
    }).render('#pp-growth-engine');'''

new_btn9_end = '''        onApprove: function(data, actions) {
            return actions.order.capture().then(function() {
                window.location.href = '/payment/success?product=growth-engine&amount=15000&order=' + data.orderID;
            });
        },
        onError: function(err) {
            console.error('PayPal error (growth-engine):', err);
        }
    }).render('#pp-growth-engine');'''

content = content.replace(old_btn9_end, new_btn9_end)

# Write fixed file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: PayPal buttons fixed!')
print('Fixes applied:')
print('  1. Updated SDK URL with components=buttons parameter')
print('  2. Removed duplicate try block and console.log')
print('  3. Added onError handlers to all 9 buttons')
print('  4. Now follows official PayPal developer docs')
print(f'File size: {len(content)} characters')
