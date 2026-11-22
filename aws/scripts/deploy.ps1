<#
  Deploys the serverless Mycelium API into LocalStack with the AWS CLI only:
  S3 bucket, three DynamoDB tables, an IAM role, the Lambda function and a REST
  API in API Gateway. Safe to run again: it tears down the previous deployment.

    pwsh/powershell:  .\aws\scripts\deploy.ps1
    other endpoint:   .\aws\scripts\deploy.ps1 -Endpoint http://localhost:4566
#>
param(
    [string]$Endpoint = "http://localhost:4566",
    [string]$Region = "us-east-1",
    [string]$Stage = "dev"
)
$ErrorActionPreference = "Stop"
$Origin = Get-Location                             # restored in the finally block below
Set-Location (Join-Path $PSScriptRoot "..")        # -> aws/
$env:AWS_ACCESS_KEY_ID = "test"                    # LocalStack accepts anything
$env:AWS_SECRET_ACCESS_KEY = "test"
$env:AWS_DEFAULT_REGION = $Region
$env:AWS_PAGER = ""

$Function = "mycelium-api"
$Role = "mycelium-lambda-role"
$Bucket = "mycelium-backups"
$Tables = @{ clones = "mycelium-clones"; frames = "mycelium-frames"; backups = "mycelium-backups" }
$Account = "000000000000"

function Invoke-Aws {
    & aws --endpoint-url $Endpoint @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed (exit $LASTEXITCODE)" }
}
function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

try {
# 0. LocalStack must be up ---------------------------------------------------
Step "Waiting for LocalStack at $Endpoint"
$ready = $false
foreach ($i in 1..40) {
    try { Invoke-RestMethod "$Endpoint/_localstack/health" -TimeoutSec 3 | Out-Null; $ready = $true; break }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw "LocalStack is not reachable. Start it: docker compose -f aws/docker-compose.yaml up -d" }

# 1. clean slate -----------------------------------------------------------------
Step "Removing a previous deployment (if any)"
& "$PSScriptRoot\destroy.ps1" -Endpoint $Endpoint -Region $Region -Quiet

# 2. package -----------------------------------------------------------------------
Step "Packaging the function"
python "$PSScriptRoot\package.py"
if ($LASTEXITCODE -ne 0) { throw "packaging failed" }

# 3. S3 + DynamoDB ------------------------------------------------------------------
Step "S3 bucket $Bucket"
Invoke-Aws s3api create-bucket --bucket $Bucket | Out-Null

Step "DynamoDB tables"
Invoke-Aws dynamodb create-table --table-name $Tables.clones `
    --attribute-definitions "AttributeName=clone_id,AttributeType=S" `
    --key-schema "AttributeName=clone_id,KeyType=HASH" `
    --billing-mode PAY_PER_REQUEST | Out-Null
foreach ($pair in @(@($Tables.frames, "frame_id"), @($Tables.backups, "backup_id"))) {
    Invoke-Aws dynamodb create-table --table-name $pair[0] `
        --attribute-definitions "AttributeName=clone_id,AttributeType=S" "AttributeName=$($pair[1]),AttributeType=S" `
        --key-schema "AttributeName=clone_id,KeyType=HASH" "AttributeName=$($pair[1]),KeyType=RANGE" `
        --billing-mode PAY_PER_REQUEST | Out-Null
}
Invoke-Aws dynamodb list-tables --query "TableNames" --output text

# 4. IAM role (LocalStack does not enforce it, but a function needs one) --------------
Step "IAM role $Role"
@'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
'@ | Set-Content -Encoding ascii build/trust.json
Invoke-Aws iam create-role --role-name $Role --assume-role-policy-document file://build/trust.json | Out-Null
$RoleArn = "arn:aws:iam::${Account}:role/$Role"

