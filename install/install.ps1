#Requires -Version 5.1
<#
.SYNOPSIS
  Install paper-dual-reduce as a skill for DSH and Claude Code.

.DESCRIPTION
  Creates a directory junction (or a copy, with -Mode copy) from the host
  skill directory to this repository, so both stay in sync while you edit.

.PARAMETER Mode
  junction (default) or copy.

.PARAMETER ProjectDir
  Optional. When given, also copies the Cursor rule file into
  <ProjectDir>/.cursor/rules/.

.EXAMPLE
  pwsh install/install.ps1
  pwsh install/install.ps1 -Mode copy
  pwsh install/install.ps1 -ProjectDir C:\work\my-thesis
#>
[CmdletBinding()]
param(
    [ValidateSet('junction', 'copy')]
    [string]$Mode = 'junction',
    [string]$ProjectDir = ''
)

$ErrorActionPreference = 'Stop'

$Repo = Split-Path -Parent $PSScriptRoot
$Name = 'paper-dual-reduce'

if (-not (Test-Path (Join-Path $Repo 'run.py'))) {
    throw "Not a paper-dual-reduce checkout: $Repo"
}

$targets = @(
    (Join-Path $env:USERPROFILE ".dsh\skills\$Name"),
    (Join-Path $env:USERPROFILE ".claude\skills\$Name")
)

foreach ($dest in $targets) {
    $parent = Split-Path -Parent $dest
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    if (Test-Path $dest) {
        $item = Get-Item $dest -Force
        if ($item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') {
            Write-Host "[skip] exists (link): $dest"
        } else {
            Write-Host "[skip] exists (real dir): $dest"
        }
        continue
    }
    if ($Mode -eq 'junction') {
        try {
            New-Item -ItemType Junction -Path $dest -Target $Repo | Out-Null
            Write-Host "[ok]   junction: $dest -> $Repo"
            continue
        } catch {
            Write-Host "[warn] junction failed ($($_.Exception.Message)); falling back to copy"
        }
    }
    Copy-Item -Path $Repo -Destination $dest -Recurse -Force
    Write-Host "[ok]   copied:   $dest"
}

if ($ProjectDir) {
    $rules = Join-Path $ProjectDir '.cursor\rules'
    New-Item -ItemType Directory -Path $rules -Force | Out-Null
    Copy-Item -Path (Join-Path $Repo '.cursor\rules\paper-dual-reduce.mdc') -Destination $rules -Force
    Write-Host "[ok]   cursor rule: $rules"
}

Write-Host ''
Write-Host 'Next steps by host:'
Write-Host '  DSH / Claude Code : restart the session, then ask for de-AI / de-dup on a .docx'
Write-Host '  Codex / WorkBuddy: open the repo as the workspace; AGENTS.md is already at the root'
Write-Host '  Cursor            : pwsh install/install.ps1 -ProjectDir <your project>'
Write-Host '  Chat-only (Doubao): paste PROMPT.md content, then the paper text'
Write-Host ''
Write-Host "Smoke test: python `"$Repo\run.py`" doctor"
