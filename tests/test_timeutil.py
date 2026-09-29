"""Ported from JMusicBot's TimeUtilTest, plus format_time."""

from pymusicbot.timeutil import format_time, parse_seek


def test_single_digit():
    assert parse_seek("5").milliseconds == 5000


def test_multiple_digits():
    assert parse_seek("99:9:999").milliseconds == 357939000
    assert parse_seek("99h9m999s").milliseconds == 357939000


def test_decimal_digits():
    assert parse_seek("99.5:9.0:999.777").milliseconds == 359739777


def test_seeking():
    seek = parse_seek("5")
    assert not seek.relative
    assert seek.milliseconds == 5000


def test_relative_seeking():
    forward = parse_seek("+5")
    assert forward.relative and forward.milliseconds == 5000
    backward = parse_seek("-5")
    assert backward.relative and backward.milliseconds == -5000


def test_empty_argument():
    assert parse_seek("") is None


def test_timestamp_total_units():
    assert parse_seek("1:1:1:1") is None
    assert parse_seek("1h2m3m4s5s").milliseconds == 3909000


def test_relative_symbol():
    assert parse_seek("+-1:-+1:+-1") is None


def test_timestamp_number_format():
    assert parse_seek("1:1:a") is None
    assert parse_seek("1a2s").milliseconds == 3000


def test_format_time():
    assert format_time(0) == "00:00"
    assert format_time(65) == "01:05"
    assert format_time(3725) == "1:02:05"
    assert format_time(59.5) == "01:00"  # rounds half up like Java
    assert format_time(None) == "LIVE"
