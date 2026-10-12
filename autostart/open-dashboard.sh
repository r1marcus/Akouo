#!/bin/bash
# Oeffnet das Akouo-Dashboard, sobald die API antwortet.
# Wird bei der Anmeldung von ~/Library/LaunchAgents/de.akouo.dashboard.plist gestartet.
#
# Umstellbar:
#   AKOUO_DASHBOARD_URL     Standard http://localhost:8000
#   AKOUO_DASHBOARD_BROWSER Browser-Name wie fuer "open -a". Ohne Angabe wird der
#                           Standardbrowser des Systems benutzt.

URL="${AKOUO_DASHBOARD_URL:-http://localhost:8000}"

# Bis zu zwei Minuten auf die API (LaunchDaemon de.akouo.api) warten.
for _ in $(seq 1 120); do
    if curl -sf -o /dev/null "$URL/healthz"; then
        break
    fi
    sleep 1
done

if [ -n "${AKOUO_DASHBOARD_BROWSER:-}" ]; then
    open -a "$AKOUO_DASHBOARD_BROWSER" "$URL"
else
    open "$URL"
fi
