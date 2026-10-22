# 可选的自家 MFAAvalonia 构建

常规构建继续读取 `requirements.txt` 中的 `MFAA_TAG`，从 `MaaXYZ/MFAAvalonia`
下载客户端。无需修改该文件即可临时选择自家客户端。

1. 在 `sunyink/MFAAvalonia` 的 `mfabd2` 分支合入需要的改动，运行 **Build MFABD2 prerelease**。
2. 等六个平台的包全部构建并发布为预发布，记下摘要中的六位提交号。
3. 在本仓库 **Actions → install → Run workflow** 的 `mfaa_commit` 填入该编号。
4. 按原有方式选择发布通道。留空 `mfaa_commit` 时完全沿用 `requirements.txt`。

输入为文本，必须恰好六位十六进制字符，允许前导零。该输入只影响本次手动运行，
普通 push、PR 和标签构建不会自动沿用它。它不修改客户端的“更新软件”行为。

自家版本必须来自 `sunyink/MFAAvalonia:mfabd2`，标签形如
`v2.16.3-mfbd2.g4e22d1`，并且必须是预发布。CI 会解析完整提交，检查标签所指提交、
六个平台附件、`fork-release.json` 中的大小与 SHA-256，以及解压后的
`mfaa-build.json` 平台和提交标记。不接受仅有源码而未出包的提交；找不到、编号有歧义、
附件不完整或来源不符时直接失败，不自动换回官方包。

内核探测和桌面打包使用同一份所选 MFAA。现有 `MFA_CORE_TAG` 覆盖与 Android UI
构建规则保持原有职责；本输入不把 MFAA Android shell 换入本项目的 Android 应用。
运行摘要记录实际客户端仓库、标签及完整提交，打包后的客户端保留来源标记供排查。
