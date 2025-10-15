# Contracts (specs/contracts.md)

## ContentUnit
- id: string
- family: enum('pillar','cluster','flyer','adset','script','email','whitepaper','sop','testimonial_card')
- tone: enum from engine.yaml
- text: markdown
- assets: [paths/urls]
- metadata: {cta_id, offer_id, evidence_refs[]}
- score: {R, D, E, C, B, S, Q}

## CanvaTask
- template_key: string (matches canva/manifest.json)
- slot_map: {slot_name: value or asset_ref}
- export_formats: ['PNG','PDF','MP4']

## PublishTask
- channel: enum('wordpress','meta_ads','google_ads','email')
- payload_ref: path or object
- schedule_at: iso8601 or null
