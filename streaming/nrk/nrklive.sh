#!/bin/sh
# nrklive.sh <channel>
#   TV:    nrk1 / nrk2 / nrk3   (480x270 H.264, AES-128 encrypted)
#   Radio: p1 / p2 / p3 / ...   (AAC, clear)
#
# A background feeder downloads HLS segments over http (TV: decrypt each with
# the local `aesdec`; radio: clear), and streams continuous MPEG-TS into a FIFO
# that ONE MPlayer reads (no chunks/relaunch/reload). A watchdog restarts the
# pipeline if it dies unexpectedly; a normal quit leaves the app.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export SDL_NOMOUSE=1
export HOME=/mnt/FunKey
CH="${1:-nrk1}"

# Normalise "radio" to "p1" as the default starting channel for the unified radio app
if [ "$CH" = "radio" ]; then
  CH="p1"
fi

DIR=/mnt/FunKey/nrk
# exec >"$DIR/last.log" 2>&1
WORK=/tmp/nrklive
KEY="$DIR/content.key"
AESDEC="$DIR/aesdec"
FIFO="$WORK/$CH.ts"
RAW="$WORK/raw_$CH"
mkdir -p "$WORK"

stop_players() {
  killall -TERM mplayer wget aesdec 2>/dev/null
  sleep "${1:-1}"
  killall -9 mplayer wget aesdec 2>/dev/null
}

close_control_fifo() {
  exec 4>&- 2>/dev/null
  rm -f "/tmp/mplayer-control"
}

open_control_fifo() {
  FIFO_CTRL="/tmp/mplayer-control"
  close_control_fifo
  mkfifo "$FIFO_CTRL"
  exec 4<> "$FIFO_CTRL"
}

send_control() {
  printf '%s\n' "$1" >&4 2>/dev/null
}

cleanup_playback_jobs() {
  [ -n "${TAIL_PID:-}" ] && kill -9 "$TAIL_PID" 2>/dev/null
  [ -n "${FEEDER_PID:-}" ] && kill -9 "$FEEDER_PID" 2>/dev/null
  TAIL_PID=
  FEEDER_PID=
  close_control_fifo
  rm -f "$WORK/radio_meta.pipe"
}

# Create custom input.conf for channel switching
cat << 'EOF' > "$WORK/input.conf"
r run "sh -c 'echo 12 > /tmp/mplayer-exit-code && kill -TERM $PPID'"
l run "sh -c 'echo 11 > /tmp/mplayer-exit-code && kill -TERM $PPID'"
d volume -1
u volume 1
q quit 0
a pause
b osd
h switch_ratio 1
i seek +60
j seek -60
n ignore
m ignore
v speed_mult 0.9091
o speed_mult 1.1
x sub_scale +0.1
y sub_scale -0.1
s osd_show_text "Press FN+UP to screenshot!"
EOF

# --- Kill leftovers from a previous run (gmenu2x SIGKILLs apps on exit, which
# would otherwise leave a feeder looping and stealing CPU on the next launch) ---
for p in $(ps w 2>/dev/null | grep nrklive.sh | grep -v grep | awk '{print $1}'); do
  [ "$p" = "$$" ] || kill -9 "$p" 2>/dev/null
done
for p in $(ps w 2>/dev/null | grep 'tail -n 0 -f /tmp/nrklive/radio.log' | grep -v grep | awk '{print $1}'); do
  kill -9 "$p" 2>/dev/null
done
for p in $(ps w 2>/dev/null | grep 'sleep 86400' | grep -v grep | awk '{print $1}'); do
  kill -9 "$p" 2>/dev/null
done
# Stop any leftover player GRACEFULLY first so the audio codec closes cleanly;
# slamming it with -9 is what pops the speaker and can wedge the sun4i-codec.
cleanup_playback_jobs
stop_players 1

# (Channel configurations are now handled dynamically inside the loop below)

# --- MPlayer binary (mount stock MPlayer.opk read-only) ---
MP=/tmp/mp
if [ ! -x "$MP/mplayer" ]; then
  mkdir -p "$MP"; mount -o loop /media/Applications/MPlayer.opk "$MP" 2>/dev/null
fi

