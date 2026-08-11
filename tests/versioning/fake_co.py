#!/usr/bin/env python3
"""Fake ``co`` CLI for unit tests (tests/versioning/).

Emulates the argument order and output formats of the real co binary
(https://github.com/PaddySeahorse/co) so :mod:`coffice.versioning.co_client`
can be tested without the real binary. Unlike the real binary it is lenient
about file paths (does not parse the ZIP) and additionally implements
``branch``/``merge``/``tag`` (which upstream co lacks) so the wrapper's full
API surface can be exercised.

State is persisted to a JSON file because every subprocess invocation is a
fresh process. Configure via environment variables:

- ``FAKE_CO_STATE``       path to the state JSON file (default:
                          ``./fake_co_state.json``)
- ``FAKE_CO_JSON_LOG``    ``1`` -> ``co log --json`` emits JSON instead of
                          text (real co does not support --json)
- ``FAKE_CO_NO_BRANCH``   ``1`` -> omit branch/merge/tag from --help and fail
                          with "unknown command" (mirrors upstream co)
- ``FAKE_CO_DIFF``        newline-separated diff lines to print (default:
                          ``M word/document.xml``)
- ``FAKE_CO_FAIL_COMMANDS`` comma-separated commands that exit 1 with a
                          message on stderr (error-path tests)
- ``FAKE_CO_RECORD``      append one JSON line per invocation (argv) here

Supported commands: init commit log diff checkout export import branch merge
tag status gc --help --version
"""

import hashlib
import json
import os
import sys
import time

STATE_PATH = os.environ.get("FAKE_CO_STATE", "fake_co_state.json")
JSON_LOG = os.environ.get("FAKE_CO_JSON_LOG") == "1"
NO_BRANCH = os.environ.get("FAKE_CO_NO_BRANCH") == "1"
FAIL_COMMANDS = {
    c.strip()
    for c in os.environ.get("FAKE_CO_FAIL_COMMANDS", "").split(",")
    if c.strip()
}
DIFF_LINES = os.environ.get("FAKE_CO_DIFF", "M word/document.xml").splitlines()
RECORD = os.environ.get("FAKE_CO_RECORD")


def record(argv):
    if RECORD:
        with open(RECORD, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(argv) + "\n")


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)


def doc_state(state, path):
    return state.setdefault(path, {"commits": [], "head": None, "branches": {}, "tags": {}})


def make_hash(path, message, index):
    return hashlib.sha1(f"{path}:{message}:{index}".encode()).hexdigest()


def rfc1123(epoch):
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(epoch))


def usage():
    lines = [
        "Usage: co <command> [args]",
        "Commands:",
        "  init <path>                  Initialize .co metadata inside the Office file",
        "  commit -m <msg> <path>       Create a commit of the Office file contents",
        "  log <path>                   Show commit history stored inside the Office file",
        "  status <path>                Show version control status of the Office file",
        "  diff <a> <b> <path>          Compare two commits (refs: HEAD, HEAD~N, hash, prefix)",
        "  checkout <commit> <path>     Restore the Office file contents from a specific commit",
        "  gc <path>                    Pack objects and prune unreachable objects",
        "  export <path>                Extract .co/ history into a standalone .co-bundle",
        "  import <path> <bundle>       Inject a .co-bundle's history back into an Office file",
        "  migrate <path>               Convert the repository's hash algorithm (sha1<->sha256)",
        "  branch <name> <path>         Create a branch pointing at the current HEAD",
    ]
    if not NO_BRANCH:
        lines.append("  merge <path> <branch>         Merge a branch (emulated by fake co)")
        lines.append("  tag <path> <name>             Create a tag (emulated by fake co)")
    return "\n".join(lines)


def print_log_text(commits, out=sys.stdout):
    for c in commits:
        out.write(f"commit {c['hash']}\n")
        if c.get("author"):
            out.write(f"Author: {c['author']} <{c.get('email', '')}>\n")
        if c.get("timestamp"):
            out.write(f"Date:   {rfc1123(c['timestamp'])}\n")
        out.write(f"\n    {c['message']}\n\n")


def cmd_init(args):
    path = args[0]
    state = load_state()
    doc_state(state, path)
    save_state(state)
    print(f"Initialized .co metadata inside {path}")


def cmd_commit(args):
    msg = None
    path = None
    bundle = None
    i = 0
    while i < len(args):
        if args[i] in ("-m", "--m"):
            msg = args[i + 1]
            i += 2
        elif args[i] == "--external":
            bundle = args[i + 1]
            i += 2
        else:
            path = args[i]
            i += 1
    if not msg:
        sys.stderr.write("commit message required (-m)\n")
        sys.exit(1)
    state = load_state()
    key = bundle or path
    ds = doc_state(state, key)
    index = len(ds["commits"]) + 1
    hash_ = make_hash(key, msg or "", index)
    commit = {
        "hash": hash_,
        "author": os.environ.get("CO_AUTHOR_NAME", ""),
        "email": os.environ.get("CO_AUTHOR_EMAIL", "unknown@example.com"),
        "message": msg or "",
        "timestamp": int(time.time()),
    }
    ds["commits"].append(commit)
    ds["head"] = hash_
    save_state(state)
    if bundle:
        print(f"Committed {hash_} (external: {bundle})")
    else:
        print(f"Committed {hash_}")


