# Dashboard Redesign — Clean Light CRM

**Date:** 2026-03-22
**Status:** Design approved
**Author:** Marc Pineault + Claude

## Summary

Complete visual redesign of the Calm Money Pipeline Dashboard from a cluttered 7-tab single-page layout to a clean, enterprise-grade sidebar CRM with separate pages. All existing functionality is preserved — this is a presentation overhaul, not a feature change.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Theme | Clean light (white, blue accents) | Professional, easy to scan during busy days |
| Navigation | Persistent left sidebar | Always visible, replaces tab bar |
| Architecture | Jinja2 templates + static CSS/JS | Clean separation without over-engineering the stack |
| Pipeline view | Kanban primary, table toggle | Kanban for visual flow, table when you need to sort/filter |
| Dashboard focus | Action-first | Priority actions ranked by urgency are the largest element |

## Architecture

### Current State (Problems)
- Single 4,059-line `dashboard.py` with all HTML, CSS, and JS inline
- No templates, no static files, no separation of concerns
- Colors hardcoded everywhere, no design system
- Impossible to maintain or iterate on visual design

### Target State
```
dashboard.py                  → Flask routes + data logic only (~800 lines)
templates/
  base.html                   → Shell: sidebar, header, scripts, CSS link
  dashboard.html              → Dashboard page (action items, KPIs, meetings)
  pipeline.html               → Pipeline page (kanban + table toggle)
  tasks.html                  → Tasks page
  conversations.html          → SMS conversations page
  forecast.html               → Revenue forecast page
  clients.html                → Clients page
  chat.html                   → AI chat page
  login.html                  → Login page (standalone, no base.html)
  intake.html                 → Public lead intake form (standalone, no base.html)
  partials/
    sidebar.html              → Sidebar navigation (included in base.html)
    kpi_card.html             → Reusable KPI card component
    prospect_card.html        → Kanban card component
    modal_prospect.html       → Prospect edit/detail modal
    modal_task.html           → Task create/edit modal
    modal_log.html            → Activity log modal
static/
  css/
    style.css                 → All styles (~400 lines)
  js/
    app.js                    → Core navigation, modals, AJAX helpers
    kanban.js                 → Drag-and-drop kanban logic
    charts.js                 → Chart.js initialization
    conversations.js          → SMS thread UI
```

### Key Architectural Rules
- `dashboard.py` contains ONLY route handlers and data fetching — no HTML strings
- All HTML lives in `templates/` using Jinja2
- All CSS lives in `static/css/style.css` — no inline styles
- All JS lives in `static/js/` — no inline scripts
- Templates use Jinja2 `{% block %}` inheritance from `base.html`
- Partials use `{% include %}` for reusable components

## Design System

### Colors
```
--primary:        #2563eb    (blue — buttons, active states, links)
--primary-light:  #f0f4ff    (blue tint — active sidebar item, highlights)
--primary-hover:  #1d4ed8    (darker blue — button hover)
--bg-page:        #f8f9fb    (off-white — page background)
--bg-card:        #ffffff    (white — cards, sidebar, modals)
--bg-subtle:      #f1f5f9    (light grey — secondary buttons, table rows)
--border:         #e5e7eb    (grey — card borders, dividers)
--text-primary:   #1a1a2e    (near-black — headings, body text)
--text-secondary: #64748b    (medium grey — labels, secondary text)
--text-muted:     #94a3b8    (light grey — timestamps, hints)
--success:        #27ae60    (green — won, positive changes)
--warning:        #f59e0b    (amber — warm priority, due today)
--danger:         #dc2626    (red — hot priority, overdue)
--danger-light:   #fef2f2    (red tint — overdue row background)
--warning-light:  #fffbeb    (amber tint — today row background)
--success-light:  #f0fdf4    (green tint — positive recommendation)
```

### Typography
- Font: `'Inter', system-ui, -apple-system, sans-serif`
- Page title: 18px, weight 600
- Section title: 13px, weight 600
- Body: 12px
- Small/labels: 10-11px
- KPI numbers: 24px, weight 700

### Spacing
- Page padding: 24px 28px
- Card padding: 16px
- Card gap: 12px
- Section gap: 20px
- Border radius: 8px (cards), 6px (buttons/inputs), 10px (badges)