# --- Feeder (TV only): download + decrypt new segments -> stdout (FIFO) ---
feeder() {
  last=-1; first=1
  while true; do
    PL=$(wget -T 5 -t 2 -qO- "$VARURL" 2>/dev/null)
    [ -z "$PL" ] && { sleep 1; continue; }
    base=$(printf '%s\n' "$PL" | awk -F: '/^#EXT-X-MEDIA-SEQUENCE/{print $2; exit}')
    [ -z "$base" ] && { sleep 1; continue; }
    IV=$(printf '%s\n' "$PL" | awk '/^#EXT-X-KEY/{if(match($0,/IV=0x[0-9a-fA-F]+/))print substr($0,RSTART+5,RLENGTH-5);exit}')
    if [ "$first" = 1 ]; then
      cnt=$(printf '%s\n' "$PL" | grep -c '\.ts$')
      last=$((base + cnt - 1 - BEHIND)); first=0
    fi
    printf '%s\n' "$PL" | awk -v base="$base" -v last="$last" -v sb="$SEGBASE" '
      /\.ts$/ { seq=base+(n++); if (seq>last) { u=$0; if (u !~ /^http/) u=sb "/" u; print seq "\t" u } }' > "$WORK/new_$CURRENT_CH"
    while IFS=$(printf '\t') read -r seq url; do
      if wget -T 5 -t 2 -qO "$RAW" "$url" 2>/dev/null && [ -s "$RAW" ]; then
        "$AESDEC" "$KEYHEX" "$IV" "$RAW"
        last=$seq
      fi
    done < "$WORK/new_$CURRENT_CH"
    sleep 1
  done
}

# Clean up on exit: stop player GRACEFULLY, then clean up everything
trap 'stop_players 1; cleanup_playback_jobs; rm -f "$FIFO" "/tmp/mplayer-exit-code"; exit 0' INT TERM

cd "$MP" || exit 1

LOGFILE="/mnt/FunKey/nrk/last.log"
echo "[$(date)] --- NRK Direkte App Started ---" > "$LOGFILE"; sync

# --- WiFi Connectivity Check ---
echo "[$(date)] Checking internet connection..." >> "$LOGFILE"; sync
if ! wget -T 3 -q --spider http://lyd.nrk.no/nrk_radio_p1_ostlandssendingen_mp3_h; then
  echo "[$(date)] No internet connection detected! Showing WiFi error screen." >> "$LOGFILE"; sync
  
  cat << 'EOF' > "$WORK/wifi_sub.srt"
1
00:00:00,000 --> 99:59:59,000
Ser ud så WiFi'en
isje e kobla te.
EOF

  open_control_fifo
  
  # Ensure the font is copied to subfont.ttf first if not already present
  mkdir -p /mnt/FunKey/.mplayer
  [ -f /mnt/FunKey/.mplayer/subfont.ttf ] || cp "$DIR/RasterForgeRegular-JpBgm.ttf" /mnt/FunKey/.mplayer/subfont.ttf
  
  (sleep 0.8 && send_control "sub_load $WORK/wifi_sub.srt") &
  
  nice -n -10 ./mplayer -slave -input conf="$WORK/input.conf" -input file="$FIFO_CTRL" \
      -utf8 -subcp utf8 -subfont-autoscale 0 -subfont-text-scale 16 -subpos 70 "$DIR/p1_bg.mp4" >/dev/null 2>&1
      
  close_control_fifo
  rm -f "$WORK/wifi_sub.srt"
  exit 0
fi

CURRENT_CH="$CH"

