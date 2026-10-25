# SPDX-License-Identifier: MIT OR Apache-2.0
"""Blocking control client and background change following for bridge/1."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
from typing import Any, Callable, Iterator, Protocol

__version__ = "0.1.0"
CONTRACT = "bridge/1"
MAX_LINE = 16 * 1024 * 1024


class Error(Exception):
    """Base for all control failures."""


class DiscoveryError(Error):
    """No unique endpoint could be selected."""


class TransportError(Error):
    """Connection failed; a sent command's outcome may be unknown."""


class AuthenticationError(Error):
    """The endpoint refused authentication."""


class ProtocolError(Error):
    """Malformed or mismatched reply."""


class CommandError(Error):
    """The server refused a request."""


class BusyError(CommandError):
    """A previous request is still running on this connection."""


class RunningError(CommandError):
    """The request is still running. Use pending(); never resend it."""


class UnsupportedFormatError(CommandError):
    """This Bridge version does not support the requested format."""


def control_dir() -> Path:
    """Return the platform control-token directory, honoring overrides."""
    if os.environ.get("SOLIDCRAFT_CONTROL_DIR"):
        return Path(os.environ["SOLIDCRAFT_CONTROL_DIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "SolidCraft/control"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/SolidCraft/control"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "solidcraft/control"


def _host_control_dir() -> Path | None:
    """The host's default token directory when this process runs in a Flatpak sandbox.

    A Flatpak app (Blender from Flathub, for example) gets its own XDG_DATA_HOME under
    ~/.var/app, while SolidCraft writes its tokens to the host's ~/.local/share. Reading them
    needs the sandbox to be granted that folder, e.g.
    `flatpak override --user --filesystem=~/.local/share/solidcraft:ro org.blender.Blender`.
    """
    if sys.platform == "win32" or sys.platform == "darwin" or os.environ.get("SOLIDCRAFT_CONTROL_DIR"):
        return None
    if not _in_flatpak():
        return None
    return Path.home() / ".local/share/solidcraft/control"


def _in_flatpak() -> bool:
    return sys.platform not in ("win32", "darwin") and bool(os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").exists())


def _export_parent() -> str | None:
    """Where export files go: SolidCraft writes them from outside any sandbox.

    A Flatpak's /tmp is private, so SolidCraft's file would land in the host's /tmp, out of
    reach. The sandbox's own cache folder (~/.var/app/<id>/cache) is a real host path the
    sandbox can read, so exports go there. Elsewhere the system temp folder is used.
    """
    if _in_flatpak() and os.environ.get("XDG_CACHE_HOME"):
        Path(os.environ["XDG_CACHE_HOME"]).mkdir(parents=True, exist_ok=True)
        return os.environ["XDG_CACHE_HOME"]
    return None


def token_path(port: int) -> Path:
    """Return the token location for a loopback port."""
    path = control_dir() / f"{port}.token"
    host = _host_control_dir()
    if host is not None and not path.exists() and (host / f"{port}.token").exists():
        return host / f"{port}.token"
    return path


@dataclass(frozen=True)
class Endpoint:
    """A discovered candidate; discovery does not open a connection."""
    port: int
    token_file: Path


def discover(directory: str | Path | None = None) -> list[Endpoint]:
    """List valid token filenames in ascending port order, without probing ports."""
    if directory is not None:
        folders = [Path(directory)]
    else:
        host = _host_control_dir()
        folders = [control_dir()] + ([host] if host is not None else [])
    paths = []
    for folder in folders:
        try:
            paths.extend(folder.iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DiscoveryError("Cannot list control directory") from exc
    out = []
    for path in paths:
        stem = path.stem
        if (path.suffix == ".token" and stem.isascii() and stem.isdecimal()
                and str(int(stem)) == stem and 1 <= int(stem) <= 65535 and path.is_file()):
            if all(endpoint.port != int(stem) for endpoint in out):
                out.append(Endpoint(int(stem), path))
    return sorted(out, key=lambda endpoint: endpoint.port)


class Transport(Protocol):
    """Replaceable JSON-line boundary. exchange returns exactly one reply line."""
    def exchange(self, request: bytes) -> bytes: ...
    def close(self) -> None: ...


class SocketTransport:
    """Loopback-only transport with 5 second connect and 65 second read deadlines."""
    def __init__(self, port: int, connect_timeout: float = 5, read_timeout: float = 65):
        self.sock = socket.create_connection(("127.0.0.1", port), connect_timeout)
        self.sock.settimeout(read_timeout)
        self.stream = self.sock.makefile("rb")

    def exchange(self, request: bytes) -> bytes:
        self.sock.sendall(request)
        return self.stream.readline(MAX_LINE + 1)

    def close(self) -> None:
        self.stream.close()
        self.sock.close()


@dataclass(frozen=True)
class Geometry:
    """An export in native units with the inspected body names."""
    format: str
    data: bytes
    body_names: tuple[str, ...]


@dataclass(frozen=True)
class Revision:
    """Opaque equality token; do not parse value or compare it numerically."""
    value: str
    body_names: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    """Current revision and conservative body differences; initial means full snapshot."""
    revision: Revision
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]
    initial: bool


def difference(previous: Revision | None, current: Revision) -> Change:
    """Compare body membership; surviving bodies may have changed as a group."""
    old = set(previous.body_names) if previous else set()
    new = set(current.body_names)
    return Change(current, tuple(sorted(new - old)), tuple(sorted(old - new)),
                  tuple(sorted(new & old)) if previous != current else (), previous is None)


class Session:
    """Serialized blocking access. Use a worker for every call from a UI."""
    def __init__(self, port: int, token: str | None = None, *,
                 token_file: Path | None = None,
                 transport_factory: Callable[[], Transport] | None = None):
        if not 1 <= port <= 65535:
            raise DiscoveryError("Port must be between 1 and 65535")
        self.port = port
        self._token = token
        self._token_file = token_file or token_path(port)
        self._factory = transport_factory or (lambda: SocketTransport(port))
        self._transport: Transport | None = None
        self._seq = 0
        self._lock = threading.RLock()
        self._followers: list[Follower] = []

    @classmethod
    def connect(cls, port: int | Endpoint | None = None, token: str | None = None,
                **kwargs: Any) -> Session:
        """Choose an explicit endpoint or the sole candidate, then authenticate."""
        if port is None:
            endpoints = discover()
            if len(endpoints) != 1:
                raise DiscoveryError("Choose a port explicitly; expected exactly one candidate")
            port = endpoints[0]
        if isinstance(port, Endpoint):
            kwargs.setdefault("token_file", port.token_file)
            port = port.port
        result = cls(port, token, **kwargs)
        try:
            result.call("document.inspect", {"measure": False})
        except Exception:
            result.close()
            raise
        return result

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _disconnect(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    def close(self) -> None:
        """Stop followers and close the connection; bounded by transport deadlines."""
        for follower in self._followers:
            follower.stop()
        with self._lock:
            self._disconnect()

    def reconnect(self) -> None:
        """Authenticate anew, rereading automatic tokens. Never replays commands."""
        with self._lock:
            self._disconnect()
            self.call("document.inspect", {"measure": False})

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send once. Running/busy replies preserve the connection for pending()."""
        with self._lock:
            try:
                first = self._transport is None
                token = None
                if first:
                    token = self._token if self._token is not None else self._token_file.read_text().strip()
                    if not token:
                        raise AuthenticationError("The control token is empty")
                    self._transport = self._factory()
                self._seq += 1
                request = dict(id=self._seq, method=method, params=params or {})
                if first:
                    request["token"] = token
                data = (json.dumps(request, allow_nan=False) + "\n").encode()
                if len(data) > MAX_LINE:
                    raise ProtocolError("Request exceeds 16 MiB")
                assert self._transport is not None
                line = self._transport.exchange(data)
                if not line:
                    raise TransportError("SolidCraft closed the connection")
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ProtocolError("Closed connection, oversized or incomplete reply")
                def reject_constant(value: str) -> None:
                    raise ProtocolError("Non-finite JSON number in reply")
                reply = json.loads(line, parse_constant=reject_constant)
                if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                    raise ProtocolError("Invalid reply envelope")
                if not reply["ok"]:
                    message = str(reply.get("error", "Control request failed"))
                    if message.lower().startswith("unauthorized"):
                        raise AuthenticationError(message)
                    if reply.get("running"):
                        raise RunningError(message)
                    if reply.get("busy"):
                        raise BusyError(message)
                    if reply.get("id") != self._seq:
                        raise ProtocolError("Reply id mismatch")
                    raise CommandError(message)
                if reply.get("id") != self._seq or "result" not in reply:
                    raise ProtocolError("Reply id mismatch or missing result")
                return reply["result"]
            except TransportError:
                self._disconnect()
                raise
            except (OSError, TimeoutError) as exc:
                self._disconnect()
                raise TransportError("Control I/O failed; sent commands may still apply") from exc
            except (ValueError, UnicodeError) as exc:
                self._disconnect()
                raise ProtocolError("Invalid JSON reply or request") from exc
            except (ProtocolError, AuthenticationError):
                self._disconnect()
                raise

    def _inspect(self) -> dict[str, Any]:
        info = self.call("document.inspect", {"measure": False})
        if not isinstance(info, dict):
            raise ProtocolError("document.inspect must return an object")
        return info

    def parameters(self) -> Any:
        """Read documented parameter data without measuring geometry."""
        return self._inspect().get("parameters", [])

    def bodies(self) -> list[dict[str, Any]]:
        """Read body records in the server's native schema."""
        bodies = self._inspect().get("bodies", [])
        if not isinstance(bodies, list) or any(not isinstance(b, dict) or not isinstance(b.get("name"), str) for b in bodies):
            raise ProtocolError("Invalid body list")
        return bodies

    def execute(self, command: str, params: dict[str, Any] | None = None) -> Any:
        """Run a command exactly once, including after a lost reply."""
        return self.call("engine.execute", {"command": command, "params": params or {}})

    def pending(self) -> Any:
        """Retrieve the outstanding reply on this connection after RunningError."""
        return self.call("control.pending")

    def export(self, fmt: str = "3mf") -> Geometry:
        """Export through a private temporary file, then return bytes and body names."""
        fmt = fmt.lower()
        if fmt not in ("3mf", "obj", "step"):
            raise UnsupportedFormatError("Supported formats: 3mf, obj, step; GLB awaits upstream support")
        with self._lock, tempfile.TemporaryDirectory(prefix="solidcraft-bridge-", dir=_export_parent()) as folder:
            before = self.bodies()
            path = Path(folder) / ("design." + fmt)
            self.execute("file.export", {"path": str(path), "format": fmt})
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise TransportError("Cannot read exported file") from exc
            after = self.bodies()
            if before != after:
                raise BusyError("Body metadata changed during export; retry later")
            return Geometry(fmt, data, tuple(b["name"] for b in after))

    def revision(self) -> Revision:
        """Hash canonical OBJ content, body metadata and parameters; no undo-depth guessing."""
        with self._lock:
            before = self._inspect()
            geometry = self.export("obj")
            after = self._inspect()
            fields = lambda info: {k: info.get(k) for k in ("name", "path", "bodies", "parameters")}
            if fields(before) != fields(after):
                raise BusyError("Design metadata changed during export; retry later")
            obj = b"\n".join(line.strip() for line in geometry.data.splitlines()
                             if line.strip() and not line.lstrip().startswith(b"#"))
            metadata = json.dumps(fields(after), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            return Revision("content:" + hashlib.sha256(metadata + b"\0" + obj).hexdigest(), geometry.body_names)

    def follow(self, callback: Callable[[Change], None] | None = None,
               interval: float = 1.0) -> Follower:
        """Start a worker; callbacks run there, iterators consume a coalesced queue."""
        follower = Follower(self, callback, interval)
        self._followers.append(follower)
        return follower


class Follower(Iterator[Change]):
    """Background polling with interruptible exponential reconnect backoff."""
    def __init__(self, owner: Session, callback: Callable[[Change], None] | None, interval: float):
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._queue: queue.Queue[Change] = queue.Queue(maxsize=1)
        self._thread = threading.Thread(target=self._run, args=(owner, callback, interval), daemon=True)
        self._thread.start()

    def _run(self, owner: Session, callback: Callable[[Change], None] | None, interval: float) -> None:
        previous = None
        delay = .25
        while not self._stop.is_set():
            try:
                current = owner.revision()
                if current != previous:
                    change = difference(previous, current)
                    if callback:
                        callback(change)
                        previous = current
                    else:
                        try:
                            self._queue.put_nowait(change)
                            previous = current
                        except queue.Full:
                            pass  # Recompute relative to the last delivered snapshot.
                delay = .25
                self._stop.wait(interval)
            except (TransportError, BusyError):
                self._stop.wait(delay)
                delay = min(delay * 2, 8)
            except Exception as exc:
                self.error = exc
                self._stop.set()

    def __iter__(self) -> Follower:
        return self

    def __next__(self) -> Change:
        while True:
            if self._stop.is_set():
                if self.error:
                    raise self.error
                raise StopIteration
            try:
                return self._queue.get(timeout=.1)
            except queue.Empty:
                pass

    def stop(self) -> None:
        """Request stop and join outside the worker, bounded by in-flight I/O."""
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join()
