#!/usr/bin/env python3
"""Export rooted ARM64 Android /data to a local allocated-space treemap (Python 3.8+)."""

import argparse
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import platform
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
import uuid


HEADER = struct.Struct("<IIQQQQQ")
TOTALS = ("value", "logical", "files", "folders")


def read_scan(stream, threshold):
    """Validate a complete ASTREE1 stream and aggregate it without losing totals."""
    if stream.read(8) != b"ASTREE1\n":
        raise ValueError("Invalid scanner stream (root denial, or non-binary-safe su output)")
    directories, directory_inodes, hardlinks = {}, set(), set()
    root = None
    counts = dict(records=0, directories=0, files=0, symlinks=0, hardlinks_deduplicated=0)
    error_count, first_error = 0, None
    while True:
        header = stream.read(HEADER.size)
        if len(header) != HEADER.size:
            raise ValueError("Truncated scanner stream: missing completion footer")
        length, mode, blocks, size, device, inode, links = HEADER.unpack(header)
        if not length:
            if (mode or device or inode or links or blocks != error_count
                    or size != counts["records"] or stream.read(1)):
                raise ValueError("Invalid scanner completion footer")
            break
        if length > 1024 * 1024:
            raise ValueError("Invalid scanner path length")
        raw = stream.read(length)
        if len(raw) != length:
            raise ValueError("Truncated scanner path")
        path = raw.decode("utf-8", "surrogateescape")
        if not mode:
            error_count += 1
            if first_error is None:
                first_error = (path, blocks)
            continue
        counts["records"] += 1
        is_directory = stat.S_ISDIR(mode)
        key = (device, inode)
        allocated = blocks * 512
        logical = size if stat.S_ISREG(mode) else 0
        if is_directory:
            if key in directory_inodes:
                raise ValueError("Duplicate directory inode (unexcluded bind mount): " + repr(path))
            directory_inodes.add(key)
            counts["directories"] += 1
        else:
            counts["files"] += 1
            counts["symlinks"] += int(stat.S_ISLNK(mode))
            if links > 1:
                if key in hardlinks:
                    allocated = logical = 0
                    counts["hardlinks_deduplicated"] += 1
                hardlinks.add(key)
        node = dict(name=path.rsplit("/", 1)[-1], path=path, value=allocated,
                    logical=logical, files=int(not is_directory), folders=0,
                    kind="directory" if is_directory else "file", children=[])
        if root is None:
            if not is_directory:
                raise ValueError("Scanner root must be a directory")
            root = node
        else:
            parent = directories.get(path.rsplit("/", 1)[0])
            if parent is None:
                raise ValueError("Missing scanner parent: " + repr(path))
            if is_directory or allocated >= threshold:
                parent["children"].append(node)
            else:
                for field in TOTALS:
                    parent[field] += node[field]
        if is_directory:
            directories[path] = node
    if error_count:
        raise ValueError("Scan incomplete: %d errors; first errno %d at %r"
                         % (error_count, first_error[1], first_error[0]))
    if root is None:
        raise ValueError("Empty scanner stream")
    # Iterative postorder: actual Android directories can exceed 300 levels.
    for node in reversed(list(directories.values())):
        small = {field: node[field] for field in TOTALS}
        children = []
        for child in node["children"]:
            for field in TOTALS:
                node[field] += child[field]
            node["folders"] += int(child["kind"] == "directory")
            if child["value"] >= threshold:
                children.append(child)
            else:
                for field in TOTALS:
                    small[field] += child[field]
                small["folders"] += int(child["kind"] == "directory")
        if any(small.values()):
            children.append(dict(name="小项目及目录元数据（汇总）", kind="aggregate",
                                 children=[], **small))
        node["children"] = children
    # Match the viewer's limit, including the final aggregate leaf.
    pending = [(root, 0)]
    counts["display_nodes"] = 0
    while pending:
        node, depth = pending.pop()
        if depth > 256:
            raise ValueError("Retained tree exceeds viewer depth 256; increase --min-mb")
        counts["display_nodes"] += 1
        pending.extend((child, depth + 1) for child in node["children"])
    return root, counts


