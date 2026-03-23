#!/bin/sh
# Substitute DOMAIN into nginx config template
# Only replace ${DOMAIN} — leave nginx variables ($host, $remote_addr etc.) untouched
envsubst '${DOMAIN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
echo "Nginx configured for domain: ${DOMAIN}"
exec nginx -g 'daemon off;'
