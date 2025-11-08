"""Tests for transcript settings configuration.

Tests TranscriptSettings dataclass and integration with Settings system.
"""
# Created: 2025-11-07

import pytest
import yaml
from pathlib import Path

from yanger.config.settings import (
    Settings,
    TranscriptSettings,
    load_settings
)


class TestTranscriptSettings:
    """Test TranscriptSettings dataclass."""

    def test_default_settings(self):
        """Test default transcript settings."""
        settings = TranscriptSettings()

        assert settings.enabled is True
        assert settings.auto_fetch is False
        assert settings.store_in_db is True
        assert settings.store_compressed is True
        assert settings.export_directory is None
        assert settings.export_txt is True
        assert settings.export_json is True
        assert settings.languages == ["en"]

    def test_custom_settings(self):
        """Test creating custom transcript settings."""
        settings = TranscriptSettings(
            enabled=False,
            auto_fetch=True,
            store_in_db=False,
            store_compressed=False,
            export_directory="/tmp/transcripts",
            export_txt=False,
            export_json=False,
            languages=["es", "fr"]
        )

        assert settings.enabled is False
        assert settings.auto_fetch is True
        assert settings.store_in_db is False
        assert settings.store_compressed is False
        assert settings.export_directory == "/tmp/transcripts"
        assert settings.export_txt is False
        assert settings.export_json is False
        assert settings.languages == ["es", "fr"]

    def test_multiple_languages(self):
        """Test settings with multiple preferred languages."""
        settings = TranscriptSettings(languages=["en", "es", "fr", "de"])
        assert len(settings.languages) == 4
        assert settings.languages[0] == "en"
        assert settings.languages[-1] == "de"


class TestSettingsIntegration:
    """Test TranscriptSettings integration with Settings."""

    def test_settings_includes_transcripts(self):
        """Test that Settings includes transcripts field."""
        settings = Settings()
        assert hasattr(settings, 'transcripts')
        assert isinstance(settings.transcripts, TranscriptSettings)

    def test_settings_from_dict_transcripts(self):
        """Test loading transcript settings from dictionary."""
        config_dict = {
            'transcripts': {
                'enabled': False,
                'auto_fetch': True,
                'languages': ['es', 'en']
            }
        }

        settings = Settings.from_dict(config_dict)

        assert settings.transcripts.enabled is False
        assert settings.transcripts.auto_fetch is True
        assert settings.transcripts.languages == ['es', 'en']
        # Other fields should have defaults
        assert settings.transcripts.store_in_db is True

    def test_settings_from_dict_partial_transcripts(self):
        """Test loading partial transcript settings uses defaults."""
        config_dict = {
            'transcripts': {
                'auto_fetch': True
            }
        }

        settings = Settings.from_dict(config_dict)

        # Specified field
        assert settings.transcripts.auto_fetch is True
        # Default fields
        assert settings.transcripts.enabled is True
        assert settings.transcripts.store_compressed is True

    def test_settings_merge_transcripts(self):
        """Test merging transcript settings."""
        settings1 = Settings()
        settings1.transcripts.enabled = False
        settings1.transcripts.auto_fetch = True

        settings2 = Settings()
        settings2.transcripts.auto_fetch = False
        settings2.transcripts.languages = ["fr"]

        settings1.merge(settings2)

        # Merged values from settings2
        assert settings1.transcripts.auto_fetch is False
        assert settings1.transcripts.languages == ["fr"]
        # Note: enabled will be overwritten by settings2's default (True)
        # This is expected behavior of merge() - it merges all non-None values
        assert settings1.transcripts.enabled is True  # Overwritten by settings2's default


