# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Set up Windows Internet Connection Sharing (ICS) for the RG Nano's USB link.
.DESCRIPTION
    Finds the Nano's RNDIS network adapter (USB\VID_8087&PID_011F) and your PC's
    active internet connection, turns on Windows Internet Connection Sharing
    between them, and (if adb is installed) configures the Nano and renews its
    DHCP lease.
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$Serial,
    [switch]$Disable,
    [switch]$Force,
    [switch]$Status,
    [switch]$ConfigureNano,
    [switch]$Yes,
    [switch]$DryRun,
    [switch]$Help,
    [switch]$PauseOnExit,
    # Internal parameters used across UAC self-elevation:
    [switch]$Elevated,
    [string]$TargetGuid,
    [string]$SourceGuid,
    [string]$Action,
    [string]$LogFile
)

$ErrorActionPreference = 'Stop'

$NANO_RNDIS_PNP_PREFIX = 'USB\VID_8087&PID_011F*'
$NANO_LEGACY_PNP_PREFIX = 'USB\VID_8087&PID_011E*'
$ICS_HOST_IP = '192.168.137.1'

function Write-Info([string]$msg) {
    Write-Host $msg
}

function Pause-IfRequested {
    if ($PauseOnExit -and -not $Elevated) {
        Write-Host ''
        Read-Host 'Press Enter to close' | Out-Null
    }
}

