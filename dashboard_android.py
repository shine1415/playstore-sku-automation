#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from auth_manager import AuthManager
from pricing_utils import (
    NON_BILLABLE_REGION_CODES,
    build_pricing_template_bytes,
    build_pricing_template_from_google_prices,
    build_google_region_currency_map,
    _normalize_google_region_prices,
    load_pricing_dataframe_from_google_sheet,
    load_pricing_dataframe_from_upload,
    normalize_pricing_dataframe,
    parse_bulk_catalog_rows,
    regional_configs_from_google_sheet,
    regional_configs_from_upload,
    region_name_from_code,
)


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.template.json"
LOCAL_CONFIG_PATH = PROJECT_DIR / "config.local.json"
LOCAL_SETTINGS_PATH = PROJECT_DIR / "dashboard_settings.local.json"
SECRETS_DIR = PROJECT_DIR / "secrets"

DEFAULT_PACKAGE_NAME = "com.example.app"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_BILLING_PERIOD = "P1M"
DEFAULT_GRACE_PERIOD = "P0D"
DEFAULT_RESUBSCRIBE_STATE = "RESUBSCRIBE_STATE_ACTIVE"
ELIGIBILITY_OPTIONS = {
    "New customer acquisition": "acquisition",
    "Upgrade from existing subscription": "upgrade",
    "Developer determined": "developer",
}
ACQUISITION_SCOPE_OPTIONS = {
    "This subscription": "this",
    "Any subscription in app": "any",
}
UPGRADE_SCOPE_OPTIONS = {
    "This subscription": "this",
    "Specific subscription in app": "specific",
}
PHASE_TYPE_OPTIONS = [
    "Free trial",
    "Single payment",
    "Discounted recurring payment",
]
PRICE_OVERRIDE_OPTIONS = {
    "Custom price": "price",
    "Absolute discount": "absolute_discount",
    "Relative discount": "relative_discount",
}
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
THREE_DECIMAL_CURRENCIES = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}

st.set_page_config(page_title="Google Play SKU Dashboard", layout="wide")
st.title("Google Play SKU Dashboard")
st.caption("Create Google Play subscriptions with base plans and regional pricing from file upload or Google Sheets.")

if "android_modify_loaded" not in st.session_state:
    st.session_state["android_modify_loaded"] = None
if "android_modify_preview" not in st.session_state:
    st.session_state["android_modify_preview"] = None


def load_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return json.loads(json.dumps(default))
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_local_config() -> Dict[str, Any]:
    return load_json_file(
        LOCAL_CONFIG_PATH,
        {
            "google_play": {
                "service_account_path": "",
                "package_name": DEFAULT_PACKAGE_NAME,
            }
        },
    )


def load_local_settings() -> Dict[str, Any]:
    return load_json_file(
        LOCAL_SETTINGS_PATH,
        {
            "language_code": DEFAULT_LANGUAGE,
        },
    )


def save_uploaded_service_account(uploaded_file) -> str:
    if uploaded_file is None:
        raise ValueError("Upload the Google service account JSON file first.")
    if not uploaded_file.name.lower().endswith(".json"):
        raise ValueError("Only JSON service account files are supported.")
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    destination = SECRETS_DIR / "google-service-account.json"
    destination.write_bytes(uploaded_file.getvalue())
    return str(destination)


def load_service_account_summary(service_account_path: str) -> Dict[str, str]:
    if not service_account_path:
        return {}
    path = Path(service_account_path).expanduser()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "client_email": data.get("client_email", ""),
        "project_id": data.get("project_id", ""),
    }


def build_logger() -> logging.Logger:
    logger = logging.getLogger("googleplay_dashboard")
    logger.setLevel(logging.INFO)
    logger.handlers = []
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def get_auth_manager(service_account_path: str) -> AuthManager:
    return AuthManager(service_account_file=service_account_path)


def build_preview_payload(
    package_name: str,
    product_id: str,
    base_plan_id: str,
    title: str,
    description: str,
    language_code: str,
    billing_period_duration: str,
    grace_period_duration: str,
    resubscribe_state: str,
    regional_configs: list[dict[str, Any]],
    legacy_compatible: bool = False,
    legacy_compatible_offer_id: str = "",
) -> Dict[str, Any]:
    auto_renewing: Dict[str, Any] = {
        "billingPeriodDuration": billing_period_duration,
        "gracePeriodDuration": grace_period_duration,
        "resubscribeState": resubscribe_state,
    }
    if legacy_compatible:
        auto_renewing["legacyCompatible"] = True
    if legacy_compatible_offer_id.strip():
        auto_renewing["legacyCompatibleSubscriptionOfferId"] = legacy_compatible_offer_id.strip()
    return {
        "packageName": package_name,
        "productId": product_id,
        "listings": [
            {
                "languageCode": language_code,
                "title": title,
                "description": description,
            }
        ],
        "basePlans": [
            {
                "basePlanId": base_plan_id,
                "autoRenewingBasePlanType": auto_renewing,
                "regionalConfigs": regional_configs,
            }
        ],
    }


def _money_from_decimal(value: Any, currency_code: str) -> Dict[str, Any]:
    decimal_value = Decimal(str(value))
    units = int(decimal_value)
    nanos = int((decimal_value - units) * Decimal("1000000000"))
    return {
        "units": str(units),
        "nanos": nanos,
        "currencyCode": currency_code,
    }


def _currency_quantum(currency_code: str) -> Decimal:
    currency = (currency_code or "").upper()
    if currency in ZERO_DECIMAL_CURRENCIES:
        return Decimal("1")
    if currency in THREE_DECIMAL_CURRENCIES:
        return Decimal("0.001")
    return Decimal("0.01")


def _normalize_amount_for_currency(value: Any, currency_code: str) -> Decimal:
    decimal_value = Decimal(str(value))
    return decimal_value.quantize(_currency_quantum(currency_code), rounding=ROUND_HALF_UP)


