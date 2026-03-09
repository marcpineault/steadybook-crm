"""
Scrape Co-operators term life insurance rates from term4sale.ca COMPULIFE API.
Saves results to cooperators_rates.json.
"""
import json
import time
import requests

API_URL = "https://www.term4sale.ca/apit4sc/compulifeapi/api.php/"

AGES = list(range(20, 61))  # 20 to 60
GENDERS = ["M", "F"]
SMOKER = ["N", "Y"]
TERMS = [("3", "10"), ("4", "15"), ("5", "20"), ("6", "25"), ("7", "30")]
AMOUNTS = [100000, 250000, 500000, 750000, 1000000]
HEALTH = "R"  # Regular

# Resume from existing file if available
try:
    with open("cooperators_rates.json", "r") as f:
        rates = json.load(f)
    print(f"Resuming with {len(rates)} existing rates")
except (FileNotFoundError, json.JSONDecodeError):
    rates = {}

total = len(AGES) * len(GENDERS) * len(SMOKER) * len(TERMS) * len(AMOUNTS)
count = 0
errors = 0
skipped = 0

print(f"Scraping {total} combinations...")

for age in AGES:
    birth_year = 2026 - age
    for sex in GENDERS:
        for smoke in SMOKER:
            for cat_code, term_years in TERMS:
                for face in AMOUNTS:
                    count += 1
                    key = f"{age}_{sex}_{smoke}_{term_years}_{face}"

                    # Skip if already scraped
                    if key in rates:
                        skipped += 1
                        continue

                    params = {
                        "requestType": "request",
                        "ModeUsed": "M",
                        "SortOverride1": "A",
                        "ErrOnMissingZipCode": "ON",
                        "State": "0",
                        "ZipCode": "N6A1A1",
                        "BirthMonth": "6",
                        "BirthDay": "15",
                        "BirthYear": str(birth_year),
                        "Sex": sex,
                        "Smoker": smoke,
                        "Health": HEALTH,
                        "NewCategory": cat_code,
                        "FaceAmount": str(face),
                        "CompRating": "4",
                    }

                    headers = {
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                        "Referer": "https://www.term4sale.ca/",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                    }

                    try:
                        resp = requests.get(API_URL, params=params, headers=headers, timeout=5)
                        data = resp.json()

                        # Find Co-operators in results
                        results = data.get("Compulife_ComparisonResults", {}).get("Compulife_Results", [])
                        for r in results:
                            if "Co-operators" in r.get("Compulife_company", ""):
                                rates[key] = {
                                    "age": age,
                                    "gender": sex,
                                    "smoker": smoke,
                                    "term": term_years,
                                    "amount": face,
                                    "annual": r["Compulife_premiumAnnual"].strip(),
                                    "monthly": r["Compulife_premiumM"].strip(),
                                    "product": r["Compulife_product"].strip(),
                                }
                                break

                        if key in rates:
                            if count % 100 == 0:
                                print(f"[{count}/{total}] {key}: ${rates[key]['annual']}/yr")
                        else:
                            if count % 100 == 0:
                                print(f"[{count}/{total}] {key}: Co-operators not available")

                    except Exception as e:
                        errors += 1
                        if count % 100 == 0:
                            print(f"[{count}/{total}] {key}: ERROR - {e}")
                        time.sleep(1)

                    time.sleep(0.3)

    # Save progress after each age
    with open("cooperators_rates.json", "w") as f:
        json.dump(rates, f, indent=2)
    print(f"Age {age} done — {len(rates)} rates saved so far")

# Final save
with open("cooperators_rates.json", "w") as f:
    json.dump(rates, f, indent=2)

print(f"\nDone! Scraped {len(rates)} Co-operators rates out of {total} lookups.")
print(f"Errors: {errors}")
