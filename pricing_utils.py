from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Tuple

import pandas as pd
import pycountry
import requests


REQUIRED_PRICING_COLUMNS = ["REGION", "CURRENCY CODE", "PRICE"]
NON_BILLABLE_REGION_CODES = {"ZZ"}


def extract_sheet_id_from_url(url_or_id: str) -> str | None:
    candidate = (url_or_id or "").strip()
    if not candidate:
        return None
    if "/" not in candidate and "." not in candidate:
        return candidate

    patterns = [
        r"/spreadsheets/d/([a-zA-Z0-9-_]+)",
        r"id=([a-zA-Z0-9-_]+)",
        r"^([a-zA-Z0-9-_]+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, candidate)
        if match:
            return match.group(1)
    return None


def fetch_google_sheet_csv(sheet_url_or_id: str) -> bytes:
    sheet_id = extract_sheet_id_from_url(sheet_url_or_id)
    if not sheet_id:
        raise ValueError("Invalid Google Sheets URL or sheet ID.")
    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    response = requests.get(csv_url, timeout=30)
    response.raise_for_status()
    return response.content


def _normalize_pricing_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    normalized.columns = [str(c).strip().upper().replace(" ", "_") for c in normalized.columns]
    return normalized


def normalize_pricing_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    return _normalize_pricing_dataframe(df)


def parse_decimal_value(price: Any, *, row_number: int | None = None, column_name: str = "PRICE") -> Decimal:
    raw_value = "" if price is None else str(price).strip()
    if raw_value == "":
        location = f"row {row_number}, column {column_name}" if row_number is not None else f"column {column_name}"
        raise ValueError(f"Missing numeric value in {location}")
    try:
        return Decimal(raw_value)
    except (InvalidOperation, ValueError):
        location = f"row {row_number}, column {column_name}" if row_number is not None else f"column {column_name}"
        raise ValueError(f"Invalid decimal in {location}: {raw_value!r}")


def _parse_price_to_units_nanos(price: Any, *, row_number: int | None = None, column_name: str = "PRICE") -> Tuple[str, int]:
    price_decimal = parse_decimal_value(price, row_number=row_number, column_name=column_name)
    if price_decimal <= 0:
        location = f"row {row_number}, column {column_name}" if row_number is not None else f"column {column_name}"
        raise ValueError(f"Price must be greater than zero in {location}")
    units = int(price_decimal)
    nanos = int((price_decimal - units) * Decimal("1000000000"))
    return str(units), nanos


def regional_configs_from_dataframe(df: pd.DataFrame) -> List[Dict[str, Any]]:
    normalized = _normalize_pricing_dataframe(df)
    required = [c.replace(" ", "_") for c in REQUIRED_PRICING_COLUMNS]
    missing = [c for c in required if c not in normalized.columns]
    if missing:
        raise ValueError(f"Missing required pricing columns: {missing}")

    configs: List[Dict[str, Any]] = []
    for index, row in normalized.iterrows():
        row_number = int(index) + 2
        region = str(row.get("REGION", "")).strip().upper()
        currency = str(row.get("CURRENCY_CODE", "")).strip().upper()
        price = row.get("PRICE")
        if not region or region == "NAN" or region in NON_BILLABLE_REGION_CODES:
            continue
        if not currency or currency == "NAN":
            continue
        if pd.isna(price):
            continue
        units, nanos = _parse_price_to_units_nanos(price, row_number=row_number, column_name="PRICE")
        configs.append(
            {
                "regionCode": region,
                "price": {
                    "units": units,
                    "nanos": nanos,
                    "currencyCode": currency,
                },
                "newSubscriberAvailability": True,
            }
        )
    if not configs:
        raise ValueError("No valid regional pricing rows were found.")
    return configs


def load_pricing_dataframe_from_upload(uploaded_file) -> pd.DataFrame:
    filename = getattr(uploaded_file, "name", "").lower()
    content = uploaded_file.getvalue()
    if filename.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    return pd.read_excel(io.BytesIO(content), engine="openpyxl")


def regional_configs_from_upload(uploaded_file) -> List[Dict[str, Any]]:
    df = load_pricing_dataframe_from_upload(uploaded_file)
    return regional_configs_from_dataframe(df)


def load_pricing_dataframe_from_google_sheet(sheet_url_or_id: str) -> pd.DataFrame:
    content = fetch_google_sheet_csv(sheet_url_or_id)
    return pd.read_csv(io.BytesIO(content))


def regional_configs_from_google_sheet(sheet_url_or_id: str) -> List[Dict[str, Any]]:
    df = load_pricing_dataframe_from_google_sheet(sheet_url_or_id)
    return regional_configs_from_dataframe(df)


def offer_phase_configs_from_dataframe(
    df: pd.DataFrame,
    price_column: str = "INTRO PRICE",
) -> List[Dict[str, Any]]:
    normalized = _normalize_pricing_dataframe(df)
    required = ["REGION", price_column.upper().replace(" ", "_")]
    missing = [c for c in required if c not in normalized.columns]
    if missing:
        raise ValueError(f"Missing required offer pricing columns: {missing}")

    configs: List[Dict[str, Any]] = []
    for index, row in normalized.iterrows():
        row_number = int(index) + 2
        region = str(row.get("REGION", "")).strip().upper()
        price = row.get(price_column.upper().replace(" ", "_"))
        if not region or region == "NAN" or region in NON_BILLABLE_REGION_CODES or pd.isna(price):
            continue
        config: Dict[str, Any] = {"regionCode": region}
        price_str = str(price).strip().lower()
        if price_str in {"free", "0", "0.0", "0.00"}:
            config["free"] = {}
        else:
            currency = str(row.get("CURRENCY_CODE", "")).strip().upper()
            if not currency or currency == "NAN":
                raise ValueError(f"Missing currency code for intro price region {region} in row {row_number}")
            units, nanos = _parse_price_to_units_nanos(
                price,
                row_number=row_number,
                column_name=price_column.upper().replace(" ", "_"),
            )
            config["price"] = {
                "units": units,
                "nanos": nanos,
                "currencyCode": currency,
            }
        configs.append(config)

    if not configs:
        raise ValueError("No valid offer pricing rows were found.")
    return configs


def parse_bulk_catalog_rows(
    df: pd.DataFrame,
    default_language_code: str,
    default_billing_period: str,
    default_grace_period: str,
    default_resubscribe_state: str,
) -> List[Dict[str, Any]]:
    normalized = _normalize_pricing_dataframe(df)
    required = [
        "PRODUCT_ID",
        "BASE_PLAN_ID",
        "TITLE",
        "DESCRIPTION",
        "REGION",
        "CURRENCY_CODE",
        "PRICE",
    ]
    missing = [column for column in required if column not in normalized.columns]
    if missing:
        raise ValueError(f"Missing required bulk pricing columns: {missing}")

    grouped: Dict[tuple[str, str], Dict[str, Any]] = {}
    for index, row in normalized.iterrows():
        row_number = int(index) + 2
        product_id = str(row.get("PRODUCT_ID", "")).strip()
        base_plan_id = str(row.get("BASE_PLAN_ID", "")).strip()
        region = str(row.get("REGION", "")).strip().upper()
        currency_code = str(row.get("CURRENCY_CODE", "")).strip().upper()
        price = row.get("PRICE")
        if (
            not product_id
            or not base_plan_id
            or not region
            or region == "NAN"
            or region in NON_BILLABLE_REGION_CODES
            or not currency_code
            or currency_code == "NAN"
            or pd.isna(price)
        ):
            continue
        validated_price = parse_decimal_value(price, row_number=row_number, column_name="PRICE")
        intro_price_raw = row.get("INTRO_PRICE")
        intro_price = ""
        if not pd.isna(intro_price_raw) and str(intro_price_raw).strip() != "":
            parse_decimal_value(intro_price_raw, row_number=row_number, column_name="INTRO_PRICE")
            intro_price = str(intro_price_raw).strip()
        key = (product_id, base_plan_id)
        if key not in grouped:
            grouped[key] = {
                "product_id": product_id,
                "base_plan_id": base_plan_id,
                "title": str(row.get("TITLE", "")).strip(),
                "description": str(row.get("DESCRIPTION", "")).strip(),
                "language_code": str(row.get("LANGUAGE_CODE", "")).strip() or default_language_code,
                "billing_period_duration": str(row.get("BILLING_PERIOD", "")).strip() or default_billing_period,
                "grace_period_duration": str(row.get("GRACE_PERIOD", "")).strip() or default_grace_period,
                "resubscribe_state": str(row.get("RESUBSCRIBE_STATE", "")).strip() or default_resubscribe_state,
                "offer_id": str(row.get("OFFER_ID", "")).strip(),
                "pricing_rows": [],
            }
        grouped[key]["pricing_rows"].append(
            {
                "regionCode": region,
                "currencyCode": currency_code,
                "basePrice": str(validated_price),
                "introPrice": intro_price,
            }
        )

    results = list(grouped.values())
    if not results:
        raise ValueError("No valid bulk SKU rows were found.")
    return results


def build_pricing_template_bytes() -> bytes:
    sample = pd.DataFrame(
        [
            {"REGION": "US", "CURRENCY CODE": "USD", "PRICE": 9.99},
            {"REGION": "GB", "CURRENCY CODE": "GBP", "PRICE": 7.99},
            {"REGION": "DE", "CURRENCY CODE": "EUR", "PRICE": 8.99},
        ]
    )
    return sample.to_csv(index=False).encode("utf-8")


def region_name_from_code(region_code: str) -> str:
    country = pycountry.countries.get(alpha_2=region_code.upper())
    return country.name if country else region_code.upper()


def _extract_google_price_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [
        item.get("price"),
        item.get("convertedPrice"),
        item.get("regionPrice"),
        item.get("convertedRegionPrice"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            if any(key in candidate for key in ("currencyCode", "units", "nanos")):
                return candidate
            nested_candidates = [
                candidate.get("price"),
                candidate.get("convertedPrice"),
                candidate.get("regionPrice"),
            ]
            for nested in nested_candidates:
                if isinstance(nested, dict) and any(key in nested for key in ("currencyCode", "units", "nanos")):
                    return nested
    if any(key in item for key in ("currencyCode", "units", "nanos")):
        return item
    return {}


def _normalize_google_region_prices(converted_region_prices: Any) -> List[Dict[str, Any]]:
    if isinstance(converted_region_prices, list):
        return [item for item in converted_region_prices if isinstance(item, dict)]
    if isinstance(converted_region_prices, dict):
        normalized: List[Dict[str, Any]] = []
        for region_code, price in converted_region_prices.items():
            if isinstance(price, dict):
                normalized.append(
                    {
                        "regionCode": str(region_code).strip().upper(),
                        "price": _extract_google_price_payload(price),
                    }
                )
        return normalized
    return []


def build_pricing_template_from_google_prices(converted_region_prices: Any) -> bytes:
    normalized_prices = _normalize_google_region_prices(converted_region_prices)
    records = []
    for item in normalized_prices:
        region_code = str(item.get("regionCode", "")).strip().upper()
        if not region_code or region_code in NON_BILLABLE_REGION_CODES:
            continue
        price = _extract_google_price_payload(item)
        records.append(
            {
                "REGION": region_code,
                "REGION NAME": region_name_from_code(region_code),
                "CURRENCY CODE": price.get("currencyCode", ""),
                "PRICE": "",
                "INTRO PRICE": "",
            }
        )
    if not records:
        return build_pricing_template_bytes()
    df = pd.DataFrame(records).sort_values(["REGION NAME", "REGION"])
    return df.to_csv(index=False).encode("utf-8")


def build_google_region_currency_map(converted_region_prices: Any) -> Dict[str, str]:
    normalized_prices = _normalize_google_region_prices(converted_region_prices)
    currency_map: Dict[str, str] = {}
    for item in normalized_prices:
        region_code = str(item.get("regionCode", "")).strip().upper()
        if not region_code or region_code in NON_BILLABLE_REGION_CODES:
            continue
        price = _extract_google_price_payload(item)
        currency_code = str(price.get("currencyCode", "")).strip().upper()
        if currency_code:
            currency_map[region_code] = currency_code
    return currency_map
