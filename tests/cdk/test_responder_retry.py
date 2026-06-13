from keri_cdk.handlers.witness import witness_handler as wh


def test_retry_negative_returns_first_truthy_without_extra_calls():
    calls = {"n": 0}
    def read():
        calls["n"] += 1
        return "hit"
    assert wh._retry_negative(read, attempts=4, delay=0) == "hit"
    assert calls["n"] == 1


def test_retry_negative_retries_until_value_appears():
    it = iter([None, None, "late"])
    assert wh._retry_negative(lambda: next(it), attempts=4, delay=0) == "late"


def test_retry_negative_gives_up_after_attempts():
    assert wh._retry_negative(lambda: None, attempts=3, delay=0) is None
