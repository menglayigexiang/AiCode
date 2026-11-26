---
name: est-github-skill
description: 访问 GitHub：克隆、检查远端分支、推送并核验原提交，或在本地 Git 无法打包时通过网页发布文件到新分支。
---

# EstGithubSkill

先确认仓库、目标路径或目标分支。Windows 上克隆、查询远端和推送已有提交时，优先使用本技能自带的 Dulwich + Paramiko 脚本。克隆可直接执行；推送或改走网页发布时，按对应流程先确认。

## 本机工具优先表

| 优先级 | 方式 | 本机结果（2026-11-24） | 何时使用 |
| --- | --- | --- | --- |
| 1 | Python Dulwich + Paramiko，SSH 443 | 已通过主机指纹校验和身份认证，读取并克隆测试用空仓库 | 直接开始。推送使用同一传输，GitHub 实际推送待验证 |
| 2 | 原生 Git + OpenSSH | 本机已有 GitHub SSH 443 配置，原生客户端路径待验证 | Python 依赖不可用时才尝试，先用 `git ls-remote` |
| 3 | Python HTTPS | 访问测试仓库时返回 HTTP 401 | 有已授权的 HTTPS 凭据时尝试 |
| 4 | 原生 Git HTTPS / GitHub 代理 | 原生直连连接失败，代理隧道返回 405 | 网络或代理配置变化后再测 |

