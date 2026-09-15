<#
.SYNOPSIS
  Git이 나르지 않는 운영 데이터(PostgreSQL + .env)를 압축 번들로 백업합니다.

.DESCRIPTION
  이 서비스에서 지켜야 할 것은 DB입니다.

    · 배치안·사업 (windsite_plan, windsite_project)
    · 검토 이력 (windsite_history)
    · 재현바람장 표본 (windsite_rawwind) — 좌표·고도당 수집에 약 1시간
    · 조례·법령·재결례 수집분 (windsite_ordinance, windsite_law*, windsite_korec*)

  특히 재현바람장은 다시 받으려면 사업 하나에 여러 시간이 걸립니다.

  공간데이터 원본(backend/data/의 대용량)은 배포처에서 재수급 가능하므로
  번들에 넣지 않습니다. 받는 법은 각 디렉터리의 README를 보십시오.

.EXAMPLE
  .\scripts\backup_data.ps1
#>

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Stamp       = Get-Date -Format 'yyyyMMdd_HHmmss'
$OutDir      = Join-Path (Split-Path -Parent $ProjectRoot) '260618_backup'
$Staging     = Join-Path $env:TEMP "260618_backup_$Stamp"
$Bundle      = Join-Path $OutDir "260618_data_$Stamp.tar"

New-Item -ItemType Directory -Force -Path $OutDir, $Staging | Out-Null

Write-Host "[1/3] PostgreSQL 덤프 중..."
$DbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { 're_user' }
$DbName = if ($env:POSTGRES_DB)   { $env:POSTGRES_DB }   else { 're_agent' }
$DumpPath = Join-Path $Staging 'postgres.dump'

# -Fc(커스텀 포맷)로 받아 pg_restore가 선택 복원할 수 있게 한다.
docker compose exec -T postgres pg_dump -U $DbUser -d $DbName -Fc |
    Set-Content -Path $DumpPath -Encoding Byte
if ($LASTEXITCODE -ne 0) { throw "pg_dump 실패 (컨테이너가 떠 있는지 확인하십시오)" }
Write-Host ("      덤프 크기: {0:N1} MB" -f ((Get-Item $DumpPath).Length / 1MB))

Write-Host "[2/3] .env 복사 중..."
$EnvFile = Join-Path $ProjectRoot '.env'
if (Test-Path $EnvFile) {
    Copy-Item $EnvFile -Destination (Join-Path $Staging '.env')
} else {
    Write-Warning ".env가 없습니다: $EnvFile"
}

Write-Host "[3/3] 압축 중..."
tar -cf $Bundle -C $Staging .
Remove-Item $Staging -Recurse -Force

Write-Host ""
Write-Host "완료: $Bundle" -ForegroundColor Green
Write-Host "⚠️ 번들에 .env(비밀 키)가 들어 있습니다. 안전한 채널로만 전달하십시오." -ForegroundColor Yellow
