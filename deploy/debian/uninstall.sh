#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
systemctl stop paopaogw-maintenance.timer paopaogw-maintenance.service paopaogw-core-action@check.service paopaogw-core-action@update.service paopaogw-core-action@rollback.service paopaogw-observe.service paopaogw-subscription.service paopaogw-api.service paopaogw.service
systemctl disable paopaogw-maintenance.timer paopaogw-observe.service paopaogw-subscription.service paopaogw-api.service paopaogw.service
# No glob, no system-wide firewall reset, no deletion of retained data/credentials.
rm -f /etc/systemd/system/paopaogw.service /etc/systemd/system/paopaogw-api.service /etc/systemd/system/paopaogw-subscription.service /etc/systemd/system/paopaogw-observe.service /etc/systemd/system/paopaogw-maintenance.service /etc/systemd/system/paopaogw-maintenance.timer /etc/systemd/system/paopaogw-core-action@.service
rm -rf /usr/local/lib/paopaogw
systemctl daemon-reload
echo 'Services removed. /etc/paopaogw and /var/lib/paopaogw retained.'
