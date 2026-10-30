#!/bin/bash

squid -N -f /etc/squid/squid.conf &
squid_pid=$!
socat TCP-LISTEN:16080,bind=0.0.0.0,reuseaddr,fork TCP:gui-runner:6080 &
vnc_relay_pid=$!
socat TCP-LISTEN:13001,bind=0.0.0.0,reuseaddr,fork TCP:gui-runner:3001 &
events_relay_pid=$!

trap 'kill "$squid_pid" "$vnc_relay_pid" "$events_relay_pid" 2>/dev/null || true' EXIT
wait -n "$squid_pid" "$vnc_relay_pid" "$events_relay_pid"
