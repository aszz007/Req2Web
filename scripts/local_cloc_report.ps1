[CmdletBinding()]
param(
    [switch]$PerFile
)

<#
.SYNOPSIS
    Print categorized cloc statistics for tracked source files.

.DESCRIPTION
    This local-only helper uses the pinned cloc v2.08 Windows executable. It
    verifies the repository-local cache on every run. If the cache is absent,
    it downloads only the fixed v2.08 release URL, verifies the expected
    SHA-256, and atomically publishes the verified executable to the cache.

    Only safely filtered Git-tracked source paths are passed to cloc. The
    default output contains category, language, and total summary tables.

.PARAMETER PerFile
    Also print one detail row per file after the summary tables.

.NOTES
    First use requires network access to the fixed GitHub release URL. The
    verified executable remains in .local-tools/cloc/v2.08 and is not added to
    Git by this script.
#>

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$repoRootPrefix = $repoRoot.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar

$clocVersion = '2.08'
$clocDownloadUrl = 'https://github.com/AlDanial/cloc/releases/download/v2.08/cloc-2.08.exe'
$clocExpectedSha256 = '4529557d957ade0dd45746eae10e9c51ee01061bb617eeeab256672faf6e42c6'
$clocCacheDirectory = Join-Path $repoRoot '.local-tools\cloc\v2.08'
$clocCachePath = Join-Path $clocCacheDirectory 'cloc-2.08.exe'

$temporaryList = $null
$temporaryReport = $null
$downloadStaging = $null
$locationPushed = $false

# These are source-like extensions. Documentation, datasets, and arbitrary
# configuration/data files are deliberately not treated as code inputs.
$codeExtensions = @(
    '.py', '.pyw', '.ps1', '.psm1', '.psd1',
    '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx',
    '.html', '.htm', '.css', '.scss', '.sass', '.less', '.vue',
    '.c', '.h', '.cc', '.cpp', '.cxx', '.hpp',
    '.cs', '.go', '.rs', '.java', '.kt', '.kts',
    '.rb', '.php', '.swift', '.sh', '.bash', '.zsh',
    '.bat', '.cmd', '.sql', '.lua', '.r'
)

$protectedPaths = @(
    'src/req2web_evaluation/__init__.py',
    'docs/stage3_m3_autodl_a1_seccomp_profile.md',
    'docs/stage3_d17_seccomp_repository_execution_amendment.md',
    'src/req2web_runtime/autodl_a1_seccomp.py',
    'tests/test_m3_autodl_a1_seccomp.py'
)

function Get-GitTrackedPaths {
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    # Read NUL-delimited names directly from the child process so embedded
    # whitespace is not changed by PowerShell's native pipeline formatting.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = 'git'
    $startInfo.Arguments = 'ls-files -z --'
    $startInfo.WorkingDirectory = $RepositoryRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $startInfo.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo

    try {
        if (-not $process.Start()) {
            throw 'Unable to start git ls-files.'
        }

        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        $exitCode = $process.ExitCode
    }
    finally {
        $process.Dispose()
    }

    if ($exitCode -ne 0) {
        $detail = $stderr.Trim()
        if ([string]::IsNullOrEmpty($detail)) {
            $detail = "exit code $exitCode"
        }
        throw "git ls-files failed: $detail"
    }

    $nulSeparator = [char[]]@([char]0)
    return @($stdout.Split($nulSeparator, [System.StringSplitOptions]::RemoveEmptyEntries))
}

