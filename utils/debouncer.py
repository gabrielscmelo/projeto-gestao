class Debouncer:
    def __init__(self, widget, delay=350):
        self.widget = widget
        self.delay = delay
        self._timer_id = None

    def bind(self, callback):
        def _on_key(event):
            if self._timer_id:
                self.widget.after_cancel(self._timer_id)
            self._timer_id = self.widget.after(self.delay, callback)
        self.widget.bind("<KeyRelease>", _on_key)