"""Watch file system changes in project directories."""
import time
import threading
from pathlib import Path
from collections import deque
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Extensions we care about
CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.rs', '.java',
    '.json', '.yaml', '.yml', '.toml', '.env', '.sh', '.bat',
    '.html', '.css', '.scss', '.md', '.sql', '.dockerfile',
}

# Directories to ignore
IGNORE_DIRS = {
    'node_modules', '.git', '__pycache__', 'venv', '.venv',
    'dist', 'build', '.next', '.cache', 'sentinel',
}


class ProjectEventHandler(FileSystemEventHandler):
    def __init__(self, max_events=200):
        self.events = deque(maxlen=max_events)
        self._lock = threading.Lock()

    def _should_track(self, path: str) -> bool:
        p = Path(path)
        # Ignore directories in blacklist
        for part in p.parts:
            if part in IGNORE_DIRS:
                return False
        # Only track code-related files
        if p.suffix.lower() not in CODE_EXTENSIONS:
            return False
        return True

    def on_any_event(self, event):
        if event.is_directory:
            return
        path = event.src_path
        if not self._should_track(path):
            return
        with self._lock:
            self.events.append({
                'time': time.time(),
                'type': event.event_type,  # created, modified, deleted, moved
                'path': path,
            })

    def drain_events(self) -> list:
        """Get all buffered events and clear."""
        with self._lock:
            events = list(self.events)
            self.events.clear()
        return events


class FileWatcher:
    def __init__(self, watch_dirs: list[Path]):
        self.handler = ProjectEventHandler()
        self.observer = Observer()
        for d in watch_dirs:
            if d.exists():
                self.observer.schedule(self.handler, str(d), recursive=True)

    def start(self):
        self.observer.start()

    def stop(self):
        self.observer.stop()
        self.observer.join()

    def get_events(self) -> list:
        return self.handler.drain_events()
