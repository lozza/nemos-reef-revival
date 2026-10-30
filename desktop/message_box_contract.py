"""Original Root.createMessageBox / MessageBox contract (see work/message-box-contract.md).

Recovered from MessageBox.smali and libsdr.so (onButtonPress 0x98e880,
getButtonName 0x98e8b4, MessageBoxClosedCommand::perform 0x98eb08).
"""

from dataclasses import dataclass, field

CREATE_METHOD = "createMessageBox"
CREATE_SIGNATURE = "(Ljava/lang/String;Ljava/lang/String;IJJ)Lnet/livingmobile/sdr/appui/MessageBox;"
REMOVE_METHOD = "removeMessageBox"
REMOVE_SIGNATURE = "(Lnet/livingmobile/sdr/appui/MessageBox;)V"
ON_BUTTON_PRESS = "Java_net_livingmobile_sdr_appui_MessageBox_onButtonPress"
GET_BUTTON_NAME = "Java_net_livingmobile_sdr_appui_MessageBox_getButtonName"
SDR_STRING_SIZE = 16  # getButtonName: names + (index << 4)


@dataclass(frozen=True)
class Choice:
    role: str    # Android AlertDialog button slot
    button: int  # getButtonName index and onButtonPress value


def choices_for(button_count):
    """Buttons added by MessageBox.<init>; counts other than 2 and 3 get only the positive button."""
    if button_count == 3:
        return (Choice("positive", 0), Choice("neutral", 1), Choice("negative", 2))
    if button_count == 2:
        return (Choice("positive", 0), Choice("negative", 1))
    return (Choice("positive", 0),)


def button_name_address(button_names, index):
    return (button_names + SDR_STRING_SIZE * index) & 0xffffffff


@dataclass(frozen=True)
class ClosedResult:
    """Equivalent of MessageBoxClosedCommand {button, listener}; perform() calls listener->vtable[2](button)."""
    button: int
    listener: int


@dataclass
class PendingMessageBox:
    title: str
    text: str
    button_count: int
    button_names: int
    listener: int
    labels: tuple = ()
    _result: object = field(default=None, init=False, repr=False)
    _resolved: bool = field(default=False, init=False, repr=False)

    def __post_init__(self):
        self.button_names &= 0xffffffff
        self.listener &= 0xffffffff
        self.labels = tuple(self.labels)
        if self.labels and len(self.labels) != len(self.choices):
            raise ValueError(f"expected {len(self.choices)} labels, got {len(self.labels)}")

    @property
    def choices(self):
        return choices_for(self.button_count)

    @property
    def allowed_buttons(self):
        return tuple(c.button for c in self.choices)

    def label_addresses(self):
        return tuple(button_name_address(self.button_names, c.button) for c in self.choices)

    @property
    def resolved(self):
        return self._resolved

    @property
    def result(self):
        return self._result

    def invalidate_listener(self):
        """MessageBox.invalidateListenerPointer(): later presses reach native code with 0 and are dropped."""
        self.listener = 0

    def press(self, button):
        """Resolve once. Returns the command to queue, or None when the native side drops it (listener 0)."""
        if self._resolved:
            raise RuntimeError("message box already resolved")
        if button not in self.allowed_buttons:
            raise ValueError(f"button {button!r} not offered for count {self.button_count}")
        self._resolved = True
        self._result = ClosedResult(button, self.listener) if self.listener else None
        return self._result

    def press_role(self, role):
        for choice in self.choices:
            if choice.role == role:
                return self.press(choice.button)
        raise ValueError(f"no {role!r} button for count {self.button_count}")


def on_button_press_arguments(env, this, button, listener):
    """ARM register/stack layout for the original export: r0-r2 then the jlong at sp+0/sp+4 (r3 unused)."""
    return (env, this, button, 0), (listener & 0xffffffff, (listener >> 32) & 0xffffffff)
