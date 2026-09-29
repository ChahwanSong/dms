#!/bin/sh
# dms-agent entrypoint: bring up LDAP NSS (nslcd) so the agent's
# probe_identities() (pwd.getpwnam/grp.getgrall) resolves POSIX users/groups
# from the SAME directory the DMS identity resolver uses -- exactly as an
# LDAP/SSSD-joined production node would. Then exec the agent.
#
# nslcd.conf is templated from env (not baked) so the directory endpoint stays
# a deploy-time concern, identical to how the API/controller are configured.
# Every setting MIRRORS the control plane's resolver (src/dms/identity_ldap.py)
# -- the agent and the planner must see the same identities:
#   DMS_LDAP_URI            sssd ldap_uri form: comma list, trailing '/' ok,
#                           e.g. "ldap://p1/, ldap://r3/" (required). nslcd
#                           needs one URI per `uri` line -- a comma list is
#                           taken as ONE bogus hostname (gaierror) and
#                           failover silently never happens (prod 2026-09-29).
#   DMS_LDAP_USER_BASE      passwd search base            (default dc base)
#   DMS_LDAP_GROUP_BASE     group search base             (default dc base)
#   DMS_LDAP_USE_START_TLS  "true"/"1" => StartTLS BEFORE bind, cert not
#                           verified (resolver's ssl.CERT_NONE / sssd
#                           reqcert=never). Unset => true (resolver default).
#   DMS_LDAP_BIND_DN        search-account DN (ConfigMap); empty => anonymous
#   DMS_LDAP_BIND_PW        its password (Secret key, see 50-agent-daemonset)
#
# Why bind matters (prod 2026-09-29): the site directory lets anonymous binds
# read the rootDSE but NOT user entries, so an anonymous nslcd resolved nobody
# and every request died with identity_not_ready_on_node. The testbed slapd
# allows anonymous reads, which hid it.
#
# `agent-entrypoint.sh --render-nslcd-conf <path>` only templates the config
# (to <path>) and exits -- used by tests/test_agent_nslcd_conf.py.
set -eu

render_only=""
if [ "${1:-}" = "--render-nslcd-conf" ]; then
  render_only="${2:?--render-nslcd-conf needs an output path}"
fi
conf="${render_only:-/etc/nslcd.conf}"

: "${DMS_LDAP_URI:?DMS_LDAP_URI is required for agent LDAP NSS}"

# nslcd.conf is line-oriented: a value carrying a line break would smuggle in
# extra directives. The values come from the ConfigMap/Secret (admin-owned),
# so this is a guard against paste accidents, not an attack surface -- refuse
# loudly, and never echo the value itself (it may be the password).
nl=$(printf '\n_'); nl=${nl%_}
cr=$(printf '\r')
for var in DMS_LDAP_URI DMS_LDAP_USER_BASE DMS_LDAP_GROUP_BASE DMS_LDAP_BIND_DN DMS_LDAP_BIND_PW; do
  eval "val=\${$var:-}"
  case "$val" in
    *"$nl"*|*"$cr"*)
      echo "dms-agent: $var contains a line break -- refusing to template nslcd.conf" >&2
      exit 64 ;;
  esac
done

# Derive a sane default search base from the user base's dc components if the
# explicit bases are absent (keeps the contract minimal).
default_base="$(printf '%s' "${DMS_LDAP_USER_BASE:-}" | sed -n 's/.*\(dc=.*\)$/\1/p')"
: "${default_base:=dc=dms,dc=local}"

# identity_ldap._parse_uris mirror: split on commas (and blanks), drop empties
# and the sssd-style trailing '/'.
uris=$(printf '%s\n' "$DMS_LDAP_URI" | tr ', \t' '\n\n\n' | sed 's:/*$::' | grep -v '^$' || true)
if [ -z "$uris" ]; then
  echo "dms-agent: DMS_LDAP_URI has no usable URI" >&2
  exit 64
fi

# config._parse_bool mirror: only an UNSET var takes the default (true); an
# explicit "" is false, like the resolver.
case "$(printf '%s' "${DMS_LDAP_USE_START_TLS-true}" | tr 'A-Z' 'a-z' | tr -d ' \t')" in
  true|1) start_tls=1 ;;
  *) start_tls="" ;;
esac

bind_dn="${DMS_LDAP_BIND_DN:-}"
bind_pw="${DMS_LDAP_BIND_PW:-}"
if [ -n "$bind_dn" ] && [ -z "$bind_pw" ]; then
  echo "dms-agent: WARNING DMS_LDAP_BIND_DN is set but DMS_LDAP_BIND_PW is empty" \
       "-- nslcd binds ANONYMOUSLY; directories that deny anonymous user searches" \
       "resolve nobody (identity_not_ready_on_node). Check dms-secrets." >&2
  bind_dn=""
fi
case "$bind_pw" in
  " "*|*" "|"	"*|*"	")
    echo "dms-agent: WARNING DMS_LDAP_BIND_PW has leading/trailing whitespace," \
         "which nslcd.conf cannot represent (it trims values)" >&2 ;;
esac

# umask 077 in a subshell: the file is 0600 from creation (it may hold the
# bind password) instead of briefly world-readable before a chmod.
(
  umask 077
  {
    echo "uid nslcd"
    echo "gid nslcd"
    printf '%s\n' "$uris" | sed 's/^/uri /'
    echo "base passwd ${DMS_LDAP_USER_BASE:-$default_base}"
    echo "base group ${DMS_LDAP_GROUP_BASE:-$default_base}"
    if [ -n "$start_tls" ]; then
      # StartTLS precedes the bind, so the password never crosses in clear.
      echo "ssl start_tls"
      echo "tls_reqcert never"
    fi
    if [ -n "$bind_dn" ]; then
      echo "binddn ${bind_dn}"
      echo "bindpw ${bind_pw}"
    fi
  } > "$conf"
)
chmod 0600 "$conf"

# One diagnosable line in `kubectl logs` (the 2026-09-29 incident had none):
# how many servers, TLS on/off, and who we bind as -- never the password.
n_uris=$(printf '%s\n' "$uris" | grep -c .)
if [ -n "$bind_dn" ]; then bind_desc="dn=${bind_dn} (password set)"; else bind_desc="anonymous"; fi
if [ -n "$start_tls" ]; then tls_desc=on; else tls_desc=off; fi
echo "dms-agent: nslcd uri=${n_uris} server(s) start_tls=${tls_desc} bind=${bind_desc}" >&2

[ -z "$render_only" ] || exit 0

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
