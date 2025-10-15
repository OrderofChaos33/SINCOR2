# Video Script for {{ business_name }}
**Target**: {{ business_type | business_type_friendly }} in {{ city }}
**Persona**: {{ persona }}
**Duration**: 60 seconds

## Hook (0-5 seconds)
{{ hbpc.hooks | select_by_persona(persona) }}
*Visual: Split screen - dirty vs clean business vehicle*

## Problem (5-15 seconds)
You run {{ business_name }}, and every detail matters to your reputation. But when was the last time you really looked at your company vehicles?
*Visual: Close-up of dirty vehicle with business logo barely visible*

## Solution (15-35 seconds)
{{ hbpc.benefits | select_by_persona(persona) }}
*Visual: Before/after transformation of similar business vehicle*

## Proof (35-50 seconds)  
{{ hbpc.proofs | select_by_persona(persona) }}
*Visual: Customer testimonials, before/after photos*

## CTA (50-60 seconds)
Ready to elevate {{ business_name }}'s professional image?
{{ hbpc.ctas | select_by_persona(persona) }}
*Visual: Clean vehicle with sparkling logo, contact information*

---
**Personalization Data:**
- Business: {{ business_name }}
- Location: {{ city }}, {{ state }}
- Phone: {{ phone | format_phone }}
- Rating: {{ rating }}/5 ({{ review_count }} reviews)
- Lead Score: {{ lead_score }}/100
