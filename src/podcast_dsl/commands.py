"""
DSL command classes representing different types of commands.
"""


class DSLCommand:
    """Base class for commands or segments in the DSL"""
    pass


class CameraCommand(DSLCommand):
    """Camera switch command"""
    def __init__(self, camera_name: str):
        self.camera_name = camera_name

    def __repr__(self):
        return f"CameraCommand({self.camera_name})"


class CutCommand(DSLCommand):
    """Cut padding configuration command"""
    def __init__(self, before_ms: float, after_ms: float):
        self.before_ms = before_ms
        self.after_ms = after_ms

    def __repr__(self):
        return f"CutCommand(before={self.before_ms}ms, after={self.after_ms}ms)"


class OpeningPrerollCommand(DSLCommand):
    """Episode opening preroll configuration for the first content clip/group."""

    def __init__(self, preroll_ms: float):
        self.preroll_ms = preroll_ms

    def __repr__(self):
        return f"OpeningPrerollCommand(preroll={self.preroll_ms}ms)"


class FadeToBlackCommand(DSLCommand):
    """Fade to black command - affects the previous clip"""
    def __init__(self, duration_ms: float = 100):
        self.duration_ms = duration_ms

    def __repr__(self):
        return f"FadeToBlackCommand(duration={self.duration_ms}ms)"


class FadeFromBlackCommand(DSLCommand):
    """Fade from black command - affects the next clip"""
    def __init__(self, duration_ms: float = 100):
        self.duration_ms = duration_ms

    def __repr__(self):
        return f"FadeFromBlackCommand(duration={self.duration_ms}ms)"


class BlackCommand(DSLCommand):
    """Render black frames for a duration"""
    def __init__(self, duration_ms: float):
        self.duration_ms = duration_ms

    def __repr__(self):
        return f"BlackCommand(duration={self.duration_ms}ms)"


class SegmentCommand(DSLCommand):
    """Play segment command"""
    def __init__(self, segment_id: str, comment: str = "", slice_start: float = None, slice_end: float = None):
        self.segment_id = segment_id
        self.comment = comment
        self.slice_start = slice_start  # Seconds relative to sentence start (negative = lead-in)
        self.slice_end = slice_end      # End offset in seconds (can be negative for offset from end)

    def __repr__(self):
        slice_info = ""
        if self.slice_start is not None or self.slice_end is not None:
            slice_info = f", slice({self.slice_start}:{self.slice_end})"
        return f"SegmentCommand({self.segment_id}{slice_info}, comment={self.comment[:30]}...)"


class AudioCommand(DSLCommand):
    """Audio overlay command - adds audio at current timeline position"""
    def __init__(self, audio_file: str, volume: float = 1.0, speed: float = 1.0):
        self.audio_file = audio_file
        self.volume = volume
        self.speed = speed  # Speed multiplier: <1.0 = slower/deeper, >1.0 = faster/higher

    def __repr__(self):
        return f"AudioCommand({self.audio_file}, volume={self.volume}, speed={self.speed})"


class VolumeCommand(DSLCommand):
    """Volume adjustment for main audio - applies to subsequent segments"""
    def __init__(self, volume: float = 1.0):
        self.volume = volume  # Volume multiplier (1.0 = 100%, 1.2 = 120%, 0.8 = 80%)

    def __repr__(self):
        return f"VolumeCommand(volume={self.volume}x)"


class ShortenJoinCommand(DSLCommand):
    """Marks the next segment boundary as a silence-shorten join (overlap + audio crossfade)."""

    def __init__(self, padding_ms: float, crossfade_ms: float):
        self.padding_ms = padding_ms
        self.crossfade_ms = crossfade_ms

    def __repr__(self):
        return (
            f"ShortenJoinCommand(padding={self.padding_ms}ms, "
            f"crossfade={self.crossfade_ms}ms)"
        )


class PauseFlagCommand(DSLCommand):
    """Marker for a pause-seam resume point in the final edit (reporting only)."""

    def __repr__(self):
        return "PauseFlagCommand()"
