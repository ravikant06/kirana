#!/usr/bin/env python3
"""
infra.py: status, start, stop and restart for everything Kirana runs locally.

Usage (from the repo root, or from infra/):

    python3 infra/infra.py status                     # every server: state, health, port, pid, uptime
    python3 infra/infra.py status kirana-ai           # only one group (or one server)

    python3 infra/infra.py restart kafka              # one server
    python3 infra/infra.py restart backend frontend   # several
    python3 infra/infra.py restart kirana             # a group: backend + frontend
    python3 infra/infra.py restart kirana-ai          # a group: ai-api + ai-worker + ai-catalog
    python3 infra/infra.py restart mocks              # payment-mock + warehouse-mock (images rebuilt)
    python3 infra/infra.py restart-all                # rebuild every image, recreate every container,
                                                      # then restart backend, AI service, workers, frontend

    python3 infra/infra.py start ai-catalog           # start only if not running
    python3 infra/infra.py stop ai-worker             # stop
    python3 infra/infra.py logs backend               # last 40 log lines   (-n 200 for more)
    python3 infra/infra.py logs kafka -n 100          # docker servers: docker compose logs

Servers (name: where it runs, what is checked):
    postgres        docker   container health (pg_isready)       :5432
    minio           docker   GET /minio/health/live               :9000 (console :9001)
    redis           docker   container health (redis-cli ping)    :6380
    kafka           docker   TCP connect                          :9094 (Kafka UI :8085)
    kafka-ui        docker   GET /                                :8085
    qdrant          docker   GET /healthz                         :6335 (dashboard /dashboard)
    toxiproxy       docker   GET /version                         :8474
    payment-mock    docker   GET /docs        (FastAPI, built from ../payment-mock)   :8090
    warehouse-mock  docker   GET /health      (FastAPI, built from ../warehouse-mock) :8091
    backend         host     GET /actuator/health (Spring Boot)  :8080
    frontend        host     GET /            (Vite dev server)  :5173
    ai-api          host     GET /health      (kirana-ai FastAPI, uvicorn --reload)  :8000
    ai-worker       host     process          (Kafka kb.documents.v1 -> knowledge base)
    ai-catalog      host     process          (Kafka catalog.v1 -> product index)

Groups: infra (postgres minio redis kafka kafka-ui qdrant toxiproxy), mocks, docker (infra + mocks),
        kirana (backend frontend), kirana-ai (ai-api ai-worker ai-catalog), host, all.

Host servers started by this script run in the background; their output goes to
infra/data/logs/<name>.log. A server you started yourself in a terminal is found and stopped
just the same (by port and command line), then started again in the background.
Docker restarts use `docker compose up -d --build --force-recreate`: images are rebuilt from
their Dockerfiles, containers recreated; data lives in volumes and survives.
"""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"
LOGS = INFRA / "data" / "logs"


@dataclass
class Server:
    name: str
    where: str                        # "docker" | "host"
    port: int | None = None
    check: str = ""                   # "http:<url>" | "tcp" | "docker-health" | "process"
    start: list[str] = field(default_factory=list)
    cwd: Path | None = None
    patterns: list[str] = field(default_factory=list)   # command-line fragments that identify it
    note: str = ""


def _java_major(home: str) -> int:
    """Major version of a JDK, from its `release` file (JAVA_VERSION="25.0.1" -> 25)."""
    try:
        for line in (Path(home) / "release").read_text().splitlines():
            if line.startswith("JAVA_VERSION="):
                return int(line.split("=", 1)[1].strip('"').split(".")[0])
    except (OSError, ValueError):
        pass
    return 0


def java_home() -> str | None:
    """
    A JDK for the backend: Java 21 or newer. $JAVA_HOME is used only if it is new enough;
    on this machine it pointed at Java 18, and Spring Boot then failed with
    UnsupportedClassVersionError. Otherwise Homebrew's openjdk.
    """
    candidates = [os.environ.get("JAVA_HOME", ""),
                  "/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home",
                  "/usr/local/opt/openjdk/libexec/openjdk.jdk/Contents/Home"]
    for home in candidates:
        if home and _java_major(home) >= 21:
            return home
    return None


