# Autonomous Work — Standing Instructions

## Every Run
- Pull latest, run full test suite (`python3 -m pytest tests/ --tb=short`), fix any failures
- Check for Python dependency security vulnerabilities (`pip audit` if available, otherwise check requirements.txt against known CVEs)
- Review recently changed files (last 5 commits) for code quality improvements — simplify logic, improve naming, extract functions if a file is growing too large
- Keep bot.py under 3000 lines — if it's over, extract the largest logical block into its own module
- If any test files are missing for modules that have none, write basic test coverage

## Weekly (Sunday only)
- Research SEO opportunities for calmmoney.ca — keywords, content gaps, competitor analysis — save findings to content/seo-research.md
- Audit GPT prompts across all modules (briefing.py, follow_up.py, nurture.py, compliance.py) for cost/quality optimization
- Review scheduler timing — check if notification volume is appropriate, flag any potential improvements
- Check if market_calendar events need updating for the current year

## As Needed
