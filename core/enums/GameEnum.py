"""Game enumeration with validation rules for Infinity Engine games."""

from enum import Enum


class GameEnum(Enum):
    """Enumeration of supported Infinity Engine games."""

    BGEE = "bgee"
    SOD = "sod"
    BG2EE = "bg2ee"
    EET = "eet"
    IWDEE = "iwdee"
    PSTEE = "pstee"

    def __str__(self):
        return self.value