function Test-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Adb {
    $candidates = @()
    if ($env:NANO_ADB) { $candidates += $env:NANO_ADB }
    foreach ($cmdName in @('adb.exe', 'adb')) {
        $cmd = Get-Command $cmdName -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    foreach ($root in @($env:ANDROID_SDK_ROOT, $env:ANDROID_HOME)) {
        if ($root) { $candidates += (Join-Path $root 'platform-tools\adb.exe') }
    }
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe')
        $winget = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\Google.PlatformTools*\platform-tools\adb.exe'
        $candidates += (Get-Item $winget -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    }
    $candidates += @('C:\platform-tools\adb.exe', 'C:\Android\platform-tools\adb.exe')

    $wanted = if ($Serial) { $Serial } elseif ($env:NANO_SERIAL) { $env:NANO_SERIAL } elseif ($env:ANDROID_SERIAL) { $env:ANDROID_SERIAL } else { '' }
    foreach ($cand in ($candidates | Select-Object -Unique)) {
        if (-not $cand -or -not (Test-Path -LiteralPath $cand -PathType Leaf)) { continue }
        try {
            $out = & $cand devices 2>$null
            $devs = @()
            foreach ($line in $out) {
                $clean = ($line -replace "`r", '').Trim()
                if ($clean -match '^([^\s]+)\s+device$') {
                    $devs += $Matches[1]
                }
            }
            if ($devs.Count -eq 0) { continue }
            if ($wanted) {
                if ($devs -contains $wanted) {
                    return @{ Bin = $cand; Serial = $wanted }
                }
                continue
            }
            if ($devs.Count -eq 1) {
                return @{ Bin = $cand; Serial = $devs[0] }
            }
            # Several ADB devices attached: prefer a FunKey/RG Nano if identifiable, else return null
            foreach ($d in $devs) {
                $prod = Invoke-AdbRaw $cand $d 'cat /sys/kernel/config/usb_gadget/FunKey/strings/0x409/product 2>/dev/null'
                if ($prod -match 'FunKey') {
                    return @{ Bin = $cand; Serial = $d }
                }
            }
        } catch {
            continue
        }
    }
    return $null
}

function Invoke-AdbRaw([string]$adbBin, [string]$adbSerial, [string]$shellCmd) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $adbBin
    $argList = @()
    if ($adbSerial) { $argList += @('-s', $adbSerial) }
    $argList += @('shell', $shellCmd)
    # Quote arguments safely for Windows CreateProcess / CommandLineToArgvW
    $escaped = $argList | ForEach-Object {
        if ($_ -eq '' -or $_ -match '[\s"]') {
            $s = $_ -replace '(\\*)"', '$1$1\"'
            $s = $s -replace '(\\+)$', '$1$1'
            '"' + $s + '"'
        } else {
            $_
        }
    }
    $psi.Arguments = ($escaped -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $proc.WaitForExit()
    return ($stdout -replace "`r", '').Trim()
}

function Get-NanoNetAdapter {
    $all = Get-NetAdapter -ErrorAction SilentlyContinue
    $rndis = @($all | Where-Object { $_.PnPDeviceID -like $NANO_RNDIS_PNP_PREFIX })
    if ($rndis.Count -gt 0) {
        $up = @($rndis | Where-Object { $_.Status -eq 'Up' })
        if ($up.Count -gt 0) { return @{ Adapter = $up[0]; Legacy = $false } }
        return @{ Adapter = $rndis[0]; Legacy = $false }
    }
    $legacy = @($all | Where-Object { $_.PnPDeviceID -like $NANO_LEGACY_PNP_PREFIX -and $_.InterfaceDescription -match 'NDIS' })
    if ($legacy.Count -gt 0) {
        $up = @($legacy | Where-Object { $_.Status -eq 'Up' })
        if ($up.Count -gt 0) { return @{ Adapter = $up[0]; Legacy = $true } }
        return @{ Adapter = $legacy[0]; Legacy = $true }
    }
    return $null
}

function Get-AdapterIPv4([int]$ifIndex) {
    $ips = Get-NetIPAddress -InterfaceIndex $ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
    if ($ips) {
        return ($ips | Select-Object -ExpandProperty IPAddress) -join ', '
    }
    return ''
}

function Get-InternetAdapter([string]$requestedSource, [string]$excludeGuid) {
    $all = Get-NetAdapter -ErrorAction SilentlyContinue
    if ($requestedSource) {
        $exact = @($all | Where-Object { $_.Name -ieq $requestedSource })
        if ($exact.Count -eq 0) {
            $exact = @($all | Where-Object { $_.Name -like "*$requestedSource*" -or $_.InterfaceDescription -like "*$requestedSource*" })
        }
        if ($exact.Count -eq 0) {
            throw "No network adapter matching '$requestedSource' was found."
        }
        $up = @($exact | Where-Object { $_.Status -eq 'Up' })
        if ($up.Count -gt 0) { return $up[0] }
        return $exact[0]
    }

    $routes = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Sort-Object { $_.RouteMetric + $_.InterfaceMetric }
    $candidates = @()
    $seen = @{}
    foreach ($r in $routes) {
        if ($seen.ContainsKey($r.InterfaceIndex)) { continue }
        $seen[$r.InterfaceIndex] = $true
        $ad = $all | Where-Object { $_.InterfaceIndex -eq $r.InterfaceIndex } | Select-Object -First 1
        if (-not $ad) { continue }
        if ($ad.Status -ne 'Up') { continue }
        if ($excludeGuid -and $ad.InterfaceGuid -eq $excludeGuid) { continue }
        if ($ad.InterfaceDescription -match 'Hyper-V|WSL|VirtualBox|VMware|Loopback|Remote NDIS|Bluetooth') { continue }
        $candidates += $ad
    }
    if ($candidates.Count -eq 0) {
        throw 'Could not find an active internet connection (Wi-Fi or Ethernet). Connect to the internet or pass -Source "<AdapterName>".'
    }
    if ($candidates.Count -eq 1 -or $Yes) {
        return $candidates[0]
    }
    Write-Info 'Multiple active internet adapters found:'
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-Info ("  [{0}] {1} ({2})" -f ($i + 1), $candidates[$i].Name, $candidates[$i].InterfaceDescription)
    }
    $pick = Read-Host "Which adapter has internet? [1]"
    if (-not $pick) { return $candidates[0] }
    $idx = 0
    if ([int]::TryParse($pick, [ref]$idx) -and $idx -ge 1 -and $idx -le $candidates.Count) {
        return $candidates[$idx - 1]
    }
    throw "Invalid selection '$pick'."
}

function Get-NanoUsbInfo($adb) {
    $cmd = 'CONF=/mnt/USB-link/usblink.conf; [ -f "$CONF" ] && cat "$CONF" || echo "(no usblink.conf)"; echo "---"; ip -4 addr show usb0 2>/dev/null || echo "(no usb0)"; echo "---"; ip route 2>/dev/null'
    return Invoke-AdbRaw $adb.Bin $adb.Serial $cmd
}

