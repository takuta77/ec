import importlib


def test_app_package_importable():
    importlib.import_module("app")
