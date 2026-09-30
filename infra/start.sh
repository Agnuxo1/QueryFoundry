#!/bin/bash
set -euo pipefail
install -m 600 -o qf -g qf /run/qf_authorized_keys /home/qf/.ssh/authorized_keys
# Unlock the dedicated key-only account without configuring an SSH password.
usermod -p '*' qf
ssh-keygen -A >/dev/null
/usr/sbin/sshd
exec /usr/local/bin/docker-entrypoint.sh "$@"
