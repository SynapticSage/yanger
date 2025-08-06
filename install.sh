#!/bin/bash
# YouTube Ranger Installation Script
# Created: 2025-08-03

set -e

echo "YouTube Ranger Installation"
echo "=========================="
echo

# Check Python version
echo "Checking Python version..."
python_version=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
required_version="3.10"

if [[ $(echo -e "$python_version\n$required_version" | sort -V | head -n1) != "$required_version" ]]; then
    echo "Error: Python $required_version or higher is required (found $python_version)"
    exit 1
fi
echo "✓ Python $python_version"
echo

# Create virtual environment
echo "Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✓ Virtual environment created"
else
    echo "✓ Virtual environment already exists"
fi
echo

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate
echo "✓ Virtual environment activated"
echo

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip wheel setuptools
echo "✓ pip upgraded"
echo

# Install package in development mode
echo "Installing YouTube Ranger..."
pip install -e .
echo "✓ YouTube Ranger installed"
echo

# Create config directory
echo "Creating configuration directory..."
config_dir="$HOME/.config/yanger"
mkdir -p "$config_dir"
echo "✓ Configuration directory created at $config_dir"
echo

# Check for OAuth credentials
if [ ! -f "config/client_secret.json" ]; then
    echo "⚠️  OAuth2 credentials not found!"
    echo
    echo "To complete setup:"
    echo "1. Go to https://console.cloud.google.com/"
    echo "2. Create a project and enable YouTube Data API v3"
    echo "3. Create OAuth 2.0 credentials (Desktop type)"
    echo "4. Download and save as: config/client_secret.json"
    echo "5. Run: yanger auth"
else
    echo "✓ OAuth2 credentials found"
    echo
    echo "Run 'yanger auth' to authenticate with YouTube"
fi
echo

echo "Installation complete!"
echo
echo "To use YouTube Ranger:"
echo "  source venv/bin/activate  # If not already activated"
echo "  yanger auth              # First time setup"
echo "  yanger                   # Run the application"
echo

# Make the script executable
chmod +x install.sh