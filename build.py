#!/usr/bin/env python3
"""Build a pinned Rust toolchain with a c-scape Linux target."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
RUST = ROOT / "rust"
C_WARD = ROOT / "c-ward"
ORIGIN = ROOT / "origin"
PIN = json.loads((ROOT / "toolchain.json").read_text())
HOST = PIN["host"]
TARGET = PIN["target"]
BUILD = ROOT / "build"
NATIVE = BUILD / "runtime"
OUTPUT = ROOT / "out/linux-raw"
JOBS = min(6, os.cpu_count() or 1)
PATCHES = [
    (RUST, ROOT / "patches/0001-rust-linux-raw-target.patch"),
    (RUST, ROOT / "patches/0002-rust-std-runtime.patch"),
    (RUST / "src/tools/cargo", ROOT / "patches/0003-cargo-default-target.patch"),
    (ORIGIN, ROOT / "patches/0004-origin-runtime.patch"),
    (C_WARD, ROOT / "patches/0005-c-ward-runtime.patch"),
]
COMPAT_LIBRARIES = (
    "c", "m", "dl", "rt", "pthread", "util", "gcc", "gcc_s", "gcc_eh",
    "unwind", "resolv", "crypt", "xnet",
)


def run(*command, cwd=ROOT, env=None, capture=False):
    print("+", " ".join(map(str, command)), flush=True)
    return subprocess.run(
        list(map(str, command)), cwd=cwd, env=env, check=True,
        stdout=subprocess.PIPE if capture else None, text=True,
    ).stdout


def git(repo, *arguments):
    return run("git", "-C", repo, *arguments, capture=True).strip()


def prepare_sources():
    for repo, commit in ((RUST, PIN["rust_commit"]), (ORIGIN, PIN["origin_commit"]),
                         (C_WARD, PIN["c_ward_commit"])):
        if not (repo / ".git").exists():
            run("git", "submodule", "update", "--init", "--depth", "1", repo.name)
        if git(repo, "rev-parse", "HEAD") != commit:
            if git(repo, "status", "--porcelain", "--untracked-files=normal"):
                raise RuntimeError(f"{repo.name} has local changes; refusing to switch commits")
            run("git", "-C", repo, "fetch", "--depth", "1", "origin", commit)
            run("git", "-C", repo, "checkout", "--detach", commit)
    run("git", "-C", RUST, "submodule", "update", "--init", "--depth", "1",
        "src/tools/cargo", "library/backtrace")
    cargo = RUST / "src/tools/cargo"
    if git(cargo, "rev-parse", "HEAD") != PIN["cargo_commit"]:
        raise RuntimeError("Cargo does not match the pinned Rust gitlink")
    for repo, patch in PATCHES:
        forward = subprocess.run(["git", "apply", "--check", str(patch)], cwd=repo,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if forward.returncode == 0:
            run("git", "apply", patch, cwd=repo)
        else:
            reverse = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)],
                                     cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if reverse.returncode != 0:
                raise RuntimeError(f"{patch.name} conflicts with local edits; nothing was reset")
            print(f"Already applied: {patch.name}")


def build_rust():
    NATIVE.mkdir(parents=True, exist_ok=True)
    for name in (*COMPAT_LIBRARIES, "linux_raw_runtime"):
        archive = NATIVE / f"lib{name}.a"
        if not archive.exists():
            run("ar", "crs", archive)
    config = BUILD / "bootstrap.toml"
    config.write_text(f'''change-id = "ignore"
[build]
build-dir = {json.dumps(str(BUILD / "rust"))}
extended = true
tools = ["cargo", "rustdoc"]
submodules = false
optimized-compiler-builtins = false
jobs = {JOBS}
[rust]
channel = "stable"
download-rustc = false
lto = "off"
incremental = false
codegen-units = 16
debuginfo-level = 0
lld = true
[llvm]
download-ci-llvm = true
''')
    env = os.environ.copy()
    for key in ("RUSTC", "RUSTDOC", "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "CARGO_BUILD_TARGET"):
        env.pop(key, None)
    env["RUST_LINUX_RAW_RUNTIME"] = str(NATIVE)
    env["BOOTSTRAP_SKIP_TARGET_SANITY"] = "1"
    run(sys.executable, "x.py", "--config", config, "build", "--stage", "1",
        "--target", f"{HOST},{TARGET}", "compiler/rustc", "library",
        "src/tools/cargo", "src/tools/rustdoc", cwd=RUST, env=env)
    return BUILD / "rust" / HOST / "stage1"


def build_runtime(stage):
    cargo = BUILD / "rust" / HOST / "stage0/bin/cargo"
    env = os.environ.copy()
    env["RUSTC"] = str(stage / "bin/rustc")
    env["RUSTDOC"] = str(stage / "bin/rustdoc")
    env["RUSTC_BOOTSTRAP"] = "1"
    env["RUSTFLAGS"] = "-Crelocation-model=static -Cembed-bitcode=yes"
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    output = run(cargo, "build", "--manifest-path", C_WARD / "Cargo.toml",
                 "-p", "c-scape", "--no-default-features", "--features",
                 "eyra,threadsafe-setenv", "--release", "--target", TARGET,
                 "--target-dir", BUILD / "c-scape", "--message-format=json",
                 cwd=C_WARD, env=env, capture=True)
    archives = set()
    for line in output.splitlines():
        message = json.loads(line)
        if message.get("reason") == "compiler-message":
            print(message["message"].get("rendered", ""), file=sys.stderr, end="")
        if message.get("reason") == "compiler-artifact":
            for filename in message["filenames"]:
                library = Path(filename)
                if library.suffix == ".rlib" and library.is_relative_to(BUILD / "c-scape" / TARGET):
                    archives.add(library)
    if not archives:
        raise RuntimeError("Cargo reported no runtime libraries")
    with tempfile.TemporaryDirectory(dir=NATIVE) as temporary:
        temporary = Path(temporary)
        objects = []
        for archive in sorted(archives):
            for member in run("ar", "t", archive, capture=True).splitlines():
                if member.endswith(".o"):
                    obj = temporary / f"{len(objects)}.o"
                    obj.write_bytes(subprocess.check_output(["ar", "p", str(archive), member]))
                    objects.append(obj)
        if not objects:
            raise RuntimeError("Runtime libraries contain no object files")
        archive = temporary / "liblinux_raw_runtime.a"
        for start in range(0, len(objects), 100):
            run("ar", "qcs", archive, *objects[start:start + 100])
        run("ranlib", archive)
        archive.replace(NATIVE / archive.name)


def provenance(repo):
    files = git(repo, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    digest = hashlib.sha256()
    for name in sorted(files):
        file = repo / name
        if file.is_file() and (file.suffix in (".rs", ".toml") or file.name == "Cargo.lock"):
            digest.update(name.encode() + b"\0" + file.read_bytes())
    return {"path": str(repo), "commit": git(repo, "rev-parse", "HEAD"),
            "dirty": bool(git(repo, "status", "--porcelain")),
            "source_sha256": digest.hexdigest()}


def package(stage):
    if OUTPUT.exists():
        raise RuntimeError(f"Output already exists: {OUTPUT}; move it before rebuilding")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".linux-raw-", dir=OUTPUT.parent) as temporary:
        package_dir = Path(temporary) / "toolchain"
        shutil.copytree(stage, package_dir, symlinks=False)
        cargo = BUILD / "rust" / HOST / "stage1-tools-bin/cargo"
        shutil.copy2(cargo, package_dir / "bin/cargo")
        libdir = package_dir / "lib/rustlib" / TARGET / "lib"
        for archive in NATIVE.glob("*.a"):
            shutil.copy2(archive, libdir / archive.name)
        for name in ("cargo", "rustc", "rustdoc"):
            if (package_dir / "bin" / name).read_bytes()[:4] != b"\x7fELF":
                raise RuntimeError(f"{name} is not a native ELF binary")
        manifest = {**PIN, "patches": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for _, p in PATCHES},
                    "c_ward": provenance(C_WARD), "origin": provenance(ORIGIN)}
        (package_dir / "linux-raw-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
        package_dir.rename(OUTPUT)


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Usage: python3 build.py")
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise SystemExit("this experiment requires an x86_64 Linux host")
    if OUTPUT.exists():
        raise SystemExit(f"Output already exists: {OUTPUT}; move it before rebuilding")
    prepare_sources()
    stage = build_rust()
    build_runtime(stage)
    package(stage)
    print(f"\nPackaged toolchain: {OUTPUT}")
    print(f"rustup toolchain link linux-raw {OUTPUT}")
    print("cargo +linux-raw build")


if __name__ == "__main__":
    main()
