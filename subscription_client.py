"""
Google Play Console API client for subscription management.

This module provides a client interface for interacting with the Google Play Console
Android Publisher API, specifically for managing app subscriptions.
"""

from typing import Dict, List, Optional, Any
import logging
from googleapiclient.errors import HttpError
from googleapiclient.discovery import Resource
from auth_manager import AuthManager
from data_models import SubscriptionData, CreationResult


class SubscriptionClient:
    """Client for Google Play Console subscription management API operations."""
    
    def __init__(self, auth_manager: AuthManager):
        """
        Initialize the SubscriptionClient.
        
        Args:
            auth_manager: Authenticated AuthManager instance
        """
        self.auth_manager = auth_manager
        self._service: Optional[Resource] = None
        self.logger = logging.getLogger(__name__)
    
    @property
    def service(self) -> Resource:
        """Get the authenticated Google API service, refreshing if needed."""
        if not self._service or not self.auth_manager.is_authenticated():
            self._service = self.auth_manager.get_authenticated_service()
        return self._service
    
    def create_subscription(self, package_name: str, subscription_data: SubscriptionData) -> CreationResult:
        """
        Create a new subscription in Google Play Console.
        
        Args:
            package_name: The package name of the app
            subscription_data: Complete subscription data including pricing
            
        Returns:
            CreationResult with success status and details
        """
        result = CreationResult(success=False)
        
        try:
            # Log the request for debugging
            subscription_body = self._build_subscription_request(subscription_data)
            self.logger.info(f"Creating subscription with body: {subscription_body}")
            
            # Create the subscription using the correct API endpoint
            request = self.service.monetization().subscriptions().create(
                packageName=package_name,
                body=subscription_body
            )
            
            response = request.execute()
            
            result.success = True
            result.subscription_id = subscription_data.product_id
            result.api_response = response
            
            self.logger.info(f"Successfully created subscription {subscription_data.product_id} for package {package_name}")
            
        except HttpError as e:
            error_content = e.content.decode() if e.content else "No error details"
            error_msg = f"HTTP error creating subscription: {e.resp.status} - {error_content}"
            result.add_error(error_msg)
            self.logger.error(error_msg)
            self.logger.error(f"Request body was: {subscription_body}")
            
        except Exception as e:
            error_msg = f"Unexpected error creating subscription: {str(e)}"
            result.add_error(error_msg)
            self.logger.error(error_msg)
        
        return result
    
    def get_subscription(self, package_name: str, product_id: str) -> Dict[str, Any]:
        """
        Retrieve a specific subscription from Google Play Console.
        
        Args:
            package_name: The package name of the app
            product_id: The subscription product ID
            
        Returns:
            Dictionary containing subscription details
            
        Raises:
            HttpError: If the API request fails
            Exception: For other unexpected errors
        """
        try:
            request = self.service.monetization().subscriptions().get(
                packageName=package_name,
                productId=product_id
            )
            
            response = request.execute()
            self.logger.info(f"Successfully retrieved subscription {product_id} for package {package_name}")
            return response
            
        except HttpError as e:
            error_msg = f"HTTP error retrieving subscription {product_id}: {e.resp.status} - {e.content.decode()}"
            self.logger.error(error_msg)
            raise
            
        except Exception as e:
            error_msg = f"Unexpected error retrieving subscription {product_id}: {str(e)}"
            self.logger.error(error_msg)
            raise
    
    def list_subscriptions(self, package_name: str) -> List[Dict[str, Any]]:
        """
        List all subscriptions for a given package.
        
        Args:
            package_name: The package name of the app
            
        Returns:
            List of dictionaries containing subscription details
            
        Raises:
            HttpError: If the API request fails
            Exception: For other unexpected errors
        """
        try:
            request = self.service.monetization().subscriptions().list(
                packageName=package_name
            )
            
            response = request.execute()
            subscriptions = response.get('subscriptions', [])
            
            self.logger.info(f"Successfully retrieved {len(subscriptions)} subscriptions for package {package_name}")
            return subscriptions
            
        except HttpError as e:
            error_msg = f"HTTP error listing subscriptions: {e.resp.status} - {e.content.decode()}"
            self.logger.error(error_msg)
            raise
            
        except Exception as e:
            error_msg = f"Unexpected error listing subscriptions: {str(e)}"
            self.logger.error(error_msg)
            raise
    
    def activate_subscription(self, package_name: str, product_id: str) -> CreationResult:
        """
        Activate a subscription in Google Play Console.
        
        Args:
            package_name: The package name of the app
            product_id: The subscription product ID to activate
            
        Returns:
            CreationResult with success status and details
        """
        result = CreationResult(success=False)
        
        try:
            request = self.service.monetization().subscriptions().activate(
                packageName=package_name,
                productId=product_id
            )
            
            response = request.execute()
            
            result.success = True
            result.subscription_id = product_id
            result.api_response = response
            
            self.logger.info(f"Successfully activated subscription {product_id} for package {package_name}")
            
        except HttpError as e:
            error_msg = f"HTTP error activating subscription: {e.resp.status} - {e.content.decode()}"
            result.add_error(error_msg)
            self.logger.error(error_msg)
            
        except Exception as e:
            error_msg = f"Unexpected error activating subscription: {str(e)}"
            result.add_error(error_msg)
            self.logger.error(error_msg)
        
        return result
    
    def _build_subscription_request(self, subscription_data: SubscriptionData) -> Dict[str, Any]:
        """
        Build the request body for subscription creation.
        
        Args:
            subscription_data: Complete subscription data
            
        Returns:
            Dictionary formatted for Google Play Console API v3
        """
        # According to the Google Play Console API v3 specification,
        # the correct format uses the new subscription structure with basePlans.
        # The error message shows that the API expects specific field names.
        
        # Build pricing phases for the base plan
        pricing_phases = []
        
        # Add trial period if specified
        if subscription_data.trial_period:
            pricing_phases.append({
                "duration": subscription_data.trial_period,
                "price": {
                    "currencyCode": subscription_data.country_pricing[0].currency_code,
                    "priceMicros": "0"  # Trial is free
                },
                "recurrenceCount": 1
            })
        
        # Add main billing phase
        pricing_phases.append({
            "duration": subscription_data.billing_period,
            "price": {
                "currencyCode": subscription_data.country_pricing[0].currency_code,
                "priceMicros": str(subscription_data.country_pricing[0].price_micros)
            },
            "recurrenceCount": 0  # Infinite recurrence
        })
        
        # Build regional configurations
        regional_configs = []
        for pricing in subscription_data.country_pricing:
            regional_configs.append({
                "regionCode": pricing.country_code,
                "price": {
                    "currencyCode": pricing.currency_code,
                    "priceMicros": str(pricing.price_micros)
                },
                "newSubscriberAvailability": True
            })
        
        # Build the subscription body using the correct Google Play Console API v3 format
        subscription_body = {
            "productId": subscription_data.product_id,
            "listings": {
                "en": {
                    "title": subscription_data.title,
                    "description": subscription_data.description
                }
            },
            "basePlans": [{
                "basePlanId": f"{subscription_data.product_id}_base",
                "state": "DRAFT",
                "pricing": {
                    "paymentMode": "SUBSCRIPTION",
                    "pricingPhases": pricing_phases
                },
                "regionalConfigs": regional_configs
            }]
        }
        
        return subscription_body
    
    def _handle_api_error(self, error: HttpError, operation: str) -> str:
        """
        Handle and format API errors for consistent error reporting.
        
        Args:
            error: The HttpError from the API call
            operation: Description of the operation that failed
            
        Returns:
            Formatted error message
        """
        try:
            error_content = error.content.decode()
            return f"{operation} failed: HTTP {error.resp.status} - {error_content}"
        except Exception:
            return f"{operation} failed: HTTP {error.resp.status} - Unable to decode error details"