### Components
- **KPI Card:** White bg, 1px border, top-aligned label (uppercase 10px muted), large number, optional delta
- **Action Row:** Flex row with urgency dot, name + detail, action buttons. Background color by urgency (red/amber/white)
- **Kanban Card:** White bg, 1px border, 3px left border colored by priority. Name, product, urgency text, priority badge, AUM
- **Badge:** Inline pill, 9px font, colored background (red/amber/grey/green/blue variants)
- **Button Primary:** Blue bg, white text, 6px 14px padding, rounded 6px
- **Button Secondary:** White bg, grey border, grey text
- **Modal:** Fixed overlay, white card, max-width 500px, rounded 8px, subtle shadow
- **Sidebar Item:** 9px 12px padding, rounded 7px. Active: blue bg tint + blue text. Inactive: grey text. Badge floats right.

## Pages

### 1. Dashboard (Home)
The first thing Marc sees. Action-oriented.

**Layout:** Two-column below KPIs (60/40 split)

**Sections (top to bottom):**
1. **Header** — "Good morning, Marc" + date + action count. Buttons: "+ Add Prospect", "+ Log Activity"
2. **KPI Row** — 4 cards in a grid: Active Deals, Pipeline Value (AUM), Premium YTD (with % of target), Win Rate (with delta)
3. **Left Column:**
   - **Priority Actions** — Ranked list of follow-ups/tasks by urgency. Each row has: urgency dot (red/amber/blue/grey), prospect name, detail line (product + stage + due info), action buttons (Call, SMS, Email, View). Overdue rows get red tint background, today gets amber tint.
4. **Right Column:**
   - **Today's Meetings** — Time, prospect name, product. Blue tint background.
   - **Recent Activity** — Last 5 activities with colored dots and timestamps.
5. **AI Recommendations** — 2-column grid of recommendation cards with colored left border (red=critical, green=opportunity). Shows insight text + brief reason.

### 2. Pipeline
Visual deal management.

**Layout:** Full-width kanban board with header bar

**Header Bar:** Page title + deal count + AUM summary. Search input. Kanban/Table toggle. "+ Add" button.

**Kanban View (default):**
- Horizontal scrolling board with columns for active stages: New Lead, Contacted, Discovery Call, Needs Analysis, Plan Presentation, Proposal Sent, Negotiation, Nurture
- Closed-Won and Closed-Lost are NOT shown as kanban columns — Closed-Won deals appear on the Clients page, Closed-Lost are archived
- Column headers show stage name with colored dot + count
- Cards show: name, product, urgency alert (red text if overdue, blue if meeting), priority badge, AUM/premium, last touch time
- Left border colored by priority (red=Hot, amber=Warm, grey=Cold)
- Drag and drop between columns to change stage
- Click card to open detail modal

**Table View (toggle):**
- Sortable columns: Name, Stage, Priority, Product, AUM, Revenue, Next Follow-up, Last Touch
- Priority and stage shown as colored badges
- Click row to open detail modal
- Same search/filter as kanban

### 3. Tasks
Clean task management.

**Layout:** Single column, grouped sections

**Sections:**
1. **Overdue** — Red tint header, red badge count. Task rows with checkbox, title, prospect link, due date, delete.
2. **Due Today** — Amber tint header.
3. **Upcoming** — Default styling. "+ Add Task" button in section header.
4. **Recently Completed** — Collapsed by default, last 10, greyed out with strikethrough.

**Task Row:** Checkbox | Title | Prospect (linked) | Due date | Reminder icon | Delete button

### 4. Conversations
SMS thread management.

**Layout:** Two-column (30/70 split)

**Left Panel:**
- Search bar at top
- List of phone numbers/names with last message preview and timestamp
- Unread indicator (blue dot)

**Right Panel:**
- Header: prospect name + phone
- Message thread (chat bubble style — outbound right/blue, inbound left/grey)
- Composer at bottom: text input + Send button

### 5. Forecast
Revenue projections and velocity.

**Layout:** KPI row + charts + tables

