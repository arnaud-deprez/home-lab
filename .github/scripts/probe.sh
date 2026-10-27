#!/usr/bin/env bash
# External uptime probe for one cluster, run from GitHub Actions (outside the cluster, so it also sees
# DNS, ingress and certificate problems that Prometheus inside cannot). Reads CHECKS, one per line:
#   name|url|expected_http_status
# and fails when a check returns another status or the host's TLS certificate expires within
# CERT_MIN_DAYS. Failures are posted to Telegram. Environment: CLUSTER, CHECKS, TELEGRAM_BOT_TOKEN,
# TELEGRAM_CHAT_ID, optional CERT_MIN_DAYS (default 14) and DRY_RUN=1 (print instead of sending).
set -uo pipefail

: "${CLUSTER:?}" "${CHECKS:?}"
min_days=${CERT_MIN_DAYS:-14}
failures=()
hosts=()

while IFS='|' read -r name url expect; do
  [[ -z "$name" ]] && continue
  # --retry covers transient errors (timeouts, 5xx) so a single blip does not page
  code=$(curl -s -o /dev/null -m 20 --retry 2 --retry-delay 5 -w '%{http_code}' "$url") || code=000
  [[ "$code" == "$expect" ]] || failures+=("$name: HTTP $code, expected $expect ($url)")
  host=${url#*://}; host=${host%%[/:]*}
  [[ " ${hosts[*]-} " == *" $host "* ]] || hosts+=("$host")
done <<<"$CHECKS"

for host in "${hosts[@]}"; do
  cert=$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null | openssl x509 2>/dev/null)
  if [[ -z "$cert" ]]; then
    failures+=("$host: no TLS certificate could be read")
  elif ! openssl x509 -noout -checkend $((min_days * 86400)) <<<"$cert" >/dev/null; then
    failures+=("$host: TLS certificate expires within $min_days days ($(openssl x509 -noout -enddate <<<"$cert"))")
  fi
done

if ((${#failures[@]} == 0)); then
  echo "[$CLUSTER] all checks passed"
  exit 0
fi

msg="🔴 [$CLUSTER] external probe failed"$'\n'
for f in "${failures[@]}"; do msg+="- $f"$'\n'; done
[[ -n "${GITHUB_RUN_ID:-}" ]] && msg+="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
echo "$msg"

if [[ "${DRY_RUN:-}" == 1 ]]; then
  echo "(dry run: nothing sent)"
else
  curl -fsS -m 20 --retry 2 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN:?}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:?}" --data-urlencode "text=$msg" >/dev/null || {
    echo "could not send the Telegram message"
    exit 2
  }
fi
exit 1