def cmd_log(args):
    path = None
    bundle = None
    json_log = False
    i = 0
    while i < len(args):
        if args[i] == "--json":
            json_log = True
            i += 1
        elif args[i] == "--external":
            bundle = args[i + 1]
            i += 2
        else:
            path = args[i]
            i += 1
    state = load_state()
    ds = doc_state(state, bundle or path)
    commits = list(reversed(ds["commits"]))
    if json_log or JSON_LOG:
        payload = []
        for c in commits:
            payload.append(
                {
                    "hash": c["hash"],
                    "author": c["author"],
                    "email": c["email"],
                    "message": c["message"],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(c["timestamp"])),
                }
            )
        print(json.dumps(payload))
    else:
        print_log_text(commits)


def cmd_diff(args):
    print("\n".join(DIFF_LINES))


def cmd_checkout(args):
    hash_ = args[0]
    state = load_state()
    ds = doc_state(state, args[1])
    if not any(c["hash"] == hash_ for c in ds["commits"]):
        sys.stderr.write(f"checkout failed: {hash_}\n")
        sys.exit(1)
    ds["head"] = hash_
    save_state(state)
    print(f"Checked out {hash_}")


def cmd_export(args):
    path = args[0]
    output = None
    clean = False
    clean_output = None
    i = 1
    while i < len(args):
        if args[i] in ("--output", "-o"):
            output = args[i + 1]
            i += 2
        elif args[i] == "--clean":
            clean = True
            i += 1
        elif args[i] == "--clean-output":
            clean_output = args[i + 1]
            i += 2
        else:
            i += 1
    state = load_state()
    ds = doc_state(state, path)
    bundle_path = output or f"{path}.co-bundle"
    with open(bundle_path, "w", encoding="utf-8") as fh:
        json.dump({"source": path, "commits": ds["commits"], "head": ds["head"]}, fh)
    print(f"Exported history to {bundle_path}")
    if clean:
        clean_path = clean_output or path[:-5] + ".clean" + path[-5:]
        with open(clean_path, "w", encoding="utf-8") as fh:
            fh.write("fake clean copy")
        print(f"  Clean copy: {clean_path}")
    print(f"  Original left untouched: {path}")


def cmd_import(args):
    path = args[0]
    bundle = args[1]
    with open(bundle, encoding="utf-8") as fh:
        payload = json.load(fh)
    state = load_state()
    ds = doc_state(state, path)
    existing = {c["hash"] for c in ds["commits"]}
    for c in payload.get("commits", []):
        if c["hash"] not in existing:
            ds["commits"].append(c)
    ds["head"] = payload.get("head") or ds["head"]
    save_state(state)
    print(f"Imported history from {bundle} into {path}")


def cmd_branch(args):
    name, path = args[0], args[1]
    state = load_state()
    ds = doc_state(state, path)
    ds["branches"][name] = ds["head"]
    save_state(state)
    print(f"Created branch {name}")


def cmd_merge(args):
    path, branch = args[0], args[1]
    state = load_state()
    ds = doc_state(state, path)
    index = len(ds["commits"]) + 1
    hash_ = make_hash(path, f"Merge branch {branch}", index)
    ds["commits"].append(
        {
            "hash": hash_,
            "author": os.environ.get("CO_AUTHOR_NAME", "Merge"),
            "email": os.environ.get("CO_AUTHOR_EMAIL", "unknown@example.com"),
            "message": f"Merge branch {branch}",
            "timestamp": int(time.time()),
        }
    )
    ds["head"] = hash_
    save_state(state)
    print(f"Merged {branch} into {path}")
    print(f"  merge commit: {hash_}")


def cmd_tag(args):
    path, name = args[0], args[1]
    state = load_state()
    ds = doc_state(state, path)
    ds["tags"][name] = ds["head"]
    save_state(state)
    print(f"Created tag {name} at {ds['head']}")


def cmd_status(args):
    path = args[0]
    state = load_state()
    ds = doc_state(state, path)
    if not ds["commits"]:
        print("No commits yet")
        return
    print(f"Commit: {ds['head'][:7]} ({ds['commits'][-1]['message']})")
    print("Changes since last commit: 0 file(s)")
    print(f"Bundle size: 0 bytes ({len(ds['commits'])} commits, 0 objects)")
    print("Hash algorithm: sha1")


def cmd_gc(args):
    print("Garbage collection completed:")
    print("  Reachable objects: 3")
    print("  Packed objects: 3")
    print("  Removed loose objects: 0")
    print("  Removed old packs: 0")


def main():
    argv = sys.argv[1:]
    record(["co"] + argv)
    if not argv:
        print(usage())
        return 1
    cmd = argv[0]
    if cmd in ("--help", "-h", "help"):
        print(usage())
        return 0
    if cmd in ("--version", "-v"):
        print("0.1.0-fake")
        return 0
    if cmd in FAIL_COMMANDS:
        sys.stderr.write(f"fake failure: {cmd}\n")
        return 1
    handlers = {
        "init": cmd_init,
        "commit": cmd_commit,
        "log": cmd_log,
        "diff": cmd_diff,
        "checkout": cmd_checkout,
        "export": cmd_export,
        "import": cmd_import,
        "status": cmd_status,
        "gc": cmd_gc,
    }
    if not NO_BRANCH:
        handlers.update({"merge": cmd_merge, "tag": cmd_tag})
    handlers["branch"] = cmd_branch

    if cmd not in handlers:
        # Real co prints usage to stdout and exits 1 for unknown commands.
        print(usage())
        return 1
    handlers[cmd](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