**Sections:**
1. **KPI Row** — 6 cards: Premium YTD, AUM, FYC Won, Projected Premium, Projected AUM, Projected FYC
2. **Revenue Progress** — Progress bars with pace indicators
3. **Monthly Revenue Chart** — Bar chart (Chart.js) with target line
4. **Pipeline Weighted Forecast** — Table with stage, count, avg value, probability, weighted value
5. **Stage Velocity** — Table with avg days in each stage

### 6. Clients
Closed-won book.

**Layout:** Table + breakdown cards

**Sections:**
1. **Client Table** — All closed-won deals. Columns: Name, Product, AUM, Revenue, Date Won, Source
2. **Breakdown Cards** — Pills showing count by product and by source
3. **Cross-sell Suggestions** — AI-generated suggestions for existing clients

### 7. AI Chat
The floating chat bubble, promoted to a full page.

**Layout:** Full-height chat interface

- Message history (scrollable)
- Input at bottom
- Same backend as current chat widget (`/api/chat`)
- Also keep a small floating chat icon on other pages for quick access

## Modals (Shared Across Pages)

### Prospect Detail Modal
- Triggered by clicking any prospect name/card
- Shows: all prospect fields, editable inline
- Activity timeline
- Memory/notes section
- Quick actions: Call, SMS, Email, Reschedule
- Merge section (collapsible)

### Prospect Edit Modal
- Add new or edit existing prospect
- Fields: Name, Phone, Email, Product, Stage, Priority, AUM, Revenue, Notes
- Source dropdown

### Task Modal
- Add new or edit task
- Fields: Title, Prospect (autocomplete), Due Date, Reminder, Notes

### Activity Log Modal
- Quick log: Action type (Call/Email/Meeting), Prospect, Outcome, Next Step
- Pre-fillable from action buttons

## API Endpoints (Unchanged)

All existing endpoints remain the same. No backend API changes needed.

```
POST   /api/prospect              — Add prospect
PUT    /api/prospect/<name>       — Update prospect
DELETE /api/prospect/<name>       — Delete prospect
POST   /api/prospect/merge        — Merge prospects
PUT    /api/prospect/update       — Update stage/priority/followup
GET    /api/prospect/<name>/detail — Prospect detail
POST   /api/task                  — Add task
PUT    /api/task/<id>             — Update task
DELETE /api/task/<id>             — Delete task
PUT    /api/task/<id>/complete    — Complete task
POST   /api/activity              — Log activity
GET    /api/conversations          — List SMS conversations
GET    /api/conversations/<phone>  — Get thread
POST   /api/conversations/<phone>/send — Send SMS
POST   /api/chat                  — AI chat message
GET    /health                    — Health check
GET    /login                     — Login page
POST   /login                     — Authenticate
GET    /logout                    — Clear session
GET/POST /intake/event            — Public lead intake form (standalone page, own template)
```

**Note:** `/intake/event` is a public-facing form with its own CSRF protection. It gets its own template (`templates/intake.html`) that does NOT extend `base.html` (no sidebar, no auth required). `/login` also gets its own standalone template.

## Migration Strategy

1. **Create template structure** — `templates/` and `static/` directories
2. **Extract CSS** — Pull all styles from inline `<style>` block into `static/css/style.css`, redesign as we go
3. **Extract JS** — Pull all `<script>` content into `static/js/` modules
4. **Build base template** — `base.html` with sidebar, head, scripts
5. **Convert pages one by one** — Start with login, then dashboard, then pipeline, etc.
6. **Refactor dashboard.py** — Strip HTML rendering, keep only route handlers + data logic
7. **Test each page** — Verify all AJAX calls, modals, drag-drop still work
8. **Remove old inline code** — Once all pages are converted

## Mobile Responsiveness

- **< 768px:** Sidebar collapses to hamburger menu. Kanban columns stack or scroll horizontally. KPIs go 2-per-row.
- **< 480px:** KPIs go 1-per-row. Full-width modals. Simplified action rows.
- Conversations: left panel becomes a slide-out drawer on mobile.

## What's NOT Changing

- All bot functionality (Telegram commands, SMS agent, scheduler, etc.)
- Database schema
- API endpoints and their response formats
- Authentication flow (API key / cookie)
- CSRF protection
- Security headers
- All backend Python logic
