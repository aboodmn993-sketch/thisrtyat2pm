"""
بوت تليجرام لتتبع أفضل العروض على منتج معين من عدة متاجر (أمازون السعودية، نون، إكسترا).

الفكرة:
- تبعث اسم منتج للبوت في تليجرام -> يضيفه لقائمة التتبع.
- كل مرة يشتغل فيها هذا السكربت (عبر GitHub Actions كل فترة)، يبحث عن المنتج
  في المتاجر المدعومة، ويقارن أرخص سعر لقاه بأرخص سعر سبق ولقاه.
- إذا لقى سعر أقل من قبل (أو أول مرة يلقى نتيجة) يبعثلك تنبيه في تليجرام.

ملاحظة مهمة:
هذا السكربت يعتمد على "web scraping" (قراءة صفحة نتائج البحث في كل موقع)
مو API رسمي. المواقع تغيّر تصميم صفحاتها بين فترة وأخرى، فإذا توقف متجر معين
عن إعطاء نتائج، افتح نتائج البحث في المتصفح، اعمل Inspect على عنصر السعر/العنوان،
وحدّث الـ selectors (CSS) في الدوال أدناه (search_amazon_sa / search_noon / search_extra).
"""

import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
}

REQUEST_TIMEOUT = 20


# ---------------------------------------------------------------------------
# تخزين الحالة (المنتجات المتابَعة + آخر تحديث تليجرام تمت معالجته)
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_update_id": 0, "products": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# تليجرام
# ---------------------------------------------------------------------------

