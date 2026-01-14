from fastapi import FastAPI, HTTPException, Response, Query, UploadFile, File, Form, Depends, Header
from pydantic import BaseModel, EmailStr
import asyncpg
import os
import json
from typing import Optional, List
from datetime import datetime, timedelta
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from storage import MarketplaceStorage
import stripe

app = FastAPI()
DB_DSN = os.getenv("DB_DSN")
ADMIN_API_KEY = os.getenv("MARKETPLACE_ADMIN_KEY", "admin-dev-key")

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Metrics
CATALOG_VIEWS = Counter("marketplace_catalog_views_total", "Catalog page views")
PURCHASES = Counter("marketplace_purchases_total", "Total purchases", ["category", "type"])
DOWNLOADS = Counter("marketplace_downloads_total", "Total downloads")

class CatalogItem(BaseModel):
    title: str
    description: Optional[str] = ""
    price_cents: int
    type: str  # template, mediapax_pack, script_bundle
    category: Optional[str] = "general"
    tags: Optional[List[str]] = []
    featured: Optional[bool] = False

class PurchaseRequest(BaseModel):
    item_id: str
    customer_email: EmailStr
    tenant_id: Optional[str] = None

class ReviewRequest(BaseModel):
    item_id: str
    rating: int
    review_text: Optional[str] = ""

@app.on_event("startup")
async def startup():
    app.state.db = await asyncpg.connect(DB_DSN)
    app.state.storage = MarketplaceStorage()

async def verify_admin(authorization: str = Header(None)):
    """Admin authentication"""
    if authorization != f"Bearer {ADMIN_API_KEY}":
        raise HTTPException(status_code=403, detail="Admin access required")

@app.get("/health")
async def health():
    return {"ok": True, "service": "marketplace"}

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

# Catalog Management (Admin)
@app.post("/catalog/items", dependencies=[Depends(verify_admin)])
async def create_catalog_item(
    title: str = Form(...),
    description: str = Form(""),
    price_cents: int = Form(...),
    type: str = Form(...),
    category: str = Form("general"),
    tags: str = Form(""),  # comma-separated
    featured: bool = Form(False),
    file: UploadFile = File(...)
):
    """Create a new catalog item (admin only)"""
    try:
        # Store the file
        file_content = await file.read()
        file_key = await app.state.storage.store_template(
            file_content, 
            file.filename,
            {"title": title, "type": type, "category": category}
        )
        
        # Parse tags
        tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else []
        
        # Insert into database
        item_id = await app.state.db.fetchval("""
            INSERT INTO catalog (title, description, price_cents, type, category, payload_ref, tags, featured)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
        """, title, description, price_cents, type, category, file_key, tag_list, featured)
        
        return {
            "item_id": str(item_id),
            "title": title,
            "file_key": file_key,
            "status": "created"
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create item: {str(e)}")

@app.put("/catalog/items/{item_id}", dependencies=[Depends(verify_admin)])
async def update_catalog_item(item_id: str, updates: CatalogItem):
    """Update catalog item (admin only)"""
    await app.state.db.execute("""
        UPDATE catalog 
        SET title = $1, description = $2, price_cents = $3, category = $4, 
            tags = $5, featured = $6, updated_at = now()
        WHERE id = $7
    """, updates.title, updates.description, updates.price_cents, updates.category,
        updates.tags, updates.featured, item_id)
    
    return {"item_id": item_id, "status": "updated"}

# Public Catalog
@app.get("/catalog")
async def browse_catalog(
    category: Optional[str] = None,
    type: Optional[str] = None,
    featured: Optional[bool] = None,
    search: Optional[str] = None,
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0)
):
    """Browse marketplace catalog (public)"""
    CATALOG_VIEWS.inc()
    
    query = """
        SELECT c.*, 
               (SELECT COUNT(*) FROM purchases p WHERE p.item_id = c.id AND p.status = 'completed') as sales_count,
               (SELECT COUNT(*) FROM reviews r WHERE r.item_id = c.id) as review_count
        FROM catalog c 
        WHERE c.active = true
    """
    params = []
    
    if category:
        params.append(category)
        query += f" AND c.category = ${len(params)}"
        
    if type:
        params.append(type)
        query += f" AND c.type = ${len(params)}"
        
    if featured is not None:
        params.append(featured)
        query += f" AND c.featured = ${len(params)}"
        
    if search:
        params.append(f"%{search.lower()}%")
        query += f" AND (LOWER(c.title) LIKE ${len(params)} OR LOWER(c.description) LIKE ${len(params)})"
    
    # Order by featured first, then by rating and sales
    query += " ORDER BY c.featured DESC, c.rating DESC, c.download_count DESC"
    
    params.extend([limit, offset])
    query += f" LIMIT ${len(params)-1} OFFSET ${len(params)}"
    
    rows = await app.state.db.fetch(query, *params)
    
    items = []
    for row in rows:
        items.append({
            "id": str(row['id']),
            "title": row['title'],
            "description": row['description'],
            "price_cents": row['price_cents'],
            "type": row['type'],
            "category": row['category'],
            "tags": row['tags'],
            "featured": row['featured'],
            "rating": float(row['rating']) if row['rating'] else 0.0,
            "download_count": row['download_count'],
            "sales_count": row['sales_count'],
            "review_count": row['review_count'],
            "preview_url": row['preview_url']
        })
    
    return {"items": items, "count": len(items)}

