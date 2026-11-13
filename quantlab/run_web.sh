#!/usr/bin/env bash
# 后台启停 QuantLab Web（uvicorn via run_web.py）
# 用法：./run_web.sh [start|stop|status|restart]  （无参数默认 start）
#
# 环境变量：WEB_HOST / WEB_PORT / WEB_RELOAD / WEB_ACCESS_LOG（同 run_web.py）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PID_FILE="${WEB_PID_FILE:-$ROOT/data/run_web.pid}"
LOG_FILE="${WEB_LOG_FILE:-$ROOT/data/run_web.log}"
HOST="${WEB_HOST:-127.0.0.1}"
PORT="${WEB_PORT:-8000}"

_mkdir_data() {
  mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")"
}

_is_alive() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null
}

# 父进程卡在 GC 里时，kill 它不会带走已经拉起的进程池。
_descendants() {
  local pid="$1" child kids
  kids="$(pgrep -P "$pid" 2>/dev/null || true)"
  for child in $kids; do
    echo "$child"
    _descendants "$child"
  done
}

_signal_pids() {
  local sig="$1"
  shift
  local pid
  for pid in "$@"; do
    [[ -n "${pid:-}" ]] || continue
    kill "-${sig}" "$pid" 2>/dev/null || true
  done
}

_pid_from_file() {
  if [[ -f "$PID_FILE" ]]; then
    tr -d '[:space:]' <"$PID_FILE"
  fi
}

_cmd_matches() {
  local pid="$1"
  local cmd
  cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$cmd" == *run_web.py* ]] || [[ "$cmd" == *web.app:app* ]] || [[ "$cmd" == *uvicorn* ]]
}

# 本脚本管理的进程是否在跑
_managed_pid() {
  local pid
  pid="$(_pid_from_file)"
  if [[ -n "${pid:-}" ]] && _is_alive "$pid" && _cmd_matches "$pid"; then
    echo "$pid"
    return 0
  fi
  return 1
}

_clear_stale_pid() {
  local pid
  pid="$(_pid_from_file)"
  [[ -n "${pid:-}" ]] || return 0
  if ! _is_alive "$pid" || ! _cmd_matches "$pid"; then
    rm -f "$PID_FILE"
  fi
}

# 打印占用 PORT 的监听进程；成功找到则 return 0
_port_listener_pid() {
  local line pid
  # macOS / Linux 常见：lsof
  if command -v lsof >/dev/null 2>&1; then
    line="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $2}')"
    if [[ -n "${line:-}" ]]; then
      echo "$line"
      return 0
    fi
  fi
  return 1
}

_wait_ready() {
  local pid="$1"
  local i listener
  # 冷启动要先 import web.app（本机约 7s+），6s 内杀进程会变成 Terminated: 15
  for i in $(seq 1 60); do
    if ! _is_alive "$pid"; then
      return 1
    fi
    if listener="$(_port_listener_pid)"; then
      # 监听者是我们自己，或（reload 子进程时）父进程仍在
      if [[ "$listener" == "$pid" ]] || _is_alive "$pid"; then
        return 0
      fi
    fi
    sleep 1
  done
  return 1
}

status() {
  local pid listener
  if pid="$(_managed_pid)"; then
    echo "running pid=$pid  http://${HOST}:${PORT}  log=$LOG_FILE"
    return 0
  fi
  _clear_stale_pid
  if listener="$(_port_listener_pid)"; then
    echo "not managed by this script; port ${PORT} held by pid=$listener"
    return 1
  fi
  echo "not running"
  return 1
}

start() {
  local pid listener
  if pid="$(_managed_pid)"; then
    echo "already running pid=$pid  http://${HOST}:${PORT}"
    return 0
  fi
  _clear_stale_pid

  if listener="$(_port_listener_pid)"; then
    echo "port ${PORT} already in use by pid=$listener" >&2
    echo "stop that process first, or: WEB_PORT=8001 ./run_web.sh start" >&2
    return 1
  fi

  _mkdir_data
  local py="python3"
  if [[ -x "$ROOT/.venv/bin/python3" ]]; then
    py="$ROOT/.venv/bin/python3"
  fi
  nohup "$py" "$ROOT/run_web.py" >>"$LOG_FILE" 2>&1 &
  pid=$!
  echo "$pid" >"$PID_FILE"

  if ! _wait_ready "$pid"; then
    echo "start failed (process exited or port ${PORT} not listening); see $LOG_FILE" >&2
    if _is_alive "$pid"; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    return 1
  fi
  echo "started pid=$pid  http://${HOST}:${PORT}"
  echo "log: $LOG_FILE"
}