class TestConfigFileLoading:
    """Test loading transcript settings from YAML config files."""

    def test_load_default_config(self, tmp_path):
        """Test loading transcript settings from default config."""
        # This tests against the actual default_config.yaml
        # Note: Requires the actual file to exist
        default_config = Path(__file__).parent.parent / "config" / "default_config.yaml"

        if default_config.exists():
            with open(default_config) as f:
                config = yaml.safe_load(f)

            assert 'transcripts' in config
            assert config['transcripts']['enabled'] is True
            assert config['transcripts']['auto_fetch'] is False
            assert config['transcripts']['store_in_db'] is True
            assert config['transcripts']['store_compressed'] is True
            assert config['transcripts']['languages'] == ["en"]

    def test_load_custom_user_config(self, tmp_path):
        """Test loading custom user config with transcript settings."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"

        # Write custom config
        custom_config = {
            'transcripts': {
                'enabled': True,
                'auto_fetch': True,
                'store_compressed': False,
                'export_directory': str(tmp_path / "my_transcripts"),
                'languages': ['es', 'en', 'fr']
            }
        }

        with open(config_file, 'w') as f:
            yaml.dump(custom_config, f)

        # Load settings
        settings = load_settings(config_dir)

        assert settings.transcripts.enabled is True
        assert settings.transcripts.auto_fetch is True
        assert settings.transcripts.store_compressed is False
        assert settings.transcripts.export_directory == str(tmp_path / "my_transcripts")
        assert settings.transcripts.languages == ['es', 'en', 'fr']

    def test_config_with_null_export_directory(self, tmp_path):
        """Test config with null export_directory falls back to default."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"

        custom_config = {
            'transcripts': {
                'export_directory': None
            }
        }

        with open(config_file, 'w') as f:
            yaml.dump(custom_config, f)

        settings = load_settings(config_dir)
        # When null is explicitly set, it falls back to default from default_config.yaml
        assert settings.transcripts.export_directory == '~/.cache/yanger/transcripts'

    def test_config_with_empty_languages(self, tmp_path):
        """Test config with empty languages list."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"

        custom_config = {
            'transcripts': {
                'languages': []
            }
        }

        with open(config_file, 'w') as f:
            yaml.dump(custom_config, f)

        settings = load_settings(config_dir)
        # Should accept empty list
        assert settings.transcripts.languages == []


class TestSettingsValidation:
    """Test settings validation and edge cases."""

    def test_boolean_flags_are_booleans(self):
        """Test that boolean flags are actually booleans."""
        settings = TranscriptSettings()

        assert isinstance(settings.enabled, bool)
        assert isinstance(settings.auto_fetch, bool)
        assert isinstance(settings.store_in_db, bool)
        assert isinstance(settings.store_compressed, bool)
        assert isinstance(settings.export_txt, bool)
        assert isinstance(settings.export_json, bool)

    def test_languages_is_list(self):
        """Test that languages is a list."""
        settings = TranscriptSettings()
        assert isinstance(settings.languages, list)

    def test_export_directory_can_be_none_or_string(self):
        """Test export_directory accepts None or string."""
        settings1 = TranscriptSettings(export_directory=None)
        assert settings1.export_directory is None

        settings2 = TranscriptSettings(export_directory="/tmp/test")
        assert settings2.export_directory == "/tmp/test"

    def test_inconsistent_config_handled(self):
        """Test handling of inconsistent configurations."""
        # Store in DB disabled but compressed enabled
        settings = TranscriptSettings(
            store_in_db=False,
            store_compressed=True
        )

        # Should accept the configuration (app can handle logic)
        assert settings.store_in_db is False
        assert settings.store_compressed is True


class TestSettingsSerialization:
    """Test settings serialization for saving."""

    def test_settings_to_dict_includes_transcripts(self, tmp_path):
        """Test that settings serialization includes transcripts."""
        settings = Settings()
        settings.transcripts.auto_fetch = True
        settings.transcripts.languages = ["es", "en"]

        # Simulate saving (using vars to convert to dict)
        transcripts_dict = vars(settings.transcripts)

        assert transcripts_dict['auto_fetch'] is True
        assert transcripts_dict['languages'] == ["es", "en"]
        assert 'enabled' in transcripts_dict
        assert 'store_in_db' in transcripts_dict
