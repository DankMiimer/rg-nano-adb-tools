#!/bin/sh
# Two-phase launcher. The single framebuffer can't be shared by st (the menu
# terminal) and MPlayer, so: phase 1 runs the menu inside st; when the user
# picks a video the menu writes /tmp/yt_play.json and exits; phase 2 plays it
# WITHOUT st so MPlayer owns the screen + buttons (like the NRK app); then loop
# back to the menu. No request file = the user chose Exit.
# Python 3.12, qjs and the CA bundle come from this OPK (rt/): the stock
# firmware has none of them.
HERE="$(cd "$(dirname "$0")" && pwd)"
export NANOPLAY_RUNTIME="$HERE/rt"
export LD_LIBRARY_PATH="$HERE/rt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export SSL_CERT_FILE="$HERE/rt/etc/cacert.pem"
export NANOPLAY_QJS="$HERE/rt/bin/qjs"
export PYTHONDONTWRITEBYTECODE=1
ST=/usr/bin/st
[ -x "$ST" ] || ST="$HERE/st"
export NANOPLAY_LIST_ROWS=9
# GBA Mini (DrUm78 maps its missing X/Y to Start+A/B): its 240x160 panel shows
# only pixel rows ~45-215 of the 240-row screen, i.e. lines 7-26 of st's 8-px
# lines (measured 2026-10-31), so start 6 lines down and keep screens shorter
if grep -q "MAP START+A" /etc/fkgpiod.conf 2>/dev/null; then
  export NANOPLAY_TOP_PAD=6 NANOPLAY_LIST_ROWS=8
fi
# st opens with its help screen and its own on-screen keyboard, which takes the
# D-pad. Once NanoPlay's menu is running inside it, press A (closes the help)
# and X (hides the keyboard) for the user, so the D-pad drives the menu at once
# (the GBA Mini has no X button to do it). X brings the keyboard back.
st_press_a_x() {
  i=0
  while [ $i -lt 100 ]; do
    grep -q youtube_nano /proc/[0-9]*/cmdline 2>/dev/null && break
    sleep 0.1; i=$((i + 1))
  done
  sleep 0.5
  "$HERE/rt/bin/python3" -c '
import os, struct, time
fd = os.open("/dev/input/event0", os.O_WRONLY)
for code in (30, 45):                       # KEY_A, KEY_X
    for value in (1, 0):                    # press, release
        os.write(fd, struct.pack("llHHi", int(time.time()), 0, 1, code, value))
        os.write(fd, struct.pack("llHHi", int(time.time()), 0, 0, 0, 0))   # EV_SYN
        time.sleep(0.05)
    time.sleep(0.3)
os.close(fd)' 2>/dev/null
}
while true; do
  rm -f /tmp/yt_play.json
  export SDL_NOMOUSE=1
  export NANOPLAY_SCREEN_WIDTH=38
  # after a video the console is left so that st's SDL can't get its VT
  # (black screen, frozen): reset it first, as the player does for MPlayer
  "$HERE/rt/bin/python3" /mnt/FunKey/youtube/youtube_nano.py vtreset
  st_press_a_x &
  "$ST" -e sh /mnt/FunKey/youtube/youtube.sh
  [ -f /tmp/yt_play.json ] || break
  sh /mnt/FunKey/youtube/youtube.sh play
done
