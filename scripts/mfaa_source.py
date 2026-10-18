"""Resolve the optional six-character MFAA fork override for one CI run."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

UPSTREAM = "MaaXYZ/MFAAvalonia"
FORK = "sunyink/MFAAvalonia"
FORK_BRANCH = "mfabd2"
RIDS = ("win-x64", "win-arm64", "linux-x64", "linux-arm64", "osx-x64", "osx-arm64")
MANIFEST = "fork-release.json"
STAMP = "mfaa-build.json"


def api(path):
    result = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def asset_bytes(asset_id):
    result = subprocess.run(["gh", "api", f"repos/{FORK}/releases/assets/{asset_id}",
                             "-H", "Accept: application/octet-stream"], check=True, capture_output=True)
    return result.stdout


def package_names(tag):
    return {f"MFAAvalonia-{tag}-{rid}" + (".zip" if rid.startswith("win-") else ".tar.gz") for rid in RIDS}


def validate_manifest(manifest, release, sha):
    tag = release["tag_name"]
    if (manifest.get("schema") != 1 or manifest.get("repository") != FORK
            or manifest.get("branch") != FORK_BRANCH or manifest.get("commit") != sha
            or manifest.get("short_commit") != sha[:6] or manifest.get("tag") != tag
            or not re.fullmatch(rf"v\d+\.\d+\.\d+-mfbd2\.g{sha[:6]}", tag)):
        raise ValueError("自家 MFAA 发布清单与指定提交不一致")
    packages = manifest.get("packages", [])
    expected_names = package_names(tag)
    if len(packages) != len(RIDS) or {p.get("name") for p in packages} != expected_names:
        raise ValueError("自家 MFAA 缺少完整的六平台产物清单")
    assets = release.get("assets", [])
    by_name = {asset["name"]: asset for asset in assets}
    if len(by_name) != len(assets) or set(by_name) != expected_names | {MANIFEST}:
        raise ValueError("自家 MFAA Release 附件缺失、重复或与清单不一致")
    for package in packages:
        asset = by_name[package["name"]]
        sha256 = package.get("sha256", "")
        if (not re.fullmatch(r"[0-9a-f]{64}", sha256) or package.get("size", 0) <= 0
                or asset.get("size") != package["size"] or asset.get("state") != "uploaded"
                or asset.get("digest") != f"sha256:{sha256}"):
            raise ValueError(f"自家 MFAA 附件校验不符：{package['name']}")


def resolve_source(upstream_tag, override, fetch=api, download=asset_bytes):
    short = override.strip().lower()
    if not short:
        return dict(repository=UPSTREAM, tag=upstream_tag, commit="")
    if not re.fullmatch(r"[0-9a-f]{6}", short):
        raise ValueError("自家 MFAA 提交号必须恰好为六位十六进制字符；留空则使用 requirements.txt")
    sha = fetch(f"repos/{FORK}/commits/{short}").get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not sha.startswith(short):
        raise ValueError("六位提交号未能解析到唯一的完整提交")
    candidates = []
    page = 1
    while True:
        releases = fetch(f"repos/{FORK}/releases?per_page=100&page={page}")
        candidates.extend(release for release in releases if not release.get("draft")
                          and re.fullmatch(rf"v\d+\.\d+\.\d+-mfbd2\.g{short}", release.get("tag_name", "")))
        if len(releases) < 100:
            break
        page += 1
    if len(candidates) != 1:
        raise ValueError(f"提交 {short} 需要唯一的自家预发布包，当前找到 {len(candidates)} 个；请先在 mfabd2 分支完成构建")
    release = candidates[0]
    if release.get("prerelease") is not True:
        raise ValueError("自家 MFAA 只允许预发布包")
    tag = release["tag_name"]
    if fetch(f"repos/{FORK}/commits/{quote(tag, safe='')}").get("sha") != sha:
        raise ValueError("自家 MFAA 标签指向另一提交")
    manifests = [asset for asset in release.get("assets", []) if asset.get("name") == MANIFEST]
    if len(manifests) != 1 or not 0 < manifests[0].get("size", 0) <= 65536:
        raise ValueError("自家 MFAA 发布缺少有效构建清单")
    asset = manifests[0]
    raw = download(asset["id"])
    if (len(raw) != asset["size"] or asset.get("state") != "uploaded"
            or asset.get("digest") != f"sha256:{hashlib.sha256(raw).hexdigest()}"):
        raise ValueError("自家 MFAA 发布清单校验失败")
    validate_manifest(json.loads(raw), release, sha)
    return dict(repository=FORK, tag=tag, commit=sha)


def verify_extracted(root, tag, sha, rid):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or rid not in RIDS:
        raise ValueError("自家 MFAA 校验需要完整提交与有效平台")
    stamps = list(root.rglob(STAMP))
    if len(stamps) != 1:
        raise ValueError("下载包中没有唯一的 MFAA 构建来源标记")
    stamp = json.loads(stamps[0].read_text(encoding="utf-8"))
    expected = dict(schema=1, repository=FORK, branch=FORK_BRANCH, commit=sha,
                    short_commit=sha[:6], tag=tag, rid=rid)
    if any(stamp.get(key) != value for key, value in expected.items()):
        raise ValueError("下载包的平台或源码提交不符")
    print(f"Verified MFAA {tag}, {rid}, commit {sha}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--tag", required=True)
    resolve.add_argument("--commit", default="")
    verify = sub.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--tag", required=True)
    verify.add_argument("--commit", required=True)
    verify.add_argument("--rid", choices=RIDS, required=True)
    args = parser.parse_args()
    if args.action == "verify":
        verify_extracted(args.root, args.tag, args.commit, args.rid)
        return
    source = resolve_source(args.tag, args.commit)
    print(json.dumps(source, ensure_ascii=False, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            for key, value in source.items():
                stream.write(f"{key}={value}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"### MFAAvalonia package source\n\nRepository: `{source['repository']}`\n\n"
                         f"Tag: `{source['tag']}`\n\nCommit: `{source['commit'] or 'upstream requirements.txt pin'}`\n")


if __name__ == "__main__":
    main()
