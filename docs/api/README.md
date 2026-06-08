# A2A and Runtime API Reference

This document describes the primary HTTP and JSON-RPC surfaces exposed by the current SINCOR2 Flask runtime.

## Base URL

- Local development: `http://localhost:8080`
- Production: `https://getsincor.com`

## Authentication

SINCOR2 uses two primary authentication patterns:

1. **A2A discovery and task execution** — Agent Cards advertise an `apiKey` security scheme using the `X-API-Key` header.
2. **Dashboard/admin API flows** — JWT-backed endpoints under `/api/auth` rely on standard JWT bearer authorization headers.

## Endpoint inventory

| Method | Path | Purpose |
|---|---|---|
| GET | `/.well-known/agent-card.json` | Canonical A2A v1.0.1 Agent Card |
| GET | `/.well-known/agent.json` | Legacy Agent Card alias |
| POST | `/api/a2a` | A2A JSON-RPC dispatcher |
| POST | `/api/a2a/tasks/send` | Legacy REST task submission |
| GET | `/api/a2a/tasks/<task_id>` | Legacy REST task lookup |
| POST | `/api/a2a/tasks/cancel` | Legacy REST task cancellation |
| GET | `/api/a2a/agents` | Marketplace skill catalogue and token metadata |
| POST | `/api/a2a/quote` | Quote endpoint for AXIOM task pricing |
| POST | `/api/auth/login` | User authentication |
| POST | `/api/auth/verify-token` | JWT verification |
| GET | `/api/auth/profile` | Authenticated profile lookup |
| GET | `/api/auth/admin/users` | Authenticated admin listing placeholder |
| POST | `/api/payment/stripe/create-checkout` | Stripe checkout session creation |
| POST | `/api/payment/paypal/create-order` | PayPal order creation |
| POST | `/api/waitlist` | Waitlist registration |
| POST | `/api/waitlist/join` | Alternate waitlist registration path |
| GET | `/health` | Service health probe |
| GET | `/api/monitoring/dashboard` | Dashboard metrics summary |

## A2A discovery schemas

### `GET /.well-known/agent-card.json`
Returns a v1.0.1 Agent Card with the following top-level fields:

```json
{
  "name": "SINCOR Agent Swarm",
  "description": "...",
  "version": "2.0.0",
  "supportedInterfaces": [
    {
      "url": "https://getsincor.com/api/a2a",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0.1"
    }
  ],
  "provider": {"organization": "SINCOR", "url": "https://getsincor.com"},
  "capabilities": {"streaming": true, "pushNotifications": false, "stateTransitionHistory": true},
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"],
  "skills": [],
  "securitySchemes": {"apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
  "security": [{"apiKey": []}]
}
```

### `GET /.well-known/agent.json`
Returns the legacy Agent Card alias with flattened transport metadata for older clients.

## A2A JSON-RPC interface

### `POST /api/a2a`
The dispatcher accepts JSON-RPC 2.0 envelopes.

#### Request envelope

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"text": "Verify eligibility for member 123"}]
    }
  }
}
```

#### Supported methods
- `message/send`
- `message/stream` (SSE)
- `tasks/get`
- `tasks/cancel`
- `tasks/list`
- `tasks/pushNotificationConfig/set`
- `tasks/pushNotificationConfig/get`
- `tasks/pushNotificationConfig/delete`
- `tasks/pushNotificationConfig/list`
- `tasks/resubscribe` (SSE)

#### Success envelope

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "result": {
    "id": "task-abc",
    "status": {"state": "submitted", "timestamp": "2026-01-01T00:00:00+00:00"},
    "artifacts": [],
    "metadata": {}
  }
}
```

#### Error envelope

```json
{
  "jsonrpc": "2.0",
  "id": "req-123",
  "error": {
    "code": -32601,
    "message": "Method 'unknown/method' not found"
  }
}
```

## Quote and agent catalogue endpoints

### `GET /api/a2a/agents`
Returns the advertised SINCOR skill catalogue plus chain metadata.

#### Example response

