"""
Subscription Manager for Google Play Console API.

This module provides the core business logic for subscription management,
orchestrating file processing, API calls, validation, and error handling
for subscription creation operations.
"""

import logging
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path

from auth_manager import AuthManager
from subscription_client import SubscriptionClient
from excel_processor import ExcelProcessor
from data_models import (
    SubscriptionData, ValidationResult, CreationResult,
    validate_subscription_parameters, validate_country_pricing_data
)


class SubscriptionManager:
    """
    Core business logic for subscription management operations.
    
    This class orchestrates file processing, validation, preview functionality,
    and subscription creation through the Google Play Console API.
    """
    
    def __init__(self, auth_manager: AuthManager, package_name: str):
        """
        Initialize the SubscriptionManager.
        
        Args:
            auth_manager: Authenticated AuthManager instance
            package_name: The package name of the Android app
        """
        self.auth_manager = auth_manager
        self.package_name = package_name
        self.subscription_client = SubscriptionClient(auth_manager)
        self.excel_processor = ExcelProcessor()
        
        # Set up logging
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
    
    def create_subscription_from_data(self, subscription_data: SubscriptionData) -> CreationResult:
        """
        Create a subscription from validated SubscriptionData.
        
        Args:
            subscription_data: Complete subscription data including pricing
            
        Returns:
            CreationResult with success status and details
        """
        self.logger.info(f"Creating subscription {subscription_data.product_id} for package {self.package_name}")
        
        try:
            # Final validation before creation
            validation_result = self.validate_subscription_data(subscription_data)
            if not validation_result.is_valid:
                result = CreationResult(success=False)
                result.errors.extend(validation_result.errors)
                self.logger.error(f"Validation failed for subscription {subscription_data.product_id}: {validation_result.errors}")
                return result
            
            # Create subscription via API client
            creation_result = self.subscription_client.create_subscription(
                self.package_name, subscription_data
            )
            
            if creation_result.success:
                self.logger.info(f"Successfully created subscription {subscription_data.product_id}")
                
                # Optionally activate the subscription
                activation_result = self.subscription_client.activate_subscription(
                    self.package_name, subscription_data.product_id
                )
                
                if not activation_result.success:
                    self.logger.warning(f"Subscription created but activation failed: {activation_result.errors}")
                    creation_result.errors.extend([f"Activation warning: {err}" for err in activation_result.errors])
            else:
                self.logger.error(f"Failed to create subscription {subscription_data.product_id}: {creation_result.errors}")
            
            return creation_result
            
        except Exception as e:
            error_msg = f"Unexpected error creating subscription {subscription_data.product_id}: {str(e)}"
            self.logger.error(error_msg)
            result = CreationResult(success=False)
            result.add_error(error_msg)
            return result
    
    def create_subscriptions_from_excel(self, file_path: str) -> Tuple[List[CreationResult], ValidationResult]:
        """
        Create subscriptions from an Excel file.
        
        Args:
            file_path: Path to the Excel file containing subscription data
            
        Returns:
            Tuple of (list of CreationResults, overall ValidationResult)
        """
        self.logger.info(f"Processing Excel file for subscription creation: {file_path}")
        
        # Parse and validate Excel file
        subscriptions, validation_result = self.excel_processor.parse_excel_file(file_path)
        
        if not validation_result.is_valid:
            self.logger.error(f"Excel file validation failed: {validation_result.errors}")
            return [], validation_result
        
        # Create each subscription
        creation_results = []
        for subscription in subscriptions:
            # Update package name if not set in Excel
            if not subscription.package_name:
                subscription.package_name = self.package_name
            
            result = self.create_subscription_from_data(subscription)
            creation_results.append(result)
        
        # Log summary
        successful = sum(1 for r in creation_results if r.success)
        total = len(creation_results)
        self.logger.info(f"Subscription creation completed: {successful}/{total} successful")
        
        return creation_results, validation_result
    
    def preview_subscription_from_excel(self, file_path: str) -> Tuple[List[Dict[str, Any]], ValidationResult]:
        """
        Preview subscription data from Excel file without creating subscriptions.
        
        Args:
            file_path: Path to the Excel file containing subscription data
            
        Returns:
            Tuple of (list of subscription preview data, ValidationResult)
        """
        self.logger.info(f"Previewing subscription data from Excel file: {file_path}")
        
        # Parse Excel file
        subscriptions, validation_result = self.excel_processor.parse_excel_file(file_path)
        
        # Convert to preview format
        preview_data = []
        for subscription in subscriptions:
            # Update package name if not set
            if not subscription.package_name:
                subscription.package_name = self.package_name
            
            preview = self._create_subscription_preview(subscription)
            preview_data.append(preview)
        
        return preview_data, validation_result
    
    def preview_subscription_from_data(self, subscription_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Preview subscription data from raw dictionary data.
        
        Args:
            subscription_data: Dictionary containing subscription parameters
            
        Returns:
            Dictionary with preview information and validation results
        """
        self.logger.info(f"Previewing subscription data for product: {subscription_data.get('product_id', 'unknown')}")
        
        # Validate the raw data
        validation_result = validate_subscription_parameters(subscription_data)
        
        # Validate pricing data if present
        pricing_data = subscription_data.get('country_pricing', [])
        if pricing_data:
            pricing_validation = validate_country_pricing_data(pricing_data)
            validation_result.errors.extend(pricing_validation.errors)
            validation_result.warnings.extend(pricing_validation.warnings)
            if not pricing_validation.is_valid:
                validation_result.is_valid = False
        
        # Create preview
        preview = {
            'product_id': subscription_data.get('product_id', ''),
            'package_name': subscription_data.get('package_name', self.package_name),
            'title': subscription_data.get('title', ''),
            'description': subscription_data.get('description', ''),
            'billing_period': subscription_data.get('billing_period', ''),
            'trial_period': subscription_data.get('trial_period'),
            'grace_period': subscription_data.get('grace_period'),
            'country_pricing': pricing_data,
            'validation': {
                'is_valid': validation_result.is_valid,
                'errors': validation_result.errors,
                'warnings': validation_result.warnings
            },
            'ready_for_creation': validation_result.is_valid and len(pricing_data) > 0
        }
        
        return preview
    
    def validate_subscription_data(self, subscription_data: SubscriptionData) -> ValidationResult:
        """
        Validate complete subscription data before creation.
        
        Args:
            subscription_data: SubscriptionData object to validate
            
        Returns:
            ValidationResult with validation status and any errors/warnings
        """
        result = ValidationResult(is_valid=True)
        
        try:
            # Basic validation is already done in SubscriptionData.__post_init__
            # Additional business logic validation can be added here
            
            # Validate package name matches manager's package name
            if subscription_data.package_name != self.package_name:
                result.add_warning(f"Package name mismatch: expected {self.package_name}, got {subscription_data.package_name}")
            
            # Validate pricing data
            if not subscription_data.country_pricing:
                result.add_error("At least one country pricing entry is required")
            
            # Check for duplicate countries
            seen_countries = set()
            for pricing in subscription_data.country_pricing:
                if pricing.country_code in seen_countries:
                    result.add_error(f"Duplicate country code: {pricing.country_code}")
                seen_countries.add(pricing.country_code)
            
            # Validate that we have authentication
            if not self.auth_manager.is_authenticated():
                result.add_error("Authentication required for subscription creation")
            
        except Exception as e:
            result.add_error(f"Validation error: {str(e)}")
        
        return result
    
    def get_subscription_creation_summary(self, creation_results: List[CreationResult]) -> Dict[str, Any]:
        """
        Generate a summary of subscription creation results.
        
        Args:
            creation_results: List of CreationResult objects
            
        Returns:
            Dictionary containing summary statistics and details
        """
        total = len(creation_results)
        successful = sum(1 for r in creation_results if r.success)
        failed = total - successful
        
        # Collect all errors
        all_errors = []
        successful_subscriptions = []
        failed_subscriptions = []
        
        for result in creation_results:
            if result.success:
                successful_subscriptions.append({
                    'subscription_id': result.subscription_id,
                    'api_response': result.api_response
                })
            else:
                failed_subscriptions.append({
                    'subscription_id': result.subscription_id,
                    'errors': result.errors
                })
                all_errors.extend(result.errors)
        
        summary = {
            'total_subscriptions': total,
            'successful_creations': successful,
            'failed_creations': failed,
            'success_rate': (successful / total * 100) if total > 0 else 0,
            'successful_subscriptions': successful_subscriptions,
            'failed_subscriptions': failed_subscriptions,
            'all_errors': all_errors,
            'package_name': self.package_name
        }
        
        return summary
    
    def _create_subscription_preview(self, subscription: SubscriptionData) -> Dict[str, Any]:
        """
        Create a preview dictionary from SubscriptionData.
        
        Args:
            subscription: SubscriptionData object
            
        Returns:
            Dictionary with preview information
        """
        # Validate the subscription
        validation_result = self.validate_subscription_data(subscription)
        
        # Format pricing information
        pricing_summary = []
        for pricing in subscription.country_pricing:
            pricing_summary.append({
                'country': pricing.country_code,
                'currency': pricing.currency_code,
                'price': float(pricing.price_decimal),
                'price_micros': pricing.price_micros
            })
        
        preview = {
            'product_id': subscription.product_id,
            'package_name': subscription.package_name,
            'title': subscription.title,
            'description': subscription.description,
            'billing_period': subscription.billing_period,
            'trial_period': subscription.trial_period,
            'grace_period': subscription.grace_period,
            'countries': subscription.get_countries(),
            'pricing_summary': pricing_summary,
            'total_countries': len(subscription.country_pricing),
            'validation': {
                'is_valid': validation_result.is_valid,
                'errors': validation_result.errors,
                'warnings': validation_result.warnings
            },
            'ready_for_creation': validation_result.is_valid
        }
        
        return preview