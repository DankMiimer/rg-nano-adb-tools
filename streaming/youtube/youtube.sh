#!/bin/sh
# NanoPlay launcher. Runs inside st-sdl and delegates UI/playback to
# youtube_nano.py.

# On stock firmware the OPK's run.sh puts its own Python 3.12, qjs and CA bundle
# first in PATH (NANOPLAY_RUNTIME); the custom firmware had them in the rootfs.
export PATH=${NANOPLAY_RUNTIME:+$NANOPLAY_RUNTIME/bin:}/mnt/FunKey/youtube:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export SDL_NOMOUSE=1
export HOME=/mnt/FunKey
export YOUTUBE_APP_DIR=/mnt/FunKey/youtube
export SSL_CERT_FILE="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
export NANOPLAY_QJS="${NANOPLAY_QJS:-$(command -v qjs)}"
export NANOPLAY_SCREEN_WIDTH="${NANOPLAY_SCREEN_WIDTH:-38}"
export NANOPLAY_LIST_ROWS="${NANOPLAY_LIST_ROWS:-9}"

DIR=/mnt/FunKey/youtube
WORK=/tmp/youtube_nano
LOG="$DIR/last.log"

cleanup()
{
    killall -TERM mplayer yt-dlp 2>/dev/null
    sleep 1
    killall -9 mplayer yt-dlp 2>/dev/null
    rm -rf "$WORK"
}

trap cleanup EXIT INT TERM

mkdir -p "$DIR/data" "$DIR/cache" "$WORK"
cd "$DIR" || exit 1

{
    echo "[$(date)] --- NanoPlay started ---"
    echo "PATH=$PATH"
} > "$LOG"

fail_pause()
{
    echo ""
    echo "$1"
    echo ""
    echo "Press Enter to exit."
    read junk
    exit 1
}

draw_menu_placeholder()
{
    [ "$#" -eq 0 ] || return 0

    cookies=NOCK
    cache=COLD
    [ -f "$DIR/cookies.txt" ] && cookies=CK
    [ -n "$(ls -A "$DIR/cache" 2>/dev/null)" ] && cache=WARM
    status="$cookies $cache"

    if [ -z "${NO_COLOR:-}" ]; then
        red="$(printf '\033[31;1m')"
        dim=
        sel="$(printf '\033[7;31m')"
        reset="$(printf '\033[0m')"
    else
        red=
        dim=
        sel=
        reset=
    fi

    title=NanoPlay
    pad=$((36 - ${#title} - ${#status}))
    [ "$pad" -lt 1 ] && pad=1
    spaces="$(printf "%${pad}s" "")"

    printf '\033[H\033[2J'
    pad=0
    while [ "$pad" -lt "${NANOPLAY_TOP_PAD:-0}" ]; do echo; pad=$((pad + 1)); done
    printf '%s+------------------------------------+%s\n' "$red" "$reset"
    printf '|%s%s%s%s%s%s%s|\n' "$red" "$title" "$reset" "$spaces" "$dim" "$status" "$reset"
    printf '%s+------------------------------------+%s\n' "$red" "$reset"
    printf '%s> %-34s%s\n' "$sel" "Search" "$reset"
    printf '  %-34s\n' "Watch Again"
    printf '  %-34s\n' "Favorites"
    printf '  %-34s\n' "Watch Later"
    printf '  %-34s\n' "Test Videos"
    printf '  %-34s\n' "Categories"
    printf '  %-34s\n' "Play URL"
    printf '  %-34s\n' "Settings"
    printf '  %-34s\n' "Replay Last"
}

command -v python3 >/dev/null 2>&1 || fail_pause "python3 is missing. Open NanoPlay from its OPK (it brings Python)."
command -v qjs >/dev/null 2>&1 || fail_pause "qjs is missing. Open NanoPlay from its OPK (it brings qjs)."
[ -x "$DIR/yt-dlp" ] || fail_pause "yt-dlp is missing. Run deploy_youtube.sh from the NanoWiFi folder."

draw_menu_placeholder "$@"

# stdout MUST reach the console (the st terminal in menu mode, or the bare VT in
# play mode) so the UI/progress text is visible; only stderr goes to the log.
# "$@" carries the mode: no args = menu (phase 1), "play" = player (phase 2).
python3 "$DIR/youtube_nano.py" "$@" 2>> "$LOG"
RC=$?

exit "$RC"
