"""
DSL parsing logic for podcast editing commands.

Format:
  $segment2/0                     // Play this segment
  !camera wide                    // Switch to this camera
  !cut 50 50                      // Set padding before/after cuts
  !shorten-join [20 20]           // Next join: overlap padding + audio crossfade (ms)
  !opening 1000                   // Start first content clip/group this many ms early
  !volume 1.2                     // Set main audio volume (1.0 = 100%)
  !pause-flag                      // Pause-seam marker (reporting only)
  !fade to black 100              // Fade to black
  !fade from black 100            // Fade from black
  !black 500                      // Black frames for duration
  !audio "sound.mp3" 1.0 0.5      // Overlay audio at current position (volume, speed)
  // Comment line

Everything after // is treated as a comment.
Lines starting with $ specify a segment to play.
Lines starting with ! are commands.
Audio speed: 1.0 = normal, <1.0 = slower/deeper, >1.0 = faster/higher
"""

import sys
from typing import List, Optional

from .commands import (
    DSLCommand,
    CameraCommand,
    CutCommand,
    OpeningPrerollCommand,
    FadeToBlackCommand,
    FadeFromBlackCommand,
    BlackCommand,
    SegmentCommand,
    AudioCommand,
    VolumeCommand,
    ShortenJoinCommand,
    PauseFlagCommand,
)


