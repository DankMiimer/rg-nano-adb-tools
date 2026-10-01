#!/bin/bash
# nanoctl core: run (output, exit status, quoting, fidelity), transfers over both
# kinds of local path, and error messages.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_adb_mode

echo "== 0. syntax / help / version =="
bash -n "$R" && ok "bash -n" || bad "bash -n"
"$R" help | grep -q 'NANO_SERIAL' && ok "help lists the environment" || bad "help text"
check "version" "nanoctl 0.1.1" "$("$R" version)"
"$R" nonsense >/dev/null 2>&1; check "unknown command exits 2" 2 $?

echo "== 1. status =="
S="$("$R" status 2>&1)"
echo "$S" | sed 's/^/        /'
echo "$S" | grep -q '^device:    ' && ok "status shows the device" || bad "status device"
echo "$S" | grep -q '^usb mode:  adb (now)' && ok "status sees adb mode" || bad "status usb mode"
echo "$S" | grep -q '^battery:' && ok "status shows the battery" || bad "status battery"

echo "== 2. run: output, exit status, quoting, fidelity =="
check "plain output" "$("$R" run 'echo hello')" "hello"
"$R" run 'exit 7'; check "exit status 7 propagates" 7 $?
"$R" run 'true';   check "exit status 0 propagates" 0 $?
"$R" run 'false';  check "exit status 1 propagates" 1 $?
check "no CR in output" "0" "$("$R" run 'echo a; echo b' | grep -c $'\r')"
check "quotes and \$ survive" "it's 4 DONE" "$("$R" run "echo \"it's \$((1+3)) \$(echo done | tr a-z A-Z)\"")"
check "multi-line script" "x
y" "$("$R" run 'echo x
echo y')"
check "no trailing newline kept as text" "no-nl" "$("$R" run 'printf "no-nl"')"
check "intentional blank line kept" "a

b" "$("$R" run 'echo a; echo; echo b')"
check "empty output" "" "$("$R" run 'true')"
check "stderr is merged" "oops" "$("$R" run 'echo oops >&2')"

echo "== 3. transfers =="
T=/mnt/nanoctl_test
"$R" run "rm -rf $T" >/dev/null
WORK="$(native_tmpdir)"      # Linux filesystem: under WSL this goes through staging
DRV="$(drive_tmpdir)"        # directly readable by adb.exe

printf 'line1\r\nline2\r\n' > "$WORK/crlf.sh"
"$R" push-text "$WORK/crlf.sh" "$T/dir with space/crlf.sh" 755 2>/dev/null
check "push-text strips CRLF" "line1
line2" "$("$R" run "cat '$T/dir with space/crlf.sh'")"
check "no CR left on the device" "0" "$("$R" run "grep -c \$(printf '\r') '$T/dir with space/crlf.sh' || true")"

if command -v wslpath >/dev/null 2>&1; then
  # Regression: with a clean PATH cmd.exe is not found, which once made both of
  # these silently fall back to Linux paths (adb.exe then needed UNC paths).
  case "$DRV" in /mnt/[a-zA-Z]/*) ok "scratch dir is on a Windows drive" ;; *) bad "drive_tmpdir fell back to a Linux path" "$DRV" ;; esac
  OUT="$("$R" push "$WORK/crlf.sh" "$T/stagecheck.sh" 2>&1)"
  case "$OUT" in
    *wsl.localhost*|*'wsl$'*) bad "default staging used a UNC path instead of the Windows temp folder" "$OUT" ;;
    *) ok "default staging dir is on the Windows side" ;;
  esac
fi

head -c 3145728 /dev/urandom > "$WORK/bin.dat"                       # 3 MiB
want="$(sha256sum "$WORK/bin.dat" | cut -d' ' -f1)"
"$R" push "$WORK/bin.dat" "$T/bin.dat" 2>/dev/null
check "device sha256 after push (Linux-fs source)" "$want" "$("$R" run "sha256sum $T/bin.dat" | cut -d' ' -f1)"
"$R" pull "$T/bin.dat" "$WORK/back.dat" 2>/dev/null
check "pull to Linux fs round trip" "$want" "$(sha256sum "$WORK/back.dat" | cut -d' ' -f1)"

cp "$WORK/bin.dat" "$DRV/in.dat"
"$R" push "$DRV/in.dat" "$T/sub/in2.dat" 2>/dev/null
"$R" pull "$T/sub/in2.dat" "$DRV/out/out.dat" 2>/dev/null            # also creates the local dir
check "drive-path push/pull round trip" "$want" "$(sha256sum "$DRV/out/out.dat" | cut -d' ' -f1)"

"$R" pull /nonexistent/file "$WORK/nope" >/dev/null 2>&1
[ $? -ne 0 ] && ok "pull of a missing file fails" || bad "pull of a missing file should fail"

# With an explicit staging dir nothing may be left behind afterwards.
STAGE="$DRV/stage"
NANO_STAGE_DIR="$STAGE" "$R" push "$WORK/bin.dat" "$T/staged.dat" >/dev/null 2>&1
NANO_STAGE_DIR="$STAGE" "$R" push-text "$WORK/crlf.sh" "$T/staged.sh" >/dev/null 2>&1
NANO_STAGE_DIR="$STAGE" "$R" pull "$T/staged.dat" "$WORK/staged.back" >/dev/null 2>&1
check "staged transfers still correct" "$want" "$(sha256sum "$WORK/staged.back" | cut -d' ' -f1)"
check "no staged temp files left behind" "0" "$(ls -A "$STAGE" 2>/dev/null | wc -l)"

if command -v wslpath >/dev/null 2>&1; then
  # Regression: a Windows path of 260+ characters made adb.exe fail with
  # "cannot stat" on a file that exists, for push and for pull.
  deep="$DRV"
  while [ ${#deep} -lt 300 ]; do deep="$deep/long_directory_name_to_pass_the_windows_max_path"; done
  mkdir -p "$deep"
  cp "$WORK/bin.dat" "$deep/in.dat"
  "$R" push "$deep/in.dat" "$T/deep.dat" 2>/dev/null
  check "push from a >260-char Windows path" "$want" "$("$R" run "sha256sum $T/deep.dat" | cut -d' ' -f1)"
  "$R" pull "$T/deep.dat" "$deep/out.dat" 2>/dev/null
  check "pull to a >260-char Windows path" "$want" "$(sha256sum "$deep/out.dat" | cut -d' ' -f1)"
fi

echo "== 4. error messages =="
M="$(NANO_SERIAL=bogus "$R" run hostname 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "unknown serial -> clear error" "not found" "$M" || bad "unknown serial" "$M"
M="$(NANO_ADB=/nonexistent/adb "$R" run hostname 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "bad NANO_ADB -> clear error" "is not an executable" "$M" || bad "bad NANO_ADB" "$M"
M="$("$R" run 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "run without a command" "run needs a remote command" "$M" || bad "run without command" "$M"
M="$("$R" push /does/not/exist /tmp/x 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "push of a missing file" "missing local file" "$M" || bad "push missing" "$M"
M="$("$R" dev 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "dev without an app" "dev needs an app" "$M" || bad "dev without app" "$M"
M="$("$R" dev --bogus x 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "dev unknown option" "unknown option" "$M" || bad "dev option" "$M"
M="$("$R" dev --timeout abc x 2>&1)"; rc=$?
[ $rc -ne 0 ] && has "dev bad timeout" "whole number" "$M" || bad "dev timeout" "$M"

echo "== 5. cleanup =="
"$R" run "rm -rf $T" >/dev/null
check "device test dir removed" "gone" "$("$R" run "[ -e $T ] && echo present || echo gone")"
rm -rf "$WORK" "$DRV"

finish
