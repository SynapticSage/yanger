#!/usr/bin/env python3
"""Debug authentication issues for YouTube Ranger."""
# Created: 2025-08-03

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def check_credentials():
    """Check OAuth2 credentials file."""
    print("=== Checking OAuth2 Credentials ===\n")
    
    client_secret_path = Path("config/client_secret.json")
    
    if not client_secret_path.exists():
        print("❌ File not found: config/client_secret.json")
        return False
    
    print(f"✓ File exists: {client_secret_path}")
    
    try:
        with open(client_secret_path) as f:
            data = json.load(f)
        
        # Check structure
        if 'installed' not in data and 'web' not in data:
            print("❌ Invalid structure: Missing 'installed' or 'web' key")
            print("   Found keys:", list(data.keys()))
            return False
        
        app_type = 'installed' if 'installed' in data else 'web'
        print(f"✓ Application type: {app_type}")
        
        # Check required fields
        creds = data[app_type]
        required_fields = ['client_id', 'client_secret', 'auth_uri', 'token_uri']
        
        for field in required_fields:
            if field not in creds:
                print(f"❌ Missing required field: {field}")
                return False
            
            value = creds[field]
            if value.startswith("YOUR_") or value == "your-project-id":
                print(f"❌ Template value found in {field}: {value}")
                print("   Please replace with actual values from Google Cloud Console")
                return False
        
        # Check client_id format
        client_id = creds['client_id']
        if not client_id.endswith('.apps.googleusercontent.com'):
            print(f"❌ Invalid client_id format: {client_id}")
            print("   Should end with '.apps.googleusercontent.com'")
            return False
        
        print("✓ All required fields present")
        print(f"✓ Client ID: {client_id[:20]}...{client_id[-20:]}")
        print(f"✓ Project ID: {creds.get('project_id', 'Not specified')}")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in client_secret.json: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading credentials: {e}")
        return False

def test_auth():
    """Test authentication flow."""
    print("\n=== Testing Authentication ===\n")
    
    try:
        from yanger.auth import YouTubeAuth
        
        auth = YouTubeAuth()
        print("✓ YouTubeAuth initialized")
        
        print("\nAttempting authentication...")
        print("Note: A browser window should open for Google sign-in")
        
        auth.authenticate()
        print("✓ Authentication completed")
        
        if auth.test_authentication():
            print("✓ API connection successful!")
            return True
        else:
            print("❌ API test failed")
            return False
            
    except FileNotFoundError as e:
        print(f"❌ FileNotFoundError: {e}")
        return False
    except Exception as e:
        print(f"❌ Authentication error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_environment():
    """Check environment setup."""
    print("\n=== Environment Check ===\n")
    
    # Check Python version
    print(f"Python version: {sys.version}")
    
    # Check if running in virtual environment
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("✓ Running in virtual environment")
    else:
        print("⚠️  Not running in virtual environment")
    
    # Check for required packages
    try:
        import google.auth
        print(f"✓ google-auth version: {google.auth.__version__}")
    except ImportError:
        print("❌ google-auth not installed")
    
    try:
        import googleapiclient
        print("✓ google-api-python-client installed")
    except ImportError:
        print("❌ google-api-python-client not installed")

def main():
    """Run all checks."""
    print("YouTube Ranger Authentication Debugger")
    print("=====================================\n")
    
    check_environment()
    
    if not check_credentials():
        print("\n⚠️  Please fix the credentials file issues above and try again.")
        print("\nTo get valid credentials:")
        print("1. Go to https://console.cloud.google.com/")
        print("2. Create or select a project")
        print("3. Enable YouTube Data API v3")
        print("4. Go to 'APIs & Services' > 'Credentials'")
        print("5. Click 'Create Credentials' > 'OAuth client ID'")
        print("6. Choose 'Desktop app' as application type")
        print("7. Download the JSON file")
        print("8. Save it as config/client_secret.json")
        return 1
    
    if test_auth():
        print("\n✅ Authentication successful! You can now use yanger.")
        return 0
    else:
        print("\n❌ Authentication failed. Check the error messages above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())