#!/usr/bin/env python3
"""
CLI-прототип кредитного конвейера — кейс 2.

Использование:
    python 04_прототип.py applications.csv
    python 04_прототип.py applications.csv --out decisions.csv

TODO:
    1. Дописать validate() — правила из 02_правила.md.
    2. Дописать bki_emulator() и smev_emulator().
    3. Дописать score() — веса из 02_правила.md.
    4. Дописать decide() — 8 правил отказа.
    5. Считать DTI по формуле.
    6. Записать decisions.csv.
"""
import csv
import hashlib
import sys

OUT_FIELDS = ["app_id", "decision", "reason", "score", "dti"]


def bki_emulator(passport_hash):
    """TODO: 0..5 — score 600+, 6..8 — score 450+, 9 — delinquency."""
    h = int(hashlib.md5(passport_hash.encode()).hexdigest(), 16) % 10
    if h <= 5:
        return (600 + h * 20, False)
    if h <= 8:
        return (450 + (h-6) * 20, False)
    return (450, True)


def smev_emulator(client_id):
    """TODO: MATCH для 9 из 10, MISMATCH для 10-го."""
    h = int(hashlib.md5(client_id.encode()).hexdigest(), 16) % 10
    return "MISMATCH" if h == 0 else "MATCH"


def validate(app):
    """TODO: проверить age, income, employment, family, amount, term."""
    errors = []
    # ОБРАЗЕЦ — age
    if not app.get("age", "").isdigit():
        errors.append(("age", "MISSING_AGE"))
    else:
        age = int(app["age"])
        if age < 18 or age > 70:
            errors.append(("age", "AGE_OUT_OF_RANGE"))

    employment = app.get("employment", "").strip()
    if employment == "unemployed":
        errors.append(("employment", "INVALID_EMPLOYMENT"))
    elif employment not in ("employed", "self-employed", "retired", "student"):
        errors.append(("employment", "MISSING_EMPLOYMENT"))

    if not app.get("income", "").isdigit():
        errors.append(("income", "MISSING_INCOME"))
    elif int(app["income"]) < 30000:
        errors.append(("income", "LOW_INCOME"))

    if app.get("family", "").strip() not in ("married", "children", "single"):
        errors.append(("family", "MISSING_FAMILY"))

    if not app.get("amount", "").isdigit():
        errors.append(("amount", "MISSING_AMOUNT"))

    if not app.get("term", "").isdigit():
        errors.append(("term", "TERM_OUT_OF_RANGE"))
    else:
        term = int(app["term"])
        if term <= 0 or term >= 84:
            errors.append(("term", "TERM_OUT_OF_RANGE"))

    return errors


def score(app, bki_score, delinquency):
    """TODO: считать score по весам."""
    income = float(app["income"])
    s_income = 30 if income >= 150000 else 20 if income >= 80000 else 10 if income >= 40000 else 0
    s_emp = {"employed": 25, "self-employed": 20, "retired": 10,
             "student": 5, "unemployed": 0}[app["employment"]]
    age = int(app["age"])
    if 25 <= age <= 50:
        s_age = 20
    elif 18 <= age <= 24 or 51 <= age <= 60:
        s_age = 10
    elif 61 <= age <= 70:
        s_age = 5
    else:
        s_age = 0

    family = str(app["family"])
    s_family = {
        "married": 15,
        "children": 5,
        "single": 10,
    }[app["family"]]

    if delinquency or bki_score < 500:
        s_bki = 0
    elif bki_score >= 700:
        s_bki = 20
    elif bki_score >= 600:
        s_bki = 15
    else:
        s_bki = 10
    # TODO: возраст, семья, БКИ
    return s_income + s_emp + s_age + s_family + s_bki


def compute_dti(amount, term, income):
    mr = 0.18 / 12
    payment = amount * mr / (1 - (1 + mr) ** (-term))
    return round(payment / income, 2) if income else None


def decide(app, score_total, dti, delinquency, smev):
    """TODO: 8 правил отказа."""
    income = int(app["income"])
    amount = int(app["amount"])
    emp = str(app["employment"])

    if emp == "unemployed":
        return "REFUSED", "INVALID_EMPLOYMENT"
    if delinquency or bki_score < 500:
        return "REFUSED", "BKI_DELINQUENCY"
    if amount > 10 * income:
        return "REFUSED", "AMOUNT_LIMIT"
    if dti is not None and dti > 0.5:
        return "REFUSED", "HIGH_DTI"
    if score_total < 60:
        return "REFUSED", "LOW_SCORE"
    if smev == "MISMATCH":
        return "REFUSED", "SMEV_MISMATCH"
    return "APPROVED", "-"


def process(app):
    errors = validate(app)
    if errors:
        reason = errors[0][1]
        # ИСПРАВЛЕНО: эти три причины = отказ, остальные = нужно больше данных
        if reason in ("AGE_OUT_OF_RANGE", "INVALID_EMPLOYMENT", "LOW_INCOME"):
            decision = "REFUSED"
        else:
            decision = "NEED_MORE_DATA"

        return {"app_id": app["app_id"], "decision": "NEED_MORE_DATA",
                "reason": reason, "score": "", "dti": ""}

    bki_score, delinq = bki_emulator(app["passport_hash"])
    smev = smev_emulator(app["client_id"])
    score_total = score(app, bki_score, delinq)
    dti = compute_dti(float(app["amount"]), int(app["term"]), float(app["income"]))
    decision, reason = decide(app, score_total, dti, delinq, smev)

    return {"app_id": app["app_id"], "decision": decision, "reason": reason,
            "score": score_total, "dti": dti}


def main():
    if len(sys.argv) < 2:
        print("Использование: python 04_прототип.py applications.csv [--out decisions.csv]")
        sys.exit(1)

    src = sys.argv[1]
    out = "decisions.csv"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    with open(src, encoding="utf-8") as f:
        apps = list(csv.DictReader(f))

    results = [process(a) for a in apps]

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in results:
            w.writerow(r)

    stats = {}
    for r in results:
        stats[r["decision"]] = stats.get(r["decision"], 0) + 1
    print(f"OK: {out}")
    print(f"    обработано: {len(results)}")
    print(f"    решения:    {stats}")


if __name__ == "__main__":
    main()