#!/usr/bin/env python3
"""Document exactly which game asset paths are in the APK and which are CDN-only.

The bundle sweep recovers art and audio from the 7,868 UnityFS bundles packed
inside `assets/AssetBundles/BundleFragment0.bytes`, but the `gameres` manifest
describes the game's *whole* asset universe - 95,602 paths across 32,564
bundles, most of which the studio serves from its CDN on first run. "The assets
are recovered" is therefore a claim that needs a boundary attached to it, and
the boundary has three shades rather than two:

  recovered          the object is in an APK bundle and produced a file
  content CDN-only   the object is in an APK bundle but its payload (usually a
                     Texture2D's pixel stream) is empty there - the bytes come
                     from the CDN
  not exported       the path belongs to an APK bundle but no object for it was
                     exported (the sweep only writes Sprite, Texture2D,
                     AudioClip and TextAsset)
  CDN-only           every bundle carrying the path is missing from the APK
                     entirely - nothing about it is recoverable locally
  decode failed      the object is in an APK bundle but could not be decoded

Two inputs, both produced by the pipeline:

  source-app/game-assets/inventory.jsonl     one row per object the sweep saw
  decompiled/apktool/assets/AssetBundles/gameres   the game's path manifest

The manifest parser is imported from `extract-game-assets.py` rather than
reimplemented, so the format knowledge stays in exactly one place. The output
is deterministic - sorted lists, stable JSON - so the reports can be diffed
against a fresh generation and guarded in CI.

Writes asset_completeness_report.md and asset_completeness_report.json at the
repository root.

Usage:
  python3 tools/asset-completeness-report.py            # write both reports
  python3 tools/asset-completeness-report.py --check    # exit 1 if they are stale

Exits 0 on success, 1 when --check finds stale or missing reports, 2 when an
input is missing or inconsistent (fail loudly rather than emit a report built
on a silent mismatch).
"""

import collections
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY = os.path.join(ROOT, "source-app", "game-assets", "inventory.jsonl")
MANIFEST = os.path.join(ROOT, "decompiled", "apktool", "assets", "AssetBundles",
                        "gameres")
REPORT_MD = os.path.join(ROOT, "asset_completeness_report.md")
REPORT_JSON = os.path.join(ROOT, "asset_completeness_report.json")

# Only these object types are exported to files (see EXPORTED_TYPES in
# extract-game-assets.py); everything else in an APK bundle is "not exported".
EXPORTED_TYPES = ("Texture2D", "Sprite", "AudioClip", "TextAsset")


