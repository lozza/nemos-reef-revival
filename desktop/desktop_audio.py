"""Desktop implementation of the recovered SdrMediaPlayer Java contract."""
from pathlib import PurePosixPath
import threading

from desktop_music import read_music_asset

CLASS = 'net/livingmobile/sdr/sfx/SdrMediaPlayer'


class MediaPlayer:
    def __init__(self, files):
        self.files = files
        self.device = None
        self.pcm = b''
        self.position = 0
        self.loop = False
        self.prepared = False
        self.playing = False
        self.error = None
        self.name = None
        self.generation = 0
        self.cleanup_worker = None
        self.state_lock = threading.RLock()

    def create(self, name, loop, from_assets):
        self.release()
        self.name = name
        self.loop = bool(loop)
        import miniaudio
        try:
            data = read_music_asset(self.files, name)
            if data is None:
                if from_assets:
                    path = PurePosixPath(name)
                    if path.is_absolute() or '..' in path.parts or '\\' in name:
                        raise ValueError('audio asset path leaves APK assets')
                    data = self.files.read_asset(name)
                else:
                    data = self.files.resolve(name).read_bytes()
            from audio_resources import DecodedAudioCache
            cache = getattr(self.files, '_decoded_audio_cache', None)
            if cache is None:
                self.files._decoded_audio_cache = cache = DecodedAudioCache()
            self.pcm = cache.decode(data, lambda source: miniaudio.decode(
                source, output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=2, sample_rate=44100).samples.tobytes())
            self.prepared = bool(self.pcm)
            self.error = None
        except (OSError, KeyError, ValueError, miniaudio.MiniaudioError) as error:
            # The Java wrapper catches missing/unreadable files and stays unprepared.
            self.error = str(error)

    def render(self, frames, generation=None):
        with self.state_lock:
            if generation is not None and generation != self.generation:
                return bytes(frames*4)
            return self._render(frames)

    def _render(self, frames):
        remaining = frames * 4
        output = bytearray()
        while remaining and self.playing:
            count = min(remaining, len(self.pcm) - self.position)
            output.extend(self.pcm[self.position:self.position + count])
            self.position += count
            remaining -= count
            if self.position == len(self.pcm):
                if self.loop:
                    self.position = 0
                else:
                    self.playing = False
        output.extend(bytes(remaining))
        return bytes(output)

    def stream(self):
        generation = self.generation
        frames = yield b''
        while True:
            frames = yield self.render(frames, generation)

    def start(self):
        with self.state_lock:
            if not self.prepared:
                return
            if self.playing or self.position == len(self.pcm):
                self.position = 0
        if self.device is None:
            import miniaudio
            try:
                self.device = miniaudio.PlaybackDevice(output_format=miniaudio.SampleFormat.SIGNED16,
                                                      nchannels=2, sample_rate=44100)
                stream = self.stream()
                next(stream)
                self.device.start(stream)
            except (miniaudio.MiniaudioError, OSError) as error:
                self.error = str(error)
                self.playing = self.prepared = False
                if self.device is not None:
                    try:
                        self.device.close()
                    except (miniaudio.MiniaudioError, OSError):
                        pass
                    self.device = None
                return
        with self.state_lock:
            self.playing = True

    def pause(self):
        with self.state_lock:
            self.playing = False

    def stop(self):
        with self.state_lock:
            self.playing = False
            self.position = 0

    def release(self):
        with self.state_lock:
            self.playing = False
            self.generation += 1
            device, self.device = self.device, None
            self.pcm = b''
            self.position = 0
            self.prepared = False
        if device is not None:
            from audio_resources import AudioCleanup
            cleanup = getattr(self.files, '_audio_cleanup', None) if self.files is not None else self.cleanup_worker
            if cleanup is None or cleanup.closed:
                cleanup = AudioCleanup()
                if self.files is not None:
                    self.files._audio_cleanup = cleanup
            self.cleanup_worker = cleanup
            cleanup.release(device)


def invoke(runtime, receiver, method, arguments):
    reference = runtime.ref(receiver)
    if reference['class'] != CLASS or 'media' not in reference or method.get('static'):
        raise RuntimeError('invalid desktop media receiver')
    player = reference['media']
    signature = method['method'], method['descriptor']
    if signature == ('createMediaPlayer', '(Ljava/lang/String;ZZ)V'):
        name, loop, assets = arguments
        player.create(runtime.ref(name)['value'], loop, assets)
        runtime.platform_logs.append({'service': 'audio', 'prepared': player.prepared,
                                      'file': runtime.ref(name)['value'], 'error': player.error})
    elif signature == ('setLooping', '(Z)V'):
        player.loop = bool(arguments[0])
    elif signature == ('isPlaying', '()Z'):
        return int(player.prepared and player.playing)
    elif signature == ('isPaused', '()Z'):
        return int(player.prepared and not player.playing)
    elif signature in ((name, '()V') for name in ('start', 'stop', 'pause', 'release')):
        getattr(player, method['method'])()
        if player.name and (player.name.startswith('music/') or '/music/' in player.name):
            runtime.platform_logs.append({'service': 'audio', 'operation': method['method'],
                                          'file': player.name, 'prepared': player.prepared,
                                          'playing': player.playing, 'loop': player.loop,
                                          'error': player.error})
        if method['method'] == 'start' and player.error:
            runtime.platform_logs.append({'service': 'audio', 'operation': 'start',
                                          'prepared': player.prepared, 'error': player.error})
    else:
        raise RuntimeError(f'unimplemented desktop media contract: {method}')
    return 0


def close_all(runtime):
    seen = set()
    cleanups = set()
    for reference in runtime.references.values():
        player = reference.get('media')
        if player is not None and id(player) not in seen:
            seen.add(id(player))
            player.release()
            if player.cleanup_worker is not None:
                cleanups.add(player.cleanup_worker)
    shared_cleanup = getattr(getattr(runtime, 'files', None), '_audio_cleanup', None)
    if shared_cleanup is not None:
        cleanups.add(shared_cleanup)
    for cleanup in cleanups:
        cleanup.finish()