function ConvertTo-SafeGitRelativePath {
    param([Parameter(Mandatory)][string]$Path)

    if ([string]::IsNullOrEmpty($Path)) {
        throw 'Git returned an empty tracked path.'
    }
    if ($Path.IndexOfAny([char[]]@([char]0, [char]13, [char]10)) -ge 0) {
        throw 'A tracked path contains a line separator and cannot be represented safely in a cloc list file.'
    }
    if ($Path -ne $Path.Trim()) {
        throw 'A tracked path has leading or trailing whitespace and cannot be represented safely.'
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        throw "Git returned an unexpected absolute path: $Path"
    }

    $normalized = $Path.Replace('\', '/')
    if ($normalized.StartsWith('/') -or $normalized.IndexOf(':') -ge 0) {
        throw "Git returned an unsafe repository-relative path: $Path"
    }

    $segments = $normalized.Split('/')
    foreach ($segment in $segments) {
        if ([string]::IsNullOrEmpty($segment) -or $segment -eq '.' -or $segment -eq '..') {
            throw "Git returned an unsafe repository-relative path: $Path"
        }
    }

    $nativeRelative = $normalized.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $nativeRelative))
    if (-not $fullPath.StartsWith($repoRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Tracked path escapes the repository root: $Path"
    }

    return $normalized
}

function Test-IncludedTrackedCodePath {
    param([Parameter(Mandatory)][string]$Path)

    $normalized = $Path.Replace('\', '/')
    $lower = $normalized.ToLowerInvariant()
    $fileName = [System.IO.Path]::GetFileName($normalized)
    $extension = [System.IO.Path]::GetExtension($fileName).ToLowerInvariant()

    if ($protectedPaths -contains $lower) { return $false }
    if ($codeExtensions -notcontains $extension) { return $false }
    if ($lower -match '(^|/)(data|outputs|models?|checkpoints|results?|artifacts?|runs?)(/|$)') { return $false }
    if ($lower -match '(^|/)tmp-[^/]*(/|$)') { return $false }
    if ($lower -match '(^|/)(\.git|\.venv)(/|$)') { return $false }
    if ($lower.EndsWith('.docx')) { return $false }

    return $true
}

function Assert-PinnedClocHash {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Pinned cloc cache is not a regular file: $Path"
    }

    $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $clocExpectedSha256) {
        throw "Pinned cloc SHA-256 mismatch at '$Path'. Expected $clocExpectedSha256 but found $actualHash. The executable will not be run."
    }
}

