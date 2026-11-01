#!/usr/bin/env python3
"""Read a completed Synology Storage Analyzer v2 report without modifying it.

Requires Python 3.8+ and the installed Storage Analyzer package on the NAS.
The Windows wrapper sends this file over SSH; no NAS-side installation is needed.
"""

import argparse
import base64
import configparser
import ctypes as C
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sys


LIBRARY = "/var/packages/StorageAnalyzer/target/lib/libsynoreport.so"
CONFIG = Path("/usr/syno/etc/synoreport.conf")
REPORT_DATE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\Z")
REQUIRED = ("INFO", "report.html", "folder.db")


def completed_report(path):
    return (all((path / name).is_file() for name in REQUIRED)
            and re.search(r'^consumed_time=\d+\s*$',
                          (path / "INFO").read_text(encoding="utf-8"), re.M) is not None)


def select_report(report=None, profile=None):
    """Select an explicit snapshot or the latest completed snapshot in a profile."""
    if report is None:
        config = configparser.ConfigParser(interpolation=None)
        with CONFIG.open(encoding="utf-8-sig") as source:
            config.read_file(source)
        profiles = [name for name in config.sections() if name != "global"]
        if profile is None:
            if len(profiles) != 1:
                raise ValueError("Choose --profile from: " + ", ".join(profiles))
            profile = profiles[0]
        if profile not in profiles or Path(profile).name != profile:
            raise ValueError("Unknown profile: " + profile)
        location = Path(config.get("global", "report_location"))
        if location.is_absolute():
            locations = [location]
        else:
            locations = [Path("/var/services") / location]
            locations += [volume / location for volume in Path("/").glob("volume[0-9]*")]
        matches = {path.resolve() for location in locations
                   for path in [location / "synoreport" / profile] if path.is_dir()}
        if len(matches) != 1:
            raise ValueError("Report location is missing or ambiguous; use --report PATH")
        report = matches.pop()
    report = Path(report).resolve()
    if completed_report(report):
        return report
    candidates = sorted((path for path in report.iterdir()
                         if REPORT_DATE.fullmatch(path.name) and path.is_dir()
                         and completed_report(path)), reverse=True)
    if not candidates:
        raise ValueError("No completed report (INFO consumed_time, report.html, folder.db): " + str(report))
    return candidates[0]


def read_folders(report):
    """Decode Synology's BDB through its own library, with a read-only cursor."""
    info = dict(re.findall(r'^\s*(\w+)="([^"]*)"',
                          (report / "INFO").read_text(encoding="utf-8"), re.M))
    if info.get("version") != "2":
        raise ValueError("Unsupported Storage Analyzer report version: " + str(info.get("version")))
    source = report / "folder.db"
    before = source.stat()
    lib = C.CDLL(LIBRARY)

    def bind(name, result, *args):
        function = getattr(lib, name)
        function.restype, function.argtypes = result, list(args)
        return function

    pointer = C.POINTER(C.c_void_p)
    size = C.POINTER(C.c_size_t)
    open_db = bind("SLIBCBdbOpen", C.c_void_p, C.c_char_p, C.c_int, C.c_int)
    close_db = bind("SLIBCBdbClose", C.c_int, C.c_void_p)
    set_cursor = bind("SLIBCBdbCursorSetType", C.c_int, C.c_void_p, C.c_int)
    get_cursor = bind("SLIBCBdbCursorGet", C.c_int, C.c_void_p, C.c_int, pointer, size, pointer, size)
    free = bind("SLIBCBdbFree", None, C.c_void_p)
    error = bind("SLIBCErrGet", C.c_int)
    db = open_db(os.fsencode(source), os.O_RDONLY, 0)
    if not db:
        raise OSError("Cannot open folder.db read-only (Synology error 0x%x)" % error())
    key, value = C.c_void_p(), C.c_void_p()
    key_size, value_size = C.c_size_t(), C.c_size_t()
    try:
        # Synology SDK synocore/bdb.h: FIRST=2, NEXT=4; error.h: FINISH=0xB500.
        if set_cursor(db, 2) != 0:
            raise OSError("Cannot initialize folder.db cursor")
        while True:
            status = get_cursor(db, 1, C.byref(key), C.byref(key_size),
                                C.byref(value), C.byref(value_size))
            if status != 0:
                if error() == 0xB500:
                    break
                raise OSError("Cannot read folder.db (Synology error 0x%x)" % error())
            yield int(C.string_at(key)), json.loads(C.string_at(value).decode("utf-8"))
            if set_cursor(db, 4) != 0:
                raise OSError("Cannot advance folder.db cursor")
    finally:
        free(key)
        free(value)
        close_db(db)
    after = source.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("folder.db changed during export; wait for the report to finish")


def integer(value):
    if type(value) not in (int, float) or not 0 <= value <= 2**53 - 1 or int(value) != value:
        raise ValueError("Expected a nonnegative, browser-safe integer: " + repr(value))
    return int(value)


