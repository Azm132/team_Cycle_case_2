#!/usr/bin/env python3
"""
CLI-прототип кредитного конвейера — кейс 2.

Использование:
    python 04_прототип.py applications.csv
    python 04_прототип.py applications.csv --out decisions.csv
    python 04_прототип.py applications.csv --explain app_0001

Изюминка: peer-check
    После базовых правил ищем k похожих заявок в портфеле.
    Если «соседи» токсичные, а решение было APPROVED → MANUAL_REVIEW / PEER_HIGH_RISK.
    Peer-check никогда не одобряет то, что правила уже отказали.

Реализовано:
    1. validate() — правила из 02_правила.md
    2. bki_emulator() / smev_emulator() — 06_external_services.md
    3. score() — веса из 02_правила.md
    4. decide() — правила отказа в порядке приоритета
    5. DTI — аннуитет 18% годовых
    6. peer-check — look-alike по портфелю
    7. decisions.csv + сводка
"""
import csv
import hashlib
import sys

OUT_FIELDS = [
    "app_id",
    "decision",
    "reason",
    "score",
    "dti",
    "peer_count",
    "peer_approve_rate",
    "peer_signal",
]

PEER_K = 5  # сколько похожих заявок смотрим


# ---------------------------------------------------------------------------
# Эмуляторы внешних сервисов
# ---------------------------------------------------------------------------

def bki_emulator(passport_hash):
    """
    Детерминированный эмулятор БКИ.
    h = md5(passport_hash) % 10
    0..5 → score 600..700, delinquency=False
    6..8 → score 450..550, delinquency=False
    9    → score 400,      delinquency=True
    """
    h = int(hashlib.md5(str(passport_hash).encode()).hexdigest(), 16) % 10
    if h <= 5:
        return (600 + h * 20, False)
    if 6 <= h <= 8:
        return (450 + (h - 6) * 50, False)
    return (400, True)


def smev_emulator(client_id):
    """h = md5(client_id) % 10; h == 0 → MISMATCH, иначе MATCH."""
    h = int(hashlib.md5(str(client_id).encode()).hexdigest(), 16) % 10
    return "MISMATCH" if h == 0 else "MATCH"


# ---------------------------------------------------------------------------
# Валидация / скоринг / DTI / decide
# ---------------------------------------------------------------------------

def validate(app):
    """Возвращает список (field, reason_code). MISSING_INCOME — первым."""
    errors = []

    income_raw = app.get("income")
    if income_raw is None or str(income_raw).strip() == "":
        errors.append(("income", "MISSING_INCOME"))
    else:
        try:
            if float(income_raw) < 40000:
                errors.append(("income", "LOW_INCOME"))
        except (TypeError, ValueError):
            errors.append(("income", "MISSING_INCOME"))

    age_raw = app.get("age")
    if age_raw is None or str(age_raw).strip() == "":
        errors.append(("age", "AGE_OUT_OF_RANGE"))
    else:
        try:
            age = int(age_raw)
            if age < 18 or age > 70:
                errors.append(("age", "AGE_OUT_OF_RANGE"))
        except (TypeError, ValueError):
            errors.append(("age", "AGE_OUT_OF_RANGE"))

    emp = (app.get("employment") or "").strip().lower()
    valid_emp = {"employed", "self-employed", "retired", "student", "unemployed"}
    if emp not in valid_emp or emp == "unemployed":
        errors.append(("employment", "INVALID_EMPLOYMENT"))

    amount_raw = app.get("amount")
    if amount_raw is None or str(amount_raw).strip() == "":
        errors.append(("amount", "AMOUNT_LIMIT"))
    else:
        try:
            if float(amount_raw) <= 0:
                errors.append(("amount", "AMOUNT_LIMIT"))
        except (TypeError, ValueError):
            errors.append(("amount", "AMOUNT_LIMIT"))

    term_raw = app.get("term")
    if term_raw is None or str(term_raw).strip() == "":
        errors.append(("term", "TERM_OUT_OF_RANGE"))
    else:
        try:
            term = int(term_raw)
            if term <= 0 or term >= 84:
                errors.append(("term", "TERM_OUT_OF_RANGE"))
        except (TypeError, ValueError):
            errors.append(("term", "TERM_OUT_OF_RANGE"))

    return errors


