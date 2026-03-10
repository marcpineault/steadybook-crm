"""
Scrape Co-operators term life insurance rates from term4sale.ca COMPULIFE API.
Saves results to cooperators_rates.json.
Can be run standalone or called from the bot.
"""
import json
import os
import random
import time
import requests

API_URL = "https://www.term4sale.ca/apit4sc/compulifeapi/api.php/"

AGES = list(range(20, 61))  # 20 to 60
GENDERS = ["M", "F"]
SMOKER = ["N", "Y"]
TERMS = [("3", "10"), ("4", "15"), ("5", "20"), ("6", "25"), ("7", "30")]
AMOUNTS = [100000, 250000, 500000, 750000, 1000000]
HEALTH = "R"  # Regular

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def get_rates_path():
    """Get path to rates file, respecting DATA_DIR for Railway."""
    data_dir = os.environ.get("DATA_DIR", "")
    if data_dir:
        return os.path.join(data_dir, "cooperators_rates.json")
    return "cooperators_rates.json"


def scrape_rates(callback=None):
    """Scrape all Co-operators rates. callback(msg) is called with progress updates."""
    rates_path = get_rates_path()

    # Resume from existing file if available
    try:
        with open(rates_path, "r") as f:
            rates = json.load(f)
        if callback:
            callback(f"Resuming with {len(rates)} existing rates")
    except (FileNotFoundError, json.JSONDecodeError):
        rates = {}

    total = len(AGES) * len(GENDERS) * len(SMOKER) * len(TERMS) * len(AMOUNTS)
    count = 0
    errors = 0
    skipped = 0
    consecutive_errors = 0
    new_this_run = 0

    if callback:
        callback(f"Scraping {total} combinations ({len(rates)} already done)...")

    for age in AGES:
        birth_year = 2026 - age
        for sex in GENDERS:
            for smoke in SMOKER:
                for cat_code, term_years in TERMS:
                    for face in AMOUNTS:
                        count += 1
                        key = f"{age}_{sex}_{smoke}_{term_years}_{face}"

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
                            "User-Agent": random.choice(USER_AGENTS),
                            "Referer": "https://www.term4sale.ca/",
                            "Accept": "application/json, text/javascript, */*; q=0.01",
                            "X-Requested-With": "XMLHttpRequest",
                        }

                        try:
                            resp = requests.get(API_URL, params=params, headers=headers, timeout=10)

                            # Check for block
                            if "scraping" in resp.text.lower() and len(resp.text) < 50:
                                if callback:
                                    callback(f"Blocked by term4sale.ca at {len(rates)} rates. Stopping.")
                                # Save what we have
                                with open(rates_path, "w") as f:
                                    json.dump(rates, f, indent=2)
                                return {"total": len(rates), "new": new_this_run, "blocked": True}

                            data = resp.json()

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
                                    new_this_run += 1
                                    break

                        except Exception as e:
                            errors += 1
                            consecutive_errors += 1
                            if consecutive_errors >= 3:
                                backoff = min(60, 5 * consecutive_errors)
                                time.sleep(backoff)
                            else:
                                time.sleep(2)
                            continue

                        consecutive_errors = 0
                        time.sleep(random.uniform(0.8, 2.0))

        # Save progress after each age
        with open(rates_path, "w") as f:
            json.dump(rates, f, indent=2)
        time.sleep(random.uniform(3, 6))

        if callback and count % 500 < 200:
            callback(f"Age {age} done — {len(rates)} rates ({new_this_run} new this run)")

    # Final save
    with open(rates_path, "w") as f:
        json.dump(rates, f, indent=2)

    if callback:
        callback(f"Done! {len(rates)} total rates, {new_this_run} new this run, {errors} errors.")

    return {"total": len(rates), "new": new_this_run, "blocked": False}


if __name__ == "__main__":
    scrape_rates(callback=print)
