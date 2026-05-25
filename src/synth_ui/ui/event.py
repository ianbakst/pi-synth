from dataclasses import dataclass, field


@dataclass
class UIEvent:
    """Normalized input event with pixel coordinates.

    All positional data is in pixel space — components never need to know
    the screen dimensions or convert normalized finger coordinates.
    """
    type: int
    pos: tuple[int, int] = field(default_factory=lambda: (0, 0))
    dy: int = 0   # pixels: scroll delta or vertical motion
    key: int = 0
