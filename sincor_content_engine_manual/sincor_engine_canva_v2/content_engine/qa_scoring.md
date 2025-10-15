# QA Scoring (content_engine/qa_scoring.md)
**Score Q** ∈ [0,1] is a weighted blend:
- Readability (R): 0.20
- Information Density (D): 0.25
- Evidence Presence (E): 0.20
- CTA Cadence (C): 0.15
- Brand Integrity (B): 0.10
- Similarity Inverse (S): 0.10  (1 - Jaccard)

Q = 0.20R + 0.25D + 0.20E + 0.15C + 0.10B + 0.10S

Fail any hard constraint (PII leak, policy-violating claim) → auto-reject regardless of Q.
