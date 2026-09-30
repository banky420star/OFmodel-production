#!/bin/bash
# Persona Studio launcher — the executable inside "Persona Studio.app".
#
# What it does: make sure the API (:8000) and the web UI (:3000) are answering,
# then open the UI in the default browser. If it cannot get them up, it says so
# in a dialog instead of opening a dead page, and it never leaves a service
# stopped that it did not stop.
#
# Two things worth knowing before editing this:
#
#  1. Nothing here starts a process by hand. Both services are managed by
#     launchd (`com.persona.driveapi`, `com.persona.driveweb`), so they survive
#     reboots and come back if they crash. A process started from this script
#     would be re-parented to pid 1 and be gone after the next reboot — the
#     exact reason the UI used to disappear.
#
#  2. The probe runs before any `kickstart -k`, and `kickstart -k` kills the
#     service before restarting it. That matters: a build runs *inside* the API
#     process, so killing it mid-build throws away up to two hours of work (see
#     the note about `build_write_lock`). It is only safe because health failing
#     for the whole grace period means the API is not there to have a build in
#     flight — a cold start (torch imports take ~55s) is waited out, not killed.
#
# Exit codes: 0 opened, 1 something would not come up.

set -u

API_BASE="http://localhost:8000"
WEB_BASE="http://localhost:3000"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
REPO_SCRIPTS="/Volumes/AI_DRIVE/persona/scripts"

# Seconds to wait before concluding a service is down rather than starting up.
COLD_START_GRACE=20
# Seconds to wait after a restart, which includes a full cold start.
AFTER_KICK=90

up() { /usr/bin/curl -fsS --max-time 4 -o /dev/null "$1" 2>/dev/null; }

notify_failure() {
    # A dialog, not a notification: a notification can be missed, and by then
    # the user is looking at a browser that never opened.
    /usr/bin/osascript -e "display dialog \"$1\" with title \"Persona Studio\" \
buttons {\"OK\"} default button 1 with icon caution" >/dev/null 2>&1
}

# Wait for a probe, then — only after the grace period — restart the service and
# wait again. `bootstrap` is the fallback for a service that was never loaded
# (a fresh login, or a plist that was just installed).
ensure_service() {
    local label="$1" probe="$2" plist="$3"

    local i
    for ((i = 0; i < COLD_START_GRACE; i++)); do
        up "$probe" && return 0
        sleep 1
    done

    /bin/launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1

    if ! /bin/launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && [ -f "$plist" ]; then
        /bin/launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1
    fi

    for ((i = 0; i < AFTER_KICK; i++)); do
        up "$probe" && return 0
        sleep 1
    done
    return 1
}

if ! ensure_service com.persona.driveapi "$API_BASE/api/v1/health" \
        "$LAUNCH_AGENTS/com.persona.driveapi.plist"; then
    notify_failure "The Persona Studio API did not answer on $API_BASE after $AFTER_KICK s.\\n\\nCheck /tmp/persona_driveapi.log, then try: launchctl kickstart -k gui/$(id -u)/com.persona.driveapi"
    exit 1
fi

if ! ensure_service com.persona.driveweb "$WEB_BASE" \
        "$LAUNCH_AGENTS/com.persona.driveweb.plist"; then
    notify_failure "The API is up, but the Persona Studio UI did not answer on $WEB_BASE after $AFTER_KICK s.\\n\\nCheck /tmp/persona_driveweb.log. The API is still available at $API_BASE/docs"
    exit 1
fi

/usr/bin/open "$WEB_BASE"
exit 0
