#!/usr/bin/env python3
"""
Reduce all product prices to much more affordable levels
Make SINCOR accessible to everyone
"""

with open('templates/buy.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Price reductions mapping
price_changes = {
    # Service products - one-time purchases
    '97.00': '19.00',    # Business Intelligence Report: $97 -> $19
    '149.00': '29.00',   # Competitive Analysis: $149 -> $29
    '2500.00': '97.00',  # Intelligence Hub & Content Standard: $2,500 -> $97
    '500.00': '49.00',   # Content Micro: $500 -> $49
    '15000.00': '497.00', # Content Enterprise & Growth Engine: $15,000 -> $497
    '5000.00': '197.00',  # Creative Forge: $5,000 -> $197
    '9500.00': '297.00',  # Ops Core: $9,500 -> $297

    # Display prices (with $ and commas)
    '$97': '$19',
    '$149': '$29',
    '$2,500': '$97',
    '$500': '$49',
    '$15,000': '$497',
    '$5,000': '$197',
    '$9,500': '$297',
}

# SWARM subscription price reductions
swarm_changes = {
    # Monthly subscriptions
    '$497': '$49',      # Scout Squad: $497/mo -> $49/mo
    '$4,997': '$497',   # Full Swarm: $4,997/mo -> $497/mo
    '$1,997': '$97',    # Custom Squad: $1,997/mo -> $97/mo
    '$14,997': '$997',  # Enterprise: $14,997/mo -> $997/mo
}

# Apply service product price changes
for old_price, new_price in price_changes.items():
    content = content.replace(old_price, new_price)

# Apply SWARM subscription price changes
for old_price, new_price in swarm_changes.items():
    content = content.replace(old_price, new_price)

# Update PayPal button values to match
paypal_value_changes = {
    "value: '97.00'": "value: '19.00'",
    "value: '149.00'": "value: '29.00'",
    "value: '2500.00'": "value: '97.00'",
    "value: '500.00'": "value: '49.00'",
    "value: '15000.00'": "value: '497.00'",
    "value: '5000.00'": "value: '197.00'",
    "value: '9500.00'": "value: '297.00'",
}

for old_val, new_val in paypal_value_changes.items():
    content = content.replace(old_val, new_val)

# Update URL parameters for payment success redirects
url_param_changes = {
    'amount=97&': 'amount=19&',
    'amount=149&': 'amount=29&',
    'amount=2500&': 'amount=97&',
    'amount=500&': 'amount=49&',
    'amount=15000&': 'amount=497&',
    'amount=5000&': 'amount=197&',
    'amount=9500&': 'amount=297&',
    'amount=497&': 'amount=49&',
    'amount=4997&': 'amount=497&',
    'amount=1997&': 'amount=97&',
    'amount=14997&': 'amount=997&',
}

for old_url, new_url in url_param_changes.items():
    content = content.replace(old_url, new_url)

# Write updated file
with open('templates/buy.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('SUCCESS: All prices dramatically reduced!')
print('\nNEW PRICING - Way More Affordable:')
print('\nService Products (One-time):')
print('  1. Business Intelligence Report: $97 -> $19 (-80%)')
print('  2. Competitive Analysis: $149 -> $29 (-81%)')
print('  3. Intelligence Hub: $2,500 -> $97 (-96%)')
print('  4. Content Micro: $500 -> $49 (-90%)')
print('  5. Content Standard: $2,500 -> $97 (-96%)')
print('  6. Content Enterprise: $15,000 -> $497 (-97%)')
print('  7. Creative Forge: $5,000 -> $197 (-96%)')
print('  8. Ops Core: $9,500 -> $297 (-97%)')
print('  9. Growth Engine: $15,000 -> $497 (-97%)')
print('\nSWARM Subscriptions (Monthly):')
print('  10. Scout Squad: $497/mo -> $49/mo (-90%)')
print('  11. Full 42-Agent Swarm: $4,997/mo -> $497/mo (-90%)')
print('  12. Custom Squad: $1,997/mo -> $97/mo (-95%)')
print('  13. Enterprise Unlimited: $14,997/mo -> $997/mo (-93%)')
print('\nNow accessible to everyone!')
print(f'File size: {len(content)} characters')
