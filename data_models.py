"""
Data models and validation for subscription management system.

This module contains the core data structures used throughout the subscription
management system, including validation logic for subscription parameters
and country pricing data.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import re
from decimal import Decimal, InvalidOperation


@dataclass
class CountryPricing:
    """Represents pricing information for a specific country."""
    country_code: str  # ISO 3166-1 alpha-2
    currency_code: str  # ISO 4217
    price_micros: int  # Price in micros (1,000,000 = 1 unit)

    def __post_init__(self):
        """Validate country pricing data after initialization."""
        if not self.country_code or len(self.country_code) != 2:
            raise ValueError("Country code must be a 2-character ISO 3166-1 alpha-2 code")
        
        if not self.currency_code or len(self.currency_code) != 3:
            raise ValueError("Currency code must be a 3-character ISO 4217 code")
        
        if self.price_micros < 0:
            raise ValueError("Price micros must be non-negative")
        
        # Convert to uppercase for consistency
        self.country_code = self.country_code.upper()
        self.currency_code = self.currency_code.upper()

    @property
    def price_decimal(self) -> Decimal:
        """Convert price from micros to decimal representation."""
        return Decimal(self.price_micros) / Decimal('1000000')

    @classmethod
    def from_decimal_price(cls, country_code: str, currency_code: str, price: float) -> 'CountryPricing':
        """Create CountryPricing from decimal price value."""
        # Use Decimal for precise conversion to avoid floating-point errors
        price_decimal = Decimal(str(price))
        price_micros = int(price_decimal * Decimal('1000000'))
        return cls(country_code, currency_code, price_micros)


@dataclass
class SubscriptionData:
    """Represents complete subscription data including pricing for multiple countries."""
    product_id: str
    package_name: str
    title: str
    description: str
    billing_period: str  # P1M, P3M, P1Y, etc.
    country_pricing: List[CountryPricing]
    trial_period: Optional[str] = None
    grace_period: Optional[str] = None

    def __post_init__(self):
        """Validate subscription data after initialization."""
        if not self.product_id or not re.match(r'^[a-zA-Z0-9_]+$', self.product_id):
            raise ValueError("Product ID must be alphanumeric with underscores only")
        
        if not self.package_name:
            raise ValueError("Package name is required")
        
        if not self.title or not self.title.strip():
            raise ValueError("Title is required and cannot be empty")
        
        if not self.description or not self.description.strip():
            raise ValueError("Description is required and cannot be empty")
        
        if not self._is_valid_billing_period(self.billing_period):
            raise ValueError("Billing period must be in ISO 8601 duration format (P1M, P3M, P6M, P1Y)")
        
        if not self.country_pricing:
            raise ValueError("At least one country pricing entry is required")
        
        # Validate trial and grace periods if provided
        if self.trial_period and not self._is_valid_billing_period(self.trial_period):
            raise ValueError("Trial period must be in ISO 8601 duration format")
        
        if self.grace_period and not self._is_valid_billing_period(self.grace_period):
            raise ValueError("Grace period must be in ISO 8601 duration format")

    def _is_valid_billing_period(self, period: str) -> bool:
        """Validate ISO 8601 duration format for billing periods."""
        if not period:
            return False
        
        # Common Google Play billing periods and trial/grace periods
        valid_periods = {'P1M', 'P3M', 'P6M', 'P1Y', 'P1W', 'P2W', 'P4W', 'P3D', 'P7D', 'P14D'}
        return period in valid_periods

    def get_countries(self) -> List[str]:
        """Get list of country codes for this subscription."""
        return [pricing.country_code for pricing in self.country_pricing]

    def get_pricing_for_country(self, country_code: str) -> Optional[CountryPricing]:
        """Get pricing information for a specific country."""
        for pricing in self.country_pricing:
            if pricing.country_code.upper() == country_code.upper():
                return pricing
        return None


@dataclass
class ValidationResult:
    """Result of data validation operations."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    corrected_data: Optional[Dict[str, Any]] = None

    def add_error(self, error: str):
        """Add an error message and mark validation as failed."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str):
        """Add a warning message."""
        self.warnings.append(warning)

    def has_errors(self) -> bool:
        """Check if validation has any errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if validation has any warnings."""
        return len(self.warnings) > 0


@dataclass
class CreationResult:
    """Result of subscription creation operations."""
    success: bool
    subscription_id: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    api_response: Optional[Dict[str, Any]] = None

    def add_error(self, error: str):
        """Add an error message and mark creation as failed."""
        self.errors.append(error)
        self.success = False

    def has_errors(self) -> bool:
        """Check if creation has any errors."""
        return len(self.errors) > 0


# Validation functions

def validate_subscription_parameters(data: Dict[str, Any]) -> ValidationResult:
    """
    Validate subscription parameters from raw data dictionary.
    
    Args:
        data: Dictionary containing subscription parameters
        
    Returns:
        ValidationResult with validation status and any errors/warnings
    """
    result = ValidationResult(is_valid=True)
    
    # Required fields validation
    required_fields = ['product_id', 'package_name', 'title', 'description', 'billing_period']
    for field in required_fields:
        if field not in data or not data[field]:
            result.add_error(f"Required field '{field}' is missing or empty")
    
    # Product ID validation
    if 'product_id' in data and data['product_id']:
        if not re.match(r'^[a-zA-Z0-9_]+$', data['product_id']):
            result.add_error("Product ID must contain only alphanumeric characters and underscores")
        if len(data['product_id']) > 100:
            result.add_error("Product ID must be 100 characters or less")
    
    # Billing period validation
    if 'billing_period' in data and data['billing_period']:
        valid_periods = {'P1M', 'P3M', 'P6M', 'P1Y', 'P1W', 'P2W', 'P4W'}
        if data['billing_period'] not in valid_periods:
            result.add_error(f"Billing period must be one of: {', '.join(valid_periods)}")
    
    # Optional period validations
    for period_field in ['trial_period', 'grace_period']:
        if period_field in data and data[period_field]:
            valid_periods = {'P1M', 'P3M', 'P6M', 'P1Y', 'P1W', 'P2W', 'P4W', 'P3D', 'P7D', 'P14D'}
            if data[period_field] not in valid_periods:
                result.add_warning(f"{period_field.replace('_', ' ').title()} '{data[period_field]}' may not be supported")
    
    return result


def validate_country_pricing_data(pricing_data: List[Dict[str, Any]]) -> ValidationResult:
    """
    Validate country pricing data from raw data list.
    
    Args:
        pricing_data: List of dictionaries containing country pricing information
        
    Returns:
        ValidationResult with validation status and any errors/warnings
    """
    result = ValidationResult(is_valid=True)
    
    if not pricing_data:
        result.add_error("At least one country pricing entry is required")
        return result
    
    seen_countries = set()
    
    for i, pricing in enumerate(pricing_data):
        # Required fields for pricing
        required_fields = ['country_code', 'currency_code', 'price']
        for field in required_fields:
            if field not in pricing or pricing[field] is None:
                result.add_error(f"Pricing entry {i+1}: Required field '{field}' is missing")
                continue
        
        # Country code validation
        country_code = pricing.get('country_code', '').upper()
        if country_code:
            if len(country_code) != 2:
                result.add_error(f"Pricing entry {i+1}: Country code must be 2 characters (ISO 3166-1 alpha-2)")
            elif country_code in seen_countries:
                result.add_error(f"Pricing entry {i+1}: Duplicate country code '{country_code}'")
            else:
                seen_countries.add(country_code)
        
        # Currency code validation
        currency_code = pricing.get('currency_code', '').upper()
        if currency_code and len(currency_code) != 3:
            result.add_error(f"Pricing entry {i+1}: Currency code must be 3 characters (ISO 4217)")
        
        # Price validation
        price = pricing.get('price')
        if price is not None:
            try:
                price_decimal = Decimal(str(price))
                if price_decimal < 0:
                    result.add_error(f"Pricing entry {i+1}: Price must be non-negative")
                elif price_decimal == 0:
                    result.add_warning(f"Pricing entry {i+1}: Price is zero for {country_code}")
            except (InvalidOperation, ValueError):
                result.add_error(f"Pricing entry {i+1}: Invalid price format '{price}'")
    
    return result


def validate_excel_row_data(row_data: Dict[str, Any], row_number: int) -> ValidationResult:
    """
    Validate a single row of Excel data.
    
    Args:
        row_data: Dictionary containing data from one Excel row
        row_number: Row number for error reporting
        
    Returns:
        ValidationResult with validation status and any errors/warnings
    """
    result = ValidationResult(is_valid=True)
    
    # Required columns
    required_columns = ['Product ID', 'Title', 'Description', 'Billing Period', 
                       'Country Code', 'Currency', 'Price']
    
    for column in required_columns:
        if column not in row_data or row_data[column] is None or str(row_data[column]).strip() == '':
            result.add_error(f"Row {row_number}: Missing required column '{column}'")
    
    # Validate specific fields if present
    if 'Product ID' in row_data and row_data['Product ID']:
        product_id = str(row_data['Product ID']).strip()
        if not re.match(r'^[a-zA-Z0-9_]+$', product_id):
            result.add_error(f"Row {row_number}: Product ID must contain only alphanumeric characters and underscores")
    
    if 'Country Code' in row_data and row_data['Country Code']:
        country_code = str(row_data['Country Code']).strip()
        if len(country_code) != 2:
            result.add_error(f"Row {row_number}: Country Code must be 2 characters")
    
    if 'Currency' in row_data and row_data['Currency']:
        currency = str(row_data['Currency']).strip()
        if len(currency) != 3:
            result.add_error(f"Row {row_number}: Currency must be 3 characters")
    
    if 'Price' in row_data and row_data['Price'] is not None:
        try:
            price = Decimal(str(row_data['Price']))
            if price < 0:
                result.add_error(f"Row {row_number}: Price must be non-negative")
        except (InvalidOperation, ValueError):
            result.add_error(f"Row {row_number}: Invalid price format")
    
    if 'Billing Period' in row_data and row_data['Billing Period']:
        billing_period = str(row_data['Billing Period']).strip()
        valid_periods = {'P1M', 'P3M', 'P6M', 'P1Y', 'P1W', 'P2W', 'P4W'}
        if billing_period not in valid_periods:
            result.add_error(f"Row {row_number}: Invalid billing period '{billing_period}'")
    
    return result