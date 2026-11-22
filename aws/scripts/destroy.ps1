<#
  Removes everything deploy.ps1 created (idempotent: missing resources are fine).
    .\aws\scripts\destroy.ps1
#>
param(
    [string]$Endpoint = "http://localhost:4566",
    [string]$Region = "us-east-1",
    [switch]$Quiet
)
$ErrorActionPreference = "Continue"                 # a missing resource is not an error here
$env:AWS_ACCESS_KEY_ID = "test"
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:AWS_DEFAULT_REGION = $Region
$env:AWS_PAGER = ""

function Try-Aws {
    & aws --endpoint-url $Endpoint @args 2>$null | Out-Null
}

# REST APIs named mycelium-api (there can be several after failed runs)
$apis = & aws --endpoint-url $Endpoint apigateway get-rest-apis --query "items[?name=='mycelium-api'].id" --output text 2>$null
foreach ($id in ($apis -split "\s+" | Where-Object { $_ })) {
    Try-Aws apigateway delete-rest-api --rest-api-id $id
}
Try-Aws lambda delete-function --function-name mycelium-api
Try-Aws iam delete-role --role-name mycelium-lambda-role
foreach ($table in "mycelium-clones", "mycelium-frames", "mycelium-backups") {
    Try-Aws dynamodb delete-table --table-name $table
}
# a bucket must be empty before it can be removed
Try-Aws s3 rm s3://mycelium-backups --recursive
Try-Aws s3api delete-bucket --bucket mycelium-backups

if (-not $Quiet) { Write-Host "destroyed (LocalStack endpoint $Endpoint)" }
