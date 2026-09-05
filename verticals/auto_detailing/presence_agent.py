"""Website metadata, local SEO / AEO, gallery, and review showcase."""

from __future__ import annotations

from typing import Any, Dict, List

from .protocols import AEO_FAQS, GBP_CATEGORIES, LOCAL_SEO_KEYWORDS, PACKAGES
from .schemas import PresenceRequest


def _json_ld(req: PresenceRequest) -> Dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": ["AutoRepair", "LocalBusiness"],
        "name": req.shop_name,
        "url": req.url,
        "telephone": req.phone,
        "address": {
            "@type": "PostalAddress",
            "addressLocality": req.city,
            "addressRegion": req.region,
            "addressCountry": "US",
        },
        "image": [g.get("src") for g in req.gallery if g.get("src")],
        "priceRange": "$$-$$$",
        "knowsAbout": list(LOCAL_SEO_KEYWORDS),
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "Detailing services",
            "itemListElement": [
                {
                    "@type": "Offer",
                    "itemOffered": {"@type": "Service", "name": str(p["label"])},
                    "price": p["price"],
                    "priceCurrency": "USD",
                }
                for p in PACKAGES.values()
            ],
        },
        "aggregateRating": _aggregate(req.reviews),
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in AEO_FAQS
        ],
    }


def _aggregate(reviews: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    if not reviews:
        return None
    ratings = [float(r.get("rating", 5)) for r in reviews]
    return {
        "@type": "AggregateRating",
        "ratingValue": round(sum(ratings) / len(ratings), 2),
        "reviewCount": len(ratings),
        "bestRating": 5,
    }


class DetailingPresenceAgent:
    def optimize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = PresenceRequest(
            shop_name=payload.get("shop_name", "Northline Detail"),
            city=payload.get("city", "Dubuque"),
            region=payload.get("region", "IA"),
            phone=payload.get("phone", "(563) 555-0148"),
            url=payload.get("url", "https://northlinedetail.com"),
            services=list(payload.get("services") or list(PACKAGES.keys())),
            reviews=list(payload.get("reviews") or []),
            gallery=list(payload.get("gallery") or []),
        )
        title = f"{req.shop_name} | Ceramic, PPF & Auto Detailing in {req.city}, {req.region}"
        description = (
            f"{req.shop_name} is the {req.city} detailing shop you can actually book. "
            "Ceramic coatings, paint correction, interiors, and membership washes — "
            "instant quotes, photo intake, self-serve calendar."
        )
        title = title[:60]
        description = description[:155]
        gallery = []
        for item in req.gallery:
            alt = item.get("alt") or (
                f"{item.get('vehicle', 'Vehicle')} {item.get('kind', 'after')} — {req.shop_name} {req.city}"
            )
            gallery.append({**item, "alt": alt[:120]})
        showcase = sorted(
            [r for r in req.reviews if float(r.get("rating", 0)) >= 4],
            key=lambda r: float(r.get("rating", 0)),
            reverse=True,
        )[:6]
        score = 62
        score += 8 if 50 <= len(title) <= 60 else 0
        score += 8 if 140 <= len(description) <= 155 else 0
        score += 6 if gallery else 0
        score += 6 if showcase else 0
        score += 5  # JSON-LD always present
        score = min(score, 98)
        return {
            "title": title,
            "meta_description": description,
            "canonical": req.url,
            "og": {
                "title": title,
                "description": description,
                "type": "website",
            },
            "json_ld": _json_ld(req),
            "gbp_categories": list(GBP_CATEGORIES),
            "keywords": [f"{kw} {req.city}" for kw in LOCAL_SEO_KEYWORDS[:6]],
            "gallery": gallery,
            "review_showcase": showcase,
            "aeo_faqs": [{"q": q, "a": a} for q, a in AEO_FAQS],
            "seo_score": score,
            "fixes": [
                "Keep title 50–60 characters with city + primary service.",
                "Pin 6 five-star reviews with vehicle + service mentioned.",
                "Every gallery image needs vehicle + treatment in alt text.",
                "Publish weekly GBP photos from the bay, not stock.",
            ],
        }

    def review_reply(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        author = payload.get("author") or "there"
        rating = float(payload.get("rating", 5))
        service = payload.get("service") or "detail"
        if rating >= 5:
            text = (
                f"Thanks {author} — glad the {service} landed the way it should. "
                "Book the 30-day maintenance wash from the same link so the finish stays honest."
            )
        elif rating >= 4:
            text = (
                f"Appreciate you {author}. If anything on the {service} was a hair off, "
                "text the shop — we'll make the next visit cleaner."
            )
        else:
            text = (
                f"{author}, sorry the {service} missed. A manager will reach out today "
                "to make it right before we ask for another look."
            )
        return {"author": author, "rating": rating, "reply": text, "public": True}