def score(app, bki_score, delinquency):
    """Скоринг по 5 признакам. Порог одобрения: 60."""
    income = float(app["income"])
    if income >= 150000:
        s_income = 30
    elif income >= 80000:
        s_income = 20
    elif income >= 40000:
        s_income = 10
    else:
        s_income = 0

    s_emp = {
        "employed": 25,
        "self-employed": 20,
        "retired": 10,
        "student": 5,
        "unemployed": 0,
    }.get((app.get("employment") or "").strip().lower(), 0)

    age = int(app["age"])
    if 25 <= age <= 50:
        s_age = 20
    elif 18 <= age <= 24 or 51 <= age <= 60:
        s_age = 10
    elif 61 <= age <= 70:
        s_age = 5
    else:
        s_age = 0

    family = (app.get("family") or "").strip().lower()
    s_family = {"married": 15, "children": 5, "single": 10}.get(family, 0)

    if delinquency or bki_score < 500:
        s_bki = 0
    elif bki_score >= 700:
        s_bki = 20
    elif bki_score >= 600:
        s_bki = 15
    else:
        s_bki = 10

    return max(0, s_income + s_emp + s_age + s_family + s_bki)


def compute_dti(amount, term, income):
    """Аннуитетный платёж / доход. Ставка 18% годовых."""
    if not income or income <= 0 or term <= 0:
        return None
    mr = 0.18 / 12
    payment = amount * mr / (1 - (1 + mr) ** (-term))
    return round(payment / income, 2)


def decide(app, score_total, dti, delinquency, smev, bki_score):
    """Правила отказа в порядке приоритета."""
    income = float(app["income"])
    amount = float(app["amount"])
    emp = (app.get("employment") or "").strip().lower()

    if emp == "unemployed":
        return "REFUSED", "INVALID_EMPLOYMENT"
    if delinquency or bki_score < 500:
        return "REFUSED", "BKI_DELINQUENCY"
    if amount > 10 * income:
        return "REFUSED", "AMOUNT_LIMIT"
    if dti is not None and dti > 0.5:
        return "REFUSED", "HIGH_DTI"
    if smev == "MISMATCH":
        return "REFUSED", "SMEV_MISMATCH"
    if score_total < 60:
        return "REFUSED", "LOW_SCORE"
    return "APPROVED", ""


# ---------------------------------------------------------------------------
# Изюминка: peer-check (look-alike по портфелю)
# ---------------------------------------------------------------------------

def _peer_features(app):
    """Числовые/категориальные признаки для сравнения заявок."""
    return {
        "income": float(app["income"]),
        "age": int(app["age"]),
        "amount": float(app["amount"]),
        "term": int(app["term"]),
        "employment": (app.get("employment") or "").strip().lower(),
        "family": (app.get("family") or "").strip().lower(),
    }


def _distance(a, b):
    """
    Нормализованная дистанция. Чем меньше — тем заявки похожее.
    Веса подобраны так, чтобы доход и сумма влияли сильнее категории.
    """
    d = 0.0
    d += abs(a["income"] - b["income"]) / 100_000.0
    d += abs(a["age"] - b["age"]) / 50.0
    d += abs(a["amount"] - b["amount"]) / 500_000.0
    d += abs(a["term"] - b["term"]) / 36.0
    d += 0.0 if a["employment"] == b["employment"] else 1.0
    d += 0.0 if a["family"] == b["family"] else 0.5
    return d


def find_peers(app, all_apps, k=PEER_K):
    """
    k ближайших заявок из портфеля (кроме самой себя).
    Для эталона decision берём колонку decision из CSV (если есть),
    иначе пропускаем заявку.
    """
    try:
        fa = _peer_features(app)
    except (TypeError, ValueError, KeyError):
        return []

    scored = []
    for other in all_apps:
        if other.get("app_id") == app.get("app_id"):
            continue
        # нужен известный исход, чтобы считать approve_rate
        if not other.get("decision"):
            continue
        try:
            fo = _peer_features(other)
        except (TypeError, ValueError, KeyError):
            continue
        scored.append((_distance(fa, fo), other))

    scored.sort(key=lambda x: x[0])
    return [o for _, o in scored[:k]]


def peer_signal(peers):
    """
    Возвращает (count, approve_rate, signal).
    signal:
      - high_risk  — approve_rate < 0.30
      - caution    — 0.30 ≤ rate < 0.60
      - neutral    — rate ≥ 0.60 или нет соседей
    """
    if not peers:
        return 0, 0.0, "neutral"
    approved = sum(1 for p in peers if (p.get("decision") or "").upper() == "APPROVED")
    rate = approved / len(peers)
    if rate < 0.30:
        signal = "high_risk"
    elif rate < 0.60:
        signal = "caution"
    else:
        signal = "neutral"
    return len(peers), round(rate, 2), signal


def apply_peer_adjustment(decision, reason, signal):
    """
    Peer-check только ужесточает:
    APPROVED + high_risk → MANUAL_REVIEW / PEER_HIGH_RISK.
    Отказы и NEED_MORE_DATA не трогаем.
    """
    if decision == "APPROVED" and signal == "high_risk":
        return "MANUAL_REVIEW", "PEER_HIGH_RISK"
    return decision, reason


# ---------------------------------------------------------------------------
# Конвейер одной заявки
# ---------------------------------------------------------------------------