def parse_dsl_line(line: str) -> Optional[DSLCommand]:
    """
    Parse a single line of DSL.

    Returns:
        DSLCommand or None if the line is empty/comment-only
    """
    # Remove leading/trailing whitespace
    line = line.strip()

    # Empty line
    if not line:
        return None

    # Comment-only line
    if line.startswith('//'):
        return None

    # Extract comment if present
    comment = ""
    if '//' in line:
        line, comment = line.split('//', 1)
        line = line.strip()
        comment = comment.strip()

    # Segment command
    if line.startswith('$'):
        segment_spec = line[1:].strip()

        # Parse slice notation: $segment2/0 slice(1:-3)
        import re
        slice_match = re.search(r'slice\(([^:]*):([^)]*)\)', segment_spec)

        if slice_match:
            # Extract segment_id (everything before 'slice')
            segment_id = segment_spec[:slice_match.start()].strip()

            # Parse slice start and end
            start_str = slice_match.group(1).strip()
            end_str = slice_match.group(2).strip()

            slice_start = float(start_str) if start_str else None
            slice_end = float(end_str) if end_str else None

            return SegmentCommand(segment_id, comment, slice_start, slice_end)
        else:
            segment_id = segment_spec
            return SegmentCommand(segment_id, comment)

    # Bang command
    if line.startswith('!'):
        command_line = line[1:].strip()
        parts = command_line.split()

        if not parts:
            raise ValueError(f"Empty command: {line}")

        command_type = parts[0]

        if command_type == 'camera':
            if len(parts) != 2:
                raise ValueError(f"Camera command requires exactly one argument: {line}")
            return CameraCommand(parts[1])

        elif command_type == 'cut':
            # Support: !cut before 50 after 50  OR  !cut 50 50
            if len(parts) == 3:
                # Shorthand: !cut 50 50
                try:
                    before_ms = float(parts[1])
                    after_ms = float(parts[2])
                    return CutCommand(before_ms, after_ms)
                except ValueError:
                    raise ValueError(f"Cut command with 2 args expects numbers: {line}")
            elif len(parts) == 5 and parts[1] == 'before' and parts[3] == 'after':
                # Full form: !cut before 50 after 50
                try:
                    before_ms = float(parts[2])
                    after_ms = float(parts[4])
                    return CutCommand(before_ms, after_ms)
                except ValueError:
                    raise ValueError(f"Cut command expects numeric values: {line}")
            else:
                raise ValueError(f"Cut command format: '!cut 50 50' or '!cut before 50 after 50': {line}")

        elif command_type == 'opening':
            if len(parts) != 2:
                raise ValueError(f"Opening command requires exactly one argument: {line}")
            try:
                preroll_ms = float(parts[1])
            except ValueError:
                raise ValueError(f"Opening preroll must be a number: {line}")
            if preroll_ms < 0:
                raise ValueError(f"Opening preroll must be non-negative: {line}")
            return OpeningPrerollCommand(preroll_ms)

        elif command_type == 'fade':
            # Support: !fade to black [duration]  OR  !fade from black [duration]
            if len(parts) >= 3:
                direction = parts[1]  # 'to' or 'from'
                target = parts[2]     # 'black'

                if target != 'black':
                    raise ValueError(f"Fade command only supports 'black' target: {line}")

                # Optional duration in milliseconds
                duration_ms = 100  # default
                if len(parts) == 4:
                    try:
                        duration_ms = float(parts[3])
                    except ValueError:
                        raise ValueError(f"Fade duration must be a number: {line}")
                elif len(parts) > 4:
                    raise ValueError(f"Too many arguments for fade command: {line}")

                if direction == 'to':
                    return FadeToBlackCommand(duration_ms)
                elif direction == 'from':
                    return FadeFromBlackCommand(duration_ms)
                else:
                    raise ValueError(f"Fade direction must be 'to' or 'from': {line}")
            else:
                raise ValueError(f"Fade command format: '!fade to black [duration]' or '!fade from black [duration]': {line}")

        elif command_type == 'black':
            # Support: !black [duration]
            if len(parts) != 2:
                raise ValueError(f"Black command requires duration: {line}")
            try:
                duration_ms = float(parts[1])
                return BlackCommand(duration_ms)
            except ValueError:
                raise ValueError(f"Black duration must be a number: {line}")

        elif command_type == 'audio':
            # Support: !audio "/path/to/file.mp3" [volume] [speed]
            # Need to parse quoted path and optional volume and speed
            import re
            import os

            # Match quoted string and optional volume and speed
            match = re.match(r'audio\s+"([^"]+)"(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?', command_line)
            if not match:
                raise ValueError(f"Audio command format: '!audio \"/path/to/file.mp3\" [volume] [speed]': {line}")

            audio_file = match.group(1)
            volume = float(match.group(2)) if match.group(2) else 1.0
            speed = float(match.group(3)) if match.group(3) else 1.0

            # Check if audio file exists
            if not os.path.exists(audio_file):
                print(f"Warning: Audio file not found, skipping: {audio_file}", file=sys.stderr)
                return None  # Skip this command

            return AudioCommand(audio_file, volume, speed)

        elif command_type == 'volume':
            # Support: !volume 1.2  OR  !volume 0.8
            if len(parts) != 2:
                raise ValueError(f"Volume command requires exactly one argument: {line}")
            try:
                volume = float(parts[1])
                if volume <= 0:
                    raise ValueError(f"Volume must be positive: {volume}")
                return VolumeCommand(volume)
            except ValueError as e:
                raise ValueError(f"Volume must be a positive number: {line}")

        elif command_type == 'shorten-join':
            from .config import (
                SHORTEN_JOIN_DEFAULT_CROSSFADE_MS,
                SHORTEN_JOIN_DEFAULT_PADDING_MS,
            )
            padding_ms = SHORTEN_JOIN_DEFAULT_PADDING_MS
            crossfade_ms = SHORTEN_JOIN_DEFAULT_CROSSFADE_MS
            if len(parts) == 3:
                try:
                    padding_ms = float(parts[1])
                    crossfade_ms = float(parts[2])
                except ValueError:
                    raise ValueError(
                        f"Shorten-join expects numeric padding and crossfade ms: {line}"
                    ) from None
            elif len(parts) != 1:
                raise ValueError(
                    f"Shorten-join format: '!shorten-join' or "
                    f"'!shorten-join 20 20': {line}"
                )
            return ShortenJoinCommand(padding_ms, crossfade_ms)

        elif command_type == 'pause-flag':
            if len(parts) != 1:
                raise ValueError(f"Pause-flag format: '!pause-flag': {line}")
            return PauseFlagCommand()

        else:
            raise ValueError(f"Unknown command type: {command_type}")

    raise ValueError(f"Invalid line format: {line}")


def parse_dsl_file(filepath: str) -> List[DSLCommand]:
    """Parse a DSL file and return list of commands"""
    commands = []

    # Support stdin if filepath is "-"
    if filepath == "-":
        f = sys.stdin
        for line_num, line in enumerate(f, 1):
            try:
                cmd = parse_dsl_line(line)
                if cmd:
                    commands.append(cmd)
            except Exception as e:
                raise ValueError(f"Error parsing line {line_num}: {e}")
    else:
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    cmd = parse_dsl_line(line)
                    if cmd:
                        commands.append(cmd)
                except Exception as e:
                    raise ValueError(f"Error parsing line {line_num}: {e}")

    return commands
