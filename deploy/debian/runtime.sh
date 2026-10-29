# Sourced by ppg.sh only under the Debian service mount namespace.
net_ready() {
    ip link show dev "$PPGW_INTERFACE" >/dev/null 2>&1 || exit 1
    eth0ip=$(ip -4 -o addr show dev "$PPGW_INTERFACE" scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
    [ -n "$eth0ip" ] || { log "No IPv4 on configured interface" warn; exit 1; }
    export eth0ip
    gw=$(ip -4 route show default dev "$PPGW_INTERFACE" | awk '{print $3}' | head -1)
    export gw
}
sync_ntp() { :; } # Use Debian's configured clock service.
hot_reload_box() {
    pid=$(pidof sniffbox)
    if [ -n "$pid" ]; then kill -HUP "$pid"; else load_box; fi
}
