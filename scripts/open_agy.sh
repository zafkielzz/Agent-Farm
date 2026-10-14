#!/usr/bin/env bash
# ==============================================================================
# agy-hub: Script mở agy tương tác hoặc chạy lệnh agy cho từng Profile độc lập
# ==============================================================================
set -e

PROFILE_ID=${1:-1}

if [[ "$HOME" == *".agy_profiles"* ]]; then
    PROFILE_HOME="$HOME"
else
    PROFILE_HOME="$HOME/.agy_profiles/acc$PROFILE_ID"
fi

mkdir -p "$PROFILE_HOME/.local/share/keyrings"
export HOME="$PROFILE_HOME"

shift || true

dbus-run-session -- bash -c "
  eval \$(echo '' | gnome-keyring-daemon --unlock --components=secrets 2>/dev/null)
  if [ \$# -eq 0 ]; then
    agy
  else
    agy \"\$@\"
  fi
" -- "$@"