AI = ROOT / "kirana-ai"
SERVERS = {s.name: s for s in [
    Server("postgres", "docker", 5432, "docker-health"),
    Server("minio", "docker", 9000, "http:http://localhost:9000/minio/health/live", note="console :9001"),
    Server("redis", "docker", 6380, "docker-health"),
    Server("kafka", "docker", 9094, "tcp", note="UI :8085"),
    Server("kafka-ui", "docker", 8085, "http:http://localhost:8085/"),
    Server("qdrant", "docker", 6335, "http:http://localhost:6335/healthz", note="/dashboard"),
    Server("toxiproxy", "docker", 8474, "http:http://localhost:8474/version"),
    Server("payment-mock", "docker", 8090, "http:http://localhost:8090/docs", note="FastAPI"),
    Server("warehouse-mock", "docker", 8091, "http:http://localhost:8091/health", note="FastAPI"),
    Server("backend", "host", 8080, "http:http://localhost:8080/actuator/health",
           ["mvn", "spring-boot:run"], ROOT / "backend",
           ["spring-boot:run", "kirana/backend/target/classes"], "Spring Boot"),
    Server("frontend", "host", 5173, "http:http://localhost:5173/",
           ["npm", "run", "dev"], ROOT / "frontend",
           ["kirana/frontend/node_modules/.bin/vite"], "Vite"),
    Server("ai-api", "host", 8000, "http:http://localhost:8000/health",
           [".venv/bin/uvicorn", "kirana_ai.api:app", "--reload", "--port", "8000"], AI,
           ["kirana_ai.api:app"], "FastAPI"),
    Server("ai-worker", "host", None, "process",
           [".venv/bin/python", "-m", "kirana_ai.worker"], AI,
           ["-m kirana_ai.worker"], "kb.documents.v1"),
    Server("ai-catalog", "host", None, "process",
           [".venv/bin/python", "-m", "kirana_ai.catalog"], AI,
           ["-m kirana_ai.catalog"], "catalog.v1"),
]}

GROUPS = {
    "infra": ["postgres", "minio", "redis", "kafka", "kafka-ui", "qdrant", "toxiproxy"],
    "mocks": ["payment-mock", "warehouse-mock"],
    "kirana": ["backend", "frontend"],
    "kirana-ai": ["ai-api", "ai-worker", "ai-catalog"],
}
GROUPS["docker"] = GROUPS["infra"] + GROUPS["mocks"]
GROUPS["host"] = GROUPS["kirana"] + GROUPS["kirana-ai"]
GROUPS["all"] = GROUPS["docker"] + GROUPS["host"]
# Start order for host servers: the backend first (the AI service and workers call it), the UI last.
HOST_ORDER = ["backend", "ai-api", "ai-worker", "ai-catalog", "frontend"]

TTY = sys.stdout.isatty()
sys.stdout.reconfigure(line_buffering=True)     # our lines interleave correctly with docker's output


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


GREEN, RED, YELLOW, DIM = "32", "31", "33", "2"


# --- probing ------------------------------------------------------------------------------------

def http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read(20_000).decode(errors="replace")
            if "actuator/health" in url:
                status = json.loads(body).get("status", "?") if body.startswith("{") else "?"
                return status == "UP", f"HTTP {r.status}, {status}"
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__


def tcp_ok(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1.5):
            return True
    except OSError:
        return False


