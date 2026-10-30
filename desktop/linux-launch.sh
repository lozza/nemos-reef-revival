#!/bin/sh
set -eu
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$app_dir"
if [ "$(uname -m)" != x86_64 ]; then
    printf '%s\n' 'This test build requires 64-bit Intel/AMD Linux.' >&2
    exit 1
fi
if [ ! -f nemo.apk ]; then
    printf '%s\n' 'Place your original nemo.apk beside this launcher, then try again.' >&2
    exit 1
fi
exec ./NemosReef "$@"