def send_message(text):
    try:
        requests.post(
            f"{API_URL}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        print("send_message error:", e)


def get_updates(offset):
    try:
        r = requests.get(
            f"{API_URL}/getUpdates",
            params={"offset": offset, "timeout": 0},
            timeout=REQUEST_TIMEOUT,
        )
        return r.json().get("result", [])
    except Exception as e:
        print("get_updates error:", e)
        return []


# ---------------------------------------------------------------------------
# أدوات مساعدة
# ---------------------------------------------------------------------------

def parse_price(text):
    """يحاول يستخرج رقم السعر من نص فيه رموز عملة أو فواصل."""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("\u066c", "")
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def safe_get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            print(f"GET {url} -> status {r.status_code}")
            return None
        return r.text
    except Exception as e:
        print("safe_get error:", url, e)
        return None


# ---------------------------------------------------------------------------
# دوال البحث لكل متجر - عدّل الـ selectors هنا لو توقف متجر عن إعطاء نتائج
# ---------------------------------------------------------------------------

def search_amazon_sa(query):
    results = []
    url = f"https://www.amazon.sa/s?k={requests.utils.quote(query)}"
    html = safe_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("div[data-component-type='s-search-result']")
    for item in items[:5]:
        title_el = item.select_one("h2 a span") or item.select_one("h2 span")
        price_el = item.select_one("span.a-price > span.a-offscreen")
        link_el = item.select_one("h2 a")
        if title_el and price_el and link_el:
            price = parse_price(price_el.get_text())
            href = link_el.get("href", "")
            if price:
                results.append({
                    "store": "Amazon.sa",
                    "title": title_el.get_text(strip=True),
                    "price": price,
                    "url": href if href.startswith("http") else "https://www.amazon.sa" + href,
                })
    return results


def search_noon(query):
    results = []
    url = f"https://www.noon.com/saudi-en/search/?q={requests.utils.quote(query)}"
    html = safe_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    # نون يبني جزء كبير من الصفحة عبر JavaScript، فقد لا تظهر نتائج أحياناً
    # مع requests العادي. إذا صار كذا باستمرار، فكّر تستخدم Playwright بدلها.
    items = soup.select("div[data-qa='product-block']")
    for item in items[:5]:
        title_el = item.select_one("[data-qa='product-name']")
        price_el = item.select_one("strong")
        link_el = item.select_one("a")
        if title_el and price_el and link_el:
            price = parse_price(price_el.get_text())
            href = link_el.get("href", "")
            if price:
                results.append({
                    "store": "Noon",
                    "title": title_el.get_text(strip=True),
                    "price": price,
                    "url": href if href.startswith("http") else "https://www.noon.com" + href,
                })
    return results


def search_extra(query):
    results = []
    url = f"https://www.extra.com/en-sa/search/?q={requests.utils.quote(query)}"
    html = safe_get(url)
    if not html:
        return results
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("div.product")
    for item in items[:5]:
        title_el = item.select_one(".product-name") or item.select_one("a.product-title")
        price_el = item.select_one(".price") or item.select_one(".product-price")
        link_el = item.select_one("a")
        if title_el and price_el and link_el:
            price = parse_price(price_el.get_text())
            href = link_el.get("href", "")
            if price:
                results.append({
                    "store": "Extra",
                    "title": title_el.get_text(strip=True),
                    "price": price,
                    "url": href if href.startswith("http") else "https://www.extra.com" + href,
                })
    return results


SEARCH_FUNCS = [search_amazon_sa, search_noon, search_extra]


# ---------------------------------------------------------------------------
# منطق المقارنة والتنبيه
# ---------------------------------------------------------------------------

def check_product(product):
    all_results = []
    for func in SEARCH_FUNCS:
        try:
            all_results.extend(func(product["query"]))
        except Exception as e:
            print(f"{func.__name__} failed:", e)
        time.sleep(1)  # علشان ما نضغط على المواقع بسرعة كبيرة
    if not all_results:
        return None
    best = min(all_results, key=lambda x: x["price"])
    return best


def check_all_products(state):
    for product in state["products"]:
        best = check_product(product)
        if not best:
            continue
        prev_best = product.get("best_price")
        if prev_best is None or best["price"] < prev_best:
            product["best_price"] = best["price"]
            product["best_store"] = best["store"]
            product["best_url"] = best["url"]
            msg = (
                f"🎯 لقيت سعر أحسن لـ «{product['query']}»!\n\n"
                f"🏪 المتجر: {best['store']}\n"
                f"💰 السعر: {best['price']} ريال\n"
                f"📦 {best['title']}\n"
                f"🔗 {best['url']}"
            )
            send_message(msg)
    return state


# ---------------------------------------------------------------------------
# معالجة أوامر تليجرام (إضافة/حذف/عرض المنتجات المتابَعة)
# ---------------------------------------------------------------------------

def handle_commands(state):
    updates = get_updates(state["last_update_id"] + 1)
    for update in updates:
        state["last_update_id"] = update["update_id"]
        message = update.get("message")
        if not message:
            continue
        text = (message.get("text") or "").strip()
        if not text:
            continue

        if text in ("/list", "قائمة", "القائمة"):
            if not state["products"]:
                send_message("ما فيه منتجات متابَعة حالياً. ابعث اسم أي منتج عشان أبدأ أتابعه.")
            else:
                lines = ["📋 المنتجات المتابَعة:"]
                for i, p in enumerate(state["products"], 1):
                    best = p.get("best_price")
                    if best:
                        lines.append(f"{i}. {p['query']} — أفضل سعر: {best} ريال ({p.get('best_store')})")
                    else:
                        lines.append(f"{i}. {p['query']} — لسه ما لقيت له سعر")
                send_message("\n".join(lines))

        elif text.startswith("/remove") or text.startswith("حذف"):
            parts = text.split()
            if len(parts) >= 2 and parts[1].isdigit():
                idx = int(parts[1]) - 1
                if 0 <= idx < len(state["products"]):
                    removed = state["products"].pop(idx)
                    send_message(f"✅ تم حذف: {removed['query']}")
                else:
                    send_message("رقم غير صحيح، اكتب: قائمة عشان تشوف الأرقام.")
            else:
                send_message("اكتب: حذف رقم_المنتج (شوف الأرقام بأمر: قائمة)")

        elif text.startswith("/start"):
            send_message(
                "أهلاً 👋\nابعثلي اسم أي منتج وبتابعلك أفضل سعر له من أمازون ونون وإكسترا.\n"
                "أوامر تقدر تستخدمها:\nقائمة — تعرض المنتجات المتابَعة\nحذف [رقم] — يحذف منتج من المتابعة"
            )

        else:
            state["products"].append({
                "query": text,
                "best_price": None,
                "best_store": None,
                "best_url": None,
            })
            send_message(
                f"✅ بديت أتابع: {text}\n"
                f"بوصلك تنبيه أول ما ألقى سعر له، أو ألقى سعر أقل من اللي قبله."
            )
    return state


def main():
    state = load_state()
    state = handle_commands(state)
    state = check_all_products(state)
    save_state(state)


if __name__ == "__main__":
    main()
