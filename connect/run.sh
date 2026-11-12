#!/bin/sh
# Connect: the Wi-Fi and USB settings in one app, on the console like the menu
# (no terminal), with the Python 3.12 this OPK brings (the stock firmware has
# none). Wi-Fi and the USB link keep running after the app exits.
HERE="$(cd "$(dirname "$0")" && pwd)"
export LD_LIBRARY_PATH="$HERE/rt/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export SSL_CERT_FILE="$HERE/rt/etc/cacert.pem"
export PYTHONDONTWRITEBYTECODE=1
# the menu has already put the console in graphics mode
export NANO_NO_VT=1
mkdir -p /mnt/FunKey/wifi
exec "$HERE/rt/bin/python3" "$HERE/app/connect_gui.py" 2>> /mnt/FunKey/connect.log
