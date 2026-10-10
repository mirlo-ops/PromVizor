<#
.SYNOPSIS
    Поднимает «ПромВизор» одной командой: .env, .venv, зависимости, сервер.

.EXAMPLE
    .\run.ps1
    Полная установка (включая ultralytics ~554 МБ), сервер в текущем окне.

.EXAMPLE
    .\run.ps1 -Light
    Без ultralytics — на ~554 МБ меньше. Синтетика, Dashboard и все
    тесты работают; YOLO поднимется только при реальной детекции человека.

.EXAMPLE
    .\run.ps1 -Seed
    Поднимает сервер в фоне, наполняет базу демо-данными и оставляет
    его работать. Печатает PID и команду остановки.

.EXAMPLE
    .\run.ps1 -Port 8080
    Другой порт. Перекрывает API_PORT из .env.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\run.ps1
    Если PowerShell блокирует запуск .ps1.

.NOTES
    Скрипт работает из любого каталога: он переходит в корень проекта.
    Это важно — .env и promvizor.db читаются по относительным путям,
    и запуск из другой папки их молча не нашёл бы.
#>
[CmdletBinding()]
param(
    # Без ultralytics: экономит ~554 МБ (не тянет torch)
    [switch]$Light,

    # Поднять в фоне, наполнить демо-данными, оставить работать
    [switch]$Seed,

    # Не трогать зависимости (venv должен уже существовать)
    [switch]$NoSetup,

    # Перекрыть порт из .env
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$Py      = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$LogOut  = Join-Path $PSScriptRoot 'server.log'
$LogErr  = Join-Path $PSScriptRoot 'server.err.log'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    $text"       -ForegroundColor DarkGray }
function Warn($text) { Write-Host "    $text"       -ForegroundColor Yellow }

# --------------------------------------------------------------------------- #
# Обертка для запуска python
# --------------------------------------------------------------------------- #
# При $ErrorActionPreference = 'Stop' PowerShell считает ЛЮБОЙ вывод
# в stderr нативной команды terminating error. Python штатно пишет
# туда traceback при неудачном импорте и предупреждения при установке
# пакета — и скрипт падал, не успев ничего сделать.
#
# Здесь stderr уводится в отдельный поток, а успех определяется по
# коду возврата: он единственный показателен и не зависит от того,
# что Python решил вывести.
function Invoke-Python {
    param(
        # Бросить исключение, если python вернул не нулевой код.
        # Проверять $LASTEXITCODE после вызова функции нельзя: между
        # вызовом и проверкой успевает отработать что-то ещё.
        [switch]$FailOnError,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Py @Arguments 2>&1
        $code   = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }

    # Ошибки наружу не отдаём: их либо не было, либо скажут о ней
    # другие шаги скрипта — с понятным для человека текстом.
    foreach ($line in $output) {
        if ($line -is [System.Management.Automation.ErrorRecord]) {
            Write-Host "    $($line.Exception.Message)" -ForegroundColor DarkGray
        }
    }

    if ($code -ne 0) {
        if ($FailOnError) { throw "python вернул код $code" }
        return ''
    }
    return ($output | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
}

# --------------------------------------------------------------------------- #
# 1. Настройки
# --------------------------------------------------------------------------- #
Step 'Настройки (.env)'

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Ok 'создан .env из .env.example'
    Warn 'проверьте COST_PER_MINUTE и RTSP_URL под свою площадку'
}
else {
    Ok '.env найден'
}

# --------------------------------------------------------------------------- #
# 2. Окружение
# --------------------------------------------------------------------------- #
function Find-Python {
    foreach ($name in @('py', 'python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw 'Python не найден в PATH. Установите Python 3.10+ и повторите.'
}

if (-not (Test-Path -LiteralPath $Py)) {
    Step 'Окружение .venv'
    $base = Find-Python
    Ok "создаётся через $base"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $base -m venv (Join-Path $PSScriptRoot '.venv') 2>&1 | Out-Null
    $ErrorActionPreference = $previous
    if (-not (Test-Path -LiteralPath $Py)) {
        throw 'venv создан, но .venv\Scripts\python.exe не найден'
    }
    Ok 'готово'
}
else {
    Ok '.venv найден'
}

# --------------------------------------------------------------------------- #
# 3. Зависимости
# --------------------------------------------------------------------------- #
if ($NoSetup) {
    Step 'Зависимости'
    Warn '-NoSetup: установка пропущена'
}
else {
    Step 'Зависимости'

    # pip-имя → имя модуля: они совпадают не у всех пакетов.
    $packages = @(
        @{ Pip = 'fastapi';                Module = 'fastapi' }
        @{ Pip = 'uvicorn';                Module = 'uvicorn' }
        @{ Pip = 'opencv-python-headless'; Module = 'cv2' }
        @{ Pip = 'numpy';                  Module = 'numpy' }
    )
    if (-not $Light) {
        $packages += @{ Pip = 'ultralytics'; Module = 'ultralytics' }
    }

    # Список установленных пакетов берём у самого pip — в JSON.
    # Раньше здесь был инлайновый код на python -c, и он ломался:
    # PowerShell вырезает двойные кавычки из аргумента нативного
    # процесса, и код превращался в print(ok: + ,.join(...)) —
    # SyntaxError вместо результата.
    $raw = @(Invoke-Python '-m' 'pip' 'list' '--format=json' '--disable-pip-version-check')
    $json = ($raw -join "`n").Trim()

    if (-not $json) {
        throw 'pip не вернул список пакетов — зависимости не проверены'
    }

    try {
        $installed = ConvertFrom-Json -InputObject $json
    }
    catch {
        throw "не удалось разобрать ответ pip: $($_.Exception.Message)"
    }

    # pip пишет имя как в PyPI: через дефис. Регистр не важен.
    $names = @($installed | ForEach-Object { "$($_.name)".ToLower() })

    $missing = @()
    foreach ($package in $packages) {
        if ($names -notcontains "$($package.Pip)".ToLower()) {
            $missing += $package.Pip
        }
    }
    $missingCount = $missing.Count

    if ($missingCount -eq 0) {
        Ok 'всё на месте'
    }
    else {
        Warn "не хватает: $($missing -join ', ')"
        Ok 'устанавливается, это может занять несколько минут'
        Invoke-Python -FailOnError '-m' 'pip' 'install' '--disable-pip-version-check' '--quiet' @missing | Out-Null
        Ok 'установлено'
    }

    if (-not $Light) {
        Ok 'режим полный: YOLO доступен'
    }
    else {
        Ok 'режим -Light: без ultralytics'
    }
}

# --------------------------------------------------------------------------- #
# 4. Адрес и порт
# --------------------------------------------------------------------------- #
# Берём те же настройки, что и сам сервер: переменная окружения
# важнее .env, .env важнее умолчания. Иначе скрипт открыл бы один адрес,
# а сервер поднялся бы на другом.
Step 'Адрес'

if ($Port -gt 0) {
    $env:API_PORT = "$Port"
    Ok "порт перекрыт аргументом: $Port"
}

$settings = @(Invoke-Python '-c' 'from app.core.config import settings; print(settings.api_host); print(settings.api_port)')
if ($settings.Count -lt 2) {
    throw 'не удалось прочитать настройки из app.core.config'
}
$ApiHost = "$($settings[0])".Trim()
$ApiPort = "$($settings[1])".Trim()

# 0.0.0.0 — это «слушать на всех интерфейсах», в браузере так не открыть.
$BrowserHost = if ($ApiHost -in @('0.0.0.0', '::', '')) { '127.0.0.1' } else { $ApiHost }
$BaseUrl = "http://${BrowserHost}:${ApiPort}"

Ok "слушает: ${ApiHost}:${ApiPort}"
Ok "открывать: $BaseUrl/"

# --------------------------------------------------------------------------- #
# 5. Запуск
# --------------------------------------------------------------------------- #
if ($Seed) {
    Step 'Запуск в фоне'

    $process = Start-Process -FilePath $Py `
                             -ArgumentList '-m', 'app.api.main' `
                             -WorkingDirectory $PSScriptRoot `
                             -RedirectStandardOutput $LogOut `
                             -RedirectStandardError  $LogErr `
                             -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) {
            throw "Сервер упал сразу. Смотрите $LogErr"
        }
        try {
            $null = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2
            $ready = $true
            break
        }
        catch {
            # сервер ещё поднимается — пробуем снова
        }
    }

    if (-not $ready) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw "Сервер не ответил за 30 секунд. Смотрите $LogErr"
    }
    Ok 'сервер отвечает'

    Step 'Демо-данные'
    # Наполнение базы — только по явному запросу: молча подсовывать
    # выдуманные суммы нельзя, на экране их приняли бы за реальные.
    $null = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/demo/seed?days_back=7"
    Ok 'база наполнена за 7 дней'

    Write-Host ''
    Write-Host "  Dashboard:      $BaseUrl/"        -ForegroundColor Green
    Write-Host "  Документация:   $BaseUrl/docs"    -ForegroundColor Green
    Write-Host "  Логи:           $LogOut"          -ForegroundColor DarkGray
    Write-Host "  Логи (ошибки):  $LogErr"          -ForegroundColor DarkGray
    Write-Host ''
    Write-Host "  Остановить: Stop-Process -Id $($process.Id)" -ForegroundColor Yellow
    Write-Host ''
}
else {
    Write-Host ''
    Write-Host "  Dashboard:      $BaseUrl/"      -ForegroundColor Green
    Write-Host "  Документация:   $BaseUrl/docs"  -ForegroundColor Green
    Write-Host ''
    Write-Host '  Остановить: Ctrl+C'              -ForegroundColor DarkGray
    Write-Host ''

    # Сервер пишет в stderr логи uvicorn — это нормальный вывод,
    # а не сбой, поэтому здесь stderr НЕ глушится и ошибкой не считается.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Py -m app.api.main
    $ErrorActionPreference = $previous
}