def sh(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def compose(*args: str, capture: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", *args]
    if capture:
        return sh(cmd, cwd=INFRA)
    return subprocess.run(cmd, cwd=INFRA)


def docker_states() -> dict[str, dict]:
    """compose service -> {State, Health, Status} for every container, running or not."""
    out = compose("ps", "--all", "--format", "json").stdout.strip()
    rows = []
    if out.startswith("["):
        rows = json.loads(out)
    else:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {r["Service"]: r for r in rows}


def pids_for(s: Server) -> list[int]:
    """Processes of a host server: whatever listens on its port, plus command-line matches."""
    pids = set()
    if s.port:
        out = sh(["lsof", "-tiTCP:%d" % s.port, "-sTCP:LISTEN"]).stdout
        pids |= {int(p) for p in out.split()}
    for pattern in s.patterns:
        out = sh(["pgrep", "-f", "--", pattern]).stdout      # "--": a pattern may start with "-"
        pids |= {int(p) for p in out.split()}
    pids.discard(os.getpid())
    return sorted(pids)


def uptime(pid: int) -> str:
    return sh(["ps", "-o", "etime=", "-p", str(pid)]).stdout.strip() or "?"


def probe(s: Server, docker: dict | None = None) -> tuple[str, bool, str, str]:
    """(state, healthy, health detail, pid/uptime detail)"""
    extra = ""
    if s.where == "docker":
        row = (docker or {}).get(s.name)
        if not row:
            return "missing", False, "no container", ""
        state = row.get("State", "?")
        if state != "running":
            return state, False, row.get("Status", ""), ""
        extra = row.get("Status", "")
        if s.check == "docker-health":
            health = row.get("Health") or "no healthcheck"
            return state, health == "healthy", health, extra
    else:
        pids = pids_for(s)
        if not pids:
            return "stopped", False, "", ""
        state = "running"
        extra = f"pid {pids[0]}, up {uptime(pids[0])}" + (f" (+{len(pids) - 1})" if len(pids) > 1 else "")
    if s.check.startswith("http:"):
        ok, detail = http_ok(s.check[5:])
        return state, ok, detail, extra
    if s.check == "tcp":
        ok = tcp_ok(s.port)
        return state, ok, "port open" if ok else "port closed", extra
    return state, True, "process alive", extra


# --- commands -----------------------------------------------------------------------------------

def expand(names: list[str]) -> list[str]:
    out = []
    for n in names or ["all"]:
        if n in GROUPS:
            out += GROUPS[n]
        elif n in SERVERS:
            out.append(n)
        else:
            sys.exit(f"unknown server or group: {n}\n  servers: {', '.join(SERVERS)}\n  groups: {', '.join(GROUPS)}")
    return list(dict.fromkeys(out))


def cmd_status(names: list[str]) -> int:
    docker = docker_states() if shutil.which("docker") else {}
    print(f"  {'SERVER':15} {'WHERE':7} {'STATE':9} {'HEALTH':22} {'PORT':6} {'DETAIL'}")
    bad = 0
    for name in expand(names):
        s = SERVERS[name]
        state, ok, health, extra = probe(s, docker)
        mark = color("●", GREEN) if ok else color("●", RED if state != "running" else YELLOW)
        bad += not ok
        port = str(s.port or "-")
        detail = "  ".join(x for x in (extra, color(s.note, DIM) if s.note else "") if x)
        print(f"{mark} {name:15} {s.where:7} {state:9} {health[:22]:22} {port:6} {detail}")
    print(f"\n{color('all healthy', GREEN) if not bad else color(f'{bad} not healthy', RED)}")
    return 1 if bad else 0


def wait_healthy(s: Server, seconds: int = 120) -> bool:
    deadline = time.time() + seconds
    started = time.time()
    while time.time() < deadline:
        # A host server whose processes are all gone has crashed: say so now, not after the timeout.
        if s.where == "host" and time.time() - started > 8 and not pids_for(s):
            return False
        docker = docker_states() if s.where == "docker" else None
        state, ok, _, _ = probe(s, docker)
        if ok:
            return True
        time.sleep(2)
    return False


def stop_host(s: Server) -> None:
    pids = pids_for(s)
    if not pids:
        print(f"  {s.name}: not running")
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + 20
    while time.time() < deadline and pids_for(s):
        time.sleep(0.5)
    for pid in pids_for(s):                 # still there after 20 s: force it
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    print(f"  {s.name}: stopped (pid {', '.join(map(str, pids))})")


def start_host(s: Server) -> None:
    if pids_for(s):
        print(f"  {s.name}: already running")
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if s.name == "backend":
        home = java_home() or sys.exit("backend needs Java 21+: set JAVA_HOME or `brew install openjdk`")
        env["JAVA_HOME"] = home
        env["PATH"] = f"{home}/bin:{env.get('PATH', '')}"
    log = open(LOGS / f"{s.name}.log", "ab")
    log.write(f"\n===== started {time.strftime('%Y-%m-%d %H:%M:%S')} by infra.py =====\n".encode())
    log.flush()
    # A new session: the server outlives this script and is not hit by its Ctrl-C.
    subprocess.Popen(s.start, cwd=s.cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    if s.check == "process":
        # A worker counts as up only if it survives its own startup (config, Kafka connect).
        time.sleep(5)
        healthy = bool(pids_for(s))
    else:
        healthy = wait_healthy(s, 180 if s.name == "backend" else 60)
    where = f"logs: infra/data/logs/{s.name}.log"
    print(f"  {s.name}: {color('up', GREEN) if healthy else color('NOT healthy yet', RED)}  ({where})")


def restart_docker(names: list[str]) -> None:
    if not names:
        return
    print(f"docker: rebuild + recreate {', '.join(names)}")
    compose("up", "-d", "--build", "--force-recreate", *names, capture=False)
    for name in names:
        ok = wait_healthy(SERVERS[name], 90)
        print(f"  {name}: {color('healthy', GREEN) if ok else color('NOT healthy', RED)}")


def cmd_restart(names: list[str]) -> None:
    targets = expand(names)
    restart_docker([n for n in targets if SERVERS[n].where == "docker"])
    host = [n for n in HOST_ORDER if n in targets]
    for name in reversed(host):              # stop in reverse dependency order
        stop_host(SERVERS[name])
    for name in host:                        # start in dependency order
        start_host(SERVERS[name])


def cmd_restart_all() -> None:
    print("docker: rebuilding every image and recreating every container")
    compose("up", "-d", "--build", "--force-recreate", capture=False)
    for name in GROUPS["docker"]:
        ok = wait_healthy(SERVERS[name], 120)
        print(f"  {name}: {color('healthy', GREEN) if ok else color('NOT healthy', RED)}")
    for name in reversed(HOST_ORDER):
        stop_host(SERVERS[name])
    for name in HOST_ORDER:
        start_host(SERVERS[name])
    print()
    cmd_status(["all"])


def cmd_start(names: list[str]) -> None:
    targets = expand(names)
    docker = [n for n in targets if SERVERS[n].where == "docker"]
    if docker:
        compose("up", "-d", *docker, capture=False)
    for name in [n for n in HOST_ORDER if n in targets]:
        start_host(SERVERS[name])


def cmd_stop(names: list[str]) -> None:
    targets = expand(names)
    for name in [n for n in reversed(HOST_ORDER) if n in targets]:
        stop_host(SERVERS[name])
    docker = [n for n in targets if SERVERS[n].where == "docker"]
    if docker:
        compose("stop", *docker, capture=False)


def cmd_logs(name: str, lines: int) -> None:
    s = SERVERS.get(name) or sys.exit(f"unknown server: {name}")
    if s.where == "docker":
        compose("logs", "--tail", str(lines), name, capture=False)
        return
    path = LOGS / f"{name}.log"
    if not path.exists():
        sys.exit(f"no log yet at {path.relative_to(ROOT)} (only servers started by infra.py log there)")
    print("".join(path.read_text(errors="replace").splitlines(keepends=True)[-lines:]), end="")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "restart", "start", "stop"):
        p = sub.add_parser(name)
        p.add_argument("names", nargs="*", help="servers or groups (default for status: all)")
    sub.add_parser("restart-all", help="rebuild all images, recreate all containers, restart host servers")
    p_logs = sub.add_parser("logs")
    p_logs.add_argument("name")
    p_logs.add_argument("-n", type=int, default=40)
    args = parser.parse_args()

    if args.command == "status":
        sys.exit(cmd_status(args.names))
    if args.command in ("restart", "start", "stop") and not args.names:
        sys.exit(f"say what to {args.command}, e.g. `infra.py {args.command} kirana-ai` (or use restart-all)")
    {"restart": cmd_restart, "start": cmd_start, "stop": cmd_stop}.get(args.command, lambda _: None)(
        getattr(args, "names", []))
    if args.command == "restart-all":
        cmd_restart_all()
    if args.command == "logs":
        cmd_logs(args.name, args.n)


if __name__ == "__main__":
    main()
