"""Browser QA: 390px phone + desktop, sign-in, Today / History / day / meal / edit sheets, overflow, console errors.
Run against a throwaway server with demo data (never your real data folder):
  export FOOD_DATA_DIR=$(mktemp -d) FOOD_PASSWORD=qa FOOD_PORT=8900
  python3 scripts/seed_demo.py && python3 server.py &
  QA_URL=http://127.0.0.1:8900 QA_PASSWORD=qa python3 tests/qa_browser.py     # needs: pip install playwright
"""
import os, sys
from playwright.sync_api import sync_playwright

URL = os.environ.get("QA_URL", "http://127.0.0.1:8900")
PASSWORD = os.environ.get("QA_PASSWORD", "qa")
CHROME = os.environ.get("QA_CHROME")   # optional path to a system Chrome
OUT = os.environ.get("QA_OUT", "qa-shots")
os.makedirs(OUT, exist_ok=True)
fails = []


def overflow(pg, where):
    ow = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    if ow > 1:
        fails.append(f"{where}: horizontal overflow {ow}px")


with sync_playwright() as p:
    b = p.chromium.launch(**({"executable_path": CHROME} if CHROME else {}), args=["--no-sandbox"])
    for name, vp, mobile in (("phone", {"width": 390, "height": 844}, True), ("desktop", {"width": 1280, "height": 900}, False)):
        ctx = b.new_context(viewport=vp, is_mobile=mobile, has_touch=mobile, device_scale_factor=2 if mobile else 1,
                            timezone_id=os.environ.get("FOOD_TZ") or "UTC")
        pg = ctx.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL + "/")
        pg.wait_for_selector("#login")
        overflow(pg, f"{name}/login")
        pg.screenshot(path=f"{OUT}/{name}-login.png")
        pg.fill("#pw", "wrong"); pg.click("#login button"); pg.wait_for_selector("#err:not([hidden])")
        pg.fill("#pw", PASSWORD); pg.click("#login button")
        pg.wait_for_selector(".hero")
        errs.clear()   # the deliberate wrong password logs a 401
        overflow(pg, f"{name}/today")
        pg.screenshot(path=f"{OUT}/{name}-today.png", full_page=True)
        if pg.locator(".meal").count() < 1:
            fails.append(f"{name}: expected at least one logged meal today (run scripts/seed_demo.py first)")
        pg.goto(URL + "/#history"); pg.wait_for_selector(".days")
        overflow(pg, f"{name}/history")
        pg.screenshot(path=f"{OUT}/{name}-history.png", full_page=True)
        pg.goto(URL + "/#today"); pg.wait_for_selector(".meal")
        pg.click("#day-more"); pg.wait_for_selector("#day[open] .nut"); pg.wait_for_timeout(450)
        overflow(pg, f"{name}/day")
        pg.screenshot(path=f"{OUT}/{name}-day.png")
        pg.locator("#day .bym tr[data-meal]").first.click()
        pg.wait_for_selector("#meal[open] .flist, #meal[open] .foodl"); pg.wait_for_timeout(450)
        overflow(pg, f"{name}/meal")
        pg.screenshot(path=f"{OUT}/{name}-meal.png", full_page=True)
        pg.click("#meal-edit")
        pg.wait_for_selector("#edit[open], #add[open]")   # per-food meals edit in the review sheet
        overflow(pg, f"{name}/edit")
        pg.screenshot(path=f"{OUT}/{name}-edit.png")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        if pg.locator("#edit[open], #add[open]").count():
            fails.append(f"{name}: Esc did not close edit")
        if errs:
            fails.append(f"{name}: console errors {errs[:3]}")
        ctx.close()
    ctx = b.new_context()
    r = ctx.new_page().goto(URL + "/api/meals")
    if r.status != 401:
        fails.append(f"unauth /api/meals -> {r.status}")
    b.close()

print("screenshots:", OUT)
print("FAILS:" if fails else "ALL OK", *fails, sep="\n")
sys.exit(1 if fails else 0)
