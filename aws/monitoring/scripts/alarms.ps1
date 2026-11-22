<#
  Creates (or removes) the CloudWatch alarms for the application with the AWS CLI.

    .\aws\monitoring\scripts\alarms.ps1            # create + show
    .\aws\monitoring\scripts\alarms.ps1 -Remove    # delete them

  Two alarms on the custom metrics the instances publish (namespace Mycelium/Api):
    mycelium-errors-high   Errors  Sum     >= 5      in a 30 s period  (5xx responses)
    mycelium-latency-high  Duration Average > 150 ms in a 30 s period
  A 30 s period is valid because the metrics are published with StorageResolution=1
  (high resolution). Missing data counts as "not breaching": no traffic is not an error.
#>
param(
    [string]$Endpoint = "http://localhost:4566",
    [string]$Region = "us-east-1",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:AWS_DEFAULT_REGION = $Region
$env:AWS_PAGER = ""

function Invoke-Aws {
    & aws --endpoint-url $Endpoint @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

$Names = "mycelium-errors-high", "mycelium-latency-high"

if ($Remove) {
    Invoke-Aws cloudwatch delete-alarms --alarm-names $Names
    Write-Host "alarms deleted"
    return
}

Write-Host "==> CloudWatch API reachable?"
Invoke-Aws cloudwatch list-metrics --namespace Mycelium/Api --query "length(Metrics)" --output text

Write-Host "==> mycelium-errors-high"
Invoke-Aws cloudwatch put-metric-alarm `
    --alarm-name mycelium-errors-high `
    --alarm-description "5xx responses: 5 or more in 30 seconds" `
    --namespace Mycelium/Api --metric-name Errors `
    --dimensions "Name=Service,Value=mycelium-api" "Name=Scope,Value=all" `
    --statistic Sum --period 30 --evaluation-periods 1 --datapoints-to-alarm 1 `
    --threshold 5 --comparison-operator GreaterThanOrEqualToThreshold `
    --treat-missing-data notBreaching --unit Count

Write-Host "==> mycelium-latency-high"
Invoke-Aws cloudwatch put-metric-alarm `
    --alarm-name mycelium-latency-high `
    --alarm-description "average handling time above 150 ms over 30 seconds" `
    --namespace Mycelium/Api --metric-name Duration `
    --dimensions "Name=Service,Value=mycelium-api" "Name=Scope,Value=all" `
    --statistic Average --period 30 --evaluation-periods 1 --datapoints-to-alarm 1 `
    --threshold 150 --comparison-operator GreaterThanThreshold `
    --treat-missing-data notBreaching --unit Milliseconds

Write-Host "`n==> alarms"
Invoke-Aws cloudwatch describe-alarms --alarm-names $Names `
    --query "MetricAlarms[].[AlarmName,StateValue,MetricName,Threshold,Period]" --output table
