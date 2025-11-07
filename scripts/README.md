# Scripts Directory

Utility scripts for development and testing.

## Structure

### `test/`
Manual test scripts for verifying functionality:
- `test_auth.py` - Verify OAuth authentication
- `quick_test.py` - Quick API connectivity test

### `debug/`
Debug and development utilities:
- `debug_auth.py` - Debug authentication issues
- `demo_ui.py` - UI demo without authentication
- `test_input_visibility.py` - Debug input widget visibility

## Usage

Run scripts from the project root:

```bash
# Test authentication
python scripts/test/test_auth.py

# Run UI demo
python scripts/debug/demo_ui.py
```
