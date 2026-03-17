# Google Play SKU Dashboard

Local dashboard for creating and repairing Google Play subscription SKUs.

It helps people who spend too much time doing repetitive Google Play Console work:
- growth teams
- monetization managers
- app operators
- developers who manage subscription catalogs

The main value is simple: instead of setting up subscriptions, base plans, regional prices, and offers by hand in Google Play Console, you can prepare the input once, preview it, and run it from one place.

If you want to hand this repo to a coding agent, use `AGENTS.md`. It explains the project structure, workflow, and guardrails so an agent can work on it end to end.

## What It Does

- create subscriptions and base plans
- create multiple SKUs in one run
- upload regional pricing from CSV, Excel, or Google Sheets
- create offers
- modify or repair existing subscriptions
- show live diagnostics after create

## Why It Helps

This tool reduces the amount of manual setup work in Google Play Console.

It is useful when you need to:
- launch many SKUs
- manage many countries
- apply the same pricing workflow repeatedly
- repair partial failures without starting over

## How It Works

The dashboard uses the Google Play Android Publisher API from your machine.

Your credentials stay local:
- `config.local.json`
- `dashboard_settings.local.json`
- `secrets/`

These files should not be committed.

## Quickstart

```bash
cd googleplay-sku-dashboard
python3 -m pip install -r requirements.txt
python3 -m streamlit run dashboard_android.py
```

## What You Need

- Python 3.9+
- a Google service account JSON key
- Android Publisher API enabled in Google Cloud
- that service account added in Google Play Console with the right permissions
- your Android app package name

## First Run

1. Open the dashboard.
2. Upload the Google service account JSON.
3. Enter the package name.
4. Save settings.
5. Click `Load Google Region Template`.
6. Download the pricing template.
7. Fill the file and preview before execute.

## Pricing Inputs

You can use:
- CSV upload
- Excel upload
- Google Sheet URL or sheet ID

### Single SKU format

Required columns:
- `REGION`
- `CURRENCY CODE`
- `PRICE`

Optional:
- `INTRO PRICE`

Example:

```csv
REGION,CURRENCY CODE,PRICE,INTRO PRICE
US,USD,2.99,1.49
GB,GBP,2.99,1.49
DE,EUR,2.99,1.49
```

### Bulk create format

Required columns:
- `PRODUCT ID`
- `BASE PLAN ID`
- `TITLE`
- `DESCRIPTION`
- `REGION`
- `CURRENCY CODE`
- `PRICE`

Optional but useful:
- `LANGUAGE CODE`
- `BILLING PERIOD`
- `GRACE PERIOD`
- `RESUBSCRIBE STATE`
- `OFFER ID`
- `INTRO PRICE`
- `REGION NAME`

Example:

```csv
PRODUCT ID,BASE PLAN ID,TITLE,DESCRIPTION,LANGUAGE CODE,BILLING PERIOD,GRACE PERIOD,RESUBSCRIBE STATE,OFFER ID,REGION,REGION NAME,CURRENCY CODE,PRICE,INTRO PRICE
com.example.premium.weekly.offer.1,weekly-plan-1,Weekly Offer,Unlock all premium features.,en-US,P1W,P0D,RESUBSCRIBE_STATE_ACTIVE,launch-offer,US,United States,USD,2.99,1.49
com.example.premium.weekly.offer.1,weekly-plan-1,Weekly Offer,Unlock all premium features.,en-US,P1W,P0D,RESUBSCRIBE_STATE_ACTIVE,launch-offer,DE,Germany,EUR,2.99,1.49
com.example.premium.weekly.offer.2,weekly-plan-2,Weekly Offer,Unlock all premium features.,en-US,P1W,P0D,RESUBSCRIBE_STATE_ACTIVE,launch-offer,US,United States,USD,3.99,1.99
```

## Recommended Workflow

For a new SKU:
1. Load the Google region template.
2. Download the pricing template.
3. Fill prices.
4. Preview.
5. Execute.
6. Check the diagnostics table after create.

For an existing SKU:
1. Load the existing product ID and base plan ID.
2. Upload a pricing file or Google Sheet.
3. Preview the modify payload.
4. Execute modify or repair.

## Notes

- The Google region template gives the dashboard the current `regionsVersion`.
- The dashboard rounds prices automatically when Google requires billable precision.
- The create flow shows a diagnostics table after execution so you can quickly see base plan state, active regions, and legacy-compatible settings.
- `packageName` is the app ID.
- `productId` is the subscription SKU inside that app.

## Troubleshooting

If the product price shows in the app but purchase does not start, the problem is often app-side purchase logic rather than SKU creation.

Common things to check:
- whether the app uses the correct product ID
- whether the app is using old or new Billing Library query methods
- whether the selected offer/base plan is valid for that user
- whether the tester account setup is correct

## Files

- `dashboard_android.py`: Streamlit UI
- `pricing_utils.py`: pricing import and normalization
- `auth_manager.py`: authentication helpers
- `subscription_client.py`: lower-level Google Play API client
- `subscription_manager.py`: orchestration layer from the older project

## Verification

```bash
python3 -m py_compile dashboard_android.py pricing_utils.py auth_manager.py subscription_client.py subscription_manager.py data_models.py excel_processor.py
```
