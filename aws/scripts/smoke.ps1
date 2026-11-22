<#
  End-to-end check of the deployed API with a plain HTTP client: every route,
  the happy path and the error cases. Exit code 1 if anything is off.
    .\aws\scripts\smoke.ps1
    .\aws\scripts\smoke.ps1 -ApiUrl http://localhost:4566/restapis/<id>/dev/_user_request_
#>
param([string]$ApiUrl)
$ErrorActionPreference = "Stop"
if (-not $ApiUrl) { $ApiUrl = (Get-Content (Join-Path $PSScriptRoot "..\build\api-url.txt")).Trim() }
$clone = "smoke-" + (Get-Random -Maximum 99999)
$script:failed = 0

function Call([string]$Method, [string]$Path, $Body = $null) {
    $req = @{ Uri = "$ApiUrl$Path"; Method = $Method; UseBasicParsing = $true; ContentType = "application/json" }
    if ($null -ne $Body) { $req.Body = ($Body | ConvertTo-Json -Compress) }
    try {
        $r = Invoke-WebRequest @req
        $status = [int]$r.StatusCode; $text = $r.Content
    } catch {
        if (-not $_.Exception.Response) { throw }
        $status = [int]$_.Exception.Response.StatusCode
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $text = $reader.ReadToEnd()
    }
    $json = $null
    if ($text) { try { $json = $text | ConvertFrom-Json } catch { } }
    [pscustomobject]@{ Status = $status; Json = $json }
}
function Check([string]$Name, $Got, $Expected) {
    $ok = ($Got -eq $Expected)
    if (-not $ok) { $script:failed++ }
    Write-Host ("{0} {1,-46} got {2} (expected {3})" -f $(if ($ok) { "PASS" } else { "FAIL" }), $Name, $Got, $Expected)
}

Write-Host "API: $ApiUrl`n"
Check "GET /health" (Call GET "/health").Status 200
Check "POST /clones" (Call POST "/clones" @{ designation = $clone; tier = "standard" }).Status 201
Check "POST /clones (duplicate)" (Call POST "/clones" @{ designation = $clone }).Status 409
Check "POST /clones (invalid name)" (Call POST "/clones" @{ designation = "Bad Name" }).Status 422
Check "GET /clones/{id}" (Call GET "/clones/$clone").Status 200
Check "GET /clones/{unknown}" (Call GET "/clones/ghost-$clone").Status 404

foreach ($text in "first frame", "second frame", "third frame") {
    Check "POST /memories ($text)" (Call POST "/clones/$clone/memories" @{ content = $text }).Status 202
}
Check "POST /memories (empty content)" (Call POST "/clones/$clone/memories" @{ content = "" }).Status 422
Check "buffered_frames" (Call GET "/clones/$clone/memories/status").Json.buffered_frames 3

$backup = Call POST "/clones/$clone/backups"
Check "POST /backups (rollup)" $backup.Status 201
Check "backup entry_count" $backup.Json.entry_count 3
Check "buffer drained" (Call GET "/clones/$clone/memories/status").Json.buffered_frames 0
Check "POST /backups (nothing to back up)" (Call POST "/clones/$clone/backups").Status 409

$id = $backup.Json.backup_id
$one = Call GET "/clones/$clone/backups/$id"
Check "GET /backups/{id}" $one.Status 200
Check "payload[0].content (decoded from S3)" $one.Json.payload[0].content "first frame"
Check "GET /backups (list)" (Call GET "/clones/$clone/backups").Status 200
Check "DELETE /backups/{id}" (Call DELETE "/clones/$clone/backups/$id").Status 204
Check "GET /backups/{id} after delete" (Call GET "/clones/$clone/backups/$id").Status 404
Check "DELETE /clones/{id}" (Call DELETE "/clones/$clone").Status 204
Check "GET /clones/{id} after delete" (Call GET "/clones/$clone").Status 404

if ($script:failed) { Write-Host "`n$($script:failed) check(s) failed" -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed" -ForegroundColor Green
