#!/bin/sh
set -eu

image="${1:?usage: verify-sandbox-image.sh IMAGE}"
user="$(docker image inspect "$image" --format '{{.Config.User}}')"
[ "$user" = "10001:10001" ]
digest="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')"
printf 'sandbox image: %s\n' "$digest"
if docker history --no-trunc "$image" | grep -Eqi 'AZURE_|AWS_|GOOGLE_|NPM_TOKEN|PIP_INDEX_URL'; then
    printf '%s\n' 'secret-like build argument found in image history' >&2
    exit 1
fi
docker run --rm --network none --read-only --tmpfs /workspace/task:rw,noexec,nosuid,size=1g \
    --entrypoint /bin/sh "$image" -c 'test -w /workspace/task && id -u | grep -qx 10001'