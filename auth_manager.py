"""
Authentication Manager for Google Play Console API access.

This module supports:
- service account JSON keys, which are the primary path used by the dashboard
- optional OAuth2 fallback for older internal flows
"""

import os
import json
from typing import Optional, Tuple, Dict, Any
import urllib.parse
import google.auth.transport.requests
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build
import secrets


class AuthManager:
    """Manages Google authentication for Google Play Console API access."""
    
    # Android Publisher API scope for subscription management
    SCOPES = ['https://www.googleapis.com/auth/androidpublisher']
    
    def __init__(
        self,
        credentials_file: str = 'credentials.json',
        token_file: str = 'token.json',
        service_account_file: str = 'service-account.json',
    ):
        """
        Initialize the AuthManager.
        
        Args:
            credentials_file: Path to an optional OAuth2 client credentials file
            token_file: Path to store/load OAuth2 access and refresh tokens
            service_account_file: Path to the Google service account JSON key file
        """
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service_account_file = service_account_file
        self._credentials: Optional[Credentials] = None
        self._auth_type: Optional[str] = None  # 'oauth2' or 'service_account'
        self._auth_logged: bool = False  # Track if we've logged authentication success
    
    def get_authenticated_service(self):
        """
        Get an authenticated Google API service client.
        
        Returns:
            googleapiclient.discovery.Resource: Authenticated Android Publisher API service
        """
        if not self._credentials or not self._credentials.valid:
            self._authenticate()
        
        return build('androidpublisher', 'v3', credentials=self._credentials)
    
    def refresh_credentials(self) -> bool:
        """
        Refresh the current credentials if possible.
        
        Returns:
            bool: True if credentials were successfully refreshed, False otherwise
        """
        if not self._credentials:
            return False
            
        if self._credentials.expired and self._credentials.refresh_token:
            try:
                self._credentials.refresh(google.auth.transport.requests.Request())
                self._save_credentials()
                return True
            except Exception:
                return False
        
        return self._credentials.valid
    
    def is_authenticated(self) -> bool:
        """
        Check if we have valid credentials.
        
        Returns:
            bool: True if authenticated with valid credentials, False otherwise
        """
        # First try to load existing credentials if not already loaded
        if self._credentials is None:
            self._authenticate()
        
        # Service account credentials don't expire in the same way as OAuth2
        if self._auth_type == 'service_account':
            return self._credentials is not None and self._credentials.valid
        
        # For OAuth2 credentials, check if they need refresh
        if self._credentials and self._credentials.expired and hasattr(self._credentials, 'refresh_token') and self._credentials.refresh_token:
            try:
                self._credentials.refresh(google.auth.transport.requests.Request())
                self._save_credentials()
            except Exception:
                return False
        
        return self._credentials is not None and self._credentials.valid
    
    def get_web_auth_url(self, redirect_uri: str, state: Optional[str] = None) -> Tuple[str, str]:
        """
        Get the authorization URL for web application OAuth2 flow.
        
        Args:
            redirect_uri: The redirect URI for the OAuth2 callback
            state: Optional state parameter for CSRF protection
            
        Returns:
            Tuple[str, str]: Authorization URL and state parameter
        """
        if state is None:
            state = secrets.token_urlsafe(32)
        
        try:
            # Check if we have installed credentials and convert them for web use
            import json
            with open(self.credentials_file, 'r') as f:
                creds_data = json.load(f)
            
            if 'installed' in creds_data and 'web' not in creds_data:
                # Convert installed credentials to web format for this session
                web_creds = {
                    'web': {
                        'client_id': creds_data['installed']['client_id'],
                        'client_secret': creds_data['installed']['client_secret'],
                        'auth_uri': creds_data['installed']['auth_uri'],
                        'token_uri': creds_data['installed']['token_uri'],
                        'redirect_uris': [redirect_uri]  # Use the provided redirect URI
                    }
                }
                
                # Create flow from the converted credentials
                flow = Flow.from_client_config(
                    web_creds,
                    scopes=self.SCOPES,
                    state=state
                )
            else:
                # Use normal flow for web credentials
                flow = Flow.from_client_secrets_file(
                    self.credentials_file,
                    scopes=self.SCOPES,
                    state=state
                )
            
            flow.redirect_uri = redirect_uri
            
            authorization_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'  # Force consent to ensure refresh token
            )
            
            return authorization_url, state
            
        except Exception as e:
            print(f"Error creating OAuth2 flow: {e}")
            raise
    
    def handle_web_auth_callback(self, authorization_response: str, redirect_uri: str, state: str) -> bool:
        """
        Handle the OAuth2 callback from web application flow.
        
        Args:
            authorization_response: The full callback URL with authorization code
            redirect_uri: The redirect URI used in the initial request
            state: The state parameter for CSRF protection
            
        Returns:
            bool: True if authentication was successful, False otherwise
        """
        try:
            # Check if we have installed credentials and convert them for web use
            import json
            with open(self.credentials_file, 'r') as f:
                creds_data = json.load(f)
            
            if 'installed' in creds_data and 'web' not in creds_data:
                # Convert installed credentials to web format for this session
                web_creds = {
                    'web': {
                        'client_id': creds_data['installed']['client_id'],
                        'client_secret': creds_data['installed']['client_secret'],
                        'auth_uri': creds_data['installed']['auth_uri'],
                        'token_uri': creds_data['installed']['token_uri'],
                        'redirect_uris': [redirect_uri]  # Use the provided redirect URI
                    }
                }
                
                # Create flow from the converted credentials
                flow = Flow.from_client_config(
                    web_creds,
                    scopes=self.SCOPES,
                    state=state
                )
            else:
                # Use normal flow for web credentials
                flow = Flow.from_client_secrets_file(
                    self.credentials_file,
                    scopes=self.SCOPES,
                    state=state
                )
            
            flow.redirect_uri = redirect_uri
            
            # Fetch the token using the authorization response
            flow.fetch_token(authorization_response=authorization_response)
            
            self._credentials = flow.credentials
            self._save_credentials()
            return True
            
        except Exception as e:
            print(f"Error handling OAuth callback: {e}")
            return False
    
    def clear_credentials(self):
        """Clear stored credentials and remove token file."""
        self._credentials = None
        if os.path.exists(self.token_file):
            try:
                os.remove(self.token_file)
            except Exception:
                pass
    
    def _load_existing_credentials(self):
        """Load existing credentials from token file if available."""
        if os.path.exists(self.token_file):
            try:
                self._credentials = Credentials.from_authorized_user_file(self.token_file, self.SCOPES)
            except Exception:
                pass
    
    def _authenticate(self):
        """Perform authentication flow, prioritizing service account over OAuth2."""
        # First try service account authentication
        if self._try_service_account_auth():
            return
        
        # Fall back to OAuth2 if service account fails
        self._try_oauth2_auth()
    
    def _try_service_account_auth(self) -> bool:
        """
        Try to authenticate using service account credentials.
        
        Returns:
            bool: True if service account authentication succeeded, False otherwise
        """
        if not os.path.exists(self.service_account_file):
            print(f"Service account file not found: {self.service_account_file}")
            return False
        
        try:
            # Load the service account file
            with open(self.service_account_file, 'r') as f:
                service_account_data = json.load(f)
            
            # Handle the specific format with Parameter.Value or parameterValue
            private_key_pem = None
            if 'Parameter' in service_account_data and 'Value' in service_account_data['Parameter']:
                # Extract the private key from Parameter.Value (AWS SSM format)
                private_key_pem = service_account_data['Parameter']['Value']
            elif 'parameterValue' in service_account_data:
                # Extract the private key from parameterValue (alternative format)
                private_key_pem = service_account_data['parameterValue']
            
            if private_key_pem:
                # Fix escaped newlines (same as your TypeScript: .replace(/\\n/g, '\n'))
                # The private key should already have proper newlines, but let's ensure it's correct
                if '\\n' in private_key_pem:
                    private_key_pem = private_key_pem.replace('\\n', '\n')
                
                # Ensure the private key has proper formatting
                if not private_key_pem.startswith('-----BEGIN PRIVATE KEY-----'):
                    print("❌ Private key doesn't start with proper header")
                    return False
                if not private_key_pem.endswith('-----END PRIVATE KEY-----\n'):
                    if not private_key_pem.endswith('-----END PRIVATE KEY-----'):
                        print("❌ Private key doesn't end with proper footer")
                        return False
                    else:
                        # Add final newline if missing
                        private_key_pem += '\n'
                
                # If the input only contains a private key blob, the remaining service-account
                # fields must come from the environment.
                client_email = os.getenv('GOOGLE_SERVICE_ACCOUNT_EMAIL', 'service-account@example.iam.gserviceaccount.com')
                project_id = os.getenv('GOOGLE_PROJECT_ID', 'example-project')
                
                # Construct a proper service account info dict
                service_account_info = {
                    "type": "service_account",
                    "private_key": private_key_pem,
                    "client_email": client_email,
                    "project_id": project_id,
                    "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID', 'example-key-id'),
                    "client_id": os.getenv('GOOGLE_CLIENT_ID', ''),
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{urllib.parse.quote(client_email)}",
                    "universe_domain": "googleapis.com"
                }
                
            else:
                # Check if it's a standard service account JSON format (full service account key file)
                if 'private_key' in service_account_data and 'client_email' in service_account_data:
                    service_account_info = service_account_data
                else:
                    print("❌ Private key not found in expected format")
                    print("Expected formats:")
                    print('1. AWS SSM: {"Parameter": {"Value": "-----BEGIN PRIVATE KEY-----\\n..."}}')
                    print('2. Simple: {"parameterValue": "-----BEGIN PRIVATE KEY-----\\n..."}')
                    print('3. Standard: {"private_key": "-----BEGIN PRIVATE KEY-----\\n...", "client_email": "..."}')
                    return False
            
            # Create service account credentials (similar to GoogleAuthProvider.getAccessToken)
            self._credentials = ServiceAccountCredentials.from_service_account_info(
                service_account_info, scopes=self.SCOPES
            )
            self._auth_type = 'service_account'
            
            # Service account credentials need to be refreshed to become valid
            try:
                self._credentials.refresh(google.auth.transport.requests.Request())
                if not self._auth_logged:
                    print(f"✅ Successfully authenticated using service account: {service_account_info.get('client_email', 'unknown')}")
                    self._auth_logged = True
                return True
            except Exception as refresh_error:
                print(f"❌ Failed to refresh service account credentials: {refresh_error}")
                return False
            
        except Exception as e:
            print(f"❌ Service account authentication failed: {e}")
            print("Make sure your service account input has the correct format:")
            print('AWS SSM format: {"Parameter": {"Value": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"}}')
            print('Or simple format: {"parameterValue": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"}')
            return False
    
    def _try_oauth2_auth(self):
        """Perform OAuth2 authentication as fallback."""
        creds = None
        
        # Load existing OAuth2 credentials
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, self.SCOPES)
                self._auth_type = 'oauth2'
            except Exception:
                pass
        
        # If no valid credentials, run OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(google.auth.transport.requests.Request())
            else:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"Neither service account file ({self.service_account_file}) nor "
                        f"OAuth2 credentials file ({self.credentials_file}) found. "
                        f"Please provide one of these authentication methods."
                    )
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            self._save_credentials(creds)
        
        self._credentials = creds
        self._auth_type = 'oauth2'
    
    def _save_credentials(self, creds: Optional[Credentials] = None):
        """Save credentials to the token file."""
        credentials_to_save = creds or self._credentials
        if credentials_to_save:
            with open(self.token_file, 'w') as token:
                token.write(credentials_to_save.to_json())