while true; do
  echo "[$(date)] Loop iteration: CURRENT_CH=$CURRENT_CH" >> "$LOGFILE"; sync
  rm -f /tmp/mplayer-exit-code
  # Determine type and options for CURRENT_CH
  case "$CURRENT_CH" in
    nrk1|nrk2|nrk3)
      TYPE=tv
      MASTER="http://nrk-live-no.akamaized.net/$CURRENT_CH/muxed.m3u8"
      VARIANT=first; BEHIND=8; CACHE=512; CMIN=5
      KEYHEX=$(od -An -tx1 "$KEY" | tr -d ' \n')
      MPARGS="-vf scale=240:135 -sws 9 -vo sdl -ao sdl -fs -framedrop"
      FIFO="$WORK/$CURRENT_CH.ts"
      RAW="$WORK/raw_$CURRENT_CH"
      ;;
    *)
      TYPE=radio
      ;;
  esac

  # (input.conf is created once at startup)

  # Stop any leftover player GRACEFULLY first so the audio codec closes cleanly
  cleanup_playback_jobs
  stop_players 0.5

  if [ "$TYPE" = tv ]; then
    # --- Resolve variant playlist (TV only: lowest rung = first) ---
    echo "[$(date)] Resolving variant playlist for $CURRENT_CH" >> "$LOGFILE"; sync
    if [ "$VARIANT" = first ]; then
      MREL=$(wget -T 5 -t 2 -qO- "$MASTER" | awk '/EXT-X-STREAM-INF/{getline u; print u; exit}')
    else
      MREL=$(wget -T 5 -t 2 -qO- "$MASTER" | awk '/EXT-X-STREAM-INF/{getline u; last=u} END{print last}')
    fi
    if [ -z "$MREL" ]; then
      echo "[$(date)] ERROR: could not resolve variant for $CURRENT_CH" >> "$LOGFILE"; sync
      sleep 2
      continue
    fi
    MNOQ="${MASTER%%\?*}"
    MBASE="${MNOQ%/*}"
    VARURL="$MBASE/$MREL"
    SEGBASE="$MBASE/$(dirname "$MREL")"

    rm -f "$FIFO"; mkfifo "$FIFO"
    echo "[$(date)] Starting feeder process for $CURRENT_CH" >> "$LOGFILE"; sync
    feeder > "$FIFO" &
    FEEDER_PID=$!
    renice 19 "$FEEDER_PID" >/dev/null 2>&1

    open_control_fifo

    # Start TV MPlayer (using input.conf for D-pad switching and FIFO for slave commands)
    echo "[$(date)] Starting MPlayer for TV channel $CURRENT_CH" >> "$LOGFILE"; sync
    nice -n -10 ./mplayer -slave -input conf="$WORK/input.conf" -input file="$FIFO_CTRL" \
        -msglevel statusline=0 \
        -cache "$CACHE" -cache-min "$CMIN" \
        -demuxer lavf $MPARGS "$FIFO" > "$WORK/tv.log" 2>&1
    EXIT_CODE=$?
    echo "[$(date)] TV MPlayer exited with code $EXIT_CODE" >> "$LOGFILE"; sync
    kill -9 "$FEEDER_PID" 2>/dev/null
    FEEDER_PID=
    killall -9 aesdec 2>/dev/null
    rm -f "$FIFO"
    close_control_fifo

  else
    # Radio Mode
    case "$CURRENT_CH" in
      p1)
        STREAM_URL="http://lyd.nrk.no/nrk_radio_p1_ostlandssendingen_mp3_h"
        BADGE="$DIR/p1_bg.mp4"
        ;;
      p2)
        STREAM_URL="http://lyd.nrk.no/nrk_radio_p2_mp3_h"
        BADGE="$DIR/p2_bg.mp4"
        ;;
      p3)
        STREAM_URL="http://lyd.nrk.no/nrk_radio_p3_mp3_h"
        BADGE="$DIR/p3_bg.mp4"
        ;;
      *)
        STREAM_URL="http://lyd.nrk.no/nrk_radio_${CURRENT_CH}_mp3_h"
        BADGE="$DIR/${CURRENT_CH}_bg.mp4"
        ;;
    esac

    open_control_fifo
    RADIO_META="$WORK/radio_meta.pipe"
    rm -f "$RADIO_META"
    mkfifo "$RADIO_META"
    : > "$WORK/radio.log"
    if [ "$CURRENT_CH" = "p2" ]; then
      INITIAL_RADIO_TITLE="NRK P2 Direkte"
    else
      INITIAL_RADIO_TITLE="Henter info..."
    fi
    {
      printf '1\n'
      printf '00:00:00,000 --> 99:59:59,000\n'
      printf '%s\n' "$INITIAL_RADIO_TITLE"
    } > "$WORK/radio_sub.srt"
    LAST_RADIO_TITLE="$INITIAL_RADIO_TITLE"

    update_radio_subtitle() {
      [ -n "$1" ] || return
      [ "$1" = "$LAST_RADIO_TITLE" ] && return
      LAST_RADIO_TITLE="$1"
      FORMATTED_TITLE=$(printf '%s\n' "$1" | fold -s -w 18)
      # Write dynamic subtitle file
      {
        printf '1\n'
        printf '00:00:00,000 --> 99:59:59,000\n'
        printf '%s\n' "$FORMATTED_TITLE"
      } > "$WORK/radio_sub.srt"
      send_control "sub_remove 0"
      send_control "sub_load $WORK/radio_sub.srt"
      send_control "sub_select 0"
    }

    # Start background loop to read radio metadata from MPlayer without growing a log forever
    while read -r line; do
      case "$line" in
        *"ICY Info: StreamTitle="*)
          echo "$line" >> "$WORK/radio.log"
          TITLE=$(echo "$line" | sed -n "s/.*StreamTitle='\\([^']*\\)'.*/\\1/p")
          if [ -n "$TITLE" ]; then
            TITLE=$(echo "$TITLE" | tr '"' "'")
            update_radio_subtitle "$TITLE"
          fi
          ;;
      esac
    done < "$RADIO_META" &
    TAIL_PID=$!

    # Start MPlayer: single process doing audio & video (using default subfont.ttf)
    echo "[$(date)] Starting MPlayer for Radio channel $CURRENT_CH" >> "$LOGFILE"; sync
    nice -n -10 ./mplayer -slave -input conf="$WORK/input.conf" -input file="$FIFO_CTRL" \
        -msglevel statusline=0 \
        -cache 128 -cache-min 5 -vo sdl -ao sdl -audiofile "$STREAM_URL" \
        -utf8 -subcp utf8 -sub "$WORK/radio_sub.srt" -subfont-autoscale 0 -subfont-text-scale 16 -subpos 70 "$BADGE" > "$RADIO_META" 2>&1
    EXIT_CODE=$?
    echo "[$(date)] Radio MPlayer exited with code $EXIT_CODE" >> "$LOGFILE"; sync
    cleanup_playback_jobs
    rm -f "$WORK/radio_sub.srt" "$RADIO_META"
  fi

  # Channel switching logic
  if [ -f /tmp/mplayer-exit-code ]; then
    SWITCH_CODE=$(cat /tmp/mplayer-exit-code)
    rm -f /tmp/mplayer-exit-code
  else
    SWITCH_CODE=0
  fi

  if [ "$SWITCH_CODE" -eq 12 ]; then
    echo "[$(date)] D-pad Right pressed. Switching from $CURRENT_CH..." >> "$LOGFILE"; sync
    case "$CURRENT_CH" in
      nrk1) CURRENT_CH="nrk2" ;;
      nrk2) CURRENT_CH="nrk3" ;;
      nrk3) CURRENT_CH="p1" ;;
      p1) CURRENT_CH="p2" ;;
      p2) CURRENT_CH="p3" ;;
      p3) CURRENT_CH="nrk1" ;;
      *) CURRENT_CH="nrk1" ;;
    esac
  elif [ "$SWITCH_CODE" -eq 11 ]; then
    echo "[$(date)] D-pad Left pressed. Switching from $CURRENT_CH..." >> "$LOGFILE"; sync
    case "$CURRENT_CH" in
      nrk1) CURRENT_CH="p3" ;;
      nrk2) CURRENT_CH="nrk1" ;;
      nrk3) CURRENT_CH="nrk2" ;;
      p1) CURRENT_CH="nrk3" ;;
      p2) CURRENT_CH="p1" ;;
      p3) CURRENT_CH="p2" ;;
      *) CURRENT_CH="p3" ;;
    esac
  else
    echo "[$(date)] Breaking loop. Switch code: $SWITCH_CODE, MPlayer exit code: $EXIT_CODE" >> "$LOGFILE"; sync
    break
  fi
done
cleanup_playback_jobs
rm -f "$FIFO" "$WORK/radio_sub.srt" "$WORK/radio_meta.pipe"