function Enable-NanoRndisViaAdb($adb) {
    Write-Info ("Configuring USB link on Nano ({0}) for Windows internet..." -f $adb.Serial)
    $cmd = 'mkdir -p /mnt/USB-link; C=/mnt/USB-link/usblink.conf; T=$C.new; { [ -f "$C" ] && grep -v -E "^(NETWORK|ADDRESS|DNS)=" "$C" | tr -d "\r"; printf "NETWORK=rndis\nADDRESS=dhcp\nDNS=1.1.1.1\n"; } > "$T" && mv "$T" "$C" && sync; if [ -x /tmp/usblink-watch.sh ]; then USBLINK_QUIET=1 setsid sh /tmp/usblink-watch.sh </dev/null >/dev/null 2>&1 & echo started; else OPK=/mnt/Applications/Connect.opk; [ -f "$OPK" ] || OPK=/mnt/Applications/USBLink.opk; if [ ! -f "$OPK" ]; then echo no_opk; else M=/tmp/.usblink_setup_mnt; mkdir -p "$M"; if mount -o loop,ro "$OPK" "$M" 2>/dev/null; then cp "$M/usblink.sh" /tmp/usblink-setup.sh; chmod +x /tmp/usblink-setup.sh; umount "$M" 2>/dev/null; rmdir "$M" 2>/dev/null; USBLINK_QUIET=1 setsid sh /tmp/usblink-setup.sh </dev/null >/dev/null 2>&1 & echo started; else echo mount_failed; fi; fi; fi'
    $res = Invoke-AdbRaw $adb.Bin $adb.Serial $cmd
    if ($res -match 'no_opk') {
        throw 'Connect.opk or USBLink.opk is not installed in /mnt/Applications on the Nano.'
    }
    if ($res -match 'mount_failed') {
        throw 'Could not mount Connect.opk/USBLink.opk on the Nano; run Connect -> USB -> Reconnect on the Nano instead.'
    }
}

function Renew-NanoDhcpViaAdb($adb) {
    $cmd = 'IF=""; for f in /sys/kernel/config/usb_gadget/FunKey/functions/rndis.usb0/ifname /sys/kernel/config/usb_gadget/FunKey/functions/ncm.usb0/ifname; do [ -f "$f" ] && IF="$(cat "$f")" && break; done; [ -n "$IF" ] || exit 0; if [ -x /tmp/usblink-watch.sh ]; then USBLINK_LINKTEST="$IF" sh /tmp/usblink-watch.sh; elif [ -x /tmp/usblink-setup.sh ]; then USBLINK_LINKTEST="$IF" sh /tmp/usblink-setup.sh; else OPK=/mnt/Applications/Connect.opk; [ -f "$OPK" ] || OPK=/mnt/Applications/USBLink.opk; M=/tmp/.usblink_setup_mnt; mkdir -p "$M"; if [ -f "$OPK" ] && mount -o loop,ro "$OPK" "$M" 2>/dev/null; then cp "$M/usblink.sh" /tmp/usblink-setup.sh; chmod +x /tmp/usblink-setup.sh; umount "$M" 2>/dev/null; rmdir "$M" 2>/dev/null; USBLINK_LINKTEST="$IF" sh /tmp/usblink-setup.sh; fi; fi'
    return Invoke-AdbRaw $adb.Bin $adb.Serial $cmd
}

function Invoke-IcsElevated([string]$actionName, [string]$srcGuid, [string]$dstGuid, [bool]$forceReset) {
    if (Test-IsAdmin) {
        return Invoke-IcsWork $actionName $srcGuid $dstGuid $forceReset
    }
    $log = Join-Path $env:TEMP ("rg-nano-ics-" + [Guid]::NewGuid().ToString('N') + '.log')
    $argList = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
        '-Elevated', '-Action', $actionName, '-LogFile', ('"' + $log + '"')
    )
    if ($srcGuid) { $argList += @('-SourceGuid', ('"' + $srcGuid + '"')) }
    if ($dstGuid) { $argList += @('-TargetGuid', ('"' + $dstGuid + '"')) }
    if ($forceReset) { $argList += '-Force' }

    Write-Info 'Requesting Administrator permission (UAC) to configure Windows Internet Connection Sharing...'
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait -PassThru -WindowStyle Hidden
    if (Test-Path -LiteralPath $log) {
        Get-Content -LiteralPath $log | ForEach-Object { Write-Host $_ }
        Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
    }
    if ($proc.ExitCode -ne 0) {
        throw "Windows sharing setup failed (exit code $($proc.ExitCode))."
    }
}