若 Python 能查询或拉取远端，但 Dulwich 在本地打包时失败（例如 `Not a known type 7`），先按本地对象读取问题排查。切换 SSH 或 HTTPS 不能修复本地打包；远端可读也不代表可推送。必须保留原提交 SHA 时，停止发布并处理本地对象问题；若只需发布明确可上传的文件，走[网页发布](#网页发布)。

SSH 握手短暂中断时，同一路径最多重试两次。认证失败检查当前 SSH 身份，仓库访问被拒检查仓库权限。主机指纹不匹配时停止，查阅 [GitHub 官方指纹](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)。推送结果不确定时先验证远程 SHA，再决定是否需要继续。凭据只在本机使用，输出中避免记录密钥、令牌和认证头。

## 准备并运行

从技能目录执行。先尝试导入依赖，缺失时安装到可复用的临时目录。优先用 `ssh -G github.com` 读取当前电脑对 GitHub 生效的 `IdentityFile` 配置，按配置顺序选取实际存在的私钥。若命令不可用，查看 `~/.ssh/config` 中匹配 GitHub 的 `Host` 条目及其 `Include` 文件，或用已知的 GitHub SSH Host 别名重新查询。多个身份可用时，优先使用已知能访问目标仓库的身份。下面的示例使用查询到的第一个有效候选，必要时将 `$estKey` 设为已确认的私钥路径。

```powershell
$estDeps = Join-Path ([IO.Path]::GetTempPath()) 'est-github-skill-python'
$env:PYTHONPATH = (@($estDeps, $env:PYTHONPATH) | Where-Object { $_ }) -join [IO.Path]::PathSeparator
python -c "import dulwich, paramiko" 2>$null
if ($LASTEXITCODE -ne 0) {
    python -m pip install --target $estDeps 'dulwich>=1.2,<2' 'paramiko>=4,<6'
    if ($LASTEXITCODE -ne 0) { throw 'Python Git dependencies unavailable' }
}
$estSshConfig = ssh -G github.com 2>$null
$estKey = $estSshConfig | ForEach-Object {
    if ($_ -match '^identityfile\s+(.+)$') {
        $estCandidate = $Matches[1].Trim('"')
        if ($estCandidate -match '^~[/\\]') {
            $estCandidate = Join-Path $env:USERPROFILE $estCandidate.Substring(2)
        }
        if ($estCandidate -notlike '*.pub' -and (Test-Path -LiteralPath $estCandidate -PathType Leaf)) {
            $estCandidate
        }
    }
} | Select-Object -First 1
if (-not $estKey) { throw 'Select an existing GitHub IdentityFile from the SSH configuration' }
$estRepo = 'OWNER/REPO'
$estScript = '.\scripts\github_ssh.py'
python $estScript --key $estKey --repo $estRepo probe
python $estScript --key $estKey --repo $estRepo clone --target 'E:\Github\REPO'
```

目标目录须尚未存在，父目录须存在。已有仓库时先核对 `origin`。空远程会生成空克隆。远程未提供默认分支时，本地初始分支使用 `main`。完成后检查本地分支、`origin` 和工作区状态。

## 找不到可用的 SSH 私钥

1. 确认当前查询的是目标仓库对应的 GitHub Host 或别名，并区分三种情况。配置路径不存在时，询问用户是否已有密钥可选。只有 `.pub` 文件时，询问用户是否能找到配对的私钥。密钥存在但认证失败时，先核对 GitHub 登记和仓库权限。
2. 确认没有可用私钥后，一次只问一个问题，给出 ① 新建专用密钥（推荐）、② 指定现有私钥路径、③ 暂停。预计选 ① 后还有一次用户操作，在 GitHub 登记公钥。用户明确选择新建后，才指导其在本机交互式终端执行下方命令。用户自行输入口令。
3. 将 `.pub` 公钥登记到预期 GitHub 账号的 **Settings → SSH and GPG keys → New SSH key → Authentication Key**。如目标仓库属于要求 SSO 的组织，按组织流程为该密钥授权。私钥和口令保留在本机。等待用户确认登记后继续。[登记 SSH 公钥](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account) · [为组织 SSO 授权 SSH 密钥](https://docs.github.com/en/authentication/authenticating-with-saml-single-sign-on/authorizing-an-ssh-key-for-use-with-saml-single-sign-on)
4. 将新密钥路径作为 `--key` 参数，并用目标仓库执行 `probe`。若密钥有口令，本技能的 Python 脚本当前不会请求口令或使用 ssh-agent。此时用支持交互式输入口令的 OpenSSH，先按下面的说明配置 SSH，再以 `git ls-remote` 验证目标仓库读取权限。读取成功才继续克隆。推送仍按下节逐次确认，读取权限与推送权限分别验证。

### Windows 生成和复制公钥

在 PowerShell 中运行。选择 GitHub 专用文件名并设置口令。如果任一文件已存在，另选文件名。 [生成 SSH 密钥](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/generating-a-new-ssh-key-and-adding-it-to-the-ssh-agent)

```powershell
$estSshDir = Join-Path $env:USERPROFILE '.ssh'
$estNewKey = Join-Path $estSshDir 'id_ed25519_github'
if ((Test-Path -LiteralPath $estNewKey) -or (Test-Path -LiteralPath "$estNewKey.pub")) {
    throw 'Choose an unused key filename'
}
New-Item -ItemType Directory -Path $estSshDir -Force -ErrorAction Stop | Out-Null
ssh-keygen -t ed25519 -C 'github-access' -f $estNewKey
if ($LASTEXITCODE -ne 0) { throw 'SSH key generation failed' }
Get-Content -Raw -LiteralPath "$estNewKey.pub" | Set-Clipboard
```

用户希望后续自动发现该密钥，或需要用 OpenSSH 输入密钥口令时，在 `~/.ssh/config` 中合并下方设置。已有匹配条目时检查现有配置和其他账号的用途，再决定使用原条目还是新建 Host 别名。若使用别名，将示例中的 `github.com` 换成该别名，并在对应 SSH 远程地址中使用同一别名。

```sshconfig
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519_github
    IdentitiesOnly yes
```

用 `ssh -G github.com` 检查生效配置。OpenSSH 路径用 `git ls-remote git@github.com:OWNER/REPO.git` 测试读取，空仓库可能没有输出，检查退出码。认证失败时先检查密钥是否登记到正确账号。认证成功但目标仓库访问失败时检查仓库权限和组织 SSO。

## 推送与验证

1. 读取本地 `origin` 的实际推送地址、目标分支、待推送完整 SHA 和远程当前 SHA。脚本推送同名本地分支的已提交内容。
2. 每次推送前都向用户列出仓库、分支和待推送 SHA，等待用户再次明确确认。用户先前提出推送请求时也执行这一步。目标或提交变化时重新确认。
3. 仅执行普通推送。分支不满足快进条件时停止。收到成功结果后，以远程分支的实际 SHA 等于确认 SHA 为完成条件。

```powershell
python $estScript --key $estKey --repo $estRepo verify --branch main
# 用户确认之后执行
python $estScript --key $estKey --repo $estRepo push --worktree 'E:\Github\REPO' --branch main --confirm-sha '<完整的已确认 SHA>'
```

## 网页发布

此流程将文件提交到 GitHub 上的新分支，**会生成新提交 SHA，不会上传已有本地提交或保留其历史**。发布前列出目标仓库和可见性、基准分支及 SHA、拟创建的分支、要发布的文件及其差异；说明 SHA 会变化，等待用户确认。只提交已确认可上传的文件，不附带其他工作区内容。

1. 在 GitHub 登录有仓库写权限的账号，切到已确认的基准分支（通常是 `main`）。先核对基准 SHA 和目标分支是否已存在；基准 SHA 有变化、目标分支已存在或权限不足时，停止并重新确认。
2. 新增文件时，从仓库根目录选择 **Add file → Create new file**，填写仓库相对路径；更新文件时，打开目标文件并选择 **Edit this file**。输入已确认的内容并检查差异。
3. 选择 **Commit changes… → Create a new branch for this commit**，使用已确认的分支名，不直接提交到基准分支。完成后打开新分支，核对父提交、文件差异和新提交 SHA。需要创建 PR 时另行确认。

[GitHub 官方：创建文件](https://docs.github.com/en/repositories/working-with-files/managing-files/creating-new-files)

### REST API 备选

需要自动化且已有授权凭据时，按同一发布确认流程执行。细粒度令牌需要目标仓库的 `Contents: write` 权限；从本地安全配置读取凭据，不在对话或日志中展示。先用 [Git references API](https://docs.github.com/en/rest/git/refs#create-a-reference) 从确认的基准 SHA 创建目标分支，再用 [Contents API](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents) 提交文件，明确指定 `branch`；更新文件时同时提供该分支上的现有文件 blob SHA。写入后读取远端分支和文件差异，核对新提交 SHA。公开 API 可达不代表具备写入权限。
