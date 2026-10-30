"""Read user-supplied soundtrack tracks beside the executable or source root."""
from desktop_paths import project_root
from guest_files import DATA_PATH


def resolve_music_asset(name):
    """Return an external track path, or None for normal asset lookup.

    Keep this separate from general guest files: external music must never
    replace sound effects or expose arbitrary host paths to the original game.
    The original game asks for Ogg names; miniaudio also decodes MP3 contents.
    """
    tracks = {prefix + f'music{index}.ogg': index
              for prefix in ('music/', DATA_PATH + '/music/', DATA_PATH + '/.cache/cache/music/')
              for index in range(4)}
    index = tracks.get(name)
    if index is None:
        return None
    folder = (project_root() / 'music').resolve()
    for extension in ('ogg', 'mp3'):
        candidate = folder / f'music{index}.{extension}'
        resolved = candidate.resolve()
        if not resolved.is_relative_to(folder):
            continue
        if resolved.is_file():
            return resolved
    return None


def read_music_asset(files, name):
    """Read a recognized track without changing the APK or guest cache."""
    path = resolve_music_asset(name)
    return path.read_bytes() if path is not None else None
