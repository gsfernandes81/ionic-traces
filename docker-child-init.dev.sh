#!/usr/bin/env bash
# ionic-dev's own start-up, baked into the image at /home/dev/child-init.sh and RUN by the
# base image's entrypoint (gsrpi-dev-base's child-init seam). The base runs it after it has
# cloned or pulled /workspace — a volume holding this repo's https clone, made with the
# hub's GitHub token — and before it starts sshd, so a lockfile that pull moved is the one
# installed from, and nothing has arrived yet to meet a half-installed venv. infra's
# `make login C=ionic-dev` also runs it, after a clone start-up could not make.
#
# NON-FATAL AT THE OTHER END. A non-zero exit here is printed by the entrypoint as a
# warning and the container still comes up — a container you can ssh into and fix beats
# one that refused to start over its dependencies. So this script reports failure
# honestly rather than swallowing it with `|| true`.
#
# It touches no database: none is needed to sync, and none is provided.
set -u

rc=0

# ── the venv ────────────────────────────────────────────────────────────────
# The dependency set is baked into /home/dev/venv at build time with
# --no-install-project; this adds the editable project itself, which cannot happen at
# build time because there is no clone then. It runs on every start because the venv is
# in the image and not on a volume, so a rebuild is a fresh one.
#
# THE FLAGS MUST MATCH Dockerfile.dev's build sync (--frozen, default groups). uv sync
# makes the environment match what it is asked for, so a start with fewer would
# UNINSTALL what the build put there.
#
# --frozen: install exactly what uv.lock says and never resolve. A start-up that quietly
# re-resolved dependencies would make the container's environment a function of when it
# was last restarted.
if [ -f /workspace/pyproject.toml ]; then
    if (cd /workspace && uv sync --frozen); then
        echo "venv synced from uv.lock (Python $(/home/dev/venv/bin/python -c 'import platform; print(platform.python_version())'))"
    else
        rc=1
        echo "uv sync FAILED — /home/dev/venv is stale or incomplete."
        echo "    The container is still up: ssh in and run the same command to see why."
    fi
else
    echo "no clone in /workspace yet — venv left as built."
    echo "    The first clone needs the hub's token; 'make login C=ionic-dev' on zero clones it."
fi

exit "$rc"