def normalize_pricing_rows_for_currency(
    pricing_rows: list[dict[str, Any]],
    product_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    normalized_rows: list[dict[str, Any]] = []
    adjustments: list[dict[str, str]] = []
    for row in pricing_rows:
        updated = dict(row)
        currency_code = updated["currencyCode"]
        base_original = Decimal(str(updated["basePrice"]))
        base_normalized = _normalize_amount_for_currency(base_original, currency_code)
        if base_normalized != base_original:
            adjustments.append(
                {
                    "product_id": product_id,
                    "kind": "base_price",
                    "region": updated["regionCode"],
                    "currency": currency_code,
                    "requested": str(base_original),
                    "resolved": str(base_normalized),
                }
            )
        updated["basePrice"] = str(base_normalized)

        intro_value = updated.get("introPrice", "")
        if intro_value not in ("", None):
            intro_original = Decimal(str(intro_value))
            intro_normalized = _normalize_amount_for_currency(intro_original, currency_code)
            if intro_normalized != intro_original:
                adjustments.append(
                    {
                        "product_id": product_id,
                        "kind": "intro_price",
                        "region": updated["regionCode"],
                        "currency": currency_code,
                        "requested": str(intro_original),
                        "resolved": str(intro_normalized),
                    }
                )
            updated["introPrice"] = str(intro_normalized)

        normalized_rows.append(updated)
    return normalized_rows, adjustments


def _duration_to_days(duration: str) -> Decimal:
    match = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?", duration or "")
    if not match:
        raise ValueError(f"Unsupported ISO 8601 duration: {duration}")
    years, months, weeks, days = (int(part) if part else 0 for part in match.groups())
    return Decimal(years * 365 + months * 30 + weeks * 7 + days)


def _prorated_base_price(base_price: Decimal, billing_period: str, phase_duration: str) -> Decimal:
    base_days = _duration_to_days(billing_period)
    phase_days = _duration_to_days(phase_duration)
    if base_days <= 0 or phase_days <= 0:
        raise ValueError("Billing period and phase duration must be positive durations.")
    return (base_price * phase_days) / base_days


def build_offer_targeting(
    eligibility_mode: str,
    acquisition_scope_mode: str,
    upgrade_scope_mode: str,
    specific_subscription_id: str,
    once_per_user: bool,
) -> Optional[Dict[str, Any]]:
    if eligibility_mode == "developer":
        return None
    if eligibility_mode == "acquisition":
        scope = {"anySubscriptionInApp": {}} if acquisition_scope_mode == "any" else {"thisSubscription": {}}
        return {"acquisitionRule": {"scope": scope}}

    if upgrade_scope_mode == "specific":
        if not specific_subscription_id.strip():
            raise ValueError("Specific subscription ID is required for upgrade targeting.")
        scope: Dict[str, Any] = {"specificSubscriptionInApp": specific_subscription_id.strip()}
    else:
        scope = {"thisSubscription": {}}
    rule: Dict[str, Any] = {"scope": scope}
    if once_per_user:
        rule["oncePerUser"] = True
    return {"upgradeRule": rule}


def build_offer_phase_regional_configs(
    pricing_rows: list[dict[str, Any]],
    phase_type: str,
    price_override_mode: str,
    billing_period_duration: str,
    phase_duration: str,
) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for item in pricing_rows:
        region_code = item["regionCode"]
        currency_code = item["currencyCode"]
        base_price = Decimal(str(item["basePrice"]))
        intro_price_value = item.get("introPrice")
        config: Dict[str, Any] = {"regionCode": region_code}

        if phase_type == "Free trial":
            config["free"] = {}
        else:
            if intro_price_value in (None, "", "nan"):
                raise ValueError(f"Offer price is required for region {region_code}")
            intro_price = Decimal(str(intro_price_value))
            if price_override_mode == "price":
                config["price"] = _money_from_decimal(intro_price, currency_code)
            else:
                prorated_base = _prorated_base_price(base_price, billing_period_duration, phase_duration)
                if price_override_mode == "absolute_discount":
                    discount_value = prorated_base - intro_price
                    if discount_value < 0:
                        raise ValueError(f"Intro price for {region_code} exceeds the prorated base price.")
                    config["absoluteDiscount"] = _money_from_decimal(discount_value, currency_code)
                else:
                    if prorated_base <= 0:
                        raise ValueError(f"Base price for {region_code} must be greater than zero.")
                    fraction_paid = float(intro_price / prorated_base)
                    if not 0 < fraction_paid < 1:
                        raise ValueError(
                            f"Relative discount for {region_code} must result in a fraction between 0 and 1."
                        )
                    config["relativeDiscount"] = fraction_paid
        configs.append(config)
    return configs


def execute_create(service_account_path: str, payload: Dict[str, Any], regions_version: str) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    result = service.monetization().subscriptions().create(
        packageName=payload["packageName"],
        productId=payload["productId"],
        regionsVersion_version=regions_version,
        body=payload,
    ).execute()

    base_plan_id = payload["basePlans"][0]["basePlanId"]
    activation_result = service.monetization().subscriptions().basePlans().activate(
        packageName=payload["packageName"],
        productId=payload["productId"],
        basePlanId=base_plan_id,
    ).execute()

    return {
        "subscription_create": result,
        "base_plan_activation": activation_result,
    }


def build_offer_payload(
    package_name: str,
    product_id: str,
    base_plan_id: str,
    offer_id: str,
    phase_duration: str,
    recurrence_count: int,
    phase_regional_configs: list[dict[str, Any]],
    offer_tags: list[str],
    targeting: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    regional_configs = [
        {
            "regionCode": item["regionCode"],
            "newSubscriberAvailability": True,
        }
        for item in phase_regional_configs
    ]
    payload: Dict[str, Any] = {
        "packageName": package_name,
        "productId": product_id,
        "basePlanId": base_plan_id,
        "offerId": offer_id,
        "phases": [
            {
                "duration": phase_duration,
                "recurrenceCount": recurrence_count,
                "regionalConfigs": phase_regional_configs,
            }
        ],
        "regionalConfigs": regional_configs,
    }
    if offer_tags:
        payload["offerTags"] = [{"tag": tag} for tag in offer_tags]
    if targeting:
        payload["targeting"] = targeting
    return payload


def execute_offer_create(service_account_path: str, payload: Dict[str, Any], regions_version: str) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    result = service.monetization().subscriptions().basePlans().offers().create(
        packageName=payload["packageName"],
        productId=payload["productId"],
        basePlanId=payload["basePlanId"],
        offerId=payload["offerId"],
        regionsVersion_version=regions_version,
        body=payload,
    ).execute()

    activation_result = service.monetization().subscriptions().basePlans().offers().activate(
        packageName=payload["packageName"],
        productId=payload["productId"],
        basePlanId=payload["basePlanId"],
        offerId=payload["offerId"],
        body={},
    ).execute()

    return {
        "offer_create": result,
        "offer_activation": activation_result,
    }


def load_existing_subscription(
    service_account_path: str,
    package_name: str,
    product_id: str,
    base_plan_id: str,
) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    subscription = service.monetization().subscriptions().get(
        packageName=package_name,
        productId=product_id,
    ).execute()
    offers_response = service.monetization().subscriptions().basePlans().offers().list(
        packageName=package_name,
        productId=product_id,
        basePlanId=base_plan_id,
    ).execute()
    offers = offers_response.get("subscriptionOffers", [])

    matching_base_plan = None
    for candidate in subscription.get("basePlans", []):
        if candidate.get("basePlanId") == base_plan_id:
            matching_base_plan = candidate
            break
    if matching_base_plan is None:
        raise ValueError(f"Base plan {base_plan_id} was not found on subscription {product_id}.")

    return {
        "subscription": subscription,
        "base_plan": matching_base_plan,
        "offers": offers,
    }


def summarize_subscription_diagnostics(
    subscription: Dict[str, Any],
    base_plan: Dict[str, Any],
    offers: list[Dict[str, Any]],
) -> Dict[str, Any]:
    regional_configs = base_plan.get("regionalConfigs", []) or []
    active_regions = [
        item.get("regionCode", "")
        for item in regional_configs
        if item.get("newSubscriberAvailability") is True
    ]
    priced_regions = [
        item.get("regionCode", "")
        for item in regional_configs
        if isinstance(item.get("price"), dict) and item.get("price")
    ]
    auto_renewing = base_plan.get("autoRenewingBasePlanType", {}) or {}
    active_offers = [offer.get("offerId", "") for offer in offers if offer.get("state") == "ACTIVE"]
    draft_offers = [offer.get("offerId", "") for offer in offers if offer.get("state") == "DRAFT"]

    return {
        "product_id": subscription.get("productId", ""),
        "base_plan_id": base_plan.get("basePlanId", ""),
        "base_plan_state": base_plan.get("state", ""),
        "active_region_count": len(active_regions),
        "priced_region_count": len(priced_regions),
        "legacy_compatible": bool(auto_renewing.get("legacyCompatible")),
        "legacy_compatible_offer_id": auto_renewing.get("legacyCompatibleSubscriptionOfferId", ""),
        "listing_count": len(subscription.get("listings", []) or []),
        "active_offer_count": len(active_offers),
        "draft_offer_count": len(draft_offers),
        "purchase_ready_hint": (
            "likely purchasable"
            if base_plan.get("state") == "ACTIVE" and active_regions and priced_regions
            else "needs review"
        ),
    }


def execute_modify_subscription(
    service_account_path: str,
    payload: Dict[str, Any],
    regions_version: str,
) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    result = service.monetization().subscriptions().patch(
        packageName=payload["packageName"],
        productId=payload["productId"],
        regionsVersion_version=regions_version,
        updateMask="listings,basePlans",
        body=payload,
    ).execute()
    return {"subscription_patch": result}


def execute_modify_offer(
    service_account_path: str,
    payload: Dict[str, Any],
    regions_version: str,
) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    result = service.monetization().subscriptions().basePlans().offers().patch(
        packageName=payload["packageName"],
        productId=payload["productId"],
        basePlanId=payload["basePlanId"],
        offerId=payload["offerId"],
        regionsVersion_version=regions_version,
        allowMissing=True,
        updateMask="phases,regionalConfigs,offerTags,targeting",
        body=payload,
    ).execute()

    activation_result = service.monetization().subscriptions().basePlans().offers().activate(
        packageName=payload["packageName"],
        productId=payload["productId"],
        basePlanId=payload["basePlanId"],
        offerId=payload["offerId"],
        body={},
    ).execute()

    return {
        "offer_patch": result,
        "offer_activation": activation_result,
    }


def fetch_google_region_pricing_catalog(service_account_path: str, package_name: str) -> Dict[str, Any]:
    auth_manager = get_auth_manager(service_account_path)
    service = auth_manager.get_authenticated_service()
    return service.monetization().convertRegionPrices(
        packageName=package_name,
        body={
            "price": {
                "currencyCode": "USD",
                "units": "1",
                "nanos": 0,
            }
        },
    ).execute()


def build_bulk_template_bytes(converted_region_prices: Any) -> bytes:
    normalized_prices = _normalize_google_region_prices(converted_region_prices)
    base_records = []
    for item in normalized_prices:
        region_code = item.get("regionCode", "")
        price = item.get("price", {}) if isinstance(item, dict) else {}
        base_records.append(
            {
                "PRODUCT ID": "sub_weekly",
                "BASE PLAN ID": "weekly-plan",
                "TITLE": "Premium Weekly",
                "DESCRIPTION": "Unlock all premium features.",
                "LANGUAGE CODE": "en-US",
                "BILLING PERIOD": "P1W",
                "GRACE PERIOD": "P0D",
                "RESUBSCRIBE STATE": "RESUBSCRIBE_STATE_ACTIVE",
                "OFFER ID": "launch-offer",
                "REGION": region_code,
                "REGION NAME": region_name_from_code(region_code),
                "CURRENCY CODE": price.get("currencyCode", ""),
                "PRICE": "",
                "INTRO PRICE": "",
            }
        )
    if not base_records:
        base_records = [
            {
                "PRODUCT ID": "sub_weekly",
                "BASE PLAN ID": "weekly-plan",
                "TITLE": "Premium Weekly",
                "DESCRIPTION": "Unlock all premium features.",
                "LANGUAGE CODE": "en-US",
                "BILLING PERIOD": "P1W",
                "GRACE PERIOD": "P0D",
                "RESUBSCRIBE STATE": "RESUBSCRIBE_STATE_ACTIVE",
                "OFFER ID": "launch-offer",
                "REGION": "US",
                "REGION NAME": "United States",
                "CURRENCY CODE": "USD",
                "PRICE": "9.99",
                "INTRO PRICE": "4.99",
            }
        ]
    return pd.DataFrame(base_records).to_csv(index=False).encode("utf-8")


def validate_pricing_currencies_against_google_template(
    pricing_df: pd.DataFrame,
    converted_region_prices: Any,
) -> list[dict[str, str]]:
    expected_currency_by_region = build_google_region_currency_map(converted_region_prices)
    if not expected_currency_by_region:
        return []

    normalized = normalize_pricing_dataframe(pricing_df)
    if "REGION" not in normalized.columns or "CURRENCY_CODE" not in normalized.columns:
        return []

    mismatches: list[dict[str, str]] = []
    for index, row in normalized.iterrows():
        region_code = str(row.get("REGION", "")).strip().upper()
        currency_code = str(row.get("CURRENCY_CODE", "")).strip().upper()
        if not region_code or region_code in NON_BILLABLE_REGION_CODES or not currency_code:
            continue
        expected_currency = expected_currency_by_region.get(region_code)
        if expected_currency and expected_currency != currency_code:
            mismatches.append(
                {
                    "row": str(int(index) + 2),
                    "region": region_code,
                    "expected_currency": expected_currency,
                    "actual_currency": currency_code,
                }
            )
    return mismatches


def validate_payload_regions_against_google_template(
    payloads: list[Dict[str, Any]],
    converted_region_prices: Any,
) -> list[dict[str, str]]:
    allowed_regions = set(build_google_region_currency_map(converted_region_prices).keys())
    if not allowed_regions:
        return []

    findings: list[dict[str, str]] = []
    for payload in payloads:
        product_id = payload.get("subscription", {}).get("productId", "")
        sections = [
            ("base_plan", payload.get("subscription", {}).get("basePlans", [{}])[0].get("regionalConfigs", [])),
        ]
        offer = payload.get("offer")
        if offer:
            sections.append(("offer", offer.get("regionalConfigs", [])))
            for index, phase in enumerate(offer.get("phases", []), start=1):
                sections.append((f"offer_phase_{index}", phase.get("regionalConfigs", [])))

        for section_name, configs in sections:
            for config in configs:
                region_code = str(config.get("regionCode", "")).strip().upper()
                if not region_code:
                    continue
                if region_code not in allowed_regions:
                    findings.append(
                        {
                            "product_id": product_id,
                            "section": section_name,
                            "region": region_code,
                        }
                    )
    return findings


def find_non_billable_regions_in_payload(payloads: list[Dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for payload in payloads:
        product_id = payload.get("subscription", {}).get("productId", "")
        for regional_config in payload.get("subscription", {}).get("basePlans", [{}])[0].get("regionalConfigs", []):
            region_code = str(regional_config.get("regionCode", "")).strip().upper()
            if region_code in NON_BILLABLE_REGION_CODES:
                findings.append({"product_id": product_id, "section": "base_plan", "region": region_code})
        offer = payload.get("offer")
        if not offer:
            continue
        for regional_config in offer.get("regionalConfigs", []):
            region_code = str(regional_config.get("regionCode", "")).strip().upper()
            if region_code in NON_BILLABLE_REGION_CODES:
                findings.append({"product_id": product_id, "section": "offer", "region": region_code})
        for phase in offer.get("phases", []):
            for regional_config in phase.get("regionalConfigs", []):
                region_code = str(regional_config.get("regionCode", "")).strip().upper()
                if region_code in NON_BILLABLE_REGION_CODES:
                    findings.append({"product_id": product_id, "section": "offer_phase", "region": region_code})
    return findings


saved_config = load_local_config()
saved_settings = load_local_settings()
service_account_summary = load_service_account_summary(
    saved_config.get("google_play", {}).get("service_account_path", "")
)

with st.expander("Saved Settings", expanded=True):
    with st.form("android_settings"):
        existing_service_account_path = saved_config.get("google_play", {}).get("service_account_path", "")
        if existing_service_account_path:
            st.caption(f"Saved service account file: {existing_service_account_path}")
        service_account_upload = st.file_uploader(
            "Upload Google Service Account JSON",
            type=["json"],
            key="google_service_account_upload",
        )
        package_name = st.text_input(
            "Package Name",
            value=saved_config.get("google_play", {}).get("package_name", DEFAULT_PACKAGE_NAME),
        )
        language_code = st.selectbox(
            "Default Language Code",
            options=["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT"],
            index=["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT"].index(
                saved_settings.get("language_code", DEFAULT_LANGUAGE)
            ),
        )
        save_clicked = st.form_submit_button("Save Settings", use_container_width=True)
        if save_clicked:
            stored_path = existing_service_account_path
            if service_account_upload is not None:
                stored_path = save_uploaded_service_account(service_account_upload)
            if not stored_path:
                raise ValueError("Upload the Google service account JSON file before saving settings.")
            save_json_file(
                LOCAL_CONFIG_PATH,
                {
                    "google_play": {
                        "service_account_path": stored_path,
                        "package_name": package_name.strip(),
                    }
                },
            )
            save_json_file(
                LOCAL_SETTINGS_PATH,
                {
                    "language_code": language_code,
                },
            )
            st.success("Settings saved locally.")
            saved_config = load_local_config()
            saved_settings = load_local_settings()
            service_account_summary = load_service_account_summary(stored_path)

if service_account_summary:
    info_col1, info_col2 = st.columns(2)
    info_col1.caption(f"Service account: {service_account_summary.get('client_email', '')}")
    info_col2.caption(f"Project ID: {service_account_summary.get('project_id', '')}")

if st.button("Test Google Play Authentication", use_container_width=True):
    try:
        manager = get_auth_manager(saved_config["google_play"]["service_account_path"])
        service = manager.get_authenticated_service()
        _ = service.monetization()
        st.success("Authenticated successfully with Google Play Android Publisher API.")
    except Exception as exc:
        st.error(str(exc))

if (
    saved_config.get("google_play", {}).get("service_account_path")
    and saved_config.get("google_play", {}).get("package_name")
):
    if st.button("Load Google Region Template", use_container_width=True):
        try:
            catalog = fetch_google_region_pricing_catalog(
                service_account_path=saved_config["google_play"]["service_account_path"],
                package_name=saved_config["google_play"]["package_name"],
            )
            st.session_state["google_region_pricing_catalog"] = catalog
            st.session_state.pop("android_preview_payloads", None)
            converted = _normalize_google_region_prices(catalog.get("convertedRegionPrices", []))
            region_version = catalog.get("regionVersion", {})
            st.success(f"Loaded {len(converted)} Google Play regions.")
            if region_version:
                st.caption(f"Region version: {region_version}")
        except Exception as exc:
            st.error(str(exc))

st.subheader("Create Subscriptions")
create_mode = st.radio("Create Mode", ["Single SKU", "Bulk Create"], horizontal=True)

st.markdown("**Regional Pricing Source**")
pricing_source = st.radio(
    "Choose input method",
    options=["CSV / Excel Upload", "Google Sheet URL"],
    horizontal=True,
    key="android_pricing_source",
)
template_data = (
    build_bulk_template_bytes(st.session_state.get("google_region_pricing_catalog", {}).get("convertedRegionPrices", []))
    if create_mode == "Bulk Create"
    else (
        build_pricing_template_from_google_prices(
            st.session_state.get("google_region_pricing_catalog", {}).get("convertedRegionPrices", [])
        )
        if st.session_state.get("google_region_pricing_catalog")
        else build_pricing_template_bytes()
    )
)
template_name = "bulk_subscription_template.csv" if create_mode == "Bulk Create" else "regional_pricing_template.csv"
st.download_button(
    "Download Pricing CSV Template",
    data=template_data,
    file_name=template_name,
    mime="text/csv",
    use_container_width=True,
)

pricing_df = None
if pricing_source == "CSV / Excel Upload":
    pricing_upload = st.file_uploader(
        "Upload Pricing File",
        type=["csv", "xlsx", "xls"],
        help=(
            "Single SKU: REGION, CURRENCY CODE, PRICE, optional INTRO PRICE. "
            "Bulk: PRODUCT ID, BASE PLAN ID, TITLE, DESCRIPTION, REGION, CURRENCY CODE, PRICE, optional OFFER ID and INTRO PRICE."
        ),
    )
    if pricing_upload is not None:
        try:
            pricing_df = load_pricing_dataframe_from_upload(pricing_upload)
            st.success(f"Loaded {len(pricing_df)} pricing rows.")
        except Exception as exc:
            st.error(str(exc))
else:
    google_sheet_url = st.text_input(
        "Google Sheet URL or Sheet ID",
        help="The sheet must be accessible for export as CSV.",
    )
    if google_sheet_url.strip():
        try:
            pricing_df = load_pricing_dataframe_from_google_sheet(google_sheet_url.strip())
            st.success(f"Loaded {len(pricing_df)} pricing rows from Google Sheets.")
        except Exception as exc:
            st.error(str(exc))

default_language_code = st.selectbox(
    "Default Language Code",
    options=["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT"],
    index=["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT"].index(
        saved_settings.get("language_code", DEFAULT_LANGUAGE)
    ),
    key="create_language_code",
)

default_col1, default_col2, default_col3 = st.columns(3)
default_billing_period = default_col1.selectbox(
    "Default Billing Period",
    options=["P1W", "P1M", "P3M", "P6M", "P1Y"],
    index=["P1W", "P1M", "P3M", "P6M", "P1Y"].index(DEFAULT_BILLING_PERIOD),
)
default_grace_period = default_col2.selectbox(
    "Default Grace Period",
    options=["P0D", "P3D", "P7D", "P14D"],
    index=["P0D", "P3D", "P7D", "P14D"].index(DEFAULT_GRACE_PERIOD),
)
default_resubscribe_state = default_col3.selectbox(
    "Default Resubscribe State",
    options=["RESUBSCRIBE_STATE_ACTIVE", "RESUBSCRIBE_STATE_INACTIVE"],
    index=["RESUBSCRIBE_STATE_ACTIVE", "RESUBSCRIBE_STATE_INACTIVE"].index(DEFAULT_RESUBSCRIBE_STATE),
)
legacy_col1, legacy_col2 = st.columns(2)
default_legacy_compatible = legacy_col1.checkbox(
    "Legacy-compatible base plan",
    value=False,
    help="Enable this only if the mobile app still uses the deprecated Billing Library querySkuDetailsAsync() path.",
)
default_legacy_offer_id = legacy_col2.text_input(
    "Legacy-compatible Offer ID",
    help="Optional. Used only when Google should expose a specific offer to deprecated Billing Library clients.",
)

single_sku_inputs: Dict[str, Any] = {}
if create_mode == "Single SKU":
    product_id = st.text_input("Product ID")
    base_plan_id = st.text_input("Base Plan ID")
    title = st.text_input("Title")
    description = st.text_area("Description", height=100)
    single_sku_inputs = {
        "product_id": product_id.strip(),
        "base_plan_id": base_plan_id.strip(),
        "title": title.strip(),
        "description": description.strip(),
    }

st.markdown("**Optional Offer**")
create_offer = st.checkbox("Create an offer on this base plan", value=False)
if create_offer:
    offer_col1, offer_col2, offer_col3 = st.columns(3)
    fallback_offer_id = offer_col1.text_input(
        "Fallback Offer ID",
        help="Used for single SKU, or for bulk rows that do not provide OFFER ID.",
    )
    offer_phase_type = offer_col2.selectbox("Offer Phase Type", options=PHASE_TYPE_OPTIONS)
    offer_phase_duration = offer_col3.selectbox(
        "Offer Phase Duration",
        options=["P1W", "P1M", "P3M", "P6M", "P1Y"],
        index=0,
    )
    offer_col4, offer_col5 = st.columns(2)
    offer_recurrence_count = offer_col4.number_input(
        "Offer Recurrence Count",
        min_value=1,
        max_value=52,
        value=1,
        step=1,
        help="For single payment this should usually stay at 1.",
    )
    price_override_label = offer_col5.selectbox(
        "Price Override",
        options=list(PRICE_OVERRIDE_OPTIONS.keys()),
        disabled=offer_phase_type == "Free trial",
    )
    offer_tags_raw = st.text_input("Offer Tags", help="Optional comma-separated tags.")

    eligibility_label = st.selectbox("Eligibility Criteria", options=list(ELIGIBILITY_OPTIONS.keys()))
    eligibility_mode = ELIGIBILITY_OPTIONS[eligibility_label]
    acquisition_scope_mode = ACQUISITION_SCOPE_OPTIONS["This subscription"]
    upgrade_scope_mode = UPGRADE_SCOPE_OPTIONS["This subscription"]
    specific_subscription_id = ""
    once_per_user = False
    if eligibility_mode == "acquisition":
        acquisition_scope_label = st.selectbox("Acquisition Scope", options=list(ACQUISITION_SCOPE_OPTIONS.keys()))
        acquisition_scope_mode = ACQUISITION_SCOPE_OPTIONS[acquisition_scope_label]
    elif eligibility_mode == "upgrade":
        upgrade_scope_label = st.selectbox("Upgrade Scope", options=list(UPGRADE_SCOPE_OPTIONS.keys()))
        upgrade_scope_mode = UPGRADE_SCOPE_OPTIONS[upgrade_scope_label]
        if upgrade_scope_mode == "specific":
            specific_subscription_id = st.text_input("Specific Subscription ID")
        once_per_user = st.checkbox("Limit offer to once per user", value=False)
else:
    fallback_offer_id = ""
    offer_phase_type = "Free trial"
    offer_phase_duration = "P1W"
    offer_recurrence_count = 1
    price_override_label = "Custom price"
    offer_tags_raw = ""
    eligibility_mode = "developer"
    acquisition_scope_mode = "this"
    upgrade_scope_mode = "this"
    specific_subscription_id = ""
    once_per_user = False

preview_clicked = st.button("Preview Payload", use_container_width=True)
if preview_clicked:
    try:
        if not saved_config.get("google_play", {}).get("service_account_path"):
            raise ValueError("Save the Google service account settings first.")
        if not saved_config.get("google_play", {}).get("package_name"):
            raise ValueError("Save the package name first.")
        if pricing_df is None:
            raise ValueError("Provide pricing via upload or Google Sheet.")
        region_version = (
            st.session_state.get("google_region_pricing_catalog", {}).get("regionVersion", {}).get("version")
        )
        if not region_version:
            raise ValueError("Load Google Region Template first. A live Google regions version is required.")
        currency_mismatches = validate_pricing_currencies_against_google_template(
            pricing_df,
            st.session_state.get("google_region_pricing_catalog", {}).get("convertedRegionPrices", []),
        )
        if currency_mismatches:
            mismatch_preview = ", ".join(
                f"row {item['row']} {item['region']} expected {item['expected_currency']} got {item['actual_currency']}"
                for item in currency_mismatches[:10]
            )
            if len(currency_mismatches) > 10:
                mismatch_preview += f" (+{len(currency_mismatches) - 10} more)"
            st.warning(
                "Currency mismatches found against the loaded Google region template. "
                "The Google create API remains the source of truth, so preview will continue: "
                f"{mismatch_preview}"
            )

        preview_payloads: list[Dict[str, Any]] = []
        price_adjustments: list[dict[str, str]] = []
        if create_mode == "Single SKU":
            if not single_sku_inputs["product_id"]:
                raise ValueError("Product ID is required.")
            if not single_sku_inputs["base_plan_id"]:
                raise ValueError("Base Plan ID is required.")
            if not single_sku_inputs["title"]:
                raise ValueError("Title is required.")
            if not single_sku_inputs["description"]:
                raise ValueError("Description is required.")
        else:
            bulk_rows = parse_bulk_catalog_rows(
                pricing_df,
                default_language_code=default_language_code,
                default_billing_period=default_billing_period,
                default_grace_period=default_grace_period,
                default_resubscribe_state=default_resubscribe_state,
            )
            for item in bulk_rows:
                normalized_pricing_rows, row_adjustments = normalize_pricing_rows_for_currency(
                    item["pricing_rows"],
                    product_id=item["product_id"],
                )
                item["pricing_rows"] = normalized_pricing_rows
                price_adjustments.extend(row_adjustments)
                regional_configs = [
                    {
                        "regionCode": row["regionCode"],
                        "price": _money_from_decimal(row["basePrice"], row["currencyCode"]),
                        "newSubscriberAvailability": True,
                    }
                    for row in item["pricing_rows"]
                ]
                subscription_payload = build_preview_payload(
                    package_name=saved_config["google_play"]["package_name"],
                    product_id=item["product_id"],
                    base_plan_id=item["base_plan_id"],
                    title=item["title"],
                    description=item["description"],
                    language_code=item["language_code"],
                    billing_period_duration=item["billing_period_duration"],
                    grace_period_duration=item["grace_period_duration"],
                    resubscribe_state=item["resubscribe_state"],
                    regional_configs=regional_configs,
                    legacy_compatible=default_legacy_compatible,
                    legacy_compatible_offer_id=default_legacy_offer_id,
                )
                payload_entry: Dict[str, Any] = {"subscription": subscription_payload}
                if create_offer and (item.get("offer_id") or fallback_offer_id.strip()):
                    targeting = build_offer_targeting(
                        eligibility_mode=eligibility_mode,
                        acquisition_scope_mode=acquisition_scope_mode,
                        upgrade_scope_mode=upgrade_scope_mode,
                        specific_subscription_id=specific_subscription_id,
                        once_per_user=once_per_user,
                    )
                    phase_configs = build_offer_phase_regional_configs(
                        pricing_rows=item["pricing_rows"],
                        phase_type=offer_phase_type,
                        price_override_mode=PRICE_OVERRIDE_OPTIONS[price_override_label],
                        billing_period_duration=item["billing_period_duration"],
                        phase_duration=offer_phase_duration,
                    )
                    payload_entry["offer"] = build_offer_payload(
                        package_name=saved_config["google_play"]["package_name"],
                        product_id=item["product_id"],
                        base_plan_id=item["base_plan_id"],
                        offer_id=item.get("offer_id") or fallback_offer_id.strip(),
                        phase_duration=offer_phase_duration,
                        recurrence_count=1 if offer_phase_type in {"Free trial", "Single payment"} else int(offer_recurrence_count),
                        phase_regional_configs=phase_configs,
                        offer_tags=[tag.strip() for tag in offer_tags_raw.split(",") if tag.strip()],
                        targeting=targeting,
                    )
                preview_payloads.append(payload_entry)

        if create_mode == "Single SKU":
            normalized_pricing = normalize_pricing_dataframe(pricing_df)
            required_columns = {"REGION", "CURRENCY_CODE", "PRICE"}
            missing_columns = sorted(required_columns - set(normalized_pricing.columns))
            if missing_columns:
                raise ValueError(f"Missing required pricing columns: {missing_columns}")
                pricing_rows = []
                for _, row in normalized_pricing.iterrows():
                    region_code = str(row.get("REGION", "")).strip().upper()
                    if not region_code or region_code == "NAN":
                        continue
                    pricing_rows.append(
                        {
                            "regionCode": region_code,
                            "currencyCode": str(row.get("CURRENCY_CODE", "")).strip().upper(),
                            "basePrice": str(row.get("PRICE", "")).strip(),
                            "introPrice": "" if pd.isna(row.get("INTRO_PRICE")) else str(row.get("INTRO_PRICE", "")).strip(),
                        }
                    )
            regional_configs = [
                {
                    "regionCode": row["regionCode"],
                    "price": _money_from_decimal(row["basePrice"], row["currencyCode"]),
                    "newSubscriberAvailability": True,
                }
                for row in pricing_rows
            ]
            pricing_rows, row_adjustments = normalize_pricing_rows_for_currency(
                pricing_rows,
                product_id=single_sku_inputs["product_id"],
            )
            price_adjustments.extend(row_adjustments)
            regional_configs = [
                {
                    "regionCode": row["regionCode"],
                    "price": _money_from_decimal(row["basePrice"], row["currencyCode"]),
                    "newSubscriberAvailability": True,
                }
                for row in pricing_rows
            ]
            subscription_payload = build_preview_payload(
                package_name=saved_config["google_play"]["package_name"],
                product_id=single_sku_inputs["product_id"],
                base_plan_id=single_sku_inputs["base_plan_id"],
                title=single_sku_inputs["title"],
                description=single_sku_inputs["description"],
                language_code=default_language_code,
                billing_period_duration=default_billing_period,
                grace_period_duration=default_grace_period,
                resubscribe_state=default_resubscribe_state,
                regional_configs=regional_configs,
                legacy_compatible=default_legacy_compatible,
                legacy_compatible_offer_id=default_legacy_offer_id,
            )
            payload_entry = {"subscription": subscription_payload}
            if create_offer:
                if not fallback_offer_id.strip():
                    raise ValueError("Fallback Offer ID is required for single SKU offer creation.")
                targeting = build_offer_targeting(
                    eligibility_mode=eligibility_mode,
                    acquisition_scope_mode=acquisition_scope_mode,
                    upgrade_scope_mode=upgrade_scope_mode,
                    specific_subscription_id=specific_subscription_id,
                    once_per_user=once_per_user,
                )
                phase_configs = build_offer_phase_regional_configs(
                    pricing_rows=pricing_rows,
                    phase_type=offer_phase_type,
                    price_override_mode=PRICE_OVERRIDE_OPTIONS[price_override_label],
                    billing_period_duration=default_billing_period,
                    phase_duration=offer_phase_duration,
                )
                payload_entry["offer"] = build_offer_payload(
                    package_name=saved_config["google_play"]["package_name"],
                    product_id=single_sku_inputs["product_id"],
                    base_plan_id=single_sku_inputs["base_plan_id"],
                    offer_id=fallback_offer_id.strip(),
                    phase_duration=offer_phase_duration,
                    recurrence_count=1 if offer_phase_type in {"Free trial", "Single payment"} else int(offer_recurrence_count),
                    phase_regional_configs=phase_configs,
                    offer_tags=[tag.strip() for tag in offer_tags_raw.split(",") if tag.strip()],
                    targeting=targeting,
                )
            preview_payloads.append(payload_entry)

        region_version_payload = st.session_state["google_region_pricing_catalog"]["regionVersion"]
        for entry in preview_payloads:
            entry["regionsVersion"] = region_version_payload

        non_billable_findings = find_non_billable_regions_in_payload(preview_payloads)
        if non_billable_findings:
            finding_preview = ", ".join(
                f"{item['product_id']}:{item['section']}:{item['region']}" for item in non_billable_findings[:10]
            )
            if len(non_billable_findings) > 10:
                finding_preview += f" (+{len(non_billable_findings) - 10} more)"
            raise ValueError(
                "Non-billable regions were found in the generated payload and were blocked before execute: "
                f"{finding_preview}"
            )
        invalid_region_findings = validate_payload_regions_against_google_template(
            preview_payloads,
            st.session_state["google_region_pricing_catalog"]["convertedRegionPrices"],
        )
        if invalid_region_findings:
            finding_preview = ", ".join(
                f"{item['product_id']}:{item['section']}:{item['region']}" for item in invalid_region_findings[:10]
            )
            if len(invalid_region_findings) > 10:
                finding_preview += f" (+{len(invalid_region_findings) - 10} more)"
            raise ValueError(
                "Payload contains regions that are not present in the loaded Google billable region template: "
                f"{finding_preview}"
            )

        st.session_state["android_preview_payloads"] = preview_payloads
        st.session_state["android_price_adjustments"] = price_adjustments
        if price_adjustments:
            st.warning(
                f"Automatically rounded {len(price_adjustments)} price value(s) to the nearest billable unit "
                "for their currency."
            )
            st.dataframe(pd.DataFrame(price_adjustments), use_container_width=True)
        st.json(preview_payloads if create_mode == "Bulk Create" else preview_payloads[0])
    except Exception as exc:
        st.error(str(exc))

execute_clicked = st.button("Execute Create Subscription", type="primary", use_container_width=True)
if execute_clicked:
    try:
        preview_payloads = st.session_state.get("android_preview_payloads")
        if not preview_payloads:
            raise ValueError("Preview the payload first.")
        current_region_version = (
            st.session_state.get("google_region_pricing_catalog", {}).get("regionVersion", {}).get("version")
        )
        if not current_region_version:
            raise ValueError("Load Google Region Template first. Execute requires a live Google regions version.")
        preview_region_versions = {
            entry.get("regionsVersion", {}).get("version")
            for entry in preview_payloads
            if entry.get("regionsVersion", {}).get("version")
        }
        if preview_region_versions != {current_region_version}:
            raise ValueError(
                "The preview payload is stale compared with the currently loaded Google region template. "
                "Preview again before executing."
            )
        results = []
        diagnostics = []
        for entry in preview_payloads:
            subscription_payload = entry["subscription"]
            regions_version = entry["regionsVersion"]["version"]
            result = execute_create(
                service_account_path=saved_config["google_play"]["service_account_path"],
                payload=subscription_payload,
                regions_version=regions_version,
            )
            if entry.get("offer"):
                result["offer"] = execute_offer_create(
                    service_account_path=saved_config["google_play"]["service_account_path"],
                    payload=entry["offer"],
                    regions_version=regions_version,
                )
            results.append(
                {
                    "product_id": subscription_payload["productId"],
                    "base_plan_id": subscription_payload["basePlans"][0]["basePlanId"],
                    "result": result,
                }
            )
            live_loaded = load_existing_subscription(
                service_account_path=saved_config["google_play"]["service_account_path"],
                package_name=subscription_payload["packageName"],
                product_id=subscription_payload["productId"],
                base_plan_id=subscription_payload["basePlans"][0]["basePlanId"],
            )
            diagnostics.append(
                summarize_subscription_diagnostics(
                    subscription=live_loaded["subscription"],
                    base_plan=live_loaded["base_plan"],
                    offers=live_loaded["offers"],
                )
            )
        st.success(f"Processed {len(results)} subscription payload(s) successfully.")
        st.info(
            "Legacy compatibility and legacy price points affect existing subscriber cohorts. "
            "They are not required for a new purchase to start."
        )
        if diagnostics:
            st.dataframe(pd.DataFrame(diagnostics), use_container_width=True)
        st.json(results if create_mode == "Bulk Create" else results[0])
    except Exception as exc:
        st.error(str(exc))

st.divider()
st.subheader("Modify / Repair Subscription")
modify_product_id = st.text_input("Existing Product ID", key="modify_product_id")
modify_base_plan_id = st.text_input("Existing Base Plan ID", key="modify_base_plan_id")

if st.button("Load Existing Subscription", use_container_width=True):
    try:
        if not saved_config.get("google_play", {}).get("service_account_path"):
            raise ValueError("Save the Google service account settings first.")
        if not saved_config.get("google_play", {}).get("package_name"):
            raise ValueError("Save the package name first.")
        if not modify_product_id.strip() or not modify_base_plan_id.strip():
            raise ValueError("Existing product ID and base plan ID are required.")
        loaded = load_existing_subscription(
            service_account_path=saved_config["google_play"]["service_account_path"],
            package_name=saved_config["google_play"]["package_name"],
            product_id=modify_product_id.strip(),
            base_plan_id=modify_base_plan_id.strip(),
        )
        st.session_state["android_modify_loaded"] = loaded
        st.session_state.pop("android_modify_preview", None)
        st.success("Existing subscription loaded.")
    except Exception as exc:
        st.error(str(exc))

loaded_modify = st.session_state.get("android_modify_loaded")
if loaded_modify:
    loaded_subscription = loaded_modify["subscription"]
    loaded_base_plan = loaded_modify["base_plan"]
    loaded_offers = loaded_modify["offers"]
    loaded_listing = (loaded_subscription.get("listings") or [{}])[0]

    info_col1, info_col2, info_col3 = st.columns(3)
    info_col1.caption(f"Loaded subscription: {loaded_subscription.get('productId', '')}")
    info_col2.caption(f"Base plan state: {loaded_base_plan.get('state', '')}")
    info_col3.caption(f"Offers found: {len(loaded_offers)}")
    st.dataframe(
        pd.DataFrame(
            [
                summarize_subscription_diagnostics(
                    subscription=loaded_subscription,
                    base_plan=loaded_base_plan,
                    offers=loaded_offers,
                )
            ]
        ),
        use_container_width=True,
    )

    modify_title = st.text_input("Title", value=loaded_listing.get("title", ""), key="modify_title")
    modify_description = st.text_area(
        "Description",
        height=100,
        value=loaded_listing.get("description", ""),
        key="modify_description",
    )
    modify_language_code = st.text_input(
        "Language Code",
        value=loaded_listing.get("languageCode", default_language_code),
        key="modify_language_code",
    )
    loaded_auto_renewing = loaded_base_plan.get("autoRenewingBasePlanType", {}) or {}
    modify_legacy_col1, modify_legacy_col2 = st.columns(2)
    modify_legacy_compatible = modify_legacy_col1.checkbox(
        "Legacy-compatible base plan",
        value=bool(loaded_auto_renewing.get("legacyCompatible")),
        key="modify_legacy_compatible",
        help="Use this only if the app still queries subscriptions through the deprecated Billing Library querySkuDetailsAsync() path.",
    )
    modify_legacy_offer_id = modify_legacy_col2.text_input(
        "Legacy-compatible Offer ID",
        value=loaded_auto_renewing.get("legacyCompatibleSubscriptionOfferId", ""),
        key="modify_legacy_offer_id",
    )

    modify_pricing_source = st.radio(
        "Modify Pricing Source",
        options=["CSV / Excel Upload", "Google Sheet URL"],
        horizontal=True,
        key="android_modify_pricing_source",
    )
    modify_pricing_df = None
    if modify_pricing_source == "CSV / Excel Upload":
        modify_upload = st.file_uploader(
            "Upload Modify Pricing File",
            type=["csv", "xlsx", "xls"],
            key="modify_upload",
        )
        if modify_upload is not None:
            try:
                modify_pricing_df = load_pricing_dataframe_from_upload(modify_upload)
                st.success(f"Loaded {len(modify_pricing_df)} modify pricing rows.")
            except Exception as exc:
                st.error(str(exc))
    else:
        modify_sheet_url = st.text_input("Modify Google Sheet URL or Sheet ID", key="modify_sheet_url")
        if modify_sheet_url.strip():
            try:
                modify_pricing_df = load_pricing_dataframe_from_google_sheet(modify_sheet_url.strip())
                st.success(f"Loaded {len(modify_pricing_df)} modify pricing rows from Google Sheets.")
            except Exception as exc:
                st.error(str(exc))

    modify_offer_enabled = st.checkbox("Repair or update offer too", value=bool(loaded_offers), key="modify_offer_enabled")
    modify_offer_id_default = loaded_offers[0].get("offerId", "") if loaded_offers else ""
    modify_offer_id = st.text_input("Offer ID", value=modify_offer_id_default, key="modify_offer_id")

    modify_preview_clicked = st.button("Preview Modify", use_container_width=True)
    if modify_preview_clicked:
        try:
            region_version = st.session_state.get("google_region_pricing_catalog", {}).get("regionVersion", {}).get("version")
            if not region_version:
                raise ValueError("Load Google Region Template first. A live Google regions version is required.")
            if modify_pricing_df is None:
                raise ValueError("Provide modify pricing via upload or Google Sheet.")

            normalized = normalize_pricing_dataframe(modify_pricing_df)
            required_columns = {"REGION", "CURRENCY_CODE", "PRICE"}
            missing_columns = sorted(required_columns - set(normalized.columns))
            if missing_columns:
                raise ValueError(f"Missing required pricing columns: {missing_columns}")

            pricing_rows = []
            for _, row in normalized.iterrows():
                region_code = str(row.get("REGION", "")).strip().upper()
                if not region_code or region_code == "NAN":
                    continue
                pricing_rows.append(
                    {
                        "regionCode": region_code,
                        "currencyCode": str(row.get("CURRENCY_CODE", "")).strip().upper(),
                        "basePrice": str(row.get("PRICE", "")).strip(),
                        "introPrice": "" if pd.isna(row.get("INTRO_PRICE")) else str(row.get("INTRO_PRICE", "")).strip(),
                    }
                )
            pricing_rows, modify_adjustments = normalize_pricing_rows_for_currency(
                pricing_rows,
                product_id=loaded_subscription["productId"],
            )
            regional_configs = [
                {
                    "regionCode": row["regionCode"],
                    "price": _money_from_decimal(row["basePrice"], row["currencyCode"]),
                    "newSubscriberAvailability": True,
                }
                for row in pricing_rows
            ]
            modify_payload = build_preview_payload(
                package_name=saved_config["google_play"]["package_name"],
                product_id=loaded_subscription["productId"],
                base_plan_id=loaded_base_plan["basePlanId"],
                title=modify_title.strip(),
                description=modify_description.strip(),
                language_code=modify_language_code.strip() or default_language_code,
                billing_period_duration=loaded_base_plan.get("autoRenewingBasePlanType", {}).get("billingPeriodDuration", default_billing_period),
                grace_period_duration=loaded_base_plan.get("autoRenewingBasePlanType", {}).get("gracePeriodDuration", default_grace_period),
                resubscribe_state=loaded_base_plan.get("autoRenewingBasePlanType", {}).get("resubscribeState", default_resubscribe_state),
                regional_configs=regional_configs,
                legacy_compatible=modify_legacy_compatible,
                legacy_compatible_offer_id=modify_legacy_offer_id,
            )
            preview_bundle: Dict[str, Any] = {
                "subscription": modify_payload,
                "regionsVersion": st.session_state["google_region_pricing_catalog"]["regionVersion"],
                "priceAdjustments": modify_adjustments,
            }
            if modify_offer_enabled and modify_offer_id.strip():
                targeting = loaded_offers[0].get("targeting") if loaded_offers else None
                offer_tags = loaded_offers[0].get("offerTags", []) if loaded_offers else []
                phase_duration = (loaded_offers[0].get("phases") or [{}])[0].get("duration", "P1W") if loaded_offers else "P1W"
                recurrence_count = (loaded_offers[0].get("phases") or [{}])[0].get("recurrenceCount", 1) if loaded_offers else 1
                phase_configs = build_offer_phase_regional_configs(
                    pricing_rows=pricing_rows,
                    phase_type="Free trial" if all(not row.get("introPrice") or row.get("introPrice") == "0" for row in pricing_rows) else "Single payment",
                    price_override_mode="price",
                    billing_period_duration=loaded_base_plan.get("autoRenewingBasePlanType", {}).get("billingPeriodDuration", default_billing_period),
                    phase_duration=phase_duration,
                )
                preview_bundle["offer"] = build_offer_payload(
                    package_name=saved_config["google_play"]["package_name"],
                    product_id=loaded_subscription["productId"],
                    base_plan_id=loaded_base_plan["basePlanId"],
                    offer_id=modify_offer_id.strip(),
                    phase_duration=phase_duration,
                    recurrence_count=int(recurrence_count or 1),
                    phase_regional_configs=phase_configs,
                    offer_tags=offer_tags,
                    targeting=targeting,
                )
            st.session_state["android_modify_preview"] = preview_bundle
            if modify_adjustments:
                st.warning(f"Automatically rounded {len(modify_adjustments)} modify price value(s).")
                st.dataframe(pd.DataFrame(modify_adjustments), use_container_width=True)
            st.json(preview_bundle)
        except Exception as exc:
            st.error(str(exc))

    if st.button("Execute Modify / Repair", type="primary", use_container_width=True):
        try:
            preview_bundle = st.session_state.get("android_modify_preview")
            if not preview_bundle:
                raise ValueError("Preview the modify payload first.")
            current_region_version = st.session_state.get("google_region_pricing_catalog", {}).get("regionVersion", {}).get("version")
            if not current_region_version:
                raise ValueError("Load Google Region Template first.")
            if preview_bundle.get("regionsVersion", {}).get("version") != current_region_version:
                raise ValueError("Modify preview is stale. Preview again before executing.")
            result = execute_modify_subscription(
                service_account_path=saved_config["google_play"]["service_account_path"],
                payload=preview_bundle["subscription"],
                regions_version=current_region_version,
            )
            if preview_bundle.get("offer"):
                result["offer"] = execute_modify_offer(
                    service_account_path=saved_config["google_play"]["service_account_path"],
                    payload=preview_bundle["offer"],
                    regions_version=current_region_version,
                )
            st.success("Modify / repair completed successfully.")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
