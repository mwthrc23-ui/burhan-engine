from app import run


def test_run_greets_the_user() -> None:
    assert run() == "مرحبًا Ada"
