"""Domain enumerations with wire-compatible string values."""

from enum import Enum


class RuntimeMode(str, Enum):  # noqa: UP042 - compatible with host-side Python 3.10 checks
    """Runtime modes locked by the architecture baseline."""

    SIM = "SIM"
    REPLAY = "REPLAY"
    HIL = "HIL"
    LIVE = "LIVE"
