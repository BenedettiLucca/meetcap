#!/usr/bin/env bash
# Meetcap rofi menu — send commands to the daemon, with self-heal recovery
# Usage: bind to a key in Hyprland

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOCKET="/tmp/meetcap.sock"
STATE="/tmp/meetcap_state.json"

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
    echo "$1" | socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null
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
        send_cmd "record" > /dev/null
        ;;
    *"Stop Recording"*)
        send_cmd "stop" > /dev/null
        ;;
    *"Transcribe Last"*)
        send_cmd "transcribe" > /dev/null
        ;;
    *"Open Recordings"*)
        xdg-open "$SCRIPT_DIR/recordings" 2>/dev/null
        ;;
esac
