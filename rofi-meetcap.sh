#!/usr/bin/env bash
# Meetcap rofi menu — send commands to the daemon, with self-heal recovery
# Usage: bind to a key in Hyprland

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -n "$MEETCAP_RUNTIME_DIR" ]; then
    RUNTIME_DIR="$MEETCAP_RUNTIME_DIR"
elif [ -n "$XDG_RUNTIME_DIR" ]; then
    RUNTIME_DIR="$XDG_RUNTIME_DIR/meetcap"
else
    RUNTIME_DIR="/run/user/$(id -u)/meetcap"
fi
SOCKET="$RUNTIME_DIR/meetcap.sock"
STATE="$RUNTIME_DIR/meetcap_state.json"

daemon_ok() {
    [ -S "$SOCKET" ] && [ -n "$(echo 'status' | socat -t 2 - UNIX-CONNECT:"$SOCKET" 2>/dev/null)" ]
}

recover() {
    local choice
    choice=$(printf "🔄  Restart daemon\n🏥  Run doctor\n❌  Cancel" \
        | rofi -dmenu -i -p "Meetcap: daemon down" -theme-str 'window { width: 30%; }' 2>/dev/null)
    case "$choice" in
        *"Restart"*)
            notify-send "Meetcap" "Restarting daemon..."
            "$SCRIPT_DIR/meetcap.sh" restart >/dev/null 2>&1
            sleep 1
            ;;
        *"doctor"*)
            notify-send "Meetcap — doctor" "$("$SCRIPT_DIR/meetcap.sh" doctor 2>&1 | head -c 400)"
            ;;
    esac
}

send_cmd() {
    if ! daemon_ok; then
        recover
        if ! daemon_ok; then
            notify-send "Meetcap" "Daemon still not responding" "Run: meetcap.sh doctor"
            exit 1
        fi
    fi
    local response
    response=$(echo "$1" | socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null)
    # Parse response: notify on {"ok": false, ...} or invalid/empty JSON
    _notify_on_error "$response"
}

# Parse daemon JSON response; call notify-send if ok==false or response is unusable.
_notify_on_error() {
    local resp="$1"
    if [ -z "$resp" ]; then
        notify-send "Meetcap" "⚠️ Sem resposta do daemon (comunicação falhou)"
        return
    fi
    # Use python3 one-liner consistent with state-reading style above
    local ok errmsg
    ok=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print('false' if d.get('ok') is False else 'true')
except Exception:
    print('invalid')
" "$resp" 2>/dev/null)
    case "$ok" in
        false)
            errmsg=$(python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    print(str(d.get('error', 'Erro desconhecido'))[:200])
except Exception:
    print('Erro desconhecido')
" "$resp" 2>/dev/null)
            notify-send "Meetcap" "⚠️ ${errmsg}"
            ;;
        invalid)
            notify-send "Meetcap" "⚠️ Falha de comunicação com o daemon"
            ;;
        # ok==true: success, no notification needed
    esac
}

# Read current state
recording=false
transcribing=false
if [ -f "$STATE" ]; then
    recording=$(python3 -c "import json; print(json.load(open('$STATE')).get('recording', False))" 2>/dev/null)
    transcribing=$(python3 -c "import json; print(json.load(open('$STATE')).get('transcribing', False))" 2>/dev/null)
fi

# Build menu options based on state
if [ "$recording" = "True" ]; then
    options="⏹  Stop Recording\n📝  Transcribe Last\n📂  Open Recordings"
elif [ "$transcribing" = "True" ]; then
    options="🎙  Start Recording (queued)\n📂  Open Recordings\n⏳  Transcribing..."
else
    options="🎙  Start Recording\n📝  Transcribe Last\n📂  Open Recordings"
fi

choice=$(echo -e "$options" | rofi -dmenu -i -p "Meetcap" -theme-str 'window { width: 25%; }' 2>/dev/null)

case "$choice" in
    *"Start Recording"*)
        send_cmd "record"
        ;;
    *"Stop Recording"*)
        send_cmd "stop"
        ;;
    *"Transcribe Last"*)
        send_cmd "transcribe"
        ;;
    *"Open Recordings"*)
        xdg-open "$SCRIPT_DIR/recordings" 2>/dev/null
        ;;
esac
