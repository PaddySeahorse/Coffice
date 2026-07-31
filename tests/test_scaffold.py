import coffice


def test_package_importable() -> None:
    assert coffice.__version__ == "0.1.0"
