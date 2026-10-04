#!/bin/bash
set -e

# Substitute only INFOLADA_* vars (Asterisk ${VAR} must remain literal)
for tmpl in /etc/asterisk-templates/*.conf; do
    fname=$(basename "$tmpl")
    envsubst '${INFOLADA_SIP_HOST} ${INFOLADA_SIP_PORT} ${INFOLADA_SIP_USER} ${INFOLADA_SIP_PASSWORD}' < "$tmpl" > "/etc/asterisk/$fname"
done

# Fix permissions
chown -R asterisk:asterisk /var/log/asterisk /var/run/asterisk /var/spool/asterisk 2>/dev/null || true

exec asterisk -fp
