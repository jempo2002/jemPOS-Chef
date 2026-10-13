# Aplica db/schema.sql y todas las migraciones de migrations/ (en orden de
# nombre) a la base MySQL de Railway, desde Windows.
#
#   .\scripts\migrar_railway.ps1
#   .\scripts\migrar_railway.ps1 -Nombre "Tu nombre" -Correo tu@correo.com
#   .\scripts\migrar_railway.ps1 -Demo
#
# Usa la MYSQL_PUBLIC_URL del servicio MySQL en Railway (Variables >
# MYSQL_PUBLIC_URL, la que tiene ...proxy.rlwy.net): la toma de la linea
# MYSQL_PUBLIC_URL=... del .env local si esta, y si no la pide sin mostrarla.
# Con -Nombre y -Correo crea ademas el usuario Master en esa misma base.
# Con -Demo crea o actualiza el restaurante "Demo Chef" (scripts/crear_demo.py)
# con admin, cajero, mesero y cocina @demo.chef; la contrasena la toma de
# $env:DEMO_CLAVE o la pide.
# Las credenciales de Railway solo viven en esta ventana mientras corre.
#
# Se puede correr varias veces: run_migration.py trata como hecho lo que ya
# existe. Para antes de seguir si una migracion falla.

param([string]$Nombre, [string]$Correo, [switch]$Demo)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$url = ""
if (Test-Path .env) {
    $linea = Get-Content .env | Where-Object { $_ -match '^\s*MYSQL_PUBLIC_URL\s*=' } | Select-Object -First 1
    if ($linea) { $url = ($linea -split "=", 2)[1].Trim().Trim('"') }
}
if (-not $url) {
    $segura = Read-Host "Pega la MYSQL_PUBLIC_URL de Railway" -AsSecureString
    $url = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura))
}
$url = $url.Trim()
if (-not $url.StartsWith("mysql://")) {
    throw "Eso no parece una MYSQL_PUBLIC_URL (debe empezar por mysql://)."
}

$uri = [Uri]$url
$usuario, $clave = $uri.UserInfo.Split(":", 2)

$python = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$env:DB_HOST = $uri.Host
$env:DB_PORT = [string]$uri.Port
$env:DB_USER = [Uri]::UnescapeDataString($usuario)
$env:DB_PASSWORD = [Uri]::UnescapeDataString($clave)
$env:DB_NAME = $uri.AbsolutePath.TrimStart("/")

try {
    $archivos = @("db\schema.sql") + (Get-ChildItem migrations\*.sql | Sort-Object Name | ForEach-Object { "migrations\$($_.Name)" })
    foreach ($archivo in $archivos) {
        Write-Host "==> $archivo" -ForegroundColor Cyan
        & $python scripts\run_migration.py $archivo
        if ($LASTEXITCODE -ne 0) { throw "Fallo $archivo. No se aplicaron las siguientes." }
    }
    Write-Host "Listo: base de Railway al dia." -ForegroundColor Green
    if ($Nombre -and $Correo) {
        & $python scripts\crear_master.py $Nombre $Correo
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el usuario Master." }
    }
    if ($Demo) {
        & $python scripts\crear_demo.py
        if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el restaurante demo." }
    }
}
finally {
    Remove-Item Env:DB_HOST, Env:DB_PORT, Env:DB_USER, Env:DB_PASSWORD, Env:DB_NAME, Env:DEMO_CLAVE -ErrorAction SilentlyContinue
}
