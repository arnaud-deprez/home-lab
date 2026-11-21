#!/usr/bin/env bash
# Create the Immich albums of one external library from its folders, and share them.
# Shared, Arnaud: one album per direct sub-folder. Amelie: one album per event folder, two levels down (period/event);
# deeper sub-folders go into the event's album, and files directly in a period folder get no album.
# Runbook: docs/immich/photo-external-library.md
#
#   export IMMICH_URL=https://immich.vps.powple.com     # the server, without /api
#   export IMMICH_API_KEY=...                           # of the library OWNER (see the runbook for the permissions)
#   export SHARE_WITH="<the other user's name or email>"   # optional: share each new album with them (viewer)
#   ./create-albums.sh Shared          # then Arnaud, then Amelie (with Amelie's own key: she owns that library)
#
# It lists the albums it would create and asks before doing anything (UNATTENDED=1 skips the question).
# Only new albums are created and shared; existing albums are only completed with new photos.
# Needs: uv, curl, shasum. The album tool is third-party (MIT): pinned to a release commit and a checksum.
set -euo pipefail

TOOL_REPO=Salvoxia/immich-folder-album-creator
TOOL_REF=1b606bbec55316f256ade04d2d759efcd1ec81b7        # release 2.0.3
TOOL_SHA256_SCRIPT=84386fd19302938c52cf335b379240ec0a8207ddc03006339d4870e6681239fc
TOOL_SHA256_REQS=a08a4fbb363c975448f3ebc98bdd21931aa6f32688cee62ca4c189f68ba59044
ROOT=${EXTERNAL_ROOT:-/mnt/rclone/external}               # where the library folders are, as Immich sees them

LIBRARY=${1:?usage: $0 <Shared|Arnaud|Amelie> [extra album tool arguments]}; shift
: "${IMMICH_URL:?set IMMICH_URL, e.g. https://immich.example.com}"
: "${IMMICH_API_KEY:?set IMMICH_API_KEY (never put it on the command line)}"

case "$LIBRARY" in
  Shared|Arnaud) LEVELS=1 ;;     # album = each direct sub-folder of the library
  Amelie) LEVELS=2,2 ;;          # album = the folder two levels down (period/event): only that level names the album
  *) echo "ERROR: '$LIBRARY' is not supported (Shared, Arnaud or Amelie)" >&2; exit 2 ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd)
TOOLS=$HERE/.tools/$TOOL_REF
mkdir -p "$TOOLS"
fetch() {   # <file> <sha256>
  [ -f "$TOOLS/$1" ] || curl -fsSL "https://raw.githubusercontent.com/$TOOL_REPO/$TOOL_REF/$1" -o "$TOOLS/$1"
  [ "$(shasum -a 256 "$TOOLS/$1" | cut -d' ' -f1)" = "$2" ] || { rm -f "$TOOLS/$1"; echo "ERROR: checksum mismatch for $1" >&2; exit 1; }
}
fetch immich_auto_album.py "$TOOL_SHA256_SCRIPT"
fetch requirements.txt "$TOOL_SHA256_REQS"

# The key goes through a private temporary file (-t file): it must not show up in the process list.
KEYFILE=$(umask 077; mktemp)
trap 'rm -f "$KEYFILE"' EXIT
printf '%s' "$IMMICH_API_KEY" > "$KEYFILE"

ARGS=(-t file -a "$LEVELS")
[ -z "${SHARE_WITH:-}" ] || ARGS+=(--share-with "$SHARE_WITH" --share-role "${SHARE_ROLE:-viewer}")
[ -z "${UNATTENDED:-}" ] || ARGS+=(--unattended)

exec env -u VIRTUAL_ENV uv run --no-project --managed-python --python 3.13 --with-requirements "$TOOLS/requirements.txt" \
  python "$TOOLS/immich_auto_album.py" "${ARGS[@]}" "$@" "$ROOT/$LIBRARY" "${IMMICH_URL%/}/api" "$KEYFILE"
