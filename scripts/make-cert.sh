#!/bin/sh
set -eu
mkdir -p /etc/quotagate
openssl req -x509 -nodes -newkey rsa:2048 -days 825 -keyout /etc/quotagate/tls.key -out /etc/quotagate/tls.crt -subj '/CN=192.168.2.1' >/dev/null 2>&1
chmod 600 /etc/quotagate/tls.key
chmod 644 /etc/quotagate/tls.crt
echo 'Created /etc/quotagate/tls.crt and tls.key'
