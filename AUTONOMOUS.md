# Autonomous Work — Standing Instructions

## Every Run
- Pull latest, run full test suite (`python3 -m pytest tests/ --tb=short`), fix any failures
- Check for Python dependency security vulnerabilities (`pip audit` if available, otherwise check requirements.txt against known CVEs)
- Review recently changed files (last 5 commits) for code quality improvements — simplify logic, improve naming, extract functions if a file is growing too large
- Keep bot.py under 3000 lines — if it's over, extract the largest logical block into its own module
- If any test files are missing for modules that have none, write basic test coverage
- In `/Users/map98/Desktop/Pineault-wealth/` (the calmmoney.ca website):
  - Check all pages have proper meta titles and descriptions
  - Verify structured data (JSON-LD LocalBusiness schema) is present and correct
  - Check for broken internal links
  - Verify sitemap.xml exists and is current
  - Check image alt tags are present and descriptive
  - Fix any issues found, commit and push to Pineault-wealth repo

## Weekly (Sunday only)
- SEO research for calmmoney.ca:
  - Research 5-10 long-tail keywords for "financial advisor London Ontario" and related terms
  - Analyze top 3 competitor financial advisor websites in London ON for content gaps
  - Save findings to `content/seo/YYYY-MM-DD-research.md`
  - Update `content/seo/keywords.md` with new keyword opportunities
- Generate 1 blog post draft targeting the highest-opportunity keyword — save to `content/seo/drafts/`
  - 800-1500 words, plain language, Marc's warm voice
  - Include proper meta title, meta description
  - Internal links to calmmoney.ca service pages
  - CTA pointing to quiz (calmmoney.ca/quiz) or booking widget
- Audit GPT prompts across all modules (briefing.py, follow_up.py, nurture.py, compliance.py) for cost/quality optimization
- Review scheduler timing — check if notification volume is appropriate, flag any potential improvements
- Check if market_calendar events need updating for the current year
- Run `analytics.generate_self_tuning_report()` and review the output — make prompt adjustments if data supports it

## As Needed