```json
{
  "agents": [
    {"id": "market-intelligence", "name": "Market & Competitive Intelligence", "tags": ["market"], "axm_price_per_task": "1000000000000000000"}
  ],
  "axiom_contract": "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822",
  "sinc_contract": "0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
  "treasury": "0xAf9B539D8043C634b7E611818518BA7E850F289e",
  "chain_id": 8453
}
```

### `POST /api/a2a/quote`
Request a pricing quote for a skill.

#### Request

```json
{
  "skill_id": "lead-enrichment"
}
```

#### Response

```json
{
  "skill_id": "lead-enrichment",
  "axm_price_wei": "1000000000000000000",
  "axm_price_display": "1.0000 AXM",
  "pay_to": "0xAf9B539D8043C634b7E611818518BA7E850F289e",
  "axiom_contract": "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822",
  "chain_id": 8453,
  "note": "Transfer the exact AXM amount ..."
}
```

## Auth endpoints

### `POST /api/auth/login`

#### Request

```json
{
  "username": "operator@example.com",
  "password": "correct horse battery staple"
}
```

#### Success response

Returns the authentication payload produced by `SINCORAuth.authenticate_user(...)`.

#### Error response

```json
{
  "status": "error",
  "code": "auth_failed",
  "message": "Authentication failed",
  "details": {},
  "request_id": "..."
}
```

### `POST /api/auth/verify-token`
Validates a JWT passed in the request body.

### `GET /api/auth/profile`
Requires a valid bearer token and returns the current user id.

## Payments endpoints

### `POST /api/payment/stripe/create-checkout`
Creates a Stripe checkout session.

#### Request schema

```json
{
  "amount": 99.0,
  "currency": "USD",
  "description": "Healthcare credentialing onboarding",
  "customer_email": "ops@example.com"
}
```

#### Validation rules
- `amount` must be numeric, at least `1.00`, and at most `1,000,000`.
- `currency` must be a three-letter uppercase code.
- `description` must be at least three characters.

### `POST /api/payment/paypal/create-order`
Creates a lightweight PayPal order preview.

#### Request

```json
{
  "amount": 250.0,
  "currency": "USD"
}
```

#### Response

```json
{
  "success": true,
  "order_id": "PAY-AB12CD34",
  "amount": "250.00",
  "currency": "USD",
  "approval_url": "https://www.paypal.com/checkoutnow?token=PAY-AB12CD34"
}
```

## Waitlist endpoint

### `POST /api/waitlist` or `POST /api/waitlist/join`
Registers a waitlist submission.

#### Request schema

```json
{
  "email": "founder@example.com",
  "name": "Founder Name",
  "company": "Example Co",
  "product_interest": "healthcare_rcm",
  "message": "Interested in production deployment support"
}
```

## Monitoring endpoints

### `GET /health`
Returns service, environment, timestamp, and health status.

### `GET /api/monitoring/dashboard`
Returns a small metrics summary describing payment and waitlist subsystem availability.

## Standard error format

Runtime errors normalized through `src/sincor2/error_handling.py` follow this schema:

```json
{
  "status": "error",
  "code": "invalid_request",
  "message": "Human-readable message",
  "details": {},
  "request_id": "request correlation id"
}
```

## Common error codes

| Code | Meaning |
|---|---|
| `invalid_request` | Required fields are missing or malformed |
| `auth_failed` | Username/password validation failed |
| `invalid_token` | JWT is invalid or expired |
| `invalid_payment_request` | Payment request failed validation |
| `invalid_amount` | Numeric payment value is invalid |
| `stripe_unavailable` | Stripe subsystem is not configured |
| `stripe_error` | Stripe checkout session creation failed |
| `waitlist_unavailable` | Waitlist manager is unavailable |
| `waitlist_error` | Waitlist insertion failed |
| `not_found` | Route or resource does not exist |
| `internal_error` | Unhandled server exception |

## Operational notes

- The current A2A task store is in-memory unless replaced with a persistent backend.
- Streaming methods use Server-Sent Events with `text/event-stream` responses.
- Payment confirmation for production environments depends on Base RPC configuration and the AXIOM transfer verifier.
