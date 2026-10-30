"""Bind original message boxes to host state and deferred ARM callbacks."""
from message_box_contract import PendingMessageBox, ON_BUTTON_PRESS, choices_for, button_name_address

ROOT_CLASS = 'net/livingmobile/sdr/appui/Root'
BOX_CLASS = 'net/livingmobile/sdr/appui/MessageBox'


def create_box(runtime, arguments):
    title, text, count, names, listener = arguments

    def string(handle):
        if not handle:
            return ''
        reference = runtime.ref(handle)
        if reference.get('kind') != 'string':
            raise RuntimeError('message-box text is not a Java string')
        return reference['value']

    labels = []
    for choice in choices_for(count):
        address = button_name_address(names, choice.button)
        pointer, length = runtime.word(address + 4), runtime.word(address + 8)
        if length > 65536:
            raise RuntimeError('invalid original message-box label length')
        labels.append(bytes(runtime.machine.mem_read(pointer, length * 2)).decode('utf-16-le', 'surrogatepass')
                      if length else '')
    box = PendingMessageBox(string(title), string(text), count, names, listener, labels=tuple(labels))
    runtime.message_box = box
    runtime.message_box_delivered = None
    runtime.platform_logs.append({'service': 'message_box', 'operation': 'create',
                                  'title': box.title, 'text': box.text, 'buttons': list(box.labels)})
    return runtime.reference(BOX_CLASS, scope='local', message_box=box)


def remove_box(runtime, handle):
    reference = runtime.ref(handle)
    if reference.get('class') != BOX_CLASS or 'message_box' not in reference:
        raise RuntimeError('invalid message-box removal')
    box = reference['message_box']
    if runtime.message_box is box:
        runtime.message_box = None
    runtime.platform_logs.append({'service': 'message_box', 'operation': 'remove'})


def deliver_reply(inputs):
    """Run the recovered JNI click export only between completed guest frames."""
    runtime = inputs.runtime
    box = runtime.message_box
    if box is None or not box.resolved or runtime.message_box_delivered is box:
        return
    inputs.require_idle()
    runtime.message_box_delivered = box
    result = box.result
    if result is not None:
        from trace_startup import ENV
        # jint button in r2; jlong listener is aligned onto the stack.
        runtime.call(ON_BUTTON_PRESS, ENV, 0, result.button, 0, result.listener, 0)
    runtime.platform_logs.append({'service': 'message_box', 'operation': 'reply',
                                  'button': result.button if result is not None else None})
