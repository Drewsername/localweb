#!/bin/bash
# Watchdog: ensure avahi announces as "drew.local", not "drew-2.local" etc.
# Runs every minute via cron. If avahi has drifted to a suffixed name,
# restart it so it reclaims the correct hostname.

EXPECTED="drew"
CURRENT=$(avahi-daemon --check 2>/dev/null && \
    busctl get-property org.freedesktop.Avahi / org.freedesktop.Avahi.Server HostName 2>/dev/null | sed 's/^s "\(.*\)"$/\1/')

# Fallback: parse from systemctl status if busctl isn't available
if [ -z "$CURRENT" ]; then
    CURRENT=$(systemctl status avahi-daemon 2>/dev/null | grep -oP 'running \[\K[^\]]+')
fi

if [ -z "$CURRENT" ]; then
    exit 0
fi

if [ "$CURRENT" != "$EXPECTED" ]; then
    logger -t fix-mdns "avahi announcing as '$CURRENT' instead of '$EXPECTED', restarting"
    sudo systemctl restart avahi-daemon
fi
