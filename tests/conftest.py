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
    monkeypatch.setattr("watchkeep.core.engine.DesktopChannel", SilentDesktop)


class FakeKeyring:
    """An in-memory keyring. Tests must never touch the developer's real one."""

    priority = 5

    def __init__(self):
        self.entries = {}
        self.locked = False

    def get_keyring(self):
        return self

    def get_password(self, service, name):
        self._check()
        return self.entries.get((service, name))

    def set_password(self, service, name, value):
        self._check()
        self.entries[(service, name)] = value

    def delete_password(self, service, name):
        self._check()
        self.entries.pop((service, name), None)

    def _check(self):
        if self.locked:
            from keyring.errors import KeyringLocked

            raise KeyringLocked("locked")


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr("watchkeep.core.store.keyring", fake)
    return fake