@app.get("/catalog/items/{item_id}")
async def get_catalog_item(item_id: str):
    """Get detailed catalog item info"""
    row = await app.state.db.fetchrow("""
        SELECT c.*,
               (SELECT COUNT(*) FROM purchases p WHERE p.item_id = c.id AND p.status = 'completed') as sales_count
        FROM catalog c 
        WHERE c.id = $1 AND c.active = true
    """, item_id)
    
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
    
    # Get recent reviews
    reviews = await app.state.db.fetch("""
        SELECT rating, review_text, created_at
        FROM reviews 
        WHERE item_id = $1 
        ORDER BY created_at DESC 
        LIMIT 5
    """, item_id)
    
    return {
        "id": str(row['id']),
        "title": row['title'],
        "description": row['description'],
        "price_cents": row['price_cents'],
        "type": row['type'],
        "category": row['category'],
        "tags": row['tags'],
        "featured": row['featured'],
        "rating": float(row['rating']) if row['rating'] else 0.0,
        "download_count": row['download_count'],
        "sales_count": row['sales_count'],
        "preview_url": row['preview_url'],
        "reviews": [
            {
                "rating": r['rating'],
                "text": r['review_text'],
                "created_at": r['created_at'].isoformat()
            } for r in reviews
        ]
    }

# Purchase Flow
@app.post("/purchase")
async def purchase_item(purchase: PurchaseRequest):
    """Purchase a catalog item"""
    # Get item details
    item = await app.state.db.fetchrow("SELECT * FROM catalog WHERE id = $1 AND active = true", purchase.item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    
    try:
        # Create Stripe Payment Intent
        payment_intent = stripe.PaymentIntent.create(
            amount=item['price_cents'],
            currency='usd',
            metadata={
                'item_id': purchase.item_id,
                'customer_email': purchase.customer_email,
                'tenant_id': purchase.tenant_id or ''
            }
        )
        
        # Create purchase record
        purchase_id = await app.state.db.fetchval("""
            INSERT INTO purchases (tenant_id, customer_email, item_id, amount_cents, payment_ref, status)
            VALUES ($1, $2, $3, $4, $5, 'pending')
            RETURNING id
        """, purchase.tenant_id, purchase.customer_email, purchase.item_id, 
            item['price_cents'], payment_intent.id)
        
        PURCHASES.labels(category=item['category'], type=item['type']).inc()
        
        return {
            "purchase_id": str(purchase_id),
            "client_secret": payment_intent.client_secret,
            "amount_cents": item['price_cents'],
            "item_title": item['title']
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Purchase failed: {str(e)}")

@app.get("/receipts/{purchase_id}")
async def get_receipt(purchase_id: str):
    """Get purchase receipt and download link"""
    purchase = await app.state.db.fetchrow("""
        SELECT p.*, c.title, c.payload_ref, c.type
        FROM purchases p
        JOIN catalog c ON p.item_id = c.id
        WHERE p.id = $1
    """, purchase_id)
    
    if not purchase:
        raise HTTPException(status_code=404, detail="Receipt not found")
    
    response = {
        "purchase_id": str(purchase['id']),
        "item_title": purchase['title'],
        "amount_cents": purchase['amount_cents'],
        "status": purchase['status'],
        "created_at": purchase['created_at'].isoformat()
    }
    
    # Add download link if purchase is completed
    if purchase['status'] == 'completed' and purchase['payload_ref']:
        download_url = app.state.storage.generate_signed_url(purchase['payload_ref'], expires_in=24*3600)
        response['download_url'] = download_url
        response['download_expires_at'] = (datetime.now() + timedelta(hours=24)).isoformat()
        
        # Track download
        DOWNLOADS.inc()
        
        # Update download count
        await app.state.db.execute("""
            UPDATE catalog SET download_count = download_count + 1 WHERE id = $1
        """, purchase['item_id'])
    
    return response

# Reviews
@app.post("/reviews")
async def add_review(review: ReviewRequest):
    """Add a review for a purchased item"""
    # Verify purchase exists
    purchase = await app.state.db.fetchrow("""
        SELECT id FROM purchases 
        WHERE item_id = $1 AND status = 'completed'
        LIMIT 1
    """, review.item_id)
    
    if not purchase:
        raise HTTPException(status_code=400, detail="Must purchase item to review")
    
    # Add review
    await app.state.db.execute("""
        INSERT INTO reviews (item_id, tenant_id, rating, review_text)
        VALUES ($1, $2, $3, $4)
    """, review.item_id, None, review.rating, review.review_text)
    
    # Update average rating
    await app.state.db.execute("""
        UPDATE catalog 
        SET rating = (
            SELECT AVG(rating)::decimal(2,1) 
            FROM reviews 
            WHERE item_id = $1
        )
        WHERE id = $1
    """, review.item_id)
    
    return {"status": "review_added", "rating": review.rating}

# Webhook for payment completion
@app.post("/webhooks/payment")
async def handle_payment_webhook(request: dict):
    """Handle payment completion webhook"""
    # In production, verify webhook signature
    
    if request.get('type') == 'payment_intent.succeeded':
        payment_intent_id = request['data']['object']['id']
        
        # Mark purchase as completed
        await app.state.db.execute("""
            UPDATE purchases 
            SET status = 'completed' 
            WHERE payment_ref = $1
        """, payment_intent_id)
        
    return {"received": True}