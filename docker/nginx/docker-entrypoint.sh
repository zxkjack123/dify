#!/bin/bash

set -euo pipefail

# Wait for upstream dependencies (api, web, plugin_daemon) before starting Nginx
# Override list/timeout via env: NGINX_UPSTREAM_WAIT_HOSTS, NGINX_UPSTREAM_WAIT_TIMEOUT
: "${NGINX_UPSTREAM_WAIT_HOSTS:=api:5001 web:3000 plugin_daemon:5002}"
: "${NGINX_UPSTREAM_WAIT_TIMEOUT:=90}"

wait_for_endpoint() {
    local host_port="$1"
    local host="${host_port%%:*}"
    local port="${host_port##*:}"
    local timeout="${2:-90}"
    local start_ts
    start_ts=$(date +%s)
    echo "[nginx-entrypoint] Waiting for ${host}:${port} (timeout ${timeout}s)" >&2
    while true; do
        if exec 3<>"/dev/tcp/${host}/${port}" 2>/dev/null; then
            exec 3>&- 3<&-
            echo "[nginx-entrypoint] ${host}:${port} is reachable" >&2
            break
        fi
        sleep 2
        if (( $(date +%s) - start_ts >= timeout )); then
            echo "[nginx-entrypoint] WARNING: Timeout waiting for ${host}:${port}, continuing" >&2
            break
        fi
    done
}

for hp in ${NGINX_UPSTREAM_WAIT_HOSTS}; do
    wait_for_endpoint "$hp" "${NGINX_UPSTREAM_WAIT_TIMEOUT}"
done

HTTPS_CONFIG=''

if [ "${NGINX_HTTPS_ENABLED}" = "true" ]; then
    # Check if the certificate and key files for the specified domain exist
    if [ -n "${CERTBOT_DOMAIN}" ] && \
       [ -f "/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_FILENAME}" ] && \
       [ -f "/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_KEY_FILENAME}" ]; then
        SSL_CERTIFICATE_PATH="/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_FILENAME}"
        SSL_CERTIFICATE_KEY_PATH="/etc/letsencrypt/live/${CERTBOT_DOMAIN}/${NGINX_SSL_CERT_KEY_FILENAME}"
    else
        SSL_CERTIFICATE_PATH="/etc/ssl/${NGINX_SSL_CERT_FILENAME}"
        SSL_CERTIFICATE_KEY_PATH="/etc/ssl/${NGINX_SSL_CERT_KEY_FILENAME}"
    fi
    export SSL_CERTIFICATE_PATH
    export SSL_CERTIFICATE_KEY_PATH

    # set the HTTPS_CONFIG environment variable to the content of the https.conf.template
    HTTPS_CONFIG=$(envsubst < /etc/nginx/https.conf.template)
    export HTTPS_CONFIG
    # Substitute the HTTPS_CONFIG in the default.conf.template with content from https.conf.template
    envsubst '${HTTPS_CONFIG}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf
fi
export HTTPS_CONFIG

if [ "${NGINX_ENABLE_CERTBOT_CHALLENGE}" = "true" ]; then
    ACME_CHALLENGE_LOCATION='location /.well-known/acme-challenge/ { root /var/www/html; }'
else
    ACME_CHALLENGE_LOCATION=''
fi
export ACME_CHALLENGE_LOCATION

env_vars=$(printenv | cut -d= -f1 | sed 's/^/$/g' | paste -sd, -)

envsubst "$env_vars" < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
envsubst "$env_vars" < /etc/nginx/proxy.conf.template > /etc/nginx/proxy.conf

envsubst "$env_vars" < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

# Start Nginx using the default entrypoint
exec nginx -g 'daemon off;'