function Invoke-IcsWork([string]$actionName, [string]$srcGuid, [string]$dstGuid, [bool]$forceReset) {
    $mgr = New-Object -ComObject HNetCfg.HNetShare
    $conns = @($mgr.EnumEveryConnection)
    if ($conns.Count -eq 0) {
        throw 'HNetCfg.HNetShare returned no connections (Administrator privileges are required).'
    }

    if ($actionName -eq 'disable') {
        $changed = $false
        foreach ($c in $conns) {
            $cfg = $mgr.INetSharingConfigurationForINetConnection($c)
            if ($cfg.SharingEnabled) {
                $p = $mgr.NetConnectionProps($c)
                Write-Output ("Disabling sharing on {0} ({1})..." -f $p.Name, $p.DeviceName)
                $cfg.DisableSharing()
                $changed = $true
            }
        }
        if (-not $changed) {
            Write-Output 'Internet Connection Sharing is already off.'
        }
        return
    }

    $srcConn = $null
    $dstConn = $null
    $srcCfg = $null
    $dstCfg = $null
    foreach ($c in $conns) {
        $p = $mgr.NetConnectionProps($c)
        if ($p.Guid -ieq $srcGuid) {
            $srcConn = $c
            $srcCfg = $mgr.INetSharingConfigurationForINetConnection($c)
        }
        if ($p.Guid -ieq $dstGuid) {
            $dstConn = $c
            $dstCfg = $mgr.INetSharingConfigurationForINetConnection($c)
        }
    }
    if (-not $srcConn) { throw "Could not find internet adapter ($srcGuid) in Network Connections." }
    if (-not $dstConn) { throw "Could not find Nano adapter ($dstGuid) in Network Connections." }

    $already = $srcCfg.SharingEnabled -and ($srcCfg.SharingConnectionType -eq 0) -and
               $dstCfg.SharingEnabled -and ($dstCfg.SharingConnectionType -eq 1)
    if ($already -and -not $forceReset) {
        Write-Output 'Internet Connection Sharing is already configured for this pair.'
        return
    }

    # Clear existing ICS bindings first: switching adapters in place can leave
    # Windows ICS half-bound (IP set, but DHCP/NAT dead).
    foreach ($c in $conns) {
        $cfg = $mgr.INetSharingConfigurationForINetConnection($c)
        if ($cfg.SharingEnabled) {
            $p = $mgr.NetConnectionProps($c)
            Write-Output ("Clearing existing sharing on {0}..." -f $p.Name)
            $cfg.DisableSharing()
        }
    }
    Start-Sleep -Milliseconds 500

    $srcProps = $mgr.NetConnectionProps($srcConn)
    $dstProps = $mgr.NetConnectionProps($dstConn)
    Write-Output ("Enabling sharing: {0} -> {1}..." -f $srcProps.Name, $dstProps.Name)
    $srcCfg.EnableSharing(0) # ICSSHARINGTYPE_PUBLIC
    $dstCfg.EnableSharing(1) # ICSSHARINGTYPE_PRIVATE
    Write-Output 'Internet Connection Sharing enabled.'
}

# ---------------------------------------------------------------------------
# Elevated child entry point
# ---------------------------------------------------------------------------
if ($Elevated) {
    $exitCode = 0
    try {
        $lines = Invoke-IcsWork $Action $SourceGuid $TargetGuid ([bool]$Force)
        if ($LogFile) {
            $lines | Out-File -FilePath $LogFile -Encoding utf8
        }
    } catch {
        $msg = "ERROR: $($_.Exception.Message)"
        if ($LogFile) { $msg | Out-File -FilePath $LogFile -Encoding utf8 }
        else { Write-Error $msg }
        $exitCode = 1
    }
    exit $exitCode
}

