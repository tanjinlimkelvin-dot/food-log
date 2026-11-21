"""Fill a data folder with a week of made-up meals (for trying the app or taking screenshots).
  FOOD_DATA_DIR=/tmp/food-demo FOOD_PASSWORD=demo python3 scripts/seed_demo.py
Never point this at your real data folder."""
import datetime as dt, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S  # noqa: E402

F = lambda name, portion, g, kcal, p, c, f, sat, chol, fib, sol, sug, na: dict(
    name=name, portion=portion, grams=g, kcal=kcal, protein_g=p, carbs_g=c, fat_g=f, sat_fat_g=sat,
    cholesterol_mg=chol, fiber_g=fib, soluble_fiber_g=sol, sugar_g=sug, sodium_mg=na, confidence="medium")
DAY = [
    ("breakfast", [F("Oats with banana", "1 bowl", 300, 380, 12, 68, 7, 1.3, 0, 8, 3.2, 18, 90),
                   F("Boiled egg", "1 egg", 50, 72, 6.3, 0.4, 4.8, 1.6, 186, 0, 0, 0.2, 62)]),
    ("lunch", [F("Nasi lemak", "1 plate", 320, 620, 15, 78, 27, 13, 160, 4, 1, 6, 820),
               F("Fried chicken", "1 drumstick", 120, 300, 24, 9, 19, 5, 110, 0.5, 0, 0, 480)]),
    ("snack", [F("Greek yoghurt", "1 cup", 170, 150, 17, 7, 4, 2.5, 15, 0, 0, 6, 60)]),
    ("dinner", [F("Grilled salmon", "150 g", 150, 310, 33, 0, 19, 4, 90, 0, 0, 0, 90),
                F("Brown rice", "1 cup", 195, 220, 5, 46, 1.8, 0.4, 0, 3.5, 0.6, 0.7, 10),
                F("Stir-fried broccoli", "1 cup", 150, 90, 4, 10, 4.5, 0.7, 0, 4, 1.5, 2.5, 280)]),
]

S.init_db()
today = dt.datetime.now(S.TZ).date()
n = 0
for back in range(7):
    d = (today - dt.timedelta(days=back)).isoformat()
    for i, (meal, items) in enumerate(DAY):
        if back == 0 and meal == "dinner":
            continue   # "today" is still in progress
        if back % 3 == 1 and meal == "snack":
            continue
        try:
            S.add_meal({"date": d, "meal": meal, "foods": "; ".join(f"{x['name']} ({x['portion']})" for x in items),
                        "items": items, "note": "demo data"})
            n += 1
        except S.Bad:
            pass   # already seeded
S.save_food(F("Whey protein", "1 scoop (30 g)", 30, 120, 24, 3, 1.5, 0.8, 30, 0, 0, 1.5, 60))
print(f"seeded {n} meals into {S.DB}")
