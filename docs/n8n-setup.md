# n8n Setup Guide for SteadyBook CRM

## Overview

n8n acts as middleware between social platforms (Meta, LinkedIn, Google) and SteadyBook.
SteadyBook never calls these APIs directly — n8n handles all OAuth and webhook subscriptions,
then fires a normalized payload to SteadyBook's `/api/social-intake` endpoint.

## Webhook Endpoint

```
POST https://your-steadybook-domain.com/api/social-intake
Content-Type: application/json
X-SteadyBook-Signature: sha256=<hmac_hex>
```

The `X-SteadyBook-Signature` header is computed as:
```
HMAC-SHA256(secret=STEADYBOOK_WEBHOOK_SECRET, message=raw_request_body)
```

Set `STEADYBOOK_WEBHOOK_SECRET` in both n8n and SteadyBook's environment variables.

---

## Normalized Payload Schema

All channel payloads share this top-level structure:

```json
{
  "type": "<channel_type>",
  "tenant_id": 1,
  "data": { ... }
}
```

The `data` object varies by channel type.

---

## Channel Payload Schemas

### instagram_dm

Triggered by: Meta Webhooks → Instagram Direct Message

```json
{
  "type": "instagram_dm",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "instagram_handle": "@sarahchen",
    "message": "Hi! I saw your post about life insurance",
    "profile_pic": "https://..."
  }
}
```

### instagram_ad

Triggered by: Meta Lead Ads webhook

```json
{
  "type": "instagram_ad",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@example.com",
    "phone": "519-555-1234",
    "ad_name": "Life Insurance Lead Ad",
    "form_name": "Life Insurance Form"
  }
}
```

### linkedin_ad

Triggered by: LinkedIn Lead Gen Form webhook

```json
{
  "type": "linkedin_ad",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@mapleridge.com",
    "phone": "519-555-1234",
    "company": "Maple Ridge Construction",
    "title": "CFO",
    "campaign_name": "Financial Planning for Business Owners"
  }
}
```

### whatsapp

Triggered by: Meta Webhooks → WhatsApp Business

```json
{
  "type": "whatsapp",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "phone": "15195551234",
    "message": "I'd like to learn about life insurance options"
  }
}
```

### gmail / outlook

Triggered by: Gmail/Outlook webhook via n8n when a new email arrives from an unknown sender

```json
{
  "type": "gmail",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@mapleridge.com",
    "subject": "Interested in financial planning",
    "body_preview": "Hi, I came across your LinkedIn profile..."
  }
}
```

### calendly

Triggered by: Calendly webhook → event.created

```json
{
  "type": "calendly",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@mapleridge.com",
    "phone": "519-555-1234",
    "event_name": "15-Minute Discovery Call",
    "scheduled_at": "2026-04-15T14:00:00Z",
    "timezone": "America/Toronto"
  }
}
```

### cal_com

Triggered by: Cal.com webhook → BOOKING_CREATED

```json
{
  "type": "cal_com",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@mapleridge.com",
    "phone": "519-555-1234",
    "event_type": "Discovery Call",
    "start_time": "2026-04-15T14:00:00Z"
  }
}
```

### google_calendar / outlook_calendar

Triggered by: Google/Outlook Calendar webhook when a new meeting is created with an external attendee

```json
{
  "type": "google_calendar",
  "tenant_id": 1,
  "data": {
    "name": "Sarah Chen",
    "email": "sarah@mapleridge.com",
    "event_title": "Financial Planning Meeting",
    "start_time": "2026-04-15T14:00:00Z",
    "meeting_link": "https://meet.google.com/abc-123"
  }
}
```

---

## n8n Workflow Setup

### Step 1: Set up credentials

In n8n, add credentials for each platform:
- **Meta**: Facebook Graph API credentials (for Instagram DMs and Lead Ads)
- **LinkedIn**: LinkedIn OAuth2 credentials
- **Google**: Google OAuth2 credentials (Gmail + Calendar)
- **Microsoft**: Microsoft OAuth2 credentials (Outlook + Calendar)
- **Calendly**: Calendly API token
- **Cal.com**: Cal.com API token

### Step 2: Create one workflow per channel

Each workflow follows this pattern:
1. **Trigger node**: Platform-specific webhook trigger
2. **Transform node**: Map platform fields to SteadyBook's normalized schema
3. **HMAC Sign node**: Compute `X-SteadyBook-Signature` header
4. **HTTP Request node**: POST to `/api/social-intake`

### Step 3: HMAC signing in n8n

Add a **Code node** before the HTTP Request:

```javascript
const secret = $env.STEADYBOOK_WEBHOOK_SECRET;
const body = JSON.stringify($input.first().json);
const sig = $crypto.createHmac('sha256', secret).update(body).digest('hex');
return [{ json: { ...($input.first().json), _signature: sig } }];
```

Then in the HTTP Request node, set header:
```
X-SteadyBook-Signature: sha256={{ $json._signature }}
```

### Step 4: Environment variables

Set in SteadyBook's environment:
```
STEADYBOOK_WEBHOOK_SECRET=<random-64-char-hex>
GOOGLE_SEARCH_API_KEY=<google-custom-search-api-key>
GOOGLE_CSE_ID=<custom-search-engine-id>
```

---

## Testing a Payload

Use curl to test your n8n workflow end-to-end:

```bash
SECRET="your-webhook-secret"
BODY='{"type":"instagram_dm","tenant_id":1,"data":{"name":"Test User","message":"Hello"}}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST https://your-steadybook-domain.com/api/social-intake \
  -H "Content-Type: application/json" \
  -H "X-SteadyBook-Signature: sha256=$SIG" \
  -d "$BODY"
```

Expected response: `{"status": "ok"}`
