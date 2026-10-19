from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
CENT = Decimal("0.01")
ONE = Decimal("1")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    return Decimal(str(value))


def round_cents(value) -> Decimal:
    return D(value).quantize(CENT, ROUND_HALF_UP)


def trunc_cents(value) -> Decimal:
    return D(value).quantize(CENT, ROUND_DOWN)


def ceil_ringgit(value) -> Decimal:
    return D(value).quantize(ONE, ROUND_CEILING).quantize(CENT)


def ceil_5sen(value) -> Decimal:
    return ((D(value) * 20).quantize(ONE, ROUND_CEILING) / 20).quantize(CENT)


def floor_to_step(value, step) -> Decimal:
    step = D(step)
    return ((D(value) / step).quantize(ONE, ROUND_FLOOR) * step)


def ceil_to_step(value, step) -> Decimal:
    step = D(step)
    return ((D(value) / step).quantize(ONE, ROUND_CEILING) * step)