# 仍在跑的 Holdout 及其进程池。web stop/restart 不杀它们，新进程按 job pid 接上。
_holdout_spare_pids() {
  local py roots root kids
  py="python3"
  if [[ -x "$ROOT/.venv/bin/python3" ]]; then
    py="$ROOT/.venv/bin/python3"
  fi
  roots="$("$py" - "$ROOT" <<'PY' 2>/dev/null || true
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
mark = "run_yhat_transformer_holdout.py"
folder = root / "data" / "live" / "yhat_tf"
if not folder.is_dir():
    raise SystemExit(0)
for jf in folder.glob("*_job.json"):
    try:
        job = json.loads(jf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    if not (job.get("status") == "running" and job.get("running")):
        continue
    try:
        pid = int(job.get("pid") or 0)
    except (TypeError, ValueError):
        continue
    if pid <= 0:
        continue
    try:
        cmd = subprocess.check_output(
            ["ps", "-o", "command=", "-p", str(pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        continue
    if mark not in cmd:
        continue
    print(pid)
PY
)"
  for root in $roots; do
    echo "$root"
    kids="$(_descendants "$root")"
    if [[ -n "${kids:-}" ]]; then
      # shellcheck disable=SC2086
      printf '%s\n' $kids
    fi
  done
}

_drop_spare() {
  local pid spare=" $* "
  while IFS= read -r pid; do
    [[ -n "${pid:-}" ]] || continue
    if [[ "$spare" != *" $pid "* ]]; then
      echo "$pid"
    fi
  done
}

stop() {
  local pid listener spare kids
  spare="$(_holdout_spare_pids | awk 'NF && !seen[$0]++')"
  if [[ -n "${spare:-}" ]]; then
    echo "holdout 继续跑，本次 stop/restart 不结束这些进程：$(echo "$spare" | tr '\n' ' ')" >&2
  fi
  if pid="$(_managed_pid)"; then
    kids="$(_descendants "$pid" | _drop_spare $spare)"
    kill "$pid" 2>/dev/null || true
    # shellcheck disable=SC2086
    [[ -z "${kids:-}" ]] || _signal_pids TERM $kids
    local i
    for i in $(seq 1 20); do
      if ! _is_alive "$pid"; then
        # 父进程退出后子进程会被收养，必须按停之前记下的 pid 再杀一次。
        # shellcheck disable=SC2086
        [[ -z "${kids:-}" ]] || _signal_pids KILL $kids
        rm -f "$PID_FILE"
        echo "stopped pid=$pid"
        return 0
      fi
      sleep 0.25
    done
    echo "force kill pid=$pid" >&2
    kill -9 "$pid" 2>/dev/null || true
    # shellcheck disable=SC2086
    [[ -z "${kids:-}" ]] || _signal_pids KILL $kids
    rm -f "$PID_FILE"
    echo "stopped pid=$pid (killed)"
    return 0
  fi

  _clear_stale_pid
  if listener="$(_port_listener_pid)"; then
    echo "no managed pid; port ${PORT} still held by pid=$listener" >&2
    echo "to free it: kill $listener" >&2
    return 1
  fi
  echo "not running"
  return 0
}

usage() {
  cat <<EOF
用法: $(basename "$0") [start|stop|status|restart]

  start    后台启动 Web（python3 run_web.py；默认）
  stop     停止后台进程
  status   查看是否在跑
  restart  stop + start

环境变量:
  WEB_HOST / WEB_PORT / WEB_RELOAD / WEB_ACCESS_LOG   同 run_web.py
  WEB_PID_FILE   默认 $ROOT/data/run_web.pid
  WEB_LOG_FILE   默认 $ROOT/data/run_web.log
EOF
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  restart) stop; start ;;
  -h|--help|help) usage ;;
  *)
    echo "unknown command: $1" >&2
    usage >&2
    exit 1
    ;;
esac
