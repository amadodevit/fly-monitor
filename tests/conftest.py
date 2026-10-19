import pytest


class SilentDesktop:
    """Records batches instead of showing popups."""

    def __init__(self):
        self.sent = []

    def send(self, events):
        if events:
            self.sent.append(list(events))


@pytest.fixture(autouse=True)
def no_desktop_popups(monkeypatch):
    # Core() defaults to the real DesktopChannel; tests must never pop up on the developer's desktop.
    monkeypatch.setattr("fly_monitor.core.engine.DesktopChannel", SilentDesktop)
