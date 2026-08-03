#!/bin/sh
# dms-agent entrypoint: bring up LDAP NSS (nslcd) so the agent's
# probe_identities() (pwd.getpwnam/grp.getgrall) resolves POSIX users/groups
# from the SAME OpenLDAP the DMS identity resolver uses -- exactly as an
# LDAP/SSSD-joined production node would. Then exec the agent.
#
# nslcd.conf is templated from env (not baked) so the directory endpoint stays
# a deploy-time concern, identical to how the API/controller are configured:
#   DMS_LDAP_URI         e.g. ldap://10.10.10.30:389   (required)
#   DMS_LDAP_USER_BASE   passwd search base            (default dc base)
#   DMS_LDAP_GROUP_BASE  group search base             (default dc base)
#   DMS_LDAP_BIND_DN     optional bind DN (empty => anonymous)
#   DMS_LDAP_BIND_PW     optional bind password
set -eu

: "${DMS_LDAP_URI:?DMS_LDAP_URI is required for agent LDAP NSS}"

# Derive a sane default search base from the user base's dc components if the
# explicit bases are absent (keeps the contract minimal).
default_base="$(printf '%s' "${DMS_LDAP_USER_BASE:-}" | sed -n 's/.*\(dc=.*\)$/\1/p')"
: "${default_base:=dc=dms,dc=local}"

{
  echo "uid nslcd"
  echo "gid nslcd"
  echo "uri ${DMS_LDAP_URI}"
  echo "base passwd ${DMS_LDAP_USER_BASE:-$default_base}"
  echo "base group ${DMS_LDAP_GROUP_BASE:-$default_base}"
  if [ -n "${DMS_LDAP_BIND_DN:-}" ]; then
    echo "binddn ${DMS_LDAP_BIND_DN}"
    echo "bindpw ${DMS_LDAP_BIND_PW:-}"
  fi
} > /etc/nslcd.conf
chmod 0600 /etc/nslcd.conf

# nslcd drops to the nslcd user (setuid) BEFORE bind()ing its socket, so the
# socket dir must be writable by that user. The distro normally sets this via
# systemd-tmpfiles, absent in this container -> chown it ourselves (as root).
mkdir -p /run/nslcd
chown nslcd:nslcd /run/nslcd 2>/dev/null || true

# Run nslcd in FOREGROUND (-d) but backgrounded by the shell, instead of
# letting it self-daemonize: nslcd 0.9.12's double-fork daemonization fails in
# this container ("unable to daemonize: No data available" -- the parent never
# gets the child's readiness byte), while foreground mode connects to LDAP and
# serves the NSS socket fine. Logs go to a file so they don't interleave with
# the agent's own stdout. nslcd becomes a child of the agent (PID 1) after exec.
nslcd -d > /var/log/nslcd.log 2>&1 &

# Wait (bounded) for the NSS socket so the agent's first probe cycle can already
# resolve LDAP users instead of reporting them Missing for one interval.
i=0
while [ "$i" -lt 30 ] && [ ! -S /run/nslcd/socket ]; do
  sleep 1
  i=$((i + 1))
done
[ -S /run/nslcd/socket ] || echo "dms-agent: nslcd socket not up after ${i}s, continuing" >&2

exec "$@"