def build_document(records, host, profile, report):
    """Convert decoded (ID, record) pairs to the viewer's logical-size tree format."""
    nodes, links, paths = {}, {}, set()
    for identifier, record in records:
        identifier = integer(identifier)
        if identifier in nodes or not isinstance(record, dict) or len(record) != 1:
            raise ValueError("Duplicate ID or invalid folder record: " + str(identifier))
        name, fields = next(iter(record.items()))
        if not isinstance(name, str) or not isinstance(fields, list) or len(fields) != 8:
            raise ValueError("Unsupported folder record: " + str(identifier))
        path = fields[0]
        if (not isinstance(path, str) or not path.startswith("/")
                or len(PurePosixPath(path).parts) < 3 or path in paths):
            raise ValueError("Duplicate or invalid directory path: " + repr(path))
        if not isinstance(fields[6], list):
            raise ValueError("Invalid child directory list: " + path)
        paths.add(path)
        nodes[identifier] = dict(name=name, path=path, value=integer(fields[1]),
                                 logical=integer(fields[1]), files=integer(fields[3]),
                                 folders=0, kind="directory", children=[])
        links[identifier] = integer(fields[5]), [integer(child) for child in fields[6]]
    roots = [identifier for identifier, (parent, _) in links.items() if parent == identifier]
    if not roots:
        raise ValueError("No root directories in report")
    visited = set()
    stack = [(identifier, 1) for identifier in roots]
    order = []
    while stack:
        identifier, depth = stack.pop()
        if identifier in visited or depth > 255:
            raise ValueError("Repeated/cyclic directory or tree too deep for the viewer")
        visited.add(identifier)
        order.append(identifier)
        node = nodes[identifier]
        for child in links[identifier][1]:
            if (child not in nodes or links[child][0] != identifier
                    or str(PurePosixPath(nodes[child]["path"]).parent) != node["path"]):
                raise ValueError("Missing child or inconsistent parent: " + node["path"])
            node["children"].append(nodes[child])
            stack.append((child, depth + 1))
    if len(visited) != len(nodes):
        raise ValueError("Disconnected/cyclic directories in report")
    aggregates = 0
    for identifier in reversed(order):
        node = nodes[identifier]
        children = node["children"]
        remaining = node["value"] - sum(child["value"] for child in children)
        files = node["files"] - sum(child["files"] for child in children)
        if remaining < 0 or files < 0:
            raise ValueError("Child totals exceed parent: " + node["path"])
        node["folders"] = sum(1 + child["folders"] for child in children)
        if remaining or files:
            children.append(dict(name="本目录文件（汇总）", path=node["path"] + "/[本目录文件汇总]",
                                 value=remaining, logical=remaining, files=files, folders=0,
                                 kind="aggregate", children=[]))
            aggregates += 1
    volumes = {}
    for identifier in roots:
        node = nodes[identifier]
        volume = PurePosixPath(node["path"]).parts[1]
        volumes.setdefault(volume, []).append(node)
    datasets = []
    for volume, children in sorted(volumes.items()):
        total = integer(sum(node["value"] for node in children))
        datasets.append(dict(name=host + " /" + volume + " · 逻辑大小", path="/" + volume,
                             value=total, logical=total, files=sum(node["files"] for node in children),
                             folders=sum(1 + node["folders"] for node in children),
                             kind="volume", children=children))
    summary = dict(logical_bytes=integer(sum(node["value"] for node in datasets)),
                   files=sum(node["files"] for node in datasets), directories=len(nodes),
                   aggregates=aggregates)
    return dict(name=host + " · " + profile + " · " + report + " · 逻辑大小", unit="bytes",
                source=dict(application="Synology Storage Analyzer", host=host, profile=profile,
                            report=report, metric="logical", detail="directory", **summary),
                datasets=datasets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--report", help="Completed report directory, or profile history directory")
    selection.add_argument("--profile", help="Profile name from synoreport.conf (required if multiple)")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--output", type=Path, help="New .json.gz file; never overwrites existing files")
    output.add_argument("--base64", action="store_true", help="JSON envelope for SSH transport to Windows")
    args = parser.parse_args()
    try:
        report = select_report(args.report, args.profile)
        document = build_document(read_folders(report), socket.gethostname(), report.parent.name, report.name)
        payload = gzip.compress(json.dumps(document, ensure_ascii=False, separators=(",", ":"))
                                .encode("utf-8"), mtime=0)
        if args.base64:
            print(json.dumps(dict(source=document["source"], sha256=hashlib.sha256(payload).hexdigest(),
                                  data=base64.b64encode(payload).decode("ascii"))))
        else:
            with args.output.open("xb") as destination:
                destination.write(payload)
            print(str(args.output), file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, "Export failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