def process(app, all_apps=None):
    empty_peer = {"peer_count": "", "peer_approve_rate": "", "peer_signal": ""}

    errors = validate(app)
    if errors:
        reason = errors[0][1]
        decision = "NEED_MORE_DATA" if reason == "MISSING_INCOME" else "REFUSED"
        return {
            "app_id": app.get("app_id", ""),
            "decision": decision,
            "reason": reason,
            "score": "",
            "dti": "",
            **empty_peer,
        }

    bki_score, delinq = bki_emulator(app["passport_hash"])
    smev = smev_emulator(app["client_id"])
    score_total = score(app, bki_score, delinq)
    dti = compute_dti(
        float(app["amount"]),
        int(app["term"]),
        float(app["income"]),
    )
    decision, reason = decide(app, score_total, dti, delinq, smev, bki_score)

    # --- peer-check ---
    peers = find_peers(app, all_apps or [], k=PEER_K)
    n, rate, signal = peer_signal(peers)
    decision, reason = apply_peer_adjustment(decision, reason, signal)

    return {
        "app_id": app["app_id"],
        "decision": decision,
        "reason": reason,
        "score": score_total,
        "dti": dti,
        "peer_count": n,
        "peer_approve_rate": rate,
        "peer_signal": signal,
    }


def explain(app, all_apps):
    """Подробный разбор одной заявки (для --explain)."""
    print("=" * 60)
    print(f"EXPLAIN {app.get('app_id')}")
    print("-" * 60)
    print(f"  age={app.get('age')}  income={app.get('income')}  "
          f"employment={app.get('employment')}  family={app.get('family')}")
    print(f"  amount={app.get('amount')}  term={app.get('term')}")

    errors = validate(app)
    if errors:
        print(f"  VALIDATE FAIL → {errors[0][1]}")
        return

    bki_score, delinq = bki_emulator(app["passport_hash"])
    smev = smev_emulator(app["client_id"])
    score_total = score(app, bki_score, delinq)
    dti = compute_dti(float(app["amount"]), int(app["term"]), float(app["income"]))
    decision, reason = decide(app, score_total, dti, delinq, smev, bki_score)

    print(f"  BKI: score={bki_score}  delinquency={delinq}")
    print(f"  SMEV: {smev}")
    print(f"  score={score_total}  dti={dti}")
    print(f"  base decision: {decision}  reason={reason or '—'}")

    peers = find_peers(app, all_apps, k=PEER_K)
    n, rate, signal = peer_signal(peers)
    print(f"  peers: k={n}  approve_rate={rate}  signal={signal}")
    if peers:
        print("  nearest:")
        for i, p in enumerate(peers, 1):
            print(f"    {i}. {p.get('app_id')}  decision={p.get('decision')}  "
                  f"income={p.get('income')} amount={p.get('amount')} age={p.get('age')}")

    final_d, final_r = apply_peer_adjustment(decision, reason, signal)
    print(f"  FINAL: {final_d}  reason={final_r or '—'}")
    if final_d != decision:
        print("  ⚠ peer-check изменил решение: APPROVED → MANUAL_REVIEW")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Использование:")
        print("  python 04_прототип.py applications.csv [--out decisions.csv]")
        print("  python 04_прототип.py applications.csv --explain app_0001")
        sys.exit(1)

    src = sys.argv[1]
    out = "decisions.csv"
    explain_id = None

    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out = sys.argv[idx + 1]
    if "--explain" in sys.argv:
        idx = sys.argv.index("--explain")
        if idx + 1 < len(sys.argv):
            explain_id = sys.argv[idx + 1]

    with open(src, encoding="utf-8") as f:
        apps = list(csv.DictReader(f))

    if explain_id:
        target = next((a for a in apps if a.get("app_id") == explain_id), None)
        if not target:
            print(f"Заявка {explain_id} не найдена")
            sys.exit(1)
        explain(target, apps)
        return

    results = [process(a, all_apps=apps) for a in apps]

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for r in results:
            w.writerow(r)

    stats = {}
    peer_stats = {"high_risk": 0, "caution": 0, "neutral": 0}
    peer_overrides = 0
    for r in results:
        stats[r["decision"]] = stats.get(r["decision"], 0) + 1
        sig = r.get("peer_signal") or ""
        if sig in peer_stats:
            peer_stats[sig] += 1
        if r.get("reason") == "PEER_HIGH_RISK":
            peer_overrides += 1

    print(f"OK: {out}")
    print(f"    обработано:     {len(results)}")
    print(f"    решения:        {stats}")
    print(f"    peer-signal:    {peer_stats}")
    print(f"    peer-overrides: {peer_overrides}  (APPROVED → MANUAL_REVIEW)")


if __name__ == "__main__":
    main()
