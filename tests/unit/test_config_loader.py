import json

import pytest

from webarchiver.config.loader import load_config
from webarchiver.core.exceptions import ConfigError, ValidationError


def _write(tmp_path, data):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(data))
    return p


def test_loads_valid_config(tmp_path):
    cfg = load_config(_write(tmp_path, {"websites": [{"name": "S", "url": "https://a.com"}]}))
    assert cfg.websites[0].name == "S"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.json")


def test_invalid_url_raises(tmp_path):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, {"websites": [{"name": "S", "url": "ftp://a.com"}]}))


def test_duplicate_names_raise(tmp_path):
    data = {"websites": [
        {"name": "S", "url": "https://a.com"},
        {"name": "S", "url": "https://b.com"},
    ]}
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, data))
