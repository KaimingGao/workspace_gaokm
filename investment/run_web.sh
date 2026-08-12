#!/usr/bin/env bash
# 后台启停 Investment Web（uvicorn via run_web.py）
# 用法：./run_web.sh [start|stop|status|restart]  （无参数默认 start）
#
# 环境变量：WEB_HOST / WEB_PORT / WEB_RELOAD（同 run_web.py）

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
  for i in $(seq 1 30); do
    if ! _is_alive "$pid"; then
      return 1
    fi
    if listener="$(_port_listener_pid)"; then
      # 监听者是我们自己，或（reload 子进程时）父进程仍在
      if [[ "$listener" == "$pid" ]] || _is_alive "$pid"; then
        return 0
      fi
    fi
    sleep 0.2
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
  nohup python3 "$ROOT/run_web.py" >>"$LOG_FILE" 2>&1 &
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

stop() {
  local pid listener
  if pid="$(_managed_pid)"; then
    kill "$pid" 2>/dev/null || true
    local i
    for i in $(seq 1 20); do
      if ! _is_alive "$pid"; then
        rm -f "$PID_FILE"
        echo "stopped pid=$pid"
        return 0
      fi
      sleep 0.25
    done
    echo "force kill pid=$pid" >&2
    kill -9 "$pid" 2>/dev/null || true
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
  WEB_HOST / WEB_PORT / WEB_RELOAD   同 run_web.py
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
