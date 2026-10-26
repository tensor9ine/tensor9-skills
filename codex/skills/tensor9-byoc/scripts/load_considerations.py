#!/usr/bin/env python3
"""Query public onboarding reference data; refresh through the vendor's tensor9 CLI."""

import argparse
import base64
import binascii
import contextlib
from collections import namedtuple
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit


MANIFEST_LIMIT = 32768
CATALOG_LIMIT = 1048576
CACHE_LIMIT = 2 * CATALOG_LIMIT
TOTAL_SECONDS = 20.0
AUTOMATIC_SECONDS = 8.0
DEFAULT_MAX_CACHE_AGE_HOURS = 24.0
MAX_CACHE_AGE_HOURS_LIMIT = 8760.0
BACKOFF_SECONDS = 600.0
BACKOFF_MARKER = "refresh-backoff"
CLI_STDOUT_LIMIT = 8 * 1048576
CLI_STDERR_LIMIT = 65536
Snapshot = namedtuple("Snapshot", "manifest raw catalog serving_vendor requested_profile modified", defaults=(None, None, None))
VERSION = re.compile(r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
GUIDE = re.compile(r"/byoc/onboarding/(?:external-services|stripe|metronome)(?:#[a-z][a-z0-9-]*)?\Z")


class Rejected(Exception):
    """A bounded failure code safe to show without arbitrary response text."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "invalid-fields")


def text(value, maximum=8192):
    require(type(value) is str and 0 < len(value) <= maximum, "invalid-string")
    require(not any(ord(c) < 32 and c not in "\n\t" for c in value), "invalid-string")
    require(not any(0xD800 <= ord(c) <= 0xDFFF or ord(c) == 127 for c in value), "invalid-string")


def items(value, maximum=128, allow_empty=False):
    require(type(value) is list and (allow_empty or bool(value)) and len(value) <= maximum, "invalid-list")
    return value


def strings(value, allow_empty=False):
    for item in items(value, allow_empty=allow_empty):
        text(item)


def version(value):
    text(value, 17)
    match = VERSION.fullmatch(value)
    require(match is not None, "invalid-version")
    result = tuple(int(part) for part in match.groups())
    require(all(part <= 65535 for part in result), "invalid-version")
    return result


def https_reference(value):
    text(value, 2048)
    try:
        parsed = urlsplit(value)
        require(parsed.scheme == "https" and bool(parsed.hostname), "invalid-reference")
        require(parsed.username is None and parsed.password is None, "invalid-reference")
        require(parsed.port in (None, 443), "invalid-reference")
        require(not any(c.isspace() for c in value) and "\\" not in value, "invalid-reference")
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            require("." in hostname and len(hostname) <= 253, "nonpublic-reference")
            require(not hostname.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".invalid", ".test")), "nonpublic-reference")
            require(all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in hostname.split(".")), "invalid-reference")
            require(not hostname.split(".")[-1].isdigit(), "nonpublic-reference")
        else:
            require(address.is_global and "%" not in hostname, "nonpublic-reference")
    except ValueError:
        raise Rejected("invalid-reference") from None


def strict_json(raw, limit):
    require(type(raw) is bytes and 0 < len(raw) <= limit, "size-limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate-key")
            result[key] = value
        return result

    def nonfinite(_):
        raise Rejected("nonfinite-json")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise Rejected("invalid-json") from None


def validate_manifest(raw):
    value = strict_json(raw, MANIFEST_LIMIT)
    fields(value, "schemaVersion catalogVersion catalogPath sha256 byteCount")
    require(type(value["schemaVersion"]) is int and value["schemaVersion"] == 1, "unsupported-schema")
    version(value["catalogVersion"])
    require(value["catalogPath"] == value["catalogVersion"] + "/catalog.json", "invalid-artifact-path")
    require(type(value["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]), "invalid-digest")
    require(type(value["byteCount"]) is int and 0 < value["byteCount"] <= CATALOG_LIMIT, "size-limit")
    return value


def validate_catalog(raw, manifest):
    require(len(raw) == manifest["byteCount"], "byte-count-mismatch")
    require(hashlib.sha256(raw).hexdigest() == manifest["sha256"], "digest-mismatch")
    value = strict_json(raw, CATALOG_LIMIT)
    fields(value, "schemaVersion catalogVersion considerations")
    require(type(value["schemaVersion"]) is int and value["schemaVersion"] == 1, "unsupported-schema")
    require(value["catalogVersion"] == manifest["catalogVersion"], "version-mismatch")
    ids = set()
    for entry in items(value["considerations"], maximum=1024):
        fields(entry, "id title category summary aliases cues questions risks options references guidePath")
        text(entry["id"], 128)
        require(IDENTIFIER.fullmatch(entry["id"]) and entry["id"] not in ids, "invalid-or-duplicate-id")
        ids.add(entry["id"])
        text(entry["title"], 256)
        text(entry["category"], 128)
        text(entry["summary"])
        strings(entry["aliases"], allow_empty=True)
        strings(entry["questions"])
        for cue in items(entry["cues"]):
            fields(cue, "kind pattern explanation")
            for value_text in cue.values():
                text(value_text)
        for risk in items(entry["risks"]):
            fields(risk, "kind explanation")
            for value_text in risk.values():
                text(value_text)
        for option in items(entry["options"]):
            fields(option, "behavior accountOwnership guidance checks")
            require(option["behavior"] in ("Retain", "Replace", "Remove"), "invalid-behavior")
            require(option["accountOwnership"] in ("Vendor", "Customer", "Undecided", "NotApplicable"), "invalid-ownership")
            strings(option["guidance"])
            strings(option["checks"])
        for reference in items(entry["references"]):
            fields(reference, "url claim")
            https_reference(reference["url"])
            text(reference["claim"])
        require(type(entry["guidePath"]) is str and GUIDE.fullmatch(entry["guidePath"]), "invalid-guide-path")
    return value


def read_bounded(path, limit, directory_fd=None, cache=False):
    return read_bounded_with_metadata(path, limit, directory_fd, cache)[0]


def read_bounded_with_metadata(path, limit, directory_fd=None, cache=False):
    descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode), "non-regular-file")
        if cache:
            validate_cache_custody(metadata)
        require(metadata.st_size <= limit, "size-limit")
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "size-limit")
    return raw, metadata


def bundle_snapshot(directory):
    manifest = validate_manifest(read_bounded(directory / "latest.json", MANIFEST_LIMIT))
    raw = read_bounded(directory / manifest["catalogPath"], CATALOG_LIMIT)
    return Snapshot(manifest, raw, validate_catalog(raw, manifest))


def environment_profile(environ=None):
    """The profile the environment names: T9_PROFILE, else its old name TENSOR9_PROFILE. Blank is unset; both set differently is refused."""
    environ = os.environ if environ is None else environ
    current = environ.get("T9_PROFILE") or None
    legacy = environ.get("TENSOR9_PROFILE") or None
    current = current if current is None or current.strip() else None
    legacy = legacy if legacy is None or legacy.strip() else None
    require(current is None or legacy is None or current == legacy, "conflicting-profile-environment")
    return current if current is not None else legacy


def validate_profile(profile):
    if profile is not None:
        require(type(profile) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", profile), "invalid-profile")
    return profile


def validate_snapshot(raw, profile=None):
    envelope = strict_json(raw, CLI_STDOUT_LIMIT)
    fields(envelope, "servingVendor manifestJson catalogJson")
    require(type(envelope["servingVendor"]) is str and re.fullmatch(r"[0-9a-f]{16}", envelope["servingVendor"]), "invalid-serving-vendor")
    require(type(envelope["manifestJson"]) is str and type(envelope["catalogJson"]) is str, "invalid-snapshot")
    try:
        manifest = validate_manifest(envelope["manifestJson"].encode("utf-8"))
        catalog = envelope["catalogJson"].encode("utf-8")
    except UnicodeError:
        raise Rejected("invalid-snapshot") from None
    return Snapshot(manifest, catalog, validate_catalog(catalog, manifest), envelope["servingVendor"], validate_profile(profile))


def validate_cache_custody(metadata):
    require(metadata.st_uid == os.getuid() and not metadata.st_mode & 0o022, "unsafe-cache-permissions")


@contextlib.contextmanager
def cache_directory(directory):
    # configured parents remain trusted; only the selected cache directory is checked
    descriptor = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        require(stat.S_ISDIR(metadata.st_mode), "non-directory-cache")
        validate_cache_custody(metadata)
        yield descriptor
    finally:
        os.close(descriptor)


def cache_snapshot(directory):
    with cache_directory(directory) as descriptor:
        return read_cache_snapshot(descriptor)


def read_cache_snapshot(directory_fd):
    raw_envelope, metadata = read_bounded_with_metadata("catalog-cache.json", CACHE_LIMIT, directory_fd, cache=True)
    envelope = strict_json(raw_envelope, CACHE_LIMIT)
    fields(envelope, "manifest catalogBase64 servingVendor requestedProfile")
    manifest_raw = json.dumps(envelope["manifest"], separators=(",", ":")).encode("utf-8")
    manifest = validate_manifest(manifest_raw)
    require(type(envelope["servingVendor"]) is str and re.fullmatch(r"[0-9a-f]{16}", envelope["servingVendor"]), "invalid-serving-vendor")
    require(type(envelope["catalogBase64"]) is str, "invalid-cache")
    try:
        raw = base64.b64decode(envelope["catalogBase64"], validate=True)
    except (ValueError, binascii.Error):
        raise Rejected("invalid-cache") from None
    return Snapshot(manifest, raw, validate_catalog(raw, manifest), envelope["servingVendor"],
                    validate_profile(envelope["requestedProfile"]), metadata.st_mtime)


def profile_cache(directory, profile):
    validate_profile(profile)
    if profile is None:
        return directory
    return directory / ("profile-" + hashlib.sha256(profile.encode("utf-8")).hexdigest())


def available_snapshot(reader, directory):
    try:
        return reader(directory)
    except (Rejected, OSError):
        return None


def choose_snapshot(bundle, cache):
    require(bundle is not None or cache is not None, "no-usable-catalog")
    if bundle is None:
        return cache, "cache"
    if cache is None or version(bundle[0]["catalogVersion"]) > version(cache[0]["catalogVersion"]):
        return bundle, "bundle"
    if version(bundle[0]["catalogVersion"]) == version(cache[0]["catalogVersion"]):
        # the installed bundle remains authoritative on equal-version conflicts
        if bundle[0]["sha256"] != cache[0]["sha256"]:
            return bundle, "bundle"
    return cache, "cache"


def remaining(deadline):
    seconds = deadline - time.monotonic()
    require(seconds > 0, "deadline")
    return seconds


def fetch_cli(deadline, profile=None):
    """Run only an installed metadata command; never evaluate response text or a shell."""
    validate_profile(profile)
    # reserve a small part of the same total budget for killing/reaping the owned group
    process_deadline = deadline - min(0.2, remaining(deadline) / 10)
    executable = shutil.which("tensor9")
    require(executable is not None and Path(executable).is_absolute(), "cli-unavailable")
    executable = Path(executable).resolve(strict=True)
    require(not executable.is_relative_to(Path.cwd().resolve()), "repository-cli-refused")
    command = [str(executable), "prepare", "considerations", "-snapshot", "-json"]
    if profile is not None:
        command.extend(["-profile", profile])
    remaining(process_deadline)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    output = bytearray()
    stderr_size = 0
    try:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                for key, _ in selector.select(remaining(process_deadline)):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is process.stdout:
                        require(len(output) + len(block) <= CLI_STDOUT_LIMIT, "cli-stdout-limit")
                        output.extend(block)
                    else:
                        stderr_size += len(block)
                        require(stderr_size <= CLI_STDERR_LIMIT, "cli-stderr-limit")
        try:
            code = process.wait(timeout=remaining(process_deadline))
        except subprocess.TimeoutExpired:
            raise Rejected("deadline") from None
        require(code == 0, "cli-failed")
        return bytes(output)
    finally:
        # the new session belongs solely to this refresh, including any CLI descendants
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=max(0, min(0.2, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        process.stdout.close()
        process.stderr.close()


@contextlib.contextmanager
def cache_lock(directory, deadline):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with cache_directory(directory) as directory_fd:
        remaining(deadline)
        try:
            descriptor = os.open("catalog.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            descriptor = os.open("catalog.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            metadata = os.fstat(descriptor)
            require(stat.S_ISREG(metadata.st_mode), "non-regular-file")
            validate_cache_custody(metadata)
            while True:
                remaining(deadline)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(min(0.02, remaining(deadline)))
            yield directory_fd
        finally:
            os.close(descriptor)


def commit_snapshot(directory, incoming, bundle, deadline):
    with cache_lock(directory, deadline) as directory_fd:
        # insecure existing data must not be treated as an absent, repairable cache
        try:
            descriptor = os.open("catalog-cache.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        else:
            try:
                metadata = os.fstat(descriptor)
                require(stat.S_ISREG(metadata.st_mode), "non-regular-file")
                validate_cache_custody(metadata)
            finally:
                os.close(descriptor)
        current = available_snapshot(read_cache_snapshot, directory_fd)
        if current is not None and (current.serving_vendor, current.requested_profile) != (incoming.serving_vendor, incoming.requested_profile):
            current = None
        previous, _ = choose_snapshot(bundle, current)
        old_manifest = previous[0]
        new_manifest = incoming[0]
        comparison = (version(new_manifest["catalogVersion"]), version(old_manifest["catalogVersion"]))
        require(comparison[0] >= comparison[1], "rollback")
        if comparison[0] == comparison[1]:
            require(new_manifest["sha256"] == old_manifest["sha256"], "equal-version-conflict")
            status = "current"
        else:
            status = "updated"
        envelope = json.dumps({"manifest": new_manifest, "catalogBase64": base64.b64encode(incoming.raw).decode("ascii"),
                               "servingVendor": incoming.serving_vendor, "requestedProfile": incoming.requested_profile}, separators=(",", ":")).encode("utf-8")
        require(len(envelope) <= CACHE_LIMIT, "size-limit")
        temporary = None
        try:
            name = ".catalog-" + secrets.token_hex(16)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            temporary = name
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(envelope)
                stream.flush()
                os.fsync(stream.fileno())
            remaining(deadline)
            os.replace(temporary, "catalog-cache.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            temporary = None
        finally:
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory_fd)
        return status


def refresh(bundle_dir, cache_dir, transport=None, deadline=None, profile=None):
    deadline = deadline if deadline is not None else time.monotonic() + TOTAL_SECONDS
    try:
        validate_profile(profile)
        bundle = available_snapshot(bundle_snapshot, bundle_dir)
        current = available_snapshot(cache_snapshot, cache_dir)
        choose_snapshot(bundle, current)
        remaining(deadline)
        incoming = validate_snapshot((transport or fetch_cli)(deadline, profile), profile)
        remaining(deadline)
        status = commit_snapshot(cache_dir, incoming, bundle, deadline)
        return {"status": status, "reason": None, "servingVendor": incoming.serving_vendor,
                "requestedProfile": profile, "sha256": incoming.manifest["sha256"]}
    except Rejected as error:
        return {"status": "fallback", "reason": str(error)}
    except (OSError, ValueError):
        return {"status": "fallback", "reason": "cli-or-cache-unavailable"}


def validate_max_cache_age(hours):
    require(type(hours) is float and math.isfinite(hours) and 0 <= hours <= MAX_CACHE_AGE_HOURS_LIMIT, "invalid-max-cache-age")
    return round(hours * 3600)


def freshness(cache, ttl_seconds):
    """Describe the usable cache's age from its file mtime; a future mtime never counts as fresh."""
    if cache is None or cache.modified is None:
        return {"cacheAgeSeconds": None, "ttlSeconds": ttl_seconds, "stale": True}
    age = time.time() - cache.modified
    return {"cacheAgeSeconds": int(age), "ttlSeconds": ttl_seconds, "stale": not 0 <= age < ttl_seconds}


def recent_refresh_failure(directory):
    """Only an owned, regular, non-writable-by-others marker in the owned cache directory suppresses refreshes."""
    try:
        with cache_directory(directory) as directory_fd:
            descriptor = os.open(BACKOFF_MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            require(stat.S_ISREG(metadata.st_mode), "non-regular-file")
            validate_cache_custody(metadata)
            return 0 <= time.time() - metadata.st_mtime < BACKOFF_SECONDS
    except (Rejected, OSError):
        return False


def record_refresh_failure(directory):
    """Best effort: an unsafe directory or marker is left untouched and simply does not back off."""
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with cache_directory(directory) as directory_fd:
            descriptor = os.open(BACKOFF_MARKER, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
                require(stat.S_ISREG(metadata.st_mode), "non-regular-file")
                validate_cache_custody(metadata)
                os.utime(descriptor)
            finally:
                os.close(descriptor)
    except (Rejected, OSError):
        pass


def automatic_refresh(bundle_dir, cache_dir, ttl_seconds, transport=None, profile=None):
    """Refresh a missing or stale cache under the short automatic deadline, backing off after failures."""
    if not freshness(available_snapshot(cache_snapshot, cache_dir), ttl_seconds)["stale"]:
        return {"status": "skipped-fresh", "reason": None}
    if recent_refresh_failure(cache_dir):
        return {"status": "skipped-backoff", "reason": "recent-refresh-failure"}
    outcome = refresh(bundle_dir, cache_dir, transport, time.monotonic() + AUTOMATIC_SECONDS, profile)
    if outcome["status"] == "fallback":
        record_refresh_failure(cache_dir)
    return outcome


def query(snapshot, source, refresh_result, identifier=None, search=None, offset=0, limit=50, profile=None, cache_freshness=None):
    manifest, _, catalog = snapshot[:3]
    entries = catalog["considerations"]
    if identifier is not None:
        entries = [entry for entry in entries if entry["id"] == identifier]
    elif search is not None:
        term = search.casefold()
        entries = [entry for entry in entries if term in " ".join([entry["id"], entry["title"], entry["category"], entry["summary"]] + entry["aliases"]).casefold()]
    full_entry = identifier is not None
    require(type(offset) is int and 0 <= offset <= 1024 and type(limit) is int and 1 <= limit <= 50, "invalid-pagination")
    results = entries if full_entry else [{key: entry[key] for key in ("id", "title", "category", "guidePath")} for entry in entries[offset:offset + limit]]
    next_offset = offset + len(results) if not full_entry and offset + len(results) < len(entries) else None
    if (source == "cache" and refresh_result.get("status") in ("updated", "current")
            and refresh_result.get("servingVendor") == snapshot.serving_vendor
            and profile == snapshot.requested_profile
            and refresh_result.get("requestedProfile") == snapshot.requested_profile
            and refresh_result.get("sha256") == manifest["sha256"]):
        source = "vctrl"
    result = {
        "provenance": {"source": source, "servingVendor": snapshot.serving_vendor,
                       "sourceRequestedProfile": snapshot.requested_profile, "requestedProfile": profile, "schemaVersion": 1, "catalogVersion": manifest["catalogVersion"], "sha256": manifest["sha256"], "refresh": refresh_result,
                       "freshness": cache_freshness},
        "matchCount": len(entries), "truncated": next_offset is not None, "nextOffset": next_offset, "considerations": results,
    }
    require(len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= 65536, "query-size-limit")
    return result


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--index", action="store_true", help="list up to 50 entry IDs/titles")
    selector.add_argument("--id", help="read one complete consideration")
    selector.add_argument("--search", help="search index/aliases without executing patterns")
    parser.add_argument("--offset", type=int, default=0, help="index/search offset from nextOffset")
    parser.add_argument("--limit", type=int, default=50, help="index/search page size (1–50)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--refresh", action="store_true", help="force bounded metadata retrieval from the selected vendor VCtrl through tensor9")
    mode.add_argument("--no-refresh", action="store_true", help="never contact tensor9; query only the cache and bundle")
    parser.add_argument("--max-cache-age-hours", type=float, default=DEFAULT_MAX_CACHE_AGE_HOURS,
                        help="refresh automatically when the cache is missing or older than this (0–8760, default 24)")
    parser.add_argument("--profile", help="optional named tensor9 vendor profile; otherwise normal CLI discovery")
    parser.add_argument("--bundle-dir", type=Path, default=Path(__file__).resolve().parent.parent / "references" / "considerations")
    default_cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "tensor9" / "byoc-considerations-v1"
    parser.add_argument("--cache-dir", type=Path, default=default_cache)
    options = parser.parse_args(arguments)
    try:
        profile = validate_profile(options.profile if options.profile is not None else environment_profile())
        cache_dir = profile_cache(options.cache_dir, profile)
        if options.id is not None:
            text(options.id, 128)
            require(IDENTIFIER.fullmatch(options.id), "invalid-id")
            require(options.offset == 0 and options.limit == 50, "pagination-not-for-id")
        if options.search is not None:
            text(options.search, 128)
        ttl_seconds = validate_max_cache_age(options.max_cache_age_hours)
        if options.refresh:
            outcome = refresh(options.bundle_dir, cache_dir, profile=profile)
        elif options.no_refresh:
            outcome = {"status": "not-requested", "reason": None}
        else:
            outcome = automatic_refresh(options.bundle_dir, cache_dir, ttl_seconds, profile=profile)
        cache = available_snapshot(cache_snapshot, cache_dir)
        snapshot, source = choose_snapshot(available_snapshot(bundle_snapshot, options.bundle_dir), cache)
        result = query(snapshot, source, outcome, options.id, options.search, options.offset, options.limit, profile, freshness(cache, ttl_seconds))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (Rejected, OSError) as error:
        code = str(error) if isinstance(error, Rejected) else "local-catalog-unavailable"
        print(json.dumps({"error": code, "fallback": "Read the bundled references/considerations/index.md and catalog JSON directly."}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