function ConvertFrom-ClocReportedPath {
    param([Parameter(Mandatory)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
        if (-not $fullPath.StartsWith($repoRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "cloc reported a file outside the repository root: $Path"
        }
        $Path = $fullPath.Substring($repoRootPrefix.Length)
    }

    $normalized = $Path.Replace('\', '/')
    while ($normalized.StartsWith('./')) {
        $normalized = $normalized.Substring(2)
    }

    return ConvertTo-SafeGitRelativePath -Path $normalized
}

function ConvertTo-NonNegativeCount {
    param(
        [Parameter(Mandatory)][object]$Value,
        [Parameter(Mandatory)][string]$Field,
        [Parameter(Mandatory)][string]$Path
    )

    $parsed = [long]0
    $valid = [long]::TryParse(
        [string]$Value,
        [System.Globalization.NumberStyles]::Integer,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )
    if (-not $valid -or $parsed -lt 0) {
        throw "cloc returned an invalid $Field count for '$Path': $Value"
    }

    return $parsed
}

function Get-PathCategory {
    param([Parameter(Mandatory)][string]$Path)

    $lower = $Path.ToLowerInvariant()
    if ($lower.StartsWith('tests/')) { return 'tests' }
    if ($lower.StartsWith('scripts/')) { return 'tooling' }
    if ($lower.StartsWith('src/')) {
        if ($lower -match '(^|[/_.-])(runtime|orchestration|generation|provider|acceptance|agent)([/_.-]|$)') {
            return 'core_runtime'
        }
        return 'core_support'
    }
    return 'other'
}

try {
    $gitPaths = @(Get-GitTrackedPaths -RepositoryRoot $repoRoot)

    $includedPaths = @(
        foreach ($gitPath in $gitPaths) {
            $safePath = ConvertTo-SafeGitRelativePath -Path $gitPath
            if (Test-IncludedTrackedCodePath -Path $safePath) {
                $safePath
            }
        }
    )

    if ($includedPaths.Count -eq 0) {
        Write-Output 'No eligible tracked source files were found; cloc was not started.'
        exit 0
    }

    if (Test-Path -LiteralPath $clocCachePath) {
        Assert-PinnedClocHash -Path $clocCachePath
        Write-Host "Using verified pinned cloc v$clocVersion from: $clocCachePath"
    }
    else {
        if (Test-Path -LiteralPath $clocCacheDirectory) {
            if (-not (Test-Path -LiteralPath $clocCacheDirectory -PathType Container)) {
                throw "Pinned cloc cache directory path is not a directory: $clocCacheDirectory"
            }
        }
        else {
            New-Item -ItemType Directory -Path $clocCacheDirectory -Force | Out-Null
        }

        # This download staging file is the one intentional exception to the
        # system-temp rule: it must share the cache directory so Move-Item can
        # publish the verified bytes atomically on the same filesystem.
        $downloadStaging = Join-Path $clocCacheDirectory (
            '.cloc-2.08.exe.download-' + [guid]::NewGuid().ToString('N') + '.tmp'
        )

        Write-Host "Pinned cloc v$clocVersion is not cached."
        Write-Host "Downloading fixed release: $clocDownloadUrl"
        try {
            Invoke-WebRequest -Uri $clocDownloadUrl -OutFile $downloadStaging -UseBasicParsing | Out-Null
        }
        catch {
            throw "Pinned cloc download failed: $($_.Exception.Message)"
        }

        Assert-PinnedClocHash -Path $downloadStaging
        Move-Item -LiteralPath $downloadStaging -Destination $clocCachePath -ErrorAction Stop
        $downloadStaging = $null
        Assert-PinnedClocHash -Path $clocCachePath
        Write-Host "Downloaded and verified pinned cloc v$clocVersion."
        Write-Host "Cache path: $clocCachePath"
    }

    $temporaryList = (New-TemporaryFile).FullName
    $temporaryReport = Join-Path ([System.IO.Path]::GetTempPath()) (
        'req2web-cloc-' + [guid]::NewGuid().ToString('N') + '.csv'
    )

    # One Git path per line is the cloc --list-file format. Do not enumerate or
    # read any file contents while creating this list.
    [System.IO.File]::WriteAllLines(
        $temporaryList,
        [string[]]$includedPaths,
        [System.Text.UTF8Encoding]::new($false)
    )

    $clocArguments = @(
        '--by-file',
        '--csv',
        '--quiet',
        ('--list-file=' + $temporaryList),
        ('--out=' + $temporaryReport)
    )

    # git ls-files returns repository-relative paths, so cloc must resolve the
    # list from the repository root regardless of the caller's current path.
    Push-Location -LiteralPath $repoRoot
    $locationPushed = $true
    & $clocCachePath @clocArguments
    if ($LASTEXITCODE -ne 0) {
        throw "cloc failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $temporaryReport -PathType Leaf)) {
        throw 'cloc did not create the expected CSV report.'
    }

    $csvRows = @(Import-Csv -LiteralPath $temporaryReport -Encoding UTF8)
    if ($csvRows.Count -eq 0) {
        throw 'cloc returned an empty CSV report.'
    }

    $headers = @($csvRows[0].PSObject.Properties.Name | ForEach-Object { $_.ToLowerInvariant() })
    foreach ($requiredHeader in @('language', 'filename', 'blank', 'comment', 'code')) {
        if ($headers -notcontains $requiredHeader) {
            throw "cloc CSV is missing the required '$requiredHeader' column."
        }
    }

    $expectedPaths = @{}
    foreach ($includedPath in $includedPaths) {
        $expectedPaths[$includedPath.ToLowerInvariant()] = $true
    }
    $seenPaths = @{}

    $fileRecords = @(
        foreach ($row in $csvRows) {
            $language = [string]$row.language
            $reportedPath = [string]$row.filename
            if ($language -ieq 'SUM' -or [string]::IsNullOrEmpty($reportedPath)) {
                continue
            }
            if ([string]::IsNullOrEmpty($language)) {
                throw "cloc returned a file row without a language: $reportedPath"
            }

            $relativePath = ConvertFrom-ClocReportedPath -Path $reportedPath
            $pathKey = $relativePath.ToLowerInvariant()
            if (-not $expectedPaths.ContainsKey($pathKey)) {
                throw "cloc reported a path that was not in the filtered tracked list: $relativePath"
            }
            if ($seenPaths.ContainsKey($pathKey)) {
                throw "cloc returned duplicate rows for: $relativePath"
            }
            $seenPaths[$pathKey] = $true

            [pscustomobject][ordered]@{
                category = Get-PathCategory -Path $relativePath
                language = $language
                path = $relativePath
                blank = ConvertTo-NonNegativeCount -Value $row.blank -Field 'blank' -Path $relativePath
                comment = ConvertTo-NonNegativeCount -Value $row.comment -Field 'comment' -Path $relativePath
                code = ConvertTo-NonNegativeCount -Value $row.code -Field 'code' -Path $relativePath
            }
        }
    )

    if ($fileRecords.Count -eq 0) {
        throw 'cloc did not recognize any file from the filtered tracked source list.'
    }

    $categoryOrder = @('core_runtime', 'core_support', 'tests', 'tooling', 'other')
    $categorySummary = @(
        foreach ($category in $categoryOrder) {
            $rows = @($fileRecords | Where-Object { $_.category -eq $category })
            [pscustomobject][ordered]@{
                category = $category
                files = $rows.Count
                blank = [long](($rows | Measure-Object -Property blank -Sum).Sum)
                comment = [long](($rows | Measure-Object -Property comment -Sum).Sum)
                code = [long](($rows | Measure-Object -Property code -Sum).Sum)
            }
        }
    )

    $languageSummary = @(
        foreach ($group in @($fileRecords | Group-Object -Property language | Sort-Object -Property Name)) {
            $rows = @($group.Group)
            [pscustomobject][ordered]@{
                language = $group.Name
                files = $rows.Count
                blank = [long](($rows | Measure-Object -Property blank -Sum).Sum)
                comment = [long](($rows | Measure-Object -Property comment -Sum).Sum)
                code = [long](($rows | Measure-Object -Property code -Sum).Sum)
            }
        }
    )

    $totalSummary = [pscustomobject][ordered]@{
        scope = 'TOTAL'
        files = $fileRecords.Count
        blank = [long](($fileRecords | Measure-Object -Property blank -Sum).Sum)
        comment = [long](($fileRecords | Measure-Object -Property comment -Sum).Sum)
        code = [long](($fileRecords | Measure-Object -Property code -Sum).Sum)
    }

    Write-Host ''
    Write-Host 'Category summary'
    $categorySummary | Format-Table category, files, blank, comment, code -AutoSize | Out-Host

    Write-Host 'Language summary'
    $languageSummary | Format-Table language, files, blank, comment, code -AutoSize | Out-Host

    Write-Host 'Total'
    $totalSummary | Format-Table scope, files, blank, comment, code -AutoSize | Out-Host

    Write-Host "Eligible tracked source files supplied to cloc: $($includedPaths.Count)"
    Write-Host "Files recognized by cloc: $($fileRecords.Count)"

    if ($PerFile) {
        Write-Host ''
        Write-Host 'Per-file details'
        $fileRecords |
            Sort-Object -Property category, path |
            Format-Table category, language, path, blank, comment, code -AutoSize -Wrap |
            Out-Host
    }
}
catch {
    Write-Error -ErrorRecord $_ -ErrorAction Continue
    exit 1
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($null -ne $temporaryList -and (Test-Path -LiteralPath $temporaryList -PathType Leaf)) {
        Remove-Item -LiteralPath $temporaryList -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $temporaryReport -and (Test-Path -LiteralPath $temporaryReport -PathType Leaf)) {
        Remove-Item -LiteralPath $temporaryReport -Force -ErrorAction SilentlyContinue
    }
    if ($null -ne $downloadStaging -and (Test-Path -LiteralPath $downloadStaging -PathType Leaf)) {
        Remove-Item -LiteralPath $downloadStaging -Force -ErrorAction SilentlyContinue
    }
}
