"""Config validation must fail loudly (SPEC.md §7.1).

A config that silently degrades to a default produces a readout that is wrong in a way
nobody notices — the worst failure mode this system has, because every downstream surface
will render the wrong number confidently.
"""

from __future__ import annotations

import pytest
import yaml

from readout.config import ConfigError, load_config

BASE = {
    "experiment": "t",
    "source": {"adapter": "fixture", "path": "x.csv"},
    "variants": ["control", "treatment"],
    "control": "control",
    "intended_split": {"control": 0.5, "treatment": 0.5},
    "intended_split_source": "stated",
    "metrics": [{
        "name": "conv", "role": "primary", "type": "binary_rate",
        "horizons": [{"name": "d1", "event_type": "convert", "mode": "precomputed"}],
    }],
}


def write(tmp_path, **overrides):
    data = {**BASE, **overrides}
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_valid_config_parses(tmp_path):
    cfg = load_config(write(tmp_path))
    assert cfg.control == "control"
    assert cfg.treatments == ("treatment",)
    assert cfg.primary_metric.name == "conv"
    assert cfg.srm_is_diagnostic


@pytest.mark.parametrize("overrides, expected", [
    ({"control": "nonexistent"}, "not in variants"),
    ({"variants": ["only_one"]}, "at least 2"),
    ({"variants": ["a", "a"]}, "duplicate"),
    ({"intended_split": {"control": 0.5, "treatment": 0.4}}, "sum to"),
    ({"intended_split": {"control": 1.0}}, "must match variants"),
    ({"intended_split": {"control": 0.0, "treatment": 1.0}}, "must be > 0"),
    ({"intended_split_source": "guessed"}, "intended_split_source"),
    ({"practical_effect": -0.1}, "positive relative effect"),
])
def test_invalid_configs_raise(tmp_path, overrides, expected):
    with pytest.raises(ConfigError, match=expected):
        load_config(write(tmp_path, **overrides))


def test_unknown_top_level_key_is_an_error(tmp_path):
    """A typo must not be silently ignored — that is how a config lies about itself."""
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write(tmp_path, inteded_split_source="stated"))


def test_exactly_one_primary_metric_required(tmp_path):
    two_primaries = [
        BASE["metrics"][0],
        {**BASE["metrics"][0], "name": "other"},
    ]
    with pytest.raises(ConfigError, match="exactly one metric"):
        load_config(write(tmp_path, metrics=two_primaries))


def test_elapsed_horizon_requires_a_window(tmp_path):
    metric = {**BASE["metrics"][0],
              "horizons": [{"name": "d7", "event_type": "convert", "mode": "elapsed"}]}
    with pytest.raises(ConfigError, match="window_days"):
        load_config(write(tmp_path, metrics=[metric]))


def test_window_on_precomputed_horizon_is_rejected(tmp_path):
    """A window on a precomputed horizon means the author misunderstood the mode."""
    metric = {**BASE["metrics"][0],
              "horizons": [{"name": "d7", "event_type": "convert",
                            "mode": "precomputed", "window_days": 7}]}
    with pytest.raises(ConfigError, match="meaningless"):
        load_config(write(tmp_path, metrics=[metric]))


def test_inferred_split_marks_srm_non_diagnostic(tmp_path):
    cfg = load_config(write(tmp_path, intended_split_source="inferred"))
    assert not cfg.srm_is_diagnostic
