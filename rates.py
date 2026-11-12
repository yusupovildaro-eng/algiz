"""Курсы ЦБ Республики Узбекистан: сколько сумов за 1 единицу валюты."""

import requests

CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/"
CURRENCIES = ["UZS", "USD", "EUR", "RUB", "CNY", "TRY"]


def get_rates(manual_rates: dict | None = None) -> tuple[dict, str]:
    """Возвращает (курсы, подпись для отчёта). Если ЦБ недоступен — берёт manual_rates."""
    rates = {"UZS": 1.0}
    label = "курс ЦБ РУз недоступен"
    try:
        r = requests.get(CBU_URL, timeout=15)
        r.raise_for_status()
        for row in r.json():
            ccy = row.get("Ccy")
            if ccy in CURRENCIES:
                rates[ccy] = float(row["Rate"]) / float(row.get("Nominal") or 1)
                label = f"курс ЦБ РУз на {row.get('Date', '?')}"
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"[rates] курсы ЦБ не получены: {e}")
    for ccy, value in (manual_rates or {}).items():
        if ccy.upper() not in rates:
            rates[ccy.upper()] = float(value)
            label += f", {ccy.upper()} — из настроек"
    return rates, label
