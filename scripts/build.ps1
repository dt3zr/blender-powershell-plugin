$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$package = Join-Path $root 'src\powershell_console'
$entryPoint = Join-Path $package '__init__.py'
$vendor = Join-Path $package 'powershell_terminal_vendor'
$version = [regex]::Match(
    (Get-Content -LiteralPath $entryPoint -Raw),
    '(?m)^    "version":\s*\((\d+),\s*(\d+),\s*(\d+)\s*\)'
)
if (-not $version.Success) {
    throw "Could not read bl_info version from $entryPoint"
}

$required = @(
    'pyte\__init__.py'
    'wcwidth\__init__.py'
    'winpty\__init__.py'
)
foreach ($file in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $vendor $file) -PathType Leaf)) {
        throw "Missing bundled dependency file $file"
    }
}
foreach ($dependency in @('winpty', 'wcwidth')) {
    if (-not (Get-ChildItem -LiteralPath (Join-Path $vendor $dependency) -Filter '*.pyd' -File)) {
        throw "Missing native library for $dependency"
    }
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$number = '{0}.{1}.{2}' -f $version.Groups[1].Value, $version.Groups[2].Value, $version.Groups[3].Value
$output = Join-Path $root 'output'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$destination = Join-Path $output "powershell_console-$number.zip"
$temporary = Join-Path $output ([guid]::NewGuid().ToString() + '.tmp')

try {
    $archive = [IO.Compression.ZipFile]::Open($temporary, 'Create')
    try {
        $files = Get-ChildItem -LiteralPath $package -Recurse -File |
            Where-Object { $_.Extension -ne '.pyc' -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' } |
            Sort-Object FullName
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($package.Length + 1).Replace('\', '/')
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, $file.FullName, "powershell_console/$relative"
            ) | Out-Null
        }
    }
    finally {
        $archive.Dispose()
    }

    $archive = [IO.Compression.ZipFile]::OpenRead($temporary)
    try {
        if ($archive.Entries.Count -ne @($files).Count -or
            $null -eq $archive.GetEntry('powershell_console/__init__.py')) {
            throw 'The ZIP failed its package layout check'
        }
    }
    finally {
        $archive.Dispose()
    }

    if (Test-Path -LiteralPath $destination) {
        [IO.File]::Replace($temporary, $destination, [NullString]::Value)
    } else {
        [IO.File]::Move($temporary, $destination)
    }
    Write-Output $destination
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary
    }
}
