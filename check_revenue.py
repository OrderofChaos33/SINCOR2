#!/usr/bin/env python3
import sqlite3

db = sqlite3.connect('orders.db')
cursor = db.cursor()

# Get all orders
cursor.execute('SELECT COUNT(*) FROM orders')
total_orders = cursor.fetchone()[0]

# Get completed revenue
cursor.execute('SELECT SUM(amount) FROM orders WHERE payment_status = ?', ('completed',))
completed_revenue = cursor.fetchone()[0] or 0

# Get pending revenue  
cursor.execute('SELECT SUM(amount) FROM orders WHERE payment_status = ?', ('pending',))
pending_revenue = cursor.fetchone()[0] or 0

# Get top products
cursor.execute('SELECT product_name, COUNT(*), SUM(amount) FROM orders GROUP BY product_name ORDER BY SUM(amount) DESC LIMIT 10')
products = cursor.fetchall()

print("\n" + "="*60)
print("💰 SINCOR REVENUE STATUS")
print("="*60)
print(f"Total Orders: {total_orders}")
print(f"Completed Revenue: ${completed_revenue:.2f}")
print(f"Pending Revenue: ${pending_revenue:.2f}")
print(f"Total Revenue: ${completed_revenue + pending_revenue:.2f}")

print("\n📊 TOP PRODUCTS:")
print("="*60)
for product, count, revenue in products:
    print(f"  {product}: {count} orders, ${revenue:.2f}")

db.close()
