<#
.SYNOPSIS
  Git이 나르지 않는 운영 데이터(backend/media + .env)를 하나의 압축 번들로 백업합니다.
  이관/재현 시 이 번들을 새 PC로 옮긴 뒤 restore_data.ps1로 복원하십시오.

.PARAMETER OutDir
  번들을 저장할 폴더. 기본값: 프로젝트 상위 폴더의 260618_backup

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\backup_data.ps1
  powershell -ExecutionPolicy Bypass -File scripts\backup_data.ps1 -OutDir D:\handover
#>
param(
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot   # scripts/ 의 부모 = 프로젝트 루트
$MediaDir = Join-Path $ProjectRoot "backend\media"
$EnvFile  = Join-Path $ProjectRoot ".env"

if (-not $OutDir) { $OutDir = Join-Path (Split-Path -Parent $ProjectRoot) "260618_backup" }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Staging = Join-Path $OutDir "_staging_$Stamp"
New-Item -ItemType Directory -Path $Staging | Out-Null

Write-Host "[1/3] 스테이징 준비: $Staging"
if (Test-Path $MediaDir) {
    Copy-Item $MediaDir -Destination (Join-Path $Staging "media") -Recurse
} else { Write-Warning "media 폴더가 없습니다: $MediaDir" }
if (Test-Path $EnvFile) {
    Copy-Item $EnvFile -Destination (Join-Path $Staging ".env")
} else { Write-Warning ".env 파일이 없습니다: $EnvFile" }

$Bundle = Join-Path $OutDir "260618_data_$Stamp.zip"
Write-Host "[2/3] 압축 중 (media는 이미 압축 포맷이라 용량 축소는 적음)..."
# tar가 있으면 tar 사용(대용량에 유리), 없으면 Compress-Archive
$tar = Get-Command tar -ErrorAction SilentlyContinue
if ($tar) {
    $Bundle = Join-Path $OutDir "260618_data_$Stamp.tar"
    tar -C $Staging -cf $Bundle .
} else {
    Compress-Archive -Path (Join-Path $Staging "*") -DestinationPath $Bundle -Force
}

Write-Host "[3/3] 스테이징 정리..."
Remove-Item $Staging -Recurse -Force

$Size = "{0:N1} MB" -f ((Get-Item $Bundle).Length / 1MB)
Write-Host "완료: $Bundle ($Size)" -ForegroundColor Green
Write-Host "이 번들과 GitHub 코드를 함께 넘기면 다른 PC에서 재현 가능합니다."
