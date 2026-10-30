"""Installation identity scoped to this desktop data directory."""
import uuid

from guest_files import DATA_PATH


def installation_id(files):
    if files is None:
        raise RuntimeError("installation identity requires a desktop data directory")
    path = files.resolve(DATA_PATH + "/.desktop-installation-id", writable=True)
    try:
        with path.open("x", encoding="ascii") as output:
            value = str(uuid.uuid4())
            output.write(value + "\n")
    except FileExistsError:
        value = path.read_text(encoding="ascii").strip()
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise RuntimeError(f"invalid desktop installation identity at {path}") from error
    return str(parsed)