# 5. Lambda ------------------------------------------------------------------------------
Step "Lambda function $Function"
@{
    Variables = @{
        CLONES_TABLE = $Tables.clones; FRAMES_TABLE = $Tables.frames
        BACKUPS_TABLE = $Tables.backups; BACKUP_BUCKET = $Bucket
        # function containers reach LocalStack by its container name on the shared network
        AWS_ENDPOINT_URL = "http://localstack:4566"
    }
} | ConvertTo-Json -Compress | Set-Content -Encoding ascii build/env.json
Invoke-Aws lambda create-function --function-name $Function --runtime python3.12 `
    --handler handler.lambda_handler --role $RoleArn `
    --zip-file fileb://build/function.zip --environment file://build/env.json `
    --timeout 30 --memory-size 256 | Out-Null

Write-Host "waiting for the function to become Active (the first start pulls the runtime image)..."
$state = ""
foreach ($i in 1..90) {
    $state = (Invoke-Aws lambda get-function --function-name $Function --query "Configuration.State" --output text)
    if ($state -eq "Active") { break }
    if ($state -eq "Failed") { throw "function creation failed" }
    Start-Sleep -Seconds 3
}
if ($state -ne "Active") { throw "function is still '$state' after 270 s" }
$FunctionArn = (Invoke-Aws lambda get-function --function-name $Function --query "Configuration.FunctionArn" --output text)
Write-Host "function active: $FunctionArn"

# 6. API Gateway ----------------------------------------------------------------------------
Step "API Gateway REST API"
$Api = (Invoke-Aws apigateway create-rest-api --name mycelium-api --query id --output text)
$Root = (Invoke-Aws apigateway get-resources --rest-api-id $Api --query "items[?path=='/'].id | [0]" --output text)
$Uri = "arn:aws:apigateway:${Region}:lambda:path/2015-03-31/functions/$FunctionArn/invocations"

# (path, methods): keep in sync with ROUTES in aws/lambda/handler.py
$Routes = @(
    @("/health", @("GET")),
    @("/clones", @("POST")),
    @("/clones/{clone_id}", @("GET", "DELETE")),
    @("/clones/{clone_id}/memories", @("POST")),
    @("/clones/{clone_id}/memories/status", @("GET")),
    @("/clones/{clone_id}/backups", @("GET", "POST")),
    @("/clones/{clone_id}/backups/{backup_id}", @("GET", "DELETE"))
)
$ResourceIds = @{ "/" = $Root }
function Ensure-Resource([string]$Path) {
    if ($ResourceIds.ContainsKey($Path)) { return $ResourceIds[$Path] }
    $parentPath = $Path.Substring(0, $Path.LastIndexOf("/"))
    if ($parentPath -eq "") { $parentPath = "/" }
    $parent = Ensure-Resource $parentPath
    $part = $Path.Substring($Path.LastIndexOf("/") + 1)
    $id = (Invoke-Aws apigateway create-resource --rest-api-id $Api --parent-id $parent --path-part $part --query id --output text)
    $ResourceIds[$Path] = $id
    return $id
}
foreach ($route in $Routes) {
    $path = $route[0]
    $id = Ensure-Resource $path
    foreach ($method in $route[1]) {
        Invoke-Aws apigateway put-method --rest-api-id $Api --resource-id $id --http-method $method --authorization-type NONE | Out-Null
        Invoke-Aws apigateway put-integration --rest-api-id $Api --resource-id $id --http-method $method `
            --type AWS_PROXY --integration-http-method POST --uri $Uri | Out-Null
        Write-Host ("  {0,-6} {1}" -f $method, $path)
    }
}
Invoke-Aws lambda add-permission --function-name $Function --statement-id apigw-invoke `
    --action lambda:InvokeFunction --principal apigateway.amazonaws.com | Out-Null
Invoke-Aws apigateway create-deployment --rest-api-id $Api --stage-name $Stage | Out-Null

# 7. result -----------------------------------------------------------------------------------
$ApiUrl = "$Endpoint/restapis/$Api/$Stage/_user_request_"
$ApiUrl | Set-Content -Encoding ascii build/api-url.txt
Write-Host "`nDeployed." -ForegroundColor Green
Write-Host "API_URL = $ApiUrl"
Write-Host "Smoke test:  .\aws\scripts\smoke.ps1"
} finally {
    Set-Location $Origin   # a script changes the caller's directory otherwise
}
