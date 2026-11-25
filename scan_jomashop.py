import json
import os
import re
import urllib.request
from playwright.sync_api import sync_playwright

# --- CONFIGURATION ---
NTFY_TOPIC = os.getenv("NTFY_TOPIC") or "jomashop-rolex-84291"
MAX_PRICE_USD = 2000.0      # Alert on any Men's Rolex under $2,000
MIN_PRICE_USD = 250.0       # Ignore broken $0 listings

URLS_TO_SCAN = [
    "https://www.jomashop.com/rolex-watches.html?gender=Mens&condition=Pre-owned&sort=price_asc",
    "https://www.jomashop.com/rolex-watches.html?gender=Mens&condition=New&sort=price_asc",
]

ALERTED_FILE = "alerted_watches.json"


def load_alerted():
    if os.path.exists(ALERTED_FILE):
        with open(ALERTED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_alerted(alerted_set):
    with open(ALERTED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(alerted_set)), f, indent=2)


def send_ntfy(title, message, click_url):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "urgent",
            "Tags": "rotating_light,watch",
            "Click": click_url,
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=10)


def extract_actual_price(text):
    """Strips coupon/savings/monthly amounts and returns the watch's actual sale price."""
    cleaned = re.sub(r"(?:save|off|up to)\s*\$\s?\d[\d,]*(?:\.\d{2})?", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\$\s?\d[\d,]*(?:\.\d{2})?\s*(?:off|/mo|per month)", " ", cleaned, flags=re.IGNORECASE)

    matches = re.findall(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)", cleaned)
    prices = [float(m.replace(",", "")) for m in matches if float(m.replace(",", "")) >= MIN_PRICE_USD]

    if not prices:
        return None
    return prices[-1]


def scan():
    alerted = load_alerted()
    new_alerts = 0
    lowest_seen = (999999.0, "")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1366, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()

        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2}", lambda route: route.abort())

        for idx, url in enumerate(URLS_TO_SCAN):
            page.wait_for_timeout(2000 if idx > 0 else 1500)
            print(f"Scanning: {url}", flush=True)
            try:
                resp = page.goto(url, wait_until="commit", timeout=20000)
                page.wait_for_timeout(5000)
                status = resp.status if resp else "No response"
                print(f"  HTTP {status} | Title: '{page.title()}'", flush=True)

                cards = page.evaluate("""() => {
                    const results = [];
                    const links = document.querySelectorAll('a[href*="rolex-"][href$=".html"]');
                    const seen = new Set();
                    for (const a of links) {
                        const href = a.href;
                        if (seen.has(href)) continue;
                        let card = a.parentElement;
                        for (let i = 0; i < 6 && card; i++) {
                            if (card.innerText && card.innerText.includes('$') && card.innerText.length < 1200) {
                                break;
                            }
                            card = card.parentElement;
                        }
                        if (!card || !card.innerText) continue;
                        const text = card.innerText;
                        if (text.includes('$')) {
                            seen.add(href);
                            results.push({ href, text });
                        }
                    }
                    return results;
                }""")

                print(f"  Found {len(cards)} Rolex watch cards.", flush=True)

                for item in cards:
                    href = item["href"]
                    text = item["text"]
                    upper_text = text.upper()

                    if "OUT OF STOCK" in upper_text or "SOLD OUT" in upper_text:
                        continue
                    if any(w in upper_text for w in ["LADIES", "LADY-DATEJUST", "WOMEN'S", "WOMENS"]):
                        continue

                    current_price = extract_actual_price(text)
                    if current_price is None:
                        continue

                    slug_match = re.search(r"/([^/]+)\.html", href)
                    title_line = (
                        slug_match.group(1).replace("-", " ").title()
                        if slug_match
                        else text.strip().splitlines()[0][:75]
                    )

                    if current_price < lowest_seen[0]:
                        lowest_seen = (current_price, title_line)

                    if current_price <= MAX_PRICE_USD:
                        watch_id = slug_match.group(1) if slug_match else href
                        print(f"  [ROLEX DEAL] {title_line}: ${current_price:,.2f}", flush=True)
                        if watch_id not in alerted:
                            send_ntfy(
                                title=f"ROLEX UNDER $2K: ${current_price:,.2f}",
                                message=f"{title_line}\nPrice: ${current_price:,.2f} USD\n{href}",
                                click_url=href,
                            )
                            alerted.add(watch_id)
                            new_alerts += 1

            except Exception as e:
                print(f"  Error scanning {url}: {e}", flush=True)

        browser.close()

    if new_alerts > 0:
        save_alerted(alerted)
    if lowest_seen[0] < 999999.0:
        print(f"Lowest Men's Rolex seen this run: ${lowest_seen[0]:,.2f} ({lowest_seen[1]})", flush=True)
    print(f"Done. New Rolex alerts sent: {new_alerts}", flush=True)


if __name__ == "__main__":
    scan()
