#!/usr/bin/env bash
# Issue the TLS certificates for Splunk's management port (8089) and the Cribl
# Leader API (9000) from a small homelab CA kept on this host. The CA is created on
# the first run. Its certificate is public: the MCP gateway on k3s-01 trusts it via
# toolhive_mcp/files/homelab-ca.crt. Its key never leaves /root/homelab-ca.
# The CA can only sign names under bmosan.com, a few host names, and private IPs.
# A leaf certificate is reissued when it is missing, expires within 30 days, was
# signed by a different CA, or its names differ from the ones below. Prints "changed: splunk" / "changed: cribl" for each one
# it wrote, so deploy.sh knows what to restart.
# Run on security-tooling-01 as root (deploy.sh calls it).
set -euo pipefail

CA_DIR="${HOMELAB_CA_DIR:-/root/homelab-ca}"
SPLUNK_DIR="${SPLUNK_DATA_DIR:-/srv/splunk}"
CRIBL_DIR="${CRIBL_DATA_DIR:-/srv/cribl}"
HOST_IP="${HOST_IP:-192.168.4.11}"
# The Leader's fixed address on its compose network (docker-compose.yml). Cribl's MCP
# tools call the Leader API at the Leader's own interface address, so it must be in
# the cert.
CRIBL_LEADER_IP="${CRIBL_LEADER_IP:-192.168.237.2}"
LEAF_DAYS=825
RENEW_SECONDS=$((30 * 86400))

umask 077
mkdir -p "$CA_DIR"
chmod 700 "$CA_DIR"

if [[ ! -f "$CA_DIR/ca.key" ]]; then
    openssl req -x509 -new -newkey rsa:4096 -nodes -sha256 -days 3650 \
        -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" \
        -subj "/O=bmosan homelab/CN=bmosan homelab CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "nameConstraints=critical,permitted;DNS:bmosan.com,permitted;DNS:localhost,permitted;DNS:splunk-homelab,permitted;DNS:cribl-homelab,permitted;DNS:security-tooling-01,permitted;IP:192.168.0.0/255.255.0.0,permitted;IP:127.0.0.0/255.0.0.0" \
        2>/dev/null
    chmod 600 "$CA_DIR/ca.key"
    chmod 644 "$CA_DIR/ca.crt"
    echo "created homelab CA in $CA_DIR (copy ca.crt to toolhive_mcp/files/homelab-ca.crt)" >&2
fi

# san_list <SAN spec | cert file>: the names, one per line, sorted, as openssl prints them.
san_list() {
    if [[ -f "$1" ]]; then
        openssl x509 -noout -ext subjectAltName -in "$1" 2>/dev/null | tail -n +2 | tr ',' '\n'
    else
        tr ',' '\n' <<<"${1//IP:/IP Address:}"
    fi | sed 's/^ *//' | sort
}

# needs_issue <cert> <SAN spec>: true when the cert is missing, expiring, not from
# this CA, or has different names.
needs_issue() {
    [[ -f "$1" ]] || return 0
    openssl x509 -checkend "$RENEW_SECONDS" -noout -in "$1" >/dev/null || return 0
    openssl verify -CAfile "$CA_DIR/ca.crt" "$1" >/dev/null 2>&1 || return 0
    [[ "$(san_list "$1")" == "$(san_list "$2")" ]] || return 0
    return 1
}

# issue <name> <out-dir> <common name> <SAN list>: writes <out-dir>/<name>.{key,crt}
issue() {
    local name=$1 dir=$2 cn=$3 san=$4 ext
    mkdir -p "$dir"
    ext=$(mktemp)
    printf '%s\n' \
        "basicConstraints=critical,CA:FALSE" \
        "keyUsage=critical,digitalSignature,keyEncipherment" \
        "extendedKeyUsage=serverAuth,clientAuth" \
        "subjectAltName=$san" > "$ext"
    openssl req -new -newkey rsa:2048 -nodes -keyout "$dir/$name.key" \
        -subj "/O=bmosan homelab/CN=$cn" -out "$dir/$name.csr" 2>/dev/null
    openssl x509 -req -sha256 -days "$LEAF_DAYS" -in "$dir/$name.csr" \
        -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
        -extfile "$ext" -out "$dir/$name.crt" 2>/dev/null
    rm -f "$ext" "$dir/$name.csr"
}

# Splunk: serverCert is one PEM holding the cert, its (unencrypted) key, and the CA.
# caCertFile keeps Splunk's own default CA so nothing that already trusted it breaks.
# Skipped on a fresh VM until Splunk's first start has created etc/auth.
splunk_out="$SPLUNK_DIR/etc/auth/homelab"
splunk_san="DNS:splunk-homelab,DNS:security-tooling-01,DNS:localhost,IP:$HOST_IP,IP:127.0.0.1"
if [[ -f "$SPLUNK_DIR/etc/auth/cacert.pem" ]] && needs_issue "$splunk_out/splunk.crt" "$splunk_san"; then
    issue splunk "$splunk_out" splunk-homelab "$splunk_san"
    cat "$splunk_out/splunk.crt" "$splunk_out/splunk.key" "$CA_DIR/ca.crt" > "$splunk_out/server.pem"
    cat "$SPLUNK_DIR/etc/auth/cacert.pem" "$CA_DIR/ca.crt" > "$splunk_out/cacert.pem"
    rm -f "$splunk_out/splunk.key"
    owner=$(stat -c '%u:%g' "$SPLUNK_DIR/etc")
    chown -R "$owner" "$splunk_out"
    chmod 700 "$splunk_out"
    chmod 600 "$splunk_out"/*
    echo "changed: splunk"
fi

# Cribl Leader: separate key and cert, plus the CA for the container healthcheck.
cribl_out="$CRIBL_DIR/local/cribl/auth/homelab-tls"
cribl_san="DNS:cribl-homelab,DNS:security-tooling-01,DNS:localhost,IP:$HOST_IP,IP:$CRIBL_LEADER_IP,IP:127.0.0.1"
if needs_issue "$cribl_out/cribl.crt" "$cribl_san"; then
    issue cribl "$cribl_out" cribl-homelab "$cribl_san"
    cp "$CA_DIR/ca.crt" "$cribl_out/ca.crt"
    chmod 700 "$cribl_out"
    chmod 600 "$cribl_out/cribl.key"
    chmod 644 "$cribl_out/cribl.crt" "$cribl_out/ca.crt"
    echo "changed: cribl"
fi
