<#
.SYNOPSIS
  backup_data.ps1로 만든 번들을 프로젝트에 복원합니다 (backend/media + .env).
  새 PC에서: git clone 후 이 스크립트로 데이터를 채운 뒤 docker compose up -d 하십시오.

.PARAMETER Bundle
  복원할 .zip 또는 .tar 번들 경로 (필수).

.PARAMETER Force
  기존 backend/media / .env를 덮어씁니다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\restore_data.ps1 -Bundle ..\260618_backup\260618_data_20260716_180000.tar
#>
param(
    [Parameter(Mandatory=$true)][string]$Bundle,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $Bundle)) { throw "번들을 찾을 수 없습니다: $Bundle" }

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$MediaDir = Join-Path $ProjectRoot "backend\media"
$EnvFile  = Join-Path $ProjectRoot ".env"

if ((Test-Path $EnvFile) -and -not $Force) {
    throw ".env 가 이미 존재합니다. 덮어쓰려면 -Force 를 붙이십시오."
}

$Tmp = Join-Path $env:TEMP ("restore_" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Tmp | Out-Null

Write-Host "[1/3] 번들 해제 중..."
if ($Bundle.ToLower().EndsWith(".tar")) {
    tar -C $Tmp -xf $Bundle
} else {
    Expand-Archive -Path $Bundle -DestinationPath $Tmp -Force
}

Write-Host "[2/3] backend/media 복원..."
$srcMedia = Join-Path $Tmp "media"
if (Test-Path $srcMedia) {
    if ((Test-Path $MediaDir) -and $Force) { Remove-Item $MediaDir -Recurse -Force }
    if (-not (Test-Path (Split-Path -Parent $MediaDir))) { New-Item -ItemType Directory -Path (Split-Path -Parent $MediaDir) | Out-Null }
    Copy-Item $srcMedia -Destination $MediaDir -Recurse -Force
} else { Write-Warning "번들에 media가 없습니다." }

Write-Host "[3/3] .env 복원..."
$srcEnv = Join-Path $Tmp ".env"
if (Test-Path $srcEnv) { Copy-Item $srcEnv -Destination $EnvFile -Force }
else { Write-Warning "번들에 .env가 없습니다." }

Remove-Item $Tmp -Recurse -Force
Write-Host "복원 완료. 이제 'docker compose up -d' 후 'docker compose exec backend python manage.py ingest_media' 를 실행하십시오." -ForegroundColor Green
