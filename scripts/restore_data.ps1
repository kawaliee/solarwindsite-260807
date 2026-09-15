<#
.SYNOPSIS
  backup_data.ps1이 만든 번들에서 PostgreSQL과 .env를 복원합니다.

.DESCRIPTION
  복원 순서가 중요합니다. 컨테이너가 떠 있어야 하고, 기존 DB 위에 덮어씁니다.

    docker compose up -d postgres
    .\scripts\restore_data.ps1 -Bundle ..\260618_backup\260618_data_<타임스탬프>.tar
    docker compose exec backend python manage.py migrate

  ⚠️ 이 스크립트는 기존 데이터를 **덮어씁니다**. 되돌릴 수 없으므로 실행 전
     현재 상태를 backup_data.ps1로 먼저 받아 두십시오.

.PARAMETER Bundle
  백업 번들(.tar) 경로.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Bundle
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Bundle)) { throw "번들을 찾을 수 없습니다: $Bundle" }

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Staging     = Join-Path $env:TEMP ("260618_restore_" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

Write-Host "[1/3] 번들 푸는 중..."
tar -xf $Bundle -C $Staging

Write-Host "[2/3] .env 복원 중..."
$EnvSrc = Join-Path $Staging '.env'
if (Test-Path $EnvSrc) {
    Copy-Item $EnvSrc -Destination (Join-Path $ProjectRoot '.env') -Force
    Write-Host "      .env 복원됨"
} else {
    Write-Warning "번들에 .env가 없습니다 — .env.example로 직접 만드십시오."
}

Write-Host "[3/3] PostgreSQL 복원 중 (기존 데이터를 덮어씁니다)..."
$DumpSrc = Join-Path $Staging 'postgres.dump'
if (-not (Test-Path $DumpSrc)) { throw "번들에 postgres.dump가 없습니다." }

$DbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { 're_user' }
$DbName = if ($env:POSTGRES_DB)   { $env:POSTGRES_DB }   else { 're_agent' }

Get-Content $DumpSrc -Encoding Byte -Raw |
    docker compose exec -T postgres pg_restore -U $DbUser -d $DbName --clean --if-exists
if ($LASTEXITCODE -ne 0) { Write-Warning "pg_restore가 경고를 냈습니다. 위 출력을 확인하십시오." }

Remove-Item $Staging -Recurse -Force

Write-Host ""
Write-Host "완료. 이어서 마이그레이션을 적용하십시오:" -ForegroundColor Green
Write-Host "  docker compose exec backend python manage.py migrate"
