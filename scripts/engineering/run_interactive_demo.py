"""Opt-in supervisor for a native Django test-runner-owned fictional demo.

No business server, database maintenance, dependency installation or publication
command is implemented here. Runtime records are local, owner-only and temporary.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_SESSION = None
PERMISSIONS = (
    "catalog.add_product", "catalog.add_variant", "catalog.view_product", "catalog.view_variant",
    "catalog.add_category", "catalog.view_category", "catalog.set_product_category", "catalog.set_product_active",
    "inventory.receive_stock", "inventory.view_stockposition", "inventory.view_stockmovement",
)


class DemoError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise DemoError(message)


def private_directory(path):
    path = Path(path)
    require(not path.is_symlink(), "Session directory symlink refused")
    resolved = path.resolve()
    require(resolved != ROOT and ROOT not in resolved.parents, "Artifacts must be outside the checkout")
    metadata = resolved.stat()
    require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.getuid()
            and stat.S_IMODE(metadata.st_mode) == 0o700, "Session directory must be owner-only")
    return resolved


def private_bytes(path):
    path = Path(path)
    metadata = path.lstat()
    require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and metadata.st_uid == os.getuid()
            and stat.S_IMODE(metadata.st_mode) == 0o600, "Unsafe runtime file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        require(os.fstat(stream.fileno()).st_ino == metadata.st_ino, "Runtime file changed")
        value = stream.read(1_000_001)
    require(len(value) <= 1_000_000, "Oversized runtime file")
    return value


def read_state(path):
    return json.loads(private_bytes(path))


def write_private(path, value, *, exclusive=False):
    path = Path(path)
    private_directory(path.parent)
    data = value if isinstance(value, bytes) else (json.dumps(value, sort_keys=True) + "\n").encode()
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        return
    if path.exists() or path.is_symlink():
        private_bytes(path)
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    write_private(temporary, data, exclusive=True)
    os.replace(temporary, path)


def remove_credentials(directory):
    path = private_directory(directory) / "access.json"
    if path.exists() or path.is_symlink():
        private_bytes(path)
        path.unlink()


def verify_no_secret_logs(directory, values):
    for name in ("runner.log", "build.log", "next.log"):
        path = Path(directory) / name
        if path.exists():
            data = private_bytes(path)
            require(not any(value.encode() in data for value in values if value), "Sensitive runtime output detected; stop for review")


def process_identity(pid):
    try:
        os.kill(pid, 0)
        pgid = os.getpgid(pid)
        proc = Path(f"/proc/{pid}/stat")
        if proc.exists():
            fields = proc.read_text().rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                return None
            birth = fields[19]
            uid = proc.stat().st_uid
        else:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "uid=", "-o", "lstart="], capture_output=True, text=True,
                                    env={**os.environ, "TZ": "UTC", "LC_ALL": "C"}, timeout=3)
            if result.returncode or not result.stdout.strip():
                return None
            owner, birth = result.stdout.strip().split(None, 1)
            uid = int(owner)
        return {"pid": pid, "pgid": pgid, "uid": uid, "birth": birth}
    except (ProcessLookupError, FileNotFoundError):
        return None


def record_process(process):
    identity = process_identity(process.pid)
    require(identity is not None and identity["pgid"] == process.pid and identity["uid"] == os.getuid(), "Owned process identity unavailable")
    return identity


def signal_owned(record, number):
    require(set(record) == {"pid", "pgid", "uid", "birth"} and type(record["pid"]) is int
            and record["pid"] > 1 and record["pgid"] == record["pid"] and record["uid"] == os.getuid(), "Invalid process ownership record")
    actual = process_identity(record["pid"])
    if actual is None:
        return False
    require(actual == record, "PID identity changed; refusing to signal")
    os.killpg(record["pgid"], number)
    return True


def terminate_owned(record, *, grace=10):
    signal_owned(record, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while process_identity(record["pid"]) is not None and time.monotonic() < deadline:
        time.sleep(.05)
    if process_identity(record["pid"]) is not None:
        signal_owned(record, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while process_identity(record["pid"]) is not None and time.monotonic() < deadline:
        time.sleep(.05)
    require(process_identity(record["pid"]) is None, "Owned process did not terminate")


def port_closed(port):
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def wait_port_closed(port, timeout=5):
    deadline = time.monotonic() + timeout
    while not port_closed(port) and time.monotonic() < deadline:
        time.sleep(.05)
    return port_closed(port)


def frontend_snapshot():
    paths = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z", "apps/web"], env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).split(b"\0")
    return {path.decode(): hashlib.sha256((ROOT / path.decode()).read_bytes()).hexdigest() for path in paths if path}


def frontend_environment(api_origin):
    # Do not inherit the root .env, demo password or backend/DB credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    return {**env, "MIRA_API_BASE_URL": api_origin, "NEXT_TELEMETRY_DISABLED": "1"}


def owned_command(runtime, command, *, env, cwd, timeout=180):
    """The build is supervised too; killing its parent must not orphan workers."""
    path = runtime.directory / "build.log"
    with path.open("x") as log:
        path.chmod(0o600)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        record = record_process(process)
        write_private(runtime.directory / "build-process.json", record, exclusive=True)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                require(not runtime.should_stop(deadline), "Build cancelled or timed out")
                time.sleep(.1)
            require(process.returncode == 0, "Frontend build failed")
        finally:
            if process.poll() is None:
                terminate_owned(record)
            process.wait(timeout=5)
            write_private(runtime.directory / "build-stopped.json", {"stopped": True}, exclusive=True)


def stop_recorded_process(directory, role):
    record = directory / (role + "-process.json")
    stopped = directory / (role + "-stopped.json")
    if record.exists() and not stopped.exists():
        terminate_owned(read_state(record))


def validate_destination(destination, *, effective):
    require(destination.get("ENGINE") == "django.db.backends.postgresql", "PostgreSQL is required")
    require(destination.get("HOST") in ("localhost", "127.0.0.1", "::1") and str(destination.get("PORT")) == "55432", "Only the approved loopback PostgreSQL destination is allowed")
    test = destination.get("TEST", {})
    require(test.get("NAME") == "test_mira" and not test.get("MIRROR"), "Explicit test_mira without mirror is required")
    require(not {"dbname", "database", "service"}.intersection(destination.get("OPTIONS", {})), "Database routing override refused")
    require(destination.get("NAME") == "test_mira" if effective else bool(destination.get("NAME")) and destination.get("NAME") != "test_mira", "Native runner must select the test database")


def install_connection_guard():
    from django.db.backends.base.base import BaseDatabaseWrapper
    original = BaseDatabaseWrapper.ensure_connection

    def guarded(database):
        destination = database.settings_dict
        require(destination.get("ENGINE") == "django.db.backends.postgresql"
                and destination.get("HOST") in ("localhost", "127.0.0.1", "::1")
                and str(destination.get("PORT")) == "55432"
                and not {"dbname", "database", "service"}.intersection(destination.get("OPTIONS", {})), "Unsafe database connection refused")
        if database.alias == "default":
            validate_destination(destination, effective=True)
            active = getattr(database, "connection", None)
            require(active is None or getattr(getattr(active, "info", None), "dbname", None) == "test_mira", "Cached database identity mismatch")
        else:
            require(database.alias == "__no_db__" and destination.get("NAME") in (None, "postgres"), "Unexpected auxiliary database connection")
        return original(database)

    BaseDatabaseWrapper.ensure_connection = guarded


def verify_runner_database():
    from django.conf import settings
    from django.db import connection
    require(os.environ.get("DJANGO_SETTINGS_MODULE") == "config.settings.test", "Exact test settings required")
    validate_destination(settings.DATABASES["default"], effective=True)
    validate_destination(connection.settings_dict, effective=True)
    active = connection.connection
    require(active is None or getattr(getattr(active, "info", None), "dbname", None) == "test_mira", "Cached database identity mismatch before SQL")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        require(cursor.fetchone() == ("test_mira",), "Effective database is not test_mira")
    require(connection.connection.info.dbname == "test_mira", "Active connection identity mismatch")


def lease_path():
    return Path(tempfile.gettempdir()) / f"mira-interactive-test_mira-{os.getuid()}.lock"


class Lease:
    def __init__(self):
        self.fd = None

    def __enter__(self):
        self.fd = os.open(lease_path(), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        metadata = os.fstat(self.fd)
        require(metadata.st_uid == os.getuid() and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_nlink == 1, "Unsafe demo lock")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise DemoError("Another demo is active") from None
        os.lseek(self.fd, 0, os.SEEK_SET)
        previous = os.read(self.fd, 65536)
        try:
            if previous:
                state = json.loads(previous)
                require(state.get("state") == "closed", "Previous teardown unverified; human review required")
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def state(self, value):
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, (json.dumps(value) + "\n").encode())
        os.fsync(self.fd)

    def __exit__(self, *args):
        if self.fd is not None:
            os.close(self.fd)


class RedactedStream:
    def __init__(self, stream, values):
        self.stream, self.values, self.buffer = stream, values, ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._emit(line + "\n")
        return len(text)

    def _emit(self, text):
        for value in self.values:
            if value:
                text = text.replace(value, "[REDACTED]")
        self.stream.write(text)

    def flush(self):
        self._emit(self.buffer)
        self.buffer = ""
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class RuntimeSession:
    def __init__(self, directory, config):
        require(type(config.get("duration_seconds")) is int and 1 <= config["duration_seconds"] <= 3600
                and type(config.get("verification")) is bool, "Invalid bounded runtime duration")
        self.directory = private_directory(directory)
        self.config = config
        self.stop = threading.Event()
        self.reason = "not_started"
        self.secrets = [os.environ.get(key, "") for key in ("POSTGRES_PASSWORD", "DJANGO_SECRET_KEY")]
        self.backend_port = self.frontend_port = None
        self.fixture_cleanup_verified = False
        self.servers_stopped = False
        self.stop_seen = False

    def request_stop(self, reason):
        self.reason = reason
        self.stop.set()

    def should_stop(self, deadline):
        if self.stop.is_set():
            return True
        if (self.directory / "STOP").exists():
            reason = private_bytes(self.directory / "STOP").decode().strip()
            require(reason in ("STOP", "signal", "deadline", "startup_timeout"), "Invalid stop marker")
            self.request_stop(reason)
        elif time.monotonic() >= deadline:
            self.request_stop("deadline")
        elif process_identity(self.config["parent"]["pid"]) != self.config["parent"]:
            self.request_stop("parent_lost")
        return self.stop.is_set()

    def ready(self, frontend_port, backend_port):
        self.frontend_port, self.backend_port = frontend_port, backend_port
        deadline = time.monotonic() + self.config["duration_seconds"]
        write_private(self.directory / "ready.json", {"url": f"http://localhost:{frontend_port}/login", "frontend_port": frontend_port,
                      "backend_port": backend_port, "database": "test_mira", "deadline_monotonic": deadline,
                      "duration_seconds": self.config["duration_seconds"]}, exclusive=True)
        return deadline


def request_stop(directory, reason="STOP"):
    directory = private_directory(directory)
    marker = directory / "STOP"
    if marker.exists() or marker.is_symlink():
        private_bytes(marker)
    else:
        require(reason in ("STOP", "signal", "deadline", "startup_timeout"), "Invalid stop request")
        write_private(marker, (reason + "\n").encode(), exclusive=True)


def install_stop_signals(callback):
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in previous:
        signal.signal(number, lambda signum, frame: callback())
    return previous


def child_runner(args):
    global ACTIVE_SESSION
    directory = private_directory(args.session)
    config = read_state(directory / "session.json")
    require(not os.environ.get("CI") and not os.environ.get("GITHUB_ACTIONS"), "Interactive mode is forbidden in CI")
    require(os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.test") == "config.settings.test", "Exact test settings required")
    permit = os.read(args.permit_fd, 128)
    os.close(args.permit_fd)
    require(hashlib.sha256(permit).hexdigest() == config["permit_sha256"], "Launcher-controlled entry required")
    metadata = os.fstat(args.lock_fd)
    require(metadata.st_ino == lease_path().stat().st_ino and metadata.st_dev == lease_path().stat().st_dev and metadata.st_uid == os.getuid(), "Launcher lease required")
    require(process_identity(config["parent"]["pid"]) == config["parent"], "Launcher no longer owns startup")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
    os.environ["MIRA_RUN_INTERACTIVE_DEMO"] = "1"
    sys.path.insert(0, str(ROOT / "apps/api"))
    ACTIVE_SESSION = RuntimeSession(directory, config)
    sys.stdout = RedactedStream(sys.stdout, ACTIVE_SESSION.secrets)
    sys.stderr = RedactedStream(sys.stderr, ACTIVE_SESSION.secrets)
    install_stop_signals(lambda: ACTIVE_SESSION.request_stop("signal"))
    install_connection_guard()
    import django
    django.setup()
    from django.conf import settings
    from django.db import connections
    require(set(settings.DATABASES) == {"default"}, "Unexpected database aliases")
    validate_destination(settings.DATABASES["default"], effective=False)
    require(connections["default"].connection is None, "Unexpected pre-runner connection")
    from django.core.management import execute_from_command_line
    code = 1
    try:
        execute_from_command_line(["manage.py", "test", "system.tests.test_interactive_demo.InteractiveOperatorDemoTests",
                                   "--keepdb", "--noinput", "--settings=config.settings.test"])
        code = 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        remove_credentials(directory)
        current = ACTIVE_SESSION
        sys.stdout.flush()
        sys.stderr.flush()
        verify_no_secret_logs(directory, current.secrets)
        ports = all(wait_port_closed(port) for port in (current.frontend_port, current.backend_port) if port is not None)
        cleanup = current.fixture_cleanup_verified and current.servers_stopped and ports
        normal = code == 0 and cleanup
        result = {"normal_shutdown": normal, "fixture_cleanup_verified": current.fixture_cleanup_verified,
                  "cleanup_verified": cleanup,
                  "secret_log_scan_passed": True,
                  "servers_stopped": current.servers_stopped, "ports_released": ports,
                  "credentials_file_removed": not (directory / "access.json").exists(), "reason": current.reason}
        write_private(directory / "closed.json", result, exclusive=True)
        if cleanup:
            os.lseek(args.lock_fd, 0, os.SEEK_SET)
            os.ftruncate(args.lock_fd, 0)
            os.write(args.lock_fd, (json.dumps({"state": "closed", "session": str(directory)}) + "\n").encode())
        os.close(args.lock_fd)
        sys.stdout.flush()
        sys.stderr.flush()
    return 0 if normal else 1


def supervise(args):
    require(not os.environ.get("CI") and not os.environ.get("GITHUB_ACTIONS"), "Interactive mode is forbidden in CI")
    require(args.exclusive_test_database, "Explicit exclusive-use acknowledgement required")
    require(type(args.minutes) is int and 1 <= args.minutes <= 60, "Duration must be 1..60 minutes")
    require(os.environ.get("DJANGO_SETTINGS_MODULE", "config.settings.test") == "config.settings.test", "Exact test settings required")
    require(not subprocess.check_output(["git", "-C", str(ROOT), "diff", "HEAD", "--", "apps/web"], env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}), "Tracked frontend drift must be reviewed first")
    snapshot = frontend_snapshot()
    interrupted = threading.Event()
    with Lease() as lease:
        old_handlers = install_stop_signals(interrupted.set)
        directory = Path(tempfile.mkdtemp(prefix="mira-interactive-demo-")).resolve()
        permit = secrets.token_bytes(32)
        config = {"parent": process_identity(os.getpid()), "permit_sha256": hashlib.sha256(permit).hexdigest(),
                  "duration_seconds": 10 if args.command == "verify" else args.minutes * 60, "verification": args.command == "verify"}
        write_private(directory / "session.json", config, exclusive=True)
        lease.state({"state": "active", "session": str(directory)})
        read_fd, write_fd = os.pipe()
        os.write(write_fd, permit)
        os.close(write_fd)
        runner = None
        code = 1
        try:
            with (directory / "runner.log").open("x", encoding="utf-8") as log:
                os.chmod(directory / "runner.log", 0o600)
                command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_runner", "--session", str(directory),
                           "--permit-fd", str(read_fd), "--lock-fd", str(lease.fd)]
                runner = subprocess.Popen(command, cwd=ROOT / "apps/api", env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT,
                                          pass_fds=(read_fd, lease.fd), start_new_session=True)
                write_private(directory / "runner-process.json", record_process(runner), exclusive=True)
                os.close(read_fd)
                start_deadline = time.monotonic() + 240
                ready = None
                while runner.poll() is None:
                    if interrupted.is_set():
                        request_stop(directory, "signal")
                    if (directory / "ready.json").exists():
                        if ready is None:
                            ready = read_state(directory / "ready.json")
                            if args.command == "start":
                                print("INTERACTIVE_DEMO_READY", flush=True)
                                print("URL:", ready["url"], flush=True)
                                print("Access file:", directory / "access.json", flush=True)
                                print("Session:", directory, flush=True)
                        if time.monotonic() >= ready["deadline_monotonic"]:
                            request_stop(directory, "deadline")
                    elif time.monotonic() >= start_deadline:
                        request_stop(directory, "startup_timeout")
                        break
                    if (directory / "STOP").exists():
                        runner.wait(timeout=30)
                        break
                    time.sleep(.1)
                code = runner.wait(timeout=30)
        except (Exception, KeyboardInterrupt):
            request_stop(directory)
        finally:
            if runner is not None and runner.poll() is None:
                request_stop(directory)
                try:
                    runner.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    terminate_owned(read_state(directory / "runner-process.json"))
                    runner.wait(timeout=5)
            for role in ("build", "frontend"):
                stop_recorded_process(directory, role)
            remove_credentials(directory)
            closed = read_state(directory / "closed.json") if (directory / "closed.json").exists() else {}
            ready = read_state(directory / "ready.json") if (directory / "ready.json").exists() else {}
            ports = all(wait_port_closed(ready[key]) for key in ("frontend_port", "backend_port") if key in ready)
            unchanged = snapshot == frontend_snapshot()
            cleanup = closed.get("fixture_cleanup_verified") is True and closed.get("servers_stopped") is True and ports
            normal = code == 0 and closed.get("normal_shutdown") is True and cleanup and unchanged
            lease.state({"state": "closed" if cleanup else "incomplete", "session": str(directory)})
            result = {"normal_shutdown": normal, "native_cleanup_verified": closed.get("fixture_cleanup_verified", False),
                      "ports_released": ports, "frontend_unchanged": unchanged, "runner_exit": runner.returncode if runner else None,
                      "credentials_file_removed": not (directory / "access.json").exists(), "session": str(directory)}
            write_private(directory / "supervisor-result.json", result, exclusive=True)
            for number, handler in old_handlers.items():
                signal.signal(number, handler)
        print("DEMO_VERIFIED_CLOSED" if normal else "DEMO_FAILED_CLEANED" if cleanup else "DEMO_TEARDOWN_INCOMPLETE", flush=True)
        print("Evidence:", directory, flush=True)
        return 0 if normal else 1


def recover_stop(args):
    directory = private_directory(args.session)
    config = read_state(directory / "session.json")
    require(config["parent"]["uid"] == os.getuid(), "Wrong session owner")
    runner = read_state(directory / "runner-process.json")
    request_stop(directory)
    deadline = time.monotonic() + 30
    while process_identity(runner["pid"]) is not None and time.monotonic() < deadline:
        time.sleep(.1)
    if process_identity(runner["pid"]) is not None:
        terminate_owned(runner)
    for role in ("build", "frontend"):
        stop_recorded_process(directory, role)
    remove_credentials(directory)
    result = read_state(directory / "closed.json") if (directory / "closed.json").exists() else {}
    ready = read_state(directory / "ready.json") if (directory / "ready.json").exists() else {}
    released = all(wait_port_closed(ready[key]) for key in ("frontend_port", "backend_port") if key in ready)
    normal = result.get("normal_shutdown") is True and released
    write_private(directory / "recovery-result.json", {"normal_shutdown": normal, "ports_released": released,
                  "native_cleanup_verified": result.get("fixture_cleanup_verified", False), "credentials_file_removed": True})
    print("DEMO_VERIFIED_CLOSED" if normal else "DEMO_TEARDOWN_INCOMPLETE: native fixture/session cleanup unverified; human review required")
    return 0 if normal else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--minutes", type=int, default=30)
        p.add_argument("--exclusive-test-database", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("--session", required=True)
    child = sub.add_parser("_runner", help=argparse.SUPPRESS)
    child.add_argument("--session", required=True)
    child.add_argument("--permit-fd", type=int, required=True)
    child.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "_runner":
            return child_runner(args)
        if args.command == "stop":
            return recover_stop(args)
        return supervise(args)
    except (DemoError, OSError, ValueError, KeyError):
        # Never emit exception reprs containing settings, credentials or payloads.
        print("INTERACTIVE_DEMO_REFUSED: safety/lifecycle check failed; inspect private evidence and request review", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.modules["mira_interactive_launcher"] = sys.modules[__name__]
    raise SystemExit(main())
