#!/usr/bin/env bash
# Copy the EuroNodes data to an external disk. Meant to be run by hand every few months.
# What it does and how to restore: docs/disk-backup.md
#
#   scripts/euronodes-disk-backup.sh /Volumes/Backup            # back up
#   scripts/euronodes-disk-backup.sh /Volumes/Backup --verify   # also re-read every photo on the S3 side (slow)
#   scripts/euronodes-disk-backup.sh /Volumes/Backup --status   # age of the last run, nothing else
#   DRY_RUN=1 scripts/euronodes-disk-backup.sh /Volumes/Backup  # show what would change
#
# Needs: rclone restic sops jq (pg_restore optional: checks the Pocket ID dump), the euronodes age key and the Storage Box SSH key (see below).
# No secret is written to disk: they are decrypted from the repo's SOPS files into this process.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:?usage: $0 /Volumes/<disk> [--verify|--status]}
MODE=${2:-}

export SOPS_AGE_KEY_FILE=${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/euronodes.agekey}
SSH_KEY=${SSH_KEY:-$HOME/.ssh/euronodes-backup}
KEEP_RUNS=${KEEP_RUNS:-8}            # runs kept: archive of removed photos and snapshots in the disk's restic repos
MAX_DUMP_AGE_DAYS=${MAX_DUMP_AGE_DAYS:-3}   # fail if the newest database dump on the Storage Box is older than this
MAX_DELETE=${MAX_DELETE:-500}        # stop if one run would remove more photos than this (raise it on purpose)
PHOTO_DIRS="library upload"          # Immich folders that live on the S3 mount
IMMICH_SECRET=$REPO_DIR/apps/euronodes/immich/restic-secret.sops.yaml
POCKET_SECRET=$REPO_DIR/apps/euronodes/pocket-id/restic-secret.sops.yaml
RCLONE_SECRET=$REPO_DIR/apps/euronodes/immich/rclone-secret.sops.yaml

STAMP=$(date +%F)
PHOTOS=$DEST/immich
STAGE_ROOT=/tmp/euronodes-disk-backup   # fixed path so restic sees the same paths on every run
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
mkdir -p "$PHOTOS" "$STATE"

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

# ---- 2. Databases and small files: latest Storage Box snapshot -> small encrypted restic repos on the disk ----
# Only the listed paths are restored from the Storage Box (the Immich snapshot also holds the originals,
# which are never downloaded here), checked, and stored as a new snapshot in restic-<app> on the disk.
KNOWN=$(mktemp)
trap 'rm -rf "$KNOWN" "$STAGE_ROOT"' EXIT
secret "$IMMICH_SECRET" known_hosts > "$KNOWN"

sftp_opts() {   # <repository url>
  local host=${1#sftp:}; host=${host%%:*}
  echo "sftp.command=ssh -p 23 -i $SSH_KEY -o UserKnownHostsFile=$KNOWN -o StrictHostKeyChecking=yes $host -s sftp"
}

check_age() {   # <file>
  local age=$(( ( $(date +%s) - $(stat -f %m "$1") ) / 86400 ))
  [ "$age" -le "$MAX_DUMP_AGE_DAYS" ] || die "$(basename "$1") is $age days old (limit $MAX_DUMP_AGE_DAYS): the app or its backup job has been broken for a while. Nothing was added to the disk"
}

check_immich() {   # <stage dir>
  local dumps newest f
  dumps=$(ls -1t "$1"/immich/backups/immich-db-backup-*.sql.gz 2>/dev/null || true)
  [ -n "$dumps" ] || die "no Immich database dump in the latest snapshot. Nothing was added to the disk"
  newest=$(echo "$dumps" | head -n 1)
  check_age "$newest"
  for f in $dumps; do gzip -t "$f" || die "$f is corrupt. Nothing was added to the disk"; done
  echo "Immich dumps ok: $(echo "$dumps" | wc -l | tr -d ' ') files, newest $(basename "$newest")"
}

check_pocket() {   # <stage dir>
  local f=$1/dump/pocket-id.dump
  [ -s "$f" ] || die "no Pocket ID database dump in the latest snapshot. Nothing was added to the disk"
  check_age "$f"
  if command -v pg_restore >/dev/null; then
    pg_restore --list "$f" >/dev/null || die "Pocket ID dump is unreadable. Nothing was added to the disk"
  else
    echo "NOTICE: pg_restore not installed (brew install libpq): Pocket ID dump content not checked"
  fi
  echo "Pocket ID dump ok"
}

backup_app() {   # <app> <secret file> <check function> [paths to restore, as --include args]
  local app=$1 file=$2 check=$3; shift 3
  local stage=$STAGE_ROOT/$app disk_repo=$DEST/restic-$app box_repo opt
  export RESTIC_PASSWORD; RESTIC_PASSWORD=$(secret "$file" RESTIC_PASSWORD)   # same password on the disk repo
  box_repo=$(secret "$file" RESTIC_REPOSITORY); opt=$(sftp_opts "$box_repo")

  step "$app: latest snapshot on the Storage Box"
  RESTIC_REPOSITORY=$box_repo restic -o "$opt" snapshots --host "$app" --tag "$app" --latest 1 --compact
  [ -n "${DRY_RUN:-}" ] && return 0

  rm -rf "$stage"; mkdir -p -m 700 "$stage"
  RESTIC_REPOSITORY=$box_repo restic -o "$opt" restore latest --host "$app" --tag "$app" \
    --target "$stage" --exclude-xattr security.selinux "$@"
  "$check" "$stage"

  step "$app: adding it to $disk_repo"
  export RESTIC_REPOSITORY=$disk_repo
  restic cat config >/dev/null 2>&1 || restic init
  (cd "$stage" && restic backup --host "$app" --tag "$app-disk" .)
  restic forget --host "$app" --tag "$app-disk" --keep-last "$KEEP_RUNS" --prune
  restic check --read-data
  unset RESTIC_REPOSITORY
  rm -rf "$stage"
}

backup_app immich "$IMMICH_SECRET" check_immich --include /immich/backups --include /immich/profile
backup_app pocket-id "$POCKET_SECRET" check_pocket

# ---- 3. Bookkeeping ----------------------------------------------------------------------------------------
if [ -z "${DRY_RUN:-}" ]; then
  step "Pruning old archive folders (keeping the last $KEEP_RUNS runs) and writing the manifest"
  if [ -d "$DEST/immich-archive" ]; then
    ls -1d "$DEST"/immich-archive/????-??-?? 2>/dev/null | sort -r | tail -n +"$((KEEP_RUNS + 1))" | while IFS= read -r old; do rm -rf "$old"; done
  fi
  rclone hashsum sha256 "$PHOTOS" > "$STATE/photos.sha256.new"
  mv "$STATE/photos.sha256.new" "$STATE/photos.sha256"
  echo "$STAMP" > "$STATE/last-run"
  [ -n "${HC_PING_URL:-}" ] && curl -fsS -m 10 --retry 3 "$HC_PING_URL" >/dev/null || true
fi

step "Done: $(du -sh "$DEST" 2>/dev/null | cut -f1) on $DEST"
