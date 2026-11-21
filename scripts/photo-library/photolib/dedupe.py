"""Decide which duplicate photos may be trashed (uploaded copies of external-library photos) and do it safely."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

EXTERNAL_PREFIX = "/mnt/rclone/external/"
UPLOAD_PREFIXES = ("/data/library/", "/data/upload/")
ASPECT_TOLERANCE = 0.01


@dataclass
class Verdict:
    group_id: str
    tier: str            # "A": the uploaded copies may be trashed automatically; "B": leave for a human
    reason: str          # "ok" or why it is B
    external: Optional[dict] = None
    trash: list = field(default_factory=list)
    assets: list = field(default_factory=list)
    pairs: list = field(default_factory=list)   # (uploaded copy, the external photo it is a copy of)


def _usable(a: dict) -> bool:
    return not a.get("isTrashed") and not a.get("isOffline")


def _ratio(a: dict) -> float:
    w, h = a["width"], a["height"]
    return max(w, h) / min(w, h)


def _stem(a: dict) -> str:
    return a["originalPath"].rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()


def _same_size(a: dict, b: dict) -> bool:
    return sorted((a["width"], a["height"])) == sorted((b["width"], b["height"]))


def _match_by_name(ups: list, ext: list) -> list:
    """A burst (several external photos) with uploads: an upload is paired with an external photo only if exactly one
    external photo has the same file name (any case or extension), the same owner and the same pixel size."""
    pairs = []
    for u in ups:
        same = [e for e in ext if _stem(e) == _stem(u) and e.get("ownerId") == u.get("ownerId") and _same_size(e, u)]
        if len(same) == 1:
            pairs.append((u, same[0]))
    return pairs


def classify(group: dict, match_names: bool = False) -> Verdict:
    """One duplicate group -> verdict. The external-library photo always wins, but never at the price of
    quality: an uploaded copy is only trashed if it is not larger than the external one.
    match_names: also pair the uploads of a group with several external photos by file name and size."""
    assets = group["assets"]
    v = Verdict(group["duplicateId"], "B", "", assets=assets)
    ext = [a for a in assets if a["originalPath"].startswith(EXTERNAL_PREFIX)]
    ups = [a for a in assets if a["originalPath"].startswith(UPLOAD_PREFIXES)]
    if len(ext) + len(ups) != len(assets):
        v.reason = "unknown-location"
    elif not ext:
        v.reason = "no-external"
    elif len(ext) > 1:
        v.reason = "multi-external"
        if match_names and ups and all(_usable(a) and a.get("width") and a.get("height") for a in assets):
            pairs = _match_by_name(ups, ext)
            if pairs:
                v.tier, v.reason, v.pairs = "A", "name-match", pairs
                v.trash, v.external = [u for u, _ in pairs], pairs[0][1]
    elif not ups:
        v.reason = "no-upload"
    else:
        e = ext[0]
        v.external = e
        if not all(_usable(a) for a in assets):
            v.reason = "not-usable"
        elif any(u.get("ownerId") != e.get("ownerId") for u in ups):
            v.reason = "owner-differs"
        elif any(not (a.get("width") and a.get("height")) for a in assets):
            v.reason = "no-dimensions"
        elif any(abs(_ratio(u) - _ratio(e)) / _ratio(e) > ASPECT_TOLERANCE for u in ups):
            v.reason = "aspect-differs"
        elif any(u["width"] * u["height"] > e["width"] * e["height"] for u in ups):
            v.reason = "uploaded-larger"
        else:
            v.tier, v.reason, v.trash = "A", "ok", ups
            v.pairs = [(u, e) for u in ups]
    return v


def apply_verdicts(client, verdicts: list, log_path: Path, limit: Optional[int] = None) -> int:
    """For each tier A group: add the external photo to the albums of the uploaded copies, keep their favorite
    mark, then move the uploaded copies to Immich's trash (30 days). The log line is written first (for undo)."""
    todo = [v for v in verdicts if v.tier == "A"]
    if limit is not None:
        todo = todo[:limit]
    trashed = 0
    for v in todo:
        by_external: dict = {}
        for u, e in v.pairs:
            by_external.setdefault(e["id"], (e, []))[1].append(u)
        album_ids_all = []
        for e, uploads in by_external.values():
            album_ids = []
            for u in uploads:
                for aid in client.albums_of(u["id"]):
                    if aid not in album_ids:
                        album_ids.append(aid)
            if album_ids:
                client.add_to_albums(album_ids, [e["id"]])
            if any(u.get("isFavorite") for u in uploads) and not e.get("isFavorite"):
                client.set_favorite([e["id"]])
            album_ids_all.extend(a for a in album_ids if a not in album_ids_all)
        trash_ids = [u["id"] for u in v.trash]
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"group": v.group_id, "external": v.external["id"], "trashed": trash_ids,
                                 "albums": album_ids_all,
                                 "pairs": [[u["id"], e["id"]] for u, e in v.pairs]}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        client.trash(trash_ids)
        trashed += len(trash_ids)
    return trashed


def undo_trash(client, log_path: Path) -> int:
    """Put every asset in the log back from the trash (the albums and favorite already added stay)."""
    ids = []
    for line in Path(log_path).read_text(encoding="utf-8").splitlines():
        ids.extend(json.loads(line)["trashed"])
    if ids:
        client.restore(ids)
    return len(ids)


REPORT_COLUMNS = ["group", "tier", "reason", "external_id", "external_path", "external_wxh",
                  "trash_ids", "trash_paths", "trash_wxh", "count", "photos", "pairs"]


def summarize(verdicts: list) -> dict:
    s: dict = {"A": 0, "B": 0, "trash": 0}
    for v in verdicts:
        s[v.tier] += 1
        s["trash"] += len(v.trash)
        if v.tier == "B":
            s[f"B:{v.reason}"] = s.get(f"B:{v.reason}", 0) + 1
    return s


def write_report(verdicts: list, path: Path) -> None:
    import csv
    wxh = lambda a: f"{a.get('width')}x{a.get('height')}"  # noqa: E731
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(REPORT_COLUMNS)
        for v in verdicts:
            e = v.external
            w.writerow([v.group_id, v.tier, v.reason,
                        e["id"] if e else "", e["originalPath"] if e else "", wxh(e) if e else "",
                        " ".join(a["id"] for a in v.trash), " | ".join(a["originalPath"] for a in v.trash),
                        " ".join(wxh(a) for a in v.trash),
                        len(v.assets), " | ".join(f"{a['originalPath']} {wxh(a)}" for a in v.assets),
                        " | ".join(f"{u['originalPath']} -> {e['originalPath']}" for u, e in v.pairs)])
