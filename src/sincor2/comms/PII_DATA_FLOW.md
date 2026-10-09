# Signup / Onboarding PII Data Flow (WP2)

Owner decisions: D1 (outreach off), D2 (transactional only).
This document records what PII the signup/onboarding paths collect,
where it flows, and the retention rules. It is a living document —
update it when a path changes.

## Paths audited (2026-10-09)

### 1. EmailSender (src/sincor2/email_sender.py)
- `send_thank_you_email(customer_email, customer_name, tier, ...)`
  - Collects: email, name, tier, purchase metadata.
  - Flow: caller → EmailSender → Resend or SendGrid → provider.
  - Purpose: TRANSACTIONAL (post-purchase thank-you).
- `send_welcome_email(customer_email, customer_name, ...)`
  - Collects: email, name, onboarding metadata.
  - Flow: caller → EmailSender → Resend or SendGrid → provider.
  - Purpose: TRANSACTIONAL (onboarding).
- `send_email(to_email, to_name, subject, html_content, ...)`
  - Generic. Callers MUST classify purpose via `sincor2.comms.classify`.
  - Unclassified → marketing → blocked (D2).

### 2. OutreachEngine (src/sincor2/outreach_engine.py) — DISABLED (D1)
- `fetch_yelp_leads`, `enrich_with_google_places`, `_scrape_emails`,
  `_guess_email`: lead harvesting. NOT RUN (scheduler gated off).
- `send_outreach_email`: cold outreach. Purpose: MARKETING → always
  blocked by `CommsAdapter` even if invoked directly.
- `_load_sent_ids` / `_mark_sent`: local sent-ID tracking file.
  Retention: IDs only, no PII. Safe to retain.

### 3. WebBuilder contact capture
- Contact forms capture: name, email, project details.
- Flow: form → WebBuilder → notification email (transactional).
- Must pass through `CommsAdapter` with purpose=transactional.

## Retention rules

1. **Collect minimum**: email + name only where needed for the
   transactional purpose. No phone, address, or demographic data
   unless the customer explicitly provides it for service delivery.
2. **No marketing list**: D2 forbids marketing email. Do not build,
   buy, or retain a marketing list. Lead-harvested emails
   (`_scrape_emails`, `_guess_email`) must NOT be persisted.
3. **Suppression is forever**: opt-outs in the suppression list are
   retained indefinitely (an email address + reason + timestamp only).
   Deletion requests remove all other PII but KEEP the suppression
   entry (otherwise the user could be re-contacted).
4. **Provider minimization**: only the fields the provider needs
   (to, subject, body) are sent. No internal IDs, tiers, or metadata
   beyond what the template requires.
5. **Log redaction**: PII (email addresses, names) must NOT appear in
   application logs at INFO or above. The comms adapter logs the
   recipient at INFO for audit — this is under review; prefer hashing
   recipient in logs in a future pass.
6. **Retention window**: transactional email records (who was emailed,
   when, which template) retained max 24 months, then purged.
   Suppression entries retained indefinitely (see rule 3).

## What is NOT stored

- Full email bodies are never stored (only template IDs).
- Harvested/guessed emails are never persisted.
- No cross-referencing PII across tenants.
