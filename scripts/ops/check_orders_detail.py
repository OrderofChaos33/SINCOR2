#!/usr/bin/env python3
import sqlite3

db = sqlite3.connect('orders.db')
cursor = db.cursor()

# Get ALL orders with full details
cursor.execute('SELECT order_id, paypal_order_id, customer_email, product_name, amount, payment_status, created_at FROM orders ORDER BY created_at DESC')
orders = cursor.fetchall()

print("\n" + "="*80)
print("ALL ORDERS IN DATABASE")
print("="*80)
if not orders:
    print("NO ORDERS FOUND")
else:
    for o in orders:
        print(f"  ID: {o[0]}")
        print(f"  PayPal ID: {o[1]}")
        print(f"  Email: {o[2]}")
        print(f"  Product: {o[3]}")
        print(f"  Amount: ${o[4]}")
        print(f"  Status: {o[5]}")
        print(f"  Created: {o[6]}")
        print(f"  ---")

db.close()
