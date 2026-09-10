#!/bin/sh
set -e

# Ported from PoultryOS-CBP's backend/docker-entrypoint.sh — same Dokploy
# host, same problem. The stack's own private network (hub_internal) must
# own the default route so outbound HTTPS works — the hub backend calls
# back into client deployments' public URLs (services/deployment_client.py).
# dokploy-network is attached only so the container can reach the
# Dokploy-managed Postgres by name, but Docker tends to pick it as the
# default-route network, and it has no internet NAT — silently blackholing
# every outbound request. Compose's per-network `priority` field had no
# effect on this host's Docker version, so force the route explicitly.
#
# Identify the non-dokploy interface by EXCLUDING dokploy-network's subnet
# (one stable, platform-wide network) rather than matching hub_internal's
# own auto-assigned subnet.
if [ -n "$DOKPLOY_NETWORK_SUBNET" ]; then
    DOKPLOY_PREFIX=$(echo "$DOKPLOY_NETWORK_SUBNET" | cut -d/ -f1 | cut -d. -f1-2)
    IFACE=$(ip -4 -o addr show | awk -v pfx="$DOKPLOY_PREFIX." '$4 !~ ("^"pfx) && $2 != "lo" {print $2; exit}')
    if [ -n "$IFACE" ]; then
        SELF_ADDR=$(ip -4 -o addr show "$IFACE" | awk '{print $4}' | cut -d/ -f1)
        GATEWAY=$(echo "$SELF_ADDR" | awk -F. '{print $1"."$2"."$3".1"}')
        if [ -n "$GATEWAY" ]; then
            ip route replace default via "$GATEWAY" dev "$IFACE"
        fi
    fi
fi

exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
