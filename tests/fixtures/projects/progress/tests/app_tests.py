from src.app import regression, stable, value


def test_value() -> None:
    assert value == 2


def test_regression() -> None:
    assert regression == 2


def test_stable() -> None:
    assert stable == 1