# ---------------------------------------------------------------------------
# Main interactive / CLI flow
# ---------------------------------------------------------------------------
if ($Help) {
    Write-Info @'
Usage: Share-Internet.cmd [options]

Share this PC's internet connection with the RG Nano over USB.

Options:
  -Source <name>    Internet-facing adapter name or description (default: auto)
  -Serial <serial>  ADB serial when several devices are attached
  -ConfigureNano    Configure usblink.conf and start RNDIS via adb if needed
  -Status           Show detected adapters and Nano network state
  -Disable          Turn off Windows Internet Connection Sharing
  -Force            Re-apply sharing even if already enabled
  -Yes              Skip confirmation prompts
  -DryRun           Show what would be done without making changes
  -Help             Show this help
'@
    exit 0
}

try {
    $nano = Get-NanoNetAdapter
    $adb = Find-Adb

    if ($Status) {
        Write-Info '=== RG Nano USB Internet Status ==='
        if ($nano) {
            $ad = $nano.Adapter
            $ip = Get-AdapterIPv4 $ad.InterfaceIndex
            Write-Info ("Nano adapter:     {0} ({1})" -f $ad.Name, $ad.InterfaceDescription)
            Write-Info ("  Status:         {0}" -f $ad.Status)
            Write-Info ("  IPv4:           {0}" -f $(if ($ip) { $ip } else { '(none)' }))
            Write-Info ("  GUID:           {0}" -f $ad.InterfaceGuid)
            if ($nano.Legacy) {
                Write-Info '  Note:           Using legacy USB ID 8087:011e (upgrade to Connect.opk / latest USBLink.opk).'
            }
        } else {
            Write-Info 'Nano adapter:     not active (8087:011f RNDIS not attached)'
        }
        try {
            $inet = Get-InternetAdapter $Source $(if ($nano) { $nano.Adapter.InterfaceGuid } else { '' })
            Write-Info ("Internet source:  {0} ({1})" -f $inet.Name, $inet.InterfaceDescription)
        } catch {
            Write-Info ("Internet source:  " + $_.Exception.Message)
        }
        if ($adb) {
            Write-Info ("ADB device:       {0} (via {1})" -f $adb.Serial, $adb.Bin)
            Write-Info (Get-NanoUsbInfo $adb)
        } else {
            Write-Info 'ADB device:       not detected (or adb.exe not installed)'
        }
        Pause-IfRequested
        exit 0
    }

    if ($Disable) {
        if ($DryRun) {
            Write-Info '[DryRun] Would disable Windows Internet Connection Sharing.'
        } else {
            Invoke-IcsElevated 'disable' '' '' $false
        }
        Pause-IfRequested
        exit 0
    }

    # Ensure Nano RNDIS adapter is present & Up
    if (-not $nano -or $nano.Adapter.Status -ne 'Up') {
        if ($adb) {
            $doConfig = $ConfigureNano -or $Yes
            if (-not $doConfig) {
                $ans = Read-Host "Nano RNDIS link is not active yet. Configure Nano via ADB and switch it on now? [Y/n]"
                $doConfig = (-not $ans) -or ($ans -match '^[Yy]')
            }
            if ($doConfig) {
                if ($DryRun) {
                    Write-Info "[DryRun] Would set NETWORK=rndis ADDRESS=dhcp DNS=1.1.1.1 on $($adb.Serial) and run usblink.sh."
                } else {
                    Enable-NanoRndisViaAdb $adb
                    Write-Info 'Waiting for Windows to enumerate the Nano network adapter...'
                    for ($i = 0; $i -lt 25; $i++) {
                        Start-Sleep -Seconds 1
                        $nano = Get-NanoNetAdapter
                        if ($nano -and $nano.Adapter.Status -eq 'Up') { break }
                    }
                }
            }
        }
        if (-not $DryRun -and (-not $nano -or $nano.Adapter.Status -ne 'Up')) {
            Write-Info ''
            Write-Info 'Nano network adapter (VID_8087&PID_011F) is not active yet.'
            Write-Info 'On the Nano:'
            Write-Info '  1. Plug in via a USB-A to USB-C cable (in ADB mode).'
            Write-Info '  2. Open Connect -> USB, set Network: Windows and Internet: via host.'
            Write-Info '  3. Press Apply (or Reconnect).'
            Write-Info 'Waiting up to 120s for the adapter to appear (Ctrl+C to cancel)...'
            for ($i = 0; $i -lt 60; $i++) {
                Start-Sleep -Seconds 2
                $nano = Get-NanoNetAdapter
                if ($nano -and $nano.Adapter.Status -eq 'Up') { break }
            }
            if (-not $nano -or $nano.Adapter.Status -ne 'Up') {
                throw 'Timed out waiting for the Nano RNDIS adapter.'
            }
        }
    }

    $target = if ($nano) { $nano.Adapter } else { $null }
    if ($nano -and $nano.Legacy) {
        Write-Info 'Warning: Nano is using legacy USB ID 8087:011e; Windows drops sharing after a Nano reboot.'
        Write-Info '         Update to Connect.opk (or latest USBLink.opk) for 8087:011f.'
    }

    $inet = Get-InternetAdapter $Source $(if ($target) { $target.InterfaceGuid } else { '' })
    if ($target) {
        Write-Info ("Will share: {0} ({1}) -> {2} ({3})" -f $inet.Name, $inet.InterfaceDescription, $target.Name, $target.InterfaceDescription)
    } else {
        Write-Info ("[DryRun] Internet source: {0} ({1})" -f $inet.Name, $inet.InterfaceDescription)
        Pause-IfRequested
        exit 0
    }

    if ($DryRun) {
        Write-Info '[DryRun] Would enable Windows ICS (Public -> Private) and check Nano DHCP.'
        Pause-IfRequested
        exit 0
    }

    $currentIps = Get-AdapterIPv4 $target.InterfaceIndex
    $needIcs = $Force -or ($currentIps -notmatch [regex]::Escape($ICS_HOST_IP))
    $adb = Find-Adb
    if (-not $needIcs -and $adb) {
        $usb0 = Invoke-AdbRaw $adb.Bin $adb.Serial 'ip -4 addr show usb0 2>/dev/null'
        if ($usb0 -match '192\.168\.137\.') {
            Write-Info ("Sharing is already active ({0} -> {1}, {2}) and Nano has a DHCP lease." -f $inet.Name, $target.Name, $ICS_HOST_IP)
            Pause-IfRequested
            exit 0
        }
    }

    if (-not $Yes) {
        $ans = Read-Host "Enable Windows Internet Connection Sharing now? [Y/n]"
        if ($ans -and $ans -notmatch '^[Yy]') {
            Write-Info 'Cancelled.'
            Pause-IfRequested
            exit 0
        }
    }

    Invoke-IcsElevated 'enable' $inet.InterfaceGuid $target.InterfaceGuid ([bool]$Force)

    # Wait briefly for Windows to assign 192.168.137.1 to the Nano adapter
    $gotHostIp = $false
    for ($i = 0; $i -lt 10; $i++) {
        $ip = Get-AdapterIPv4 $target.InterfaceIndex
        if ($ip -match [regex]::Escape($ICS_HOST_IP)) {
            $gotHostIp = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if ($gotHostIp) {
        Write-Info ("Windows host link ready: {0} = {1}" -f $target.Name, $ICS_HOST_IP)
    } else {
        Write-Info ("Note: {0} does not show {1} yet (may take a moment)." -f $target.Name, $ICS_HOST_IP)
    }

    # If ADB is available, check Nano's usb0 and renew DHCP if needed
    Start-Sleep -Seconds 2
    $adb = Find-Adb
    if ($adb) {
        $usb0 = Invoke-AdbRaw $adb.Bin $adb.Serial 'ip -4 addr show usb0 2>/dev/null'
        if ($usb0 -notmatch '192\.168\.137\.') {
            Write-Info 'Renewing DHCP lease on the Nano...'
            $linkRes = Renew-NanoDhcpViaAdb $adb
            if ($linkRes) { Write-Info ("  Nano: " + $linkRes) }
        }
        $check = Invoke-AdbRaw $adb.Bin $adb.Serial 'ip -4 addr show usb0 2>/dev/null | grep inet; ip route | grep default; cat /etc/resolv.conf 2>/dev/null'
        Write-Info 'Nano network state:'
        $check -split "`n" | ForEach-Object { if ($_.Trim()) { Write-Info ("  " + $_.Trim()) } }
    } else {
        Write-Info 'Done! On the Nano, open Connect -> USB -> Reconnect if it has not picked up an IP yet.'
    }

    Pause-IfRequested
    exit 0
} catch {
    Write-Host ("Error: " + $_.Exception.Message) -ForegroundColor Red
    Pause-IfRequested
    exit 1
}
