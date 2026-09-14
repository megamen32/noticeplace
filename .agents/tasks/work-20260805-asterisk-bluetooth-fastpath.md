# Fast-path audit: Asterisk Bluetooth GSM bridge

## Role

Explorer

## Goal

Determine whether this host already has the minimal local prerequisites for an
Asterisk + Bluetooth HFP S21 trial, and report the shortest safe setup path.

## Allowed actions

Read-only: inspect installed binaries/packages, Bluetooth controller state,
Asterisk modules/config, and current S21 Bluetooth pairing visibility. Do not
install, pair, restart, change config, or place calls.

## Acceptance

Append concise evidence: present/missing prerequisites, whether `chan_mobile`
is realistically testable now, and the single first mutation required. Return
only TL;DR.

## Explorer evidence (2026-08-05)

- Missing: `asterisk`, `/etc/asterisk`, `/usr/lib/asterisk/modules`, and
  `asterisk.service`; `dpkg-query` reports no Asterisk package.
- Present: BlueZ 5.64 (`bluez`, `bluez-obexd`) and
  `pulseaudio-module-bluetooth`; `bluetoothctl`, `rfcomm`, and `hcitool` are
  installed. `apt-cache` offers `asterisk-mobile` 18.10.0, which depends on
  matching `asterisk` and provides Bluetooth phone support.
- Unavailable: `bluetooth.service` is loaded but disabled/inactive; repeated
  controller checks produced no adapter, `/sys/class/bluetooth` is absent, and
  no Bluetooth kernel/controller evidence or S21 pairing visibility exists.
- No `chan_mobile` module/config can be tested now. First mutation: install
  the distro `asterisk` plus matching `asterisk-mobile` packages; only after
  that, separately provision/enable a host Bluetooth adapter and pair S21.
