#!/usr/bin/env bash
# Copy the EuroNodes data to an external disk. Meant to be run by hand every few months.
# What it does and how to restore: docs/disk-backup.md
#
#   scripts/euronodes-disk-backup.sh /Volumes/Backup            # back up
#   scripts/euronodes-disk-backup.sh /Volumes/Backup --verify   # also re-read every photo on the S3 side (slow)
#   scripts/euronodes-disk-backup.sh /Volumes/Backup --status   # age of the last run, nothing else
#   DRY_RUN=1 scripts/euronodes-disk-backup.sh /Volumes/Backup  # show what would change
#
# Needs: rclone restic sops jq, the euronodes age key and the Storage Box SSH key (see below).
# No secret is written to disk: they are decrypted from the repo's SOPS files into this process.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:?usage: $0 /Volumes/<disk> [--verify|--status]}
MODE=${2:-}

export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/euronodes.agekey}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/euronodes-backup}
KEEP_RUNS=${KEEP_RUNS:-8}            # dated folders kept for the restic extras and the archive of removed photos
KEEP_DUMPS=${KEEP_DUMPS:-30}         # Immich database dumps kept on the disk
MAX_DELETE=${MAX_DELETE:-500}        # stop if one run would remove more photos than this (raise it on purpose)
PHOTO_DIRS="library upload"          # Immich folders that live on the S3 mount
IMMICH_SECRET=$REPO_DIR/apps/euronodes/immich/restic-secret.sops.yaml
POCKET_SECRET=$REPO_DIR/apps/euronodes/pocket-id/restic-secret.sops.yaml
RCLONE_SECRET=$REPO_DIR/apps/euronodes/immich/rclone-secret.sops.yaml

STAMP=$(date +%F)
PHOTOS=$DEST/immich
EXTRAS=$DEST/immich-extras
STATE=$DEST/.backup-state
RC_FLAGS=(--transfers 8 --checkers 16 --stats 30s --stats-one-line --log-level NOTICE)
[ -n "${DRY_RUN:-}" ] && RC_FLAGS+=(--dry-run)

