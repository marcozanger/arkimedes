import yaml

import ark


def test_package_and_yaml_import():
    assert ark.__doc__
    assert yaml.safe_load("a: 1") == {"a": 1}
