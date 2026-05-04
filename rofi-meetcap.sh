#!/usr/bin/env bash
# Meetcap rofi menu — send commands to the daemon
# Usage: bind to a key in Hyprland

SOCKET="/tmp/meetcap.sock"
STATE="/tmp/meetcap_state.json"

send_cmd() {
    if [ ! -S "$SOCKET" ]; then
        notify-send "Meetcap" "Daemon not running. Run: meetcap.sh daemon"
        exit 1
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
        xdg-open "$HOME/Projects/meetcap/recordings" 2>/dev/null
        ;;
esac