def mount_exclusions(text, root="/data"):
    """Exclude all descendant mountpoints, including same-device bind aliases."""
    paths = set()
    for line in text.splitlines():
        fields = line.split()
        path = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), fields[4])
        if path.startswith(root + "/"):
            paths.add(path)
    return sorted(paths)


def find_compiler(ndk=None):
    roots = [Path(ndk)] if ndk else [Path(os.environ[key]) for key in
            ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT") if os.environ.get(key)]
    if not ndk:
        sdks = [Path(os.environ[key]) for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT")
                if os.environ.get(key)]
        if os.environ.get("LOCALAPPDATA"):
            sdks.append(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk")
        sdks += [Path.home() / "Android/Sdk", Path.home() / "Library/Android/sdk"]
        for sdk in sdks:
            roots.extend(sorted((sdk / "ndk").glob("*"), reverse=True,
                                key=lambda p: tuple(map(int, re.findall(r"\d+", p.name)))))
    host = {"Windows": "windows", "Linux": "linux", "Darwin": "darwin"}[platform.system()]
    name = "clang.exe" if os.name == "nt" else "clang"
    for root in roots:
        for compiler in root.glob("toolchains/llvm/prebuilt/" + host + "-*/bin/" + name):
            if compiler.is_file():
                return compiler
    raise ValueError("Android NDK clang not found; use --ndk PATH or ANDROID_NDK_HOME")


def downloads_folder():
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            location = winreg.QueryValueEx(key, "{374DE290-123F-4565-9164-39C4925E467B}")[0]
        return Path(os.path.expandvars(location))
    return Path.home() / "Downloads"


def export(args):
    if args.output and args.output.exists():
        raise FileExistsError("Refusing to overwrite: " + str(args.output))
    devices = subprocess.run(["adb", "devices"], check=True, capture_output=True, text=True).stdout
    available = [parts[0] for line in devices.splitlines()
                 if len(parts := line.split()) == 2 and parts[1] == "device"]
    if args.serial:
        if args.serial not in available:
            raise ValueError("Selected device is not connected/authorized: " + args.serial)
        serial = args.serial
    else:
        if len(available) != 1:
            raise ValueError("Connect/authorize one Android device, or select --serial SERIAL: "
                             + ", ".join(available))
        serial = available[0]
    adb = ["adb", "-s", serial]
    help_result = subprocess.run(adb + ["shell", "-T", "su --help"], capture_output=True)
    root_prefix = "su -W -c " if b"--no-wrapper" in help_result.stdout + help_result.stderr else "su -c "

    def shell(command, root=False):
        if root:
            command = root_prefix + shlex.quote(command)
        return subprocess.run(adb + ["shell", "-T", command], check=True,
                              capture_output=True).stdout.decode("utf-8").strip()

    if shell("id -u", root=True) != "0":
        raise ValueError("Root access required; grant adb shell access in your su manager")
    if (shell("getprop ro.product.cpu.abi") != "arm64-v8a"
            or int(shell("getprop ro.build.version.sdk")) < 26):
        raise ValueError("This scanner requires ARM64 Android 8.0/API 26 or newer")
    model = shell("getprop ro.product.model")
    started = datetime.now(timezone.utc).isoformat()
    output = args.output
    if output is None:
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", model)
        filename = "android-%s-%s-allocated-%dMB.json.gz" % (
            label, datetime.now().strftime("%Y%m%d-%H%M%S"), args.min_mb)
        output = downloads_folder() / filename
    if output.exists():
        raise FileExistsError("Refusing to overwrite: " + str(output))
    if not output.parent.is_dir():
        raise ValueError("Output parent directory must already exist: " + str(output.parent))
    if not str(output).endswith(".json.gz"):
        raise ValueError("Output must end in .json.gz")
    compiler = find_compiler(args.ndk)
    source = Path(__file__).with_name("android_scan.c")
    remote = "/data/local/tmp/space-tree-" + uuid.uuid4().hex

    def filesystem():
        blocks, free, available, block_size = map(int, shell(
            "stat -f -c %b,%f,%a,%S /data", root=True).split(","))
        return dict(capacity=blocks * block_size, used=(blocks - free) * block_size,
                    available=available * block_size, reserved=(free - available) * block_size)

    excluded = mount_exclusions(shell("cat /proc/self/mountinfo", root=True))
    before = filesystem()
    print("Scanning %s /data, allocated space, detail >= %d MB ..." % (model, args.min_mb), flush=True)
    with tempfile.TemporaryDirectory(prefix="space-tree-android-") as temporary:
        binary = Path(temporary) / "scanner"
        subprocess.run([str(compiler), "--target=aarch64-linux-android26", "-O2", "-Wall",
                        "-Wextra", "-Werror", "-std=c11", str(source), "-o", str(binary)], check=True)
        try:
            subprocess.run(adb + ["push", str(binary), remote], check=True,
                           stdout=subprocess.DEVNULL)
            shell("chmod 700 " + shlex.quote(remote), root=True)
            command = "exec nice -n 19 " + shlex.join([remote, "/data", remote] + excluded)
            # Parse a binary pipe directly: no huge raw file is needed on the PC.
            # su/adb can hide a remote nonzero exit, so read_scan also requires
            # the terminal counts and no error records.
            with subprocess.Popen(adb + ["exec-out", root_prefix + shlex.quote(command)],
                                  stdout=subprocess.PIPE) as process:
                tree, counts = read_scan(process.stdout, args.min_mb * 1_000_000)
                status = process.wait()
                if status:
                    raise subprocess.CalledProcessError(status, process.args)
            after = filesystem()
            if mount_exclusions(shell("cat /proc/self/mountinfo", root=True)) != excluded:
                raise ValueError("/data mounts changed during scanning; run again")
        finally:
            cleanup = subprocess.run(adb + ["shell", "-T", root_prefix + shlex.quote("rm -f " + remote)],
                                     capture_output=True)
            if cleanup.returncode:
                print("Warning: temporary scanner cleanup failed: " + remote, file=sys.stderr)
    ended = datetime.now(timezone.utc).isoformat()
    enumerated = tree["value"]
    difference = after["used"] - enumerated
    if difference > 0:
        tree["children"].append(dict(name="文件系统及未归档占用", kind="system",
                                     value=difference, logical=0, files=0, folders=0, children=[]))
        tree["value"] += difference
        counts["display_nodes"] += 1
    tree.update(name=model + " /data · 分配空间 · %d MB明细" % args.min_mb, kind="volume",
                capacity=after["capacity"], free=after["available"])
    document = dict(name=tree["name"], unit="bytes", datasets=[tree], source=dict(
        application="Android root metadata scan", metric="allocated", model=model, root="/data",
        threshold_bytes=args.min_mb * 1_000_000, started=started, ended=ended, live_scan=True,
        errors=0, excluded_mounts=excluded, filesystem_before=before, filesystem_after=after,
        enumerated_allocated_bytes=enumerated, filesystem_difference_bytes=difference, **counts))
    payload = gzip.compress(json.dumps(document, ensure_ascii=True, separators=(",", ":"))
                            .encode("ascii"), mtime=0)
    created = False
    try:
        with output.open("xb") as destination:
            created = True
            destination.write(payload)
    except BaseException:
        if created:
            output.unlink()
        raise
    print(json.dumps(dict(output=str(output), bytes=len(payload), **document["source"]),
                     ensure_ascii=True, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-mb", type=int, default=50, help="Minimum retained item in decimal MB (default: 50)")
    parser.add_argument("--serial", help="ADB serial; required if multiple devices are connected")
    parser.add_argument("--ndk", type=Path, help="NDK root (otherwise discovered from environment/Android SDK)")
    parser.add_argument("--output", type=Path, help="New .json.gz file (default: Windows Downloads folder)")
    args = parser.parse_args()
    if args.min_mb <= 0:
        parser.error("--min-mb must be positive")
    try:
        export(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, "Export failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