die() { echo "ERROR: $*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
secret() { sops -d --extract "[\"stringData\"][\"$2\"]" "$1"; }

[ -d "$DEST" ] || die "$DEST does not exist: is the disk plugged in?"

if [ "$MODE" = "--status" ]; then
  if [ -f "$STATE/last-run" ]; then
    last=$(cat "$STATE/last-run")
    days=$(( ( $(date +%s) - $(date -j -f %Y-%m-%d "$last" +%s) ) / 86400 ))
    echo "last successful run: $last ($days days ago)"
  else
    echo "no run recorded on $DEST"
  fi
  exit 0
fi

for tool in rclone restic sops jq; do
  command -v "$tool" >/dev/null || die "$tool not found (brew install $tool)"
done
[ -w "$DEST" ] || die "$DEST is not writable"
[ -r "$SOPS_AGE_KEY_FILE" ] || die "age key not found: $SOPS_AGE_KEY_FILE"
[ -r "$SSH_KEY" ] || die "Storage Box SSH key not found: $SSH_KEY"
mkdir -p "$PHOTOS" "$EXTRAS" "$STATE"

# ---- 1. Photo originals: plain files, straight from the encrypted bucket --------------------------------
step "Loading the rclone crypt remote from SOPS"
while IFS= read -r kv; do export "$kv"; done < <(
  sops -d --extract '["stringData"]' --output-type json "$RCLONE_SECRET" | jq -r 'to_entries[] | "\(.key)=\(.value)"')

for d in $PHOTO_DIRS; do
  # An empty or unreachable source must never turn into "delete everything on the disk".
  rclone lsf "crypt:$d" --max-depth 1 | head -n 1 | grep -q . || die "crypt:$d is empty or unreachable: not touching the disk"
done

if [ -z "${SKIP_DISK_CHECK:-}" ] && [ -f "$STATE/photos.sha256" ]; then
  step "Checking the photos already on the disk against the last manifest (bit rot)"
  rclone checksum sha256 "$STATE/photos.sha256" "$PHOTOS" --log-level NOTICE \
    || die "files on the disk differ from the last run: see docs/disk-backup.md 'The disk check fails'"
fi

for d in $PHOTO_DIRS; do
  step "Syncing $d (removed or replaced files go to immich-archive/$STAMP/$d)"
  rclone sync "crypt:$d" "$PHOTOS/$d" "${RC_FLAGS[@]}" \
    --backup-dir "$DEST/immich-archive/$STAMP/$d" --max-delete "$MAX_DELETE"
done

if [ "$MODE" = "--verify" ]; then
  step "Re-reading every photo from S3 and comparing it with the disk (slow, downloads everything)"
  for d in $PHOTO_DIRS; do
    rclone check "crypt:$d" "$PHOTOS/$d" --download --one-way --log-level NOTICE
  done
fi

# ---- 2. Immich database dumps, profile pictures and Pocket ID: restored from the Storage Box -----------
SFTP_HOST=
sftp_opts() {
  local repo=$1 known=$2
  repo=${repo#sftp:}; SFTP_HOST=${repo%%:*}
  echo "sftp.command=ssh -p 23 -i $SSH_KEY -o UserKnownHostsFile=$known -o StrictHostKeyChecking=yes $SFTP_HOST -s sftp"
}
KNOWN=$(mktemp); trap 'rm -f "$KNOWN"' EXIT
secret "$IMMICH_SECRET" known_hosts > "$KNOWN"

restore_latest() {   # <secret-file> <tag> <target> [restic restore args...]
  local file=$1 tag=$2 target=$3; shift 3
  export RESTIC_REPOSITORY RESTIC_PASSWORD
  RESTIC_REPOSITORY=$(secret "$file" RESTIC_REPOSITORY); RESTIC_PASSWORD=$(secret "$file" RESTIC_PASSWORD)
  local opt; opt=$(sftp_opts "$RESTIC_REPOSITORY" "$KNOWN")
  restic -o "$opt" snapshots --tag "$tag" --latest 1 --compact
  [ -n "${DRY_RUN:-}" ] && return 0
  restic -o "$opt" restore latest --tag "$tag" --target "$target" --exclude-xattr security.selinux "$@"
}

step "Immich: database dumps and profile pictures from the Storage Box"
mkdir -p "$EXTRAS/backups" "$EXTRAS/profile"
TMP=$EXTRAS/.restore-tmp
rm -rf "$TMP"
restore_latest "$IMMICH_SECRET" immich "$TMP" --include /immich/backups --include /immich/profile
if [ -d "$TMP/immich" ]; then
  cp -R -n "$TMP/immich/backups/." "$EXTRAS/backups/"
  rsync -a "$TMP/immich/profile/" "$EXTRAS/profile/"
  rm -rf "$TMP"
  # keep the newest dumps only (Immich itself keeps 14 days; the disk accumulates one batch per run)
  ls -1t "$EXTRAS"/backups/immich-db-backup-* 2>/dev/null | tail -n +"$((KEEP_DUMPS + 1))" | while IFS= read -r f; do rm -f "$f"; done
fi

step "Pocket ID: database dump and data folder from the Storage Box"
restore_latest "$POCKET_SECRET" pocket-id "$DEST/pocket-id/$STAMP"

# ---- 3. Bookkeeping ----------------------------------------------------------------------------------------
if [ -z "${DRY_RUN:-}" ]; then
  step "Pruning old dated folders (keeping the last $KEEP_RUNS runs) and writing the manifest"
  for base in immich-archive pocket-id; do
    [ -d "$DEST/$base" ] || continue
    ls -1d "$DEST/$base"/????-??-?? 2>/dev/null | sort -r | tail -n +"$((KEEP_RUNS + 1))" | while IFS= read -r old; do rm -rf "$old"; done
  done
  rclone hashsum sha256 "$PHOTOS" > "$STATE/photos.sha256.new"
  mv "$STATE/photos.sha256.new" "$STATE/photos.sha256"
  echo "$STAMP" > "$STATE/last-run"
  [ -n "${HC_PING_URL:-}" ] && curl -fsS -m 10 --retry 3 "$HC_PING_URL" >/dev/null || true
fi

step "Done: $(du -sh "$DEST" 2>/dev/null | cut -f1) on $DEST"
