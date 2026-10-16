"""
Main-thread bookkeeping for who owns the recorder.

Every method here runs on the Qt main thread (key events and wake events are
both re-emitted there as queued signals), so there is no lock.
"""


class WakeGate:
    def __init__(self):
        self.source = None      # 'key' | 'wake' | None
        self.thread = None      # the ResultThread that currently owns the recorder
        self.hwnd = None        # foreground window recorded when a wake was accepted
        self.rearm_at = 0.0     # monotonic time the last recording fully finished

    def busy(self):
        return self.source is not None

    def accept_wake(self, detected_at):
        """
        A wake may start a recording only when nothing owns the recorder and it was
        detected after the last recording finished. Wakes that queued up behind a
        synchronous typewrite carry an older timestamp and are dropped.
        """
        return not self.busy() and detected_at > self.rearm_at

    def keys_ignored(self):
        """Pause press/release must not stop or restart a wake recording."""
        return self.source == 'wake'

    def begin(self, source, thread, hwnd=None):
        self.source = source
        self.thread = thread
        self.hwnd = hwnd

    def finish(self, thread, now):
        """
        Called from ResultThread.finished. Only the thread that currently owns the
        recorder may release it; a late `finished` from a replaced thread is ignored.
        """
        if thread is not self.thread:
            return False
        self.source = None
        self.thread = None
        self.hwnd = None
        self.rearm_at = now
        return True
