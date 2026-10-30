"""Desktop state for the recovered Root/TextField platform contract."""
from dataclasses import dataclass

CREATE_DESCRIPTOR = "(Ljava/lang/String;IIIIIIIILjava/lang/String;JIILjava/lang/String;Ljava/lang/String;)V"


@dataclass
class TextEntry:
    text: str
    x: int
    y: int
    width: int
    height: int
    font_height: int
    input_type: int
    character_limit: int
    filters: int
    custom_filter: str
    listener: int
    text_color: int
    text_align: int
    placeholder: str
    family: str
    active: bool = True
    changed: bool = False

    def filtered(self, value, limit=None):
        """Match the recovered filter's accepted prefix and UTF-16 length limit."""
        result = []
        units = 0
        for char in value:
            allowed = (not self.filters or
                       self.filters & 1 and ("A" <= char <= "Z" or "a" <= char <= "z") or
                       self.filters & 2 and "0" <= char <= "9" or
                       self.filters & 4 and char == "_" or
                       self.filters & 8 and char == " " or
                       self.filters & 16 and char in self.custom_filter)
            if not allowed or self.input_type == 1 and not "0" <= char <= "9":
                break
            count = len(char.encode("utf-16-le", "surrogatepass")) // 2
            bound = self.character_limit if limit is None else limit
            if bound >= 0 and (bound > 0 or limit is not None) and units + count > bound:
                break
            result.append(char)
            units += count
        return "".join(result)

    def replace(self, value):
        value = self.filtered(value)
        if self.text != value:
            self.text = value
            self.changed = True

    def replace_edited(self, value):
        """Filter only the inserted text; retain the existing valid suffix."""
        old = self.text
        prefix = 0
        while prefix < min(len(old), len(value)) and old[prefix] == value[prefix]:
            prefix += 1
        old_end, new_end = len(old), len(value)
        while old_end > prefix and new_end > prefix and old[old_end - 1] == value[new_end - 1]:
            old_end -= 1
            new_end -= 1
        retained = old[:prefix] + old[old_end:]
        remaining = (max(0, self.character_limit - len(retained.encode("utf-16-le", "surrogatepass")) // 2)
                     if self.character_limit > 0 else None)
        accepted = self.filtered(value[prefix:new_end], remaining)
        result = old[:prefix] + accepted + old[old_end:]
        if result != old:
            self.text = result
            self.changed = True


def create_entry(runtime, arguments):
    values = list(arguments)
    for index in (0, 9, 13, 14):
        handle = values[index]
        if handle:
            ref = runtime.ref(handle)
            if ref["kind"] != "string":
                raise RuntimeError("text-entry argument is not a string")
            values[index] = ref["value"]
        else:
            values[index] = ""
    entry = TextEntry(*values)
    if entry.input_type not in (0, 1, 2) or entry.filters & ~31 or not 0 <= entry.listener <= 0xffffffff:
        raise RuntimeError("unsupported desktop text-entry configuration")
    if not 0 < entry.width <= 16384 or not 0 < entry.height <= 16384:
        raise RuntimeError("invalid desktop text-entry geometry")
    initial = entry.text
    entry.text = ""
    entry.replace(initial)
    return entry
