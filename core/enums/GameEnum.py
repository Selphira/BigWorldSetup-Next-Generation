"""Game enumeration with validation rules for Infinity Engine games."""

from enum import Enum


class GameEnum(Enum):
    """Enumeration of supported Infinity Engine games."""

    EET = "eet"
    BGEE = "bgee"
    BG2EE = "bg2ee"
    SOD = "sod"
    IWDEE = "iwdee"
    PSTEE = "pstee"

    def __str__(self):
        return self.value
