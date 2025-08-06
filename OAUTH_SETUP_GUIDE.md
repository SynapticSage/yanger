# YouTube Ranger OAuth Setup Guide
<!-- Created: 2025-08-03 -->

## 📋 Prerequisites
- Google account
- Access to Google Cloud Console

## 🔐 Step-by-Step OAuth Setup

### 1. Create Google Cloud Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Name it (e.g., "YouTube Ranger")
4. Click "Create"

### 2. Enable YouTube Data API v3
1. In the sidebar, go to "APIs & Services" → "Library"
2. Search for "YouTube Data API v3"
3. Click on it and press "Enable"

### 3. Create OAuth 2.0 Credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "+ CREATE CREDENTIALS" → "OAuth client ID"
3. If prompted, configure OAuth consent screen:
   - Choose "External" (unless you have a Google Workspace)
   - Fill in required fields (app name, email)
   - Add your email to test users
   - Save and continue through all steps
4. Back at credentials, create OAuth client ID:
   - Application type: **Desktop app**
   - Name: "YouTube Ranger" (or any name)
   - Click "Create"

### 4. Download and Install Credentials
1. After creation, click the download button (⬇️) next to your OAuth client
2. Save the downloaded JSON file
3. **IMPORTANT**: Replace the ENTIRE contents of `config/client_secret.json` with this downloaded file

The real file will look like this (with actual values):
```json
{
  "installed": {
    "client_id": "123456789012-abcdefghijklmnopqrstuvwxyz123456.apps.googleusercontent.com",
    "project_id": "youtube-ranger-123456",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_secret": "GOCSPX-aBcDeFgHiJkLmNoPqRsTuVwXyZ",
    "redirect_uris": ["http://localhost"]
  }
}
```

### 5. Authenticate
```bash
# Make sure you're in the yanger directory with venv activated
source venv/bin/activate

# Run authentication
yanger auth

# A browser window will open - sign in with your Google account
# Grant permissions to access YouTube
# You'll see "Authentication successful!" when done
```

### 6. Run YouTube Ranger
```bash
yanger
```

## 🚨 Common Issues

### "Template value found in client_id"
- You haven't replaced the template file with your actual credentials
- Download the real JSON from Google Cloud Console
- Replace the ENTIRE file contents, don't edit individual values

### Browser doesn't open
- Check if you're on a remote server/SSH
- You may need to run on a local machine with a browser

### "Access blocked" error
- Make sure your OAuth consent screen is configured
- Add your email to the test users list
- If your app is in "Testing" mode, only test users can authenticate

### "Quota exceeded"
- Free tier allows 10,000 units per day
- Wait until midnight Pacific Time for reset
- Or request quota increase in Google Cloud Console

## ✅ Verification
After successful setup, test with:
```bash
# Check authentication
yanger auth

# Check quota
yanger quota

# Run the app
yanger
```

## 🔒 Security Notes
- Never commit `client_secret.json` to version control
- Keep your OAuth credentials private
- The app will create a `token.json` file - keep this private too
- Both files are already in `.gitignore`