def load_manifest_parser():
    """Import parse_manifest from the extraction tool - one format definition."""
    path = os.path.join(ROOT, "tools", "extract-game-assets.py")
    spec = importlib.util.spec_from_file_location("extract_game_assets", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_manifest


def norm_bundle(name):
    """Normalise a bundle name to `<stem>.bundle`.

    Manifest names carry a content hash (`<stem>_<32 hex>.bundle`) while the
    name embedded in each bundle - which is what inventory.jsonl records - does
    not (`<stem>.bundle`). Both spellings must collapse to the same key for the
    in-APK/CDN split to mean anything.
    """
    s = re.sub(r"_[0-9a-f]{32}(\.bundle)?$", "", name)
    return s if s.endswith(".bundle") else s + ".bundle"


def manifest_paths(m):
    """-> (path -> [bundle keys], bundle key -> (in_manifest_name, size, [paths]))"""
    path_bundles = collections.defaultdict(list)
    bundles = {}
    for _idx, info in m["Bundles"].items():
        key = norm_bundle(info["name"])
        paths = []
        for pi in info["paths"]:
            t = m["Paths"].get(pi)
            if not t:
                continue
            d, fn = t
            full = ("%s/%s" % (m["Directories"].get(d, ""), fn) if fn else "")
            paths.append(full.strip("/"))
        try:
            size = int(info["size"])
        except ValueError:
            size = 0
        bundles[key] = (info["name"], size, paths)
        for p in paths:
            path_bundles[p].append(key)
    return path_bundles, bundles


def collect(path):
    """Read inventory.jsonl -> (bundle names, object rows, file rows)."""
    bundles = set()
    rows = []
    files = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("note") == "resolved-name":
                bundles.add(rec["bundle"])
                continue
            rec["_line"] = lineno
            rows.append(rec)
            if rec.get("file"):
                files.append(rec)
    return bundles, rows, files


def top_dir(path, depth=3):
    parts = [p for p in path.split("/") if p][:depth]
    return "/".join(parts) if parts else "(root)"


def pct(n, total):
    return "%.1f%%" % (100.0 * n / total) if total else "-"


def build():
    for p in (INVENTORY, MANIFEST):
        if not os.path.exists(p):
            sys.stderr.write(
                "error: %s is missing - run the pipeline first "
                "(STEPS=bundles bash tools/decompile.sh)\n" % p)
            sys.exit(2)

    parse_manifest = load_manifest_parser()
    m = parse_manifest(MANIFEST)
    path_bundles, bundles = manifest_paths(m)
    inv_bundles, rows, file_rows = collect(INVENTORY)

    # The in-APK/CDN split is only sound if every shipped bundle resolves to a
    # manifest entry. A silent miss here would misfile whole bundles as
    # CDN-only, so refuse to report rather than report wrong numbers.
    unmatched = sorted(b for b in inv_bundles if b not in bundles)
    if unmatched:
        sys.stderr.write(
            "error: %d shipped bundle(s) absent from the gameres manifest, "
            "e.g. %s\n" % (len(unmatched), ", ".join(unmatched[:3])))
        sys.exit(2)

    # ---- per-path status ---------------------------------------------------
    # Inventory is the ground truth for anything the sweep touched; the
    # manifest decides the status of the paths it never saw.
    by_path = collections.defaultdict(list)
    for r in rows:
        if r.get("path"):
            by_path[r["path"]].append(r)

    status = {}            # path -> one of the five statuses
    flags = collections.defaultdict(set)   # flag -> paths

    for p, rs in by_path.items():
        has_file = any(r.get("file") for r in rs)
        notes = [r.get("note", "") for r in rs]
        if has_file:
            status[p] = "recovered"
        elif any(n.startswith("decode failed") for n in notes):
            status[p] = "in_apk_decode_failed"
        else:
            status[p] = "in_apk_content_cdn_only"
        if any(n.startswith("no pixel data") or n.startswith("no audio data")
               for n in notes):
            flags["content_stream_cdn_only"].add(p)
        if any(n.startswith("decode failed") for n in notes):
            flags["decode_failed"].add(p)
        bks = path_bundles.get(p)
        if bks is not None and not any(bk in inv_bundles for bk in bks):
            # Seen inside an APK bundle, yet the manifest assigns the path only
            # to bundles that are not in the APK.
            flags["manifest_assignment_disagrees"].add(p)
        elif bks is None:
            flags["absent_from_manifest"].add(p)

    for p in path_bundles:
        if p in status:
            continue
        in_apk = any(bk in inv_bundles for bk in path_bundles[p])
        status[p] = "in_apk_not_exported" if in_apk else "cdn_only"

    by_status = collections.defaultdict(list)
    for p, s in status.items():
        by_status[s].append(p)
    for s in by_status:
        by_status[s].sort()

    # ---- aggregate numbers -------------------------------------------------
    status_counts = {s: len(v) for s, v in by_status.items()}
    total_paths = len(status)
    in_apk_paths = sum(status_counts.get(s, 0) for s in
                       ("recovered", "in_apk_content_cdn_only",
                        "in_apk_decode_failed", "in_apk_not_exported"))

    type_counts = collections.defaultdict(
        lambda: collections.Counter())     # type -> Counter(rows/file/no-file)
    for r in rows:
        t = r.get("type", "?")
        type_counts[t]["rows"] += 1
        if r.get("file"):
            type_counts[t]["with_file"] += 1
        else:
            type_counts[t]["without_file"] += 1

    file_paths = collections.Counter(r["file"] for r in file_rows)
    dup_files = {p: n for p, n in file_paths.items() if n > 1}

    # Objects whose m_Name never resolved against the manifest have no path to
    # classify. They are real in-APK objects (some still produced files, under
    # assets/_unresolved/), so they are counted here rather than dropped.
    pathless = [r for r in rows if not r.get("path")]
    pathless_files = sorted({r["file"] for r in pathless if r.get("file")})

    path_to_files = collections.defaultdict(set)
    for r in file_rows:
        if r.get("path"):
            path_to_files[r["path"]].add(r["file"])
    multi_file_paths = {p: sorted(fs) for p, fs in path_to_files.items()
                        if len(fs) > 1}

    dirs = collections.defaultdict(lambda: collections.Counter())
    for p, s in status.items():
        d = top_dir(p)
        dirs[d]["total"] += 1
        if s == "cdn_only":
            dirs[d]["cdn_only"] += 1
        else:
            dirs[d]["in_apk"] += 1

    apk_keys = {k for k in bundles if k in inv_bundles}
    apk_bytes = sum(bundles[k][1] for k in apk_keys)
    cdn_bytes = sum(sz for k, (_n, sz, _p) in bundles.items() if k not in inv_bundles)

    decode_rows = [r for r in rows
                   if r.get("note", "").startswith("decode failed")]

    meta = {
        "generated_by": "tools/asset-completeness-report.py",
        "regenerate": "python3 tools/asset-completeness-report.py",
        "verify": "python3 tools/asset-completeness-report.py --check",
        "sources": {
            "inventory": {
                "path": "source-app/game-assets/inventory.jsonl",
                "rows_total": len(rows) + len(inv_bundles),
                "bundle_rows": len(inv_bundles),
                "object_rows": len(rows),
                "file_rows": len(file_rows),
                "distinct_files": len(file_paths),
            },
            "manifest": {
                "path": "decompiled/apktool/assets/AssetBundles/gameres",
                "version": m["Version"],
                "directories": len(m["Directories"]),
                "paths": len(m["Paths"]),
                "bundles": len(m["Bundles"]),
                "groups": len(m["Groups"]),
            },
            "bundles_in_apk": len(inv_bundles),
            "bundles_cdn_only": len(bundles) - len(inv_bundles),
        },
    }

    summary = {
        "path_universe": total_paths,
        "status_counts": dict(sorted(status_counts.items())),
        "in_apk_paths": in_apk_paths,
        "cdn_only_paths": status_counts.get("cdn_only", 0),
        "in_apk_share": pct(in_apk_paths, total_paths),
        "cdn_only_share": pct(total_paths - in_apk_paths, total_paths),
        "recovered_files": len(file_paths),
        "recovered_paths_with_duplicate_writes": len(dup_files),
        "object_rows_without_manifest_path": len(pathless),
        "rows_no_pixel_stream": sum(
            1 for r in rows if (r.get("note") or "").startswith("no pixel")),
        "unresolved_files_written": len(pathless_files),
        "paths_with_multiple_files": len(multi_file_paths),
        "paths_with_content_stream_cdn_only":
            len(flags["content_stream_cdn_only"]),
        "paths_manifest_assignment_disagrees":
            len(flags["manifest_assignment_disagrees"]),
        "bundle_bytes_in_apk": apk_bytes,
        "bundle_bytes_cdn_only": cdn_bytes,
        "by_type": {t: dict(sorted(c.items()))
                    for t, c in sorted(type_counts.items())},
        "by_top_directory": [
            {"dir": d,
             "paths": c["total"],
             "in_apk_paths": c["in_apk"],
             "cdn_only_paths": c["cdn_only"],
             "cdn_only_share": pct(c["cdn_only"], c["total"])}
            for d, c in sorted(dirs.items(),
                               key=lambda kv: (-kv[1]["cdn_only"], kv[0]))
        ],
    }

    edge_cases = {
        "objects_without_manifest_path": [
            {"name": r.get("name"), "path_id": r.get("path_id"),
             "type": r.get("type"), "bundle": r.get("bundle"),
             "note": r.get("note"), "file": r.get("file")}
            for r in sorted(pathless,
                            key=lambda r: (r.get("bundle") or "",
                                           r.get("name") or ""))
        ],
        "paths_with_multiple_files": multi_file_paths,
        "decode_failed": [
            {"path": r.get("path"), "name": r.get("name"),
             "path_id": r.get("path_id"), "bundle": r.get("bundle"),
             "note": r.get("note")}
            for r in sorted(decode_rows,
                            key=lambda r: (r.get("path") or "", r.get("off", 0)))
        ],
        "manifest_assignment_disagrees": sorted(
            flags["manifest_assignment_disagrees"]),
    }

    report = {
        "meta": meta,
        "summary": summary,
        "edge_cases": edge_cases,
        "by_status": {s: v for s, v in sorted(by_status.items())},
        "flags": {k: sorted(v) for k, v in sorted(flags.items())},
    }
    return report, status, by_status, summary, meta, edge_cases, dirs, \
        type_counts, file_paths, dup_files, decode_rows, pathless, \
        pathless_files, multi_file_paths


def fmt(n):
    return "{:,}".format(n)


def render_md(report, status, by_status, summary, meta, edge_cases, dirs,
              type_counts, file_paths, dup_files, decode_rows, pathless,
              pathless_files, multi_file_paths):
    s = summary
    src_i = meta["sources"]["inventory"]
    src_m = meta["sources"]["manifest"]
    lines = []
    a = lines.append

    a("# Asset completeness report - in-APK vs. CDN-only")
    a("")
    a("Which of the game's asset paths can be recovered from")
    a("`input/app.apk`, and which exist only on the studio's CDN. Generated by")
    a("`python3 tools/asset-completeness-report.py` from")
    a("`source-app/game-assets/inventory.jsonl` (the bundle sweep's own")
    a("record) cross-referenced with the `gameres` path manifest. **Do not edit")
    a("by hand** - `python3 tools/asset-completeness-report.py --check`")
    a("regenerates it and fails if the file on disk differs.")
    a("")
    a("## Sources")
    a("")
    a("| Input | What it records |")
    a("|---|---|")
    a("| `source-app/game-assets/inventory.jsonl` | %s rows: %s bundle"
      % (fmt(src_i["rows_total"]), fmt(src_i["bundle_rows"])))
    a("| | names (every bundle shipped in the APK) and %s object rows"
      % fmt(src_i["object_rows"]))
    a("| | (%s with a file written) |" % fmt(src_i["file_rows"]))
    a("| `decompiled/apktool/assets/AssetBundles/gameres` | the game's full")
    a("| | asset universe: %s paths, %s directories, %s bundles, %s groups"
      % (fmt(src_m["paths"]), fmt(src_m["directories"]),
         fmt(src_m["bundles"]), fmt(src_m["groups"])))
    a("")
    a("Of the %s bundles the manifest describes, **%s are packed inside the"
      % (fmt(src_m["bundles"]), fmt(meta["sources"]["bundles_in_apk"])))
    a("APK** (`BundleFragment0.bytes`) and **%s are not** - those are fetched"
      % fmt(meta["sources"]["bundles_cdn_only"]))
    a("from `lastwar-cdn-*` on first run (`ConstURLConfig.cs`).")
    a("")
    a("## Verdict")
    a("")
    a("Every one of the %s known asset paths falls into exactly one class:"
      % fmt(s["path_universe"]))
    a("")
    a("| Class | Asset paths | Share | Meaning |")
    a("|---|---:|---:|---|")
    c = s["status_counts"]
    a("| Recovered from the APK | %s | %s | object in an APK bundle, file on disk |"
      % (fmt(c.get("recovered", 0)), pct(c.get("recovered", 0), s["path_universe"])))
    a("| In-APK, content CDN-only | %s | %s | object in an APK bundle, payload (pixel/audio stream) empty there |"
      % (fmt(c.get("in_apk_content_cdn_only", 0)),
         pct(c.get("in_apk_content_cdn_only", 0), s["path_universe"])))
    a("| In-APK, not exported | %s | %s | in an APK bundle, but no exported object resolved to the path |"
      % (fmt(c.get("in_apk_not_exported", 0)),
         pct(c.get("in_apk_not_exported", 0), s["path_universe"])))
    a("| In-APK, decode failed | %s | %s | object in an APK bundle that the sweep could not decode |"
      % (fmt(c.get("in_apk_decode_failed", 0)),
         pct(c.get("in_apk_decode_failed", 0), s["path_universe"])))
    a("| **CDN-only** | **%s** | **%s** | every bundle carrying the path is absent from the APK |"
      % (fmt(c.get("cdn_only", 0)), pct(c.get("cdn_only", 0), s["path_universe"])))
    a("| **In-APK total** | **%s** | **%s** | present in an APK bundle in some form |"
      % (fmt(s["in_apk_paths"]), s["in_apk_share"]))
    a("")
    a("So of %s asset paths the game knows about, %s are reachable from the"
      % (fmt(s["path_universe"]), fmt(s["in_apk_paths"])))
    a("APK (%s) and %s (%s) are not - the recovered asset set is complete"
      % (s["in_apk_share"], fmt(s["cdn_only_paths"]), s["cdn_only_share"]))
    a("with respect to the APK and necessarily partial with respect to the")
    a("game. Byte-wise the same split: %s of bundle payload ships in the APK"
      % fmt(s["bundle_bytes_in_apk"]))
    a("vs %s served from the CDN."
      % fmt(s["bundle_bytes_cdn_only"]))
    a("")
    a("(`not exported` is mostly object types the sweep does not write - the")
    a("exported ones are %s - plus objects whose"
      % ", ".join(EXPORTED_TYPES))
    a("name never resolved to a manifest path at all.)")
    a("")
    a("The sweep wrote **%s files** (%s write rows; %s output paths were"
      % (fmt(s["recovered_files"]), fmt(sum(cc["with_file"]
                                           for cc in type_counts.values())),
         fmt(s["recovered_paths_with_duplicate_writes"])))
    a("written more than once, from bundles that duplicate them). They cover")
    a("**%s recovered paths**, plus %s files under `assets/_unresolved/`" 
      % (fmt(c.get("recovered", 0)), fmt(s["unresolved_files_written"])))
    a("whose objects carry no manifest path, plus %s path that produced more"
      % fmt(s["paths_with_multiple_files"]))
    a("than one file - which is exactly where the files-vs-paths difference")
    a("goes.")
    a("")
    a("## By object type (rows the sweep saw)")
    a("")
    a("| Type | Rows | With a file | Without a file |")
    a("|---|---:|---:|---:|")
    for t in sorted(type_counts):
        cc = type_counts[t]
        a("| %s | %s | %s | %s |"
          % (t, fmt(cc["rows"]), fmt(cc["with_file"]),
             fmt(cc["without_file"])))
    a("")
    a("The %s rows without a file are the boundary in one number: %s of"
      % (fmt(sum(cc["without_file"] for cc in type_counts.values())),
         fmt(s["rows_no_pixel_stream"])))
    a("them are `no pixel data in this bundle` - Texture2D objects that ship")
    a("in the APK with an empty pixel stream, whose full-resolution bytes come")
    a("from the CDN (they cover %s distinct manifest paths). UnityPy does not"
      % fmt(s["paths_with_content_stream_cdn_only"]))
    a("fail on those, it returns noise sized like real art, so the sweep")
    a("refuses to write them (see README, *What the sweep recovers*).")
    a("")
    a("## By directory (top 20 by CDN-only paths)")
    a("")
    a("| Directory | Paths | In-APK | CDN-only | CDN-only share |")
    a("|---|---:|---:|---:|---:|")
    for row in s["by_top_directory"][:20]:
        a("| `%s` | %s | %s | %s | %s |"
          % (row["dir"], fmt(row["paths"]), fmt(row["in_apk_paths"]),
             fmt(row["cdn_only_paths"]), row["cdn_only_share"]))
    a("")
    a("Full breakdown by directory, and the status of every individual path,")
    a("is in `asset_completeness_report.json` (`by_status` lists every path,")
    a("grouped by class).")
    a("")
    a("## Edge cases and data-quality notes")
    a("")
    a("- **%d object rows carry no manifest path** (their name never resolved"
      % len(pathless))
    a("  against `gameres`). %d of them still produced files, under"
      % len(pathless_files))
    a("  `assets/_unresolved/<bundle>/`; the rest are empty-stream textures")
    a("  recorded by name and path_id only. Full list in")
    a("  `edge_cases.objects_without_manifest_path` in the JSON.")
    a("- **%d decode failures**, all in-APK, listed in full:"
      % len(decode_rows))
    for r in decode_rows:
        a("  - `%s` (path_id %s, %s): `%s`"
          % (r.get("path"), r.get("path_id"), r.get("bundle"), r.get("note")))
    a("- **%d paths disagree between the two sources**: the sweep found the"
      % s["paths_manifest_assignment_disagrees"])
    a("  object inside an APK bundle, but the `gameres` manifest assigns the")
    a("  path to a bundle that is not in the APK. Inventory wins (the object")
    a("  is demonstrably local); the full list is")
    a("  `edge_cases.manifest_assignment_disagrees` in the JSON.")
    a("- **%d paths carry a written file *and* an empty-stream texture**"
      % len(sorted(set(by_status.get("recovered", []))
                   & set(report_flags(report, "content_stream_cdn_only")))))
    a("  (flagged `content_stream_cdn_only` in the JSON): the sprite exported")
    a("  fine, the standalone texture for the same path did not.")
    a("")
    a("## What CDN-only means here")
    a("")
    a("The client ships with %s bundles and downloads the rest on first run"
      % fmt(meta["sources"]["bundles_in_apk"]))
    a("from the hosts in `ConstURLConfig.cs`"
      " (`lastwar-cdn.akamaized.net`,")
    a("`cdn.lastwar.com`, ...). Nothing in this repository can materialise")
    a("those %s bundles - they are not in the APK, and the previous"
      % fmt(meta["sources"]["bundles_cdn_only"]))
    a("revision of the sweep proved that guessing their pixels is worse than")
    a("nothing: it wrote 5,281 noise PNGs that looked like real art.")
    a("")
    a("## Regenerating and verifying")
    a("")
    a("```bash")
    a("python3 tools/asset-completeness-report.py           # rewrite both reports")
    a("python3 tools/asset-completeness-report.py --check   # exit 1 if stale")
    a("```")
    a("")
    a("`tools/ci-checks.sh` runs the `--check` whenever the pipeline inputs are")
    a("present, so a report that no longer matches its inputs fails the build.")
    a("")
    return "\n".join(lines) + "\n"


def report_flags(report, key):
    return report["flags"].get(key, [])


def render_json(report):
    return json.dumps(report, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


def main(argv):
    check = "--check" in argv
    report, status, by_status, summary, meta, edge_cases, dirs, \
        type_counts, file_paths, dup_files, decode_rows, pathless, \
        pathless_files, multi_file_paths = build()
    md = render_md(report, status, by_status, summary, meta, edge_cases, dirs,
                   type_counts, file_paths, dup_files, decode_rows, pathless,
                   pathless_files, multi_file_paths)
    js = render_json(report)

    if check:
        stale = []
        for path, want in ((REPORT_MD, md), (REPORT_JSON, js)):
            name = os.path.basename(path)
            if not os.path.exists(path):
                stale.append("%s (missing)" % name)
            elif open(path, encoding="utf-8").read() != want:
                stale.append("%s (differs)" % name)
        if stale:
            sys.stderr.write("FAIL asset completeness report stale: %s\n"
                             % ", ".join(stale))
            sys.stderr.write("     rerun: python3 tools/asset-completeness-report.py\n")
            return 1
        print("asset completeness report is current "
              "(%s paths: %s in-APK, %s CDN-only)"
              % (fmt(summary["path_universe"]),
                 fmt(summary["in_apk_paths"]),
                 fmt(summary["cdn_only_paths"])))
        return 0

    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    with open(REPORT_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(js)
    print("wrote %s (%d lines)"
          % (os.path.relpath(REPORT_MD, ROOT), md.count("\n")))
    print("wrote %s (%d paths classified)"
          % (os.path.relpath(REPORT_JSON, ROOT), summary["path_universe"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
