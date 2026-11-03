#!/usr/bin/env bash
# Logs, once a minute, every process on this VM that did a lot of disk I/O, so a
# splunkd IOWait alert ("Splunk - health degraded") can be traced to a process
# afterwards. Runs as the splunk-io-top systemd unit (systemd/), so each line lands in
# the journal and reaches Splunk through the OTel agent's journald pipeline
# (index=infra, syslog.identifier=splunk-io-top). Containers' processes are visible
# here too: the host sees every container process.
#   index=infra host=security-tooling-01 "splunk-io-top" | table _time comm pid read_kBps write_kBps iodelay
# A process is logged when its average read+write over the minute is at least
# IO_TOP_MIN_KBPS (512 kB/s, about 30 MB a minute), or it spent at least
# IO_TOP_MIN_IODELAY clock ticks (100 = 1 second) waiting on block I/O. Most minutes
# log nothing or a line for splunkd.
set -euo pipefail

min_kbps="${IO_TOP_MIN_KBPS:-512}"
min_delay="${IO_TOP_MIN_IODELAY:-100}"

# pidstat -d lists only processes with I/O in the interval. -h puts each on one line:
# time UID PID kB_rd/s kB_wr/s kB_ccwr/s iodelay Command
LC_ALL=C exec pidstat -d -h 60 | awk -v kbps="$min_kbps" -v delay="$min_delay" '
    $1 ~ /^[0-9]/ && NF >= 8 && ($4 + $5 >= kbps || $7 >= delay) {
        cmd = $8; for (i = 9; i <= NF; i++) cmd = cmd " " $i
        printf "disk io: comm=\"%s\" pid=%s uid=%s read_kBps=%s write_kBps=%s cancelled_write_kBps=%s iodelay=%s\n", cmd, $3, $2, $4, $5, $6, $7
        fflush()
    }'
