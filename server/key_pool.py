import threading

from .config import BizyAirKeyConfig

class BizyAirKeyPool:
    def __init__(self, keys: tuple[BizyAirKeyConfig, ...]):
        self.keys = list(keys)
        self.next_index = 0
        self.lock = threading.Lock()

    def pick(self, preferred_id: str | None = None) -> BizyAirKeyConfig:
        with self.lock:
            if not self.keys:
                raise RuntimeError("未配置 BizyAir API Key")
            preferred_key = next((key for key in self.keys if key.id == preferred_id), None) if preferred_id else None
            if preferred_key:
                return preferred_key
            key = self.keys[self.next_index % len(self.keys)]
            self.next_index = (self.next_index + 1) % len(self.keys)
            return key
