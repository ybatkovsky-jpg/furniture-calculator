"""P2-20: ограничение частоты тяжёлых запросов."""

from app.bot.handlers.image import _is_rate_limited


def test_rate_limit_blocks_second_request_within_interval():
    user = 424242
    assert _is_rate_limited(user, now=1000.0) is False
    assert _is_rate_limited(user, now=1001.0) is True
    assert _is_rate_limited(user, now=1010.0) is False
