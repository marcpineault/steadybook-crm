# Autonomous Work — Standing Instructions

Marc has a Claude Max subscription. Use the budget generously — the goal is to make Marc money and save him time. This computer runs 24/7 as Marc's digital employee.

## Every Run (Midnight Daily)

### System Health
- Pull latest, run full test suite (`python3 -m pytest tests/ --tb=short`), fix any failures
- Check for Python dependency security vulnerabilities
- Review recently changed files for code quality improvements
- Keep bot.py under 3000 lines — extract if growing
- Write missing test coverage for any untested modules

### Website SEO (calmmoney.ca at /Users/map98/Desktop/Pineault-wealth/)
- Check all pages have proper meta titles, descriptions, and OG tags
- Verify JSON-LD LocalBusiness structured data is correct and complete
- Check for broken internal links
- Verify sitemap.xml is current
- Check image alt tags are present and descriptive
- Fix any issues found, commit and push

### Bot Intelligence
- Run `analytics.generate_self_tuning_report()` — review output
- If approval rate < 60%, analyze dismissed drafts and adjust follow-up prompts
- If any GPT prompt is producing low-quality output (based on outcome data), improve it
- Check if market_calendar events need updating

## Weekly (Sunday only)

### SEO Deep Dive
- Research 5-10 long-tail keywords related to:
  - "financial advisor London Ontario"
  - "retirement planning Ontario"
  - "insurance advisor London ON"
  - "RRSP TFSA advisor"
  - "disability insurance business owner Ontario"
  - "estate planning London Ontario"
- Analyze top 5 competitor financial advisor websites in London ON for content gaps
- Save research to `content/seo/YYYY-MM-DD-research.md`
- Update `content/seo/keywords.md` with findings

### Content Generation
- Generate 1 blog post draft (800-1500 words) targeting highest-opportunity keyword
  - Write in Marc's voice — warm, plain language, locally rooted
  - Include meta title, meta description
  - Internal links to calmmoney.ca service pages
  - CTA to quiz or booking widget
  - Save to `content/seo/drafts/`
- Generate 3 short-form video script ideas based on trending financial topics
  - Save to `content/scripts/YYYY-MM-DD-scripts.md`
  - Use the content playbook at `content/PLAYBOOK.md` for format and voice

### System Optimization
- Audit all GPT prompts for cost/quality — are we using gpt-4.1 where gpt-4.1-mini would suffice?
- Review scheduler timing — is notification volume appropriate?
- Analyze the self-tuning report and make concrete improvements
- Review and optimize database queries that run frequently

## As Needed
