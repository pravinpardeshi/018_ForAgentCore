<#
.SYNOPSIS
    Uploads and deploys this Knowledge Agent to AWS Bedrock AgentCore.

.DESCRIPTION
    Two modes:
      Code       Direct code deploy (default, no Docker needed).
                 Runs `agentcore configure` + `agentcore deploy`, then a smoke invoke.
      Container  Builds the Docker image and pushes it to Amazon ECR, then prints
                 the image URI and the next step to point an AgentCore Runtime at it.

    Run from anywhere; paths resolve relative to this script. Requires AWS
    credentials (environment, shared config, or -AwsProfile). Never commit
    secrets; set DB/Bedrock values as Runtime environment variables after
    deploy (see README).

.PARAMETER Mode
    'Code' or 'Container'. Default 'Code'.

.PARAMETER Region
    AWS region for all operations. Default 'us-east-1'.

.PARAMETER AwsProfile
    Optional AWS named profile (exported as AWS_PROFILE for child tools).

.PARAMETER Entrypoint
    Agent entrypoint file for `agentcore configure`. Default 'main.py'.

.PARAMETER EcrRepoName
    ECR repository name (Container mode). Default 'snow-ka'.

.PARAMETER ImageTag
    Docker image tag (Container mode). Default 'latest'.

.PARAMETER SmokePrompt
    Prompt used for the post-deploy smoke invoke (Code mode).

.PARAMETER SkipSmokeTest
    Skip the post-deploy smoke invoke.

.EXAMPLE
    .\scripts\Publish-ToAgentCore.ps1

.EXAMPLE
    .\scripts\Publish-ToAgentCore.ps1 -Mode Container -EcrRepoName snow-ka -Region us-east-1

.EXAMPLE
    .\scripts\Publish-ToAgentCore.ps1 -AwsProfile my-sso-profile -SkipSmokeTest
#>
[CmdletBinding()]
param(
    [ValidateSet('Code', 'Container')]
    [string]$Mode = 'Code',

    [string]$Region = 'us-east-1',

    [string]$AwsProfile = '',

    [string]$Entrypoint = 'main.py',

    [string]$EcrRepoName = 'snow-ka',

    [string]$ImageTag = 'latest',

    [string]$SmokePrompt = 'VPN error 809 on Windows 11, how was it fixed before?',

    [switch]$SkipSmokeTest
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    OK: $Message" -ForegroundColor Green
}

function Assert-Command {
    param([string]$Name, [string]$InstallHint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$Name' was not found on PATH. $InstallHint"
    }
}

function Test-NativeOk {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "'$Action' failed with exit code $LASTEXITCODE."
    }
}

try {
    if ($Region) {
        $env:AWS_REGION = $Region
        $env:AWS_DEFAULT_REGION = $Region
    }
    if ($AwsProfile) {
        $env:AWS_PROFILE = $AwsProfile
    }

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $entrypointPath = Join-Path $repoRoot $Entrypoint

    # ---- Prerequisites -----------------------------------------------------
    Write-Step "Checking prerequisites (mode: $Mode)..."
    Assert-Command 'aws' 'Install AWS CLI v2: https://aws.amazon.com/cli/'
    Assert-Command 'python' 'Install Python 3.10+: https://www.python.org/downloads/'
    if ($Mode -eq 'Code') {
        Assert-Command 'agentcore' 'Install the AgentCore CLI: npm install -g @aws/agentcore'
    }
    else {
        Assert-Command 'docker' 'Install Docker Desktop: https://www.docker.com/products/docker-desktop/'
        if (-not (Test-Path (Join-Path $repoRoot 'Dockerfile') -PathType Leaf)) {
            throw "Dockerfile not found in $repoRoot."
        }
    }
    if (-not (Test-Path $entrypointPath -PathType Leaf)) {
        throw "Entrypoint file not found: $entrypointPath"
    }
    Write-Ok 'All required tools found.'

    # ---- AWS identity ------------------------------------------------------
    Write-Step "Verifying AWS identity (region $Region)..."
    $identityJson = aws sts get-caller-identity --output json
    Test-NativeOk 'aws sts get-caller-identity'
    $identity = $identityJson | ConvertFrom-Json
    Write-Ok "Authenticated as $($identity.Arn)"

    if (-not (Test-Path (Join-Path $repoRoot '.env') -PathType Leaf)) {
        Write-Warning '.env not found. Copy .env.example to .env for local runs. After deploy, set DB/Bedrock values as Runtime environment variables (never bake secrets into the image).'
    }

    Push-Location $repoRoot
    try {
        if ($Mode -eq 'Code') {
            # ---- Direct code deploy ---------------------------------------
            Write-Step "Configuring AgentCore (entrypoint: $Entrypoint)..."
            agentcore configure --entrypoint $Entrypoint --non-interactive
            Test-NativeOk 'agentcore configure'

            Write-Step 'Deploying to AgentCore Runtime (this uploads your code)...'
            agentcore deploy
            Test-NativeOk 'agentcore deploy'
            Write-Ok 'Deploy finished.'

            if (-not $SkipSmokeTest) {
                Write-Step 'Smoke-testing the deployed agent...'
                $payload = @{ prompt = $SmokePrompt } | ConvertTo-Json -Compress
                agentcore invoke $payload
                Test-NativeOk 'agentcore invoke'
                Write-Ok 'Smoke invoke completed.'
            }

            Write-Host ''
            Write-Host 'Done. Next steps:' -ForegroundColor Green
            Write-Host '  1. Set DB/Bedrock env vars (DB_SECRET_ARN, BEDROCK_MODEL_ID, ...) on the Runtime.'
            Write-Host '  2. Ensure the Runtime can reach PostgreSQL (security groups / VPC).'
            Write-Host '  3. Smoke-test the cloud Runtime any time with: agentcore invoke ''{"prompt":"..."}''.'
        }
        else {
            # ---- Container: build + push to ECR ---------------------------
            $accountId = (aws sts get-caller-identity --query Account --output text).Trim()
            Test-NativeOk 'aws sts get-caller-identity'
            $registry = "$accountId.dkr.ecr.$Region.amazonaws.com"
            $remoteTag = "$registry/${EcrRepoName}:$ImageTag"

            Write-Step "Ensuring ECR repository '$EcrRepoName' exists..."
            aws ecr describe-repositories --repository-names $EcrRepoName --region $Region | Out-Null
            if ($LASTEXITCODE -ne 0) {
                aws ecr create-repository --repository-name $EcrRepoName --region $Region | Out-Null
                Test-NativeOk 'aws ecr create-repository'
                Write-Ok "Created ECR repository '$EcrRepoName'."
            }
            else {
                Write-Ok "ECR repository '$EcrRepoName' already exists."
            }

            Write-Step 'Logging Docker in to ECR...'
            aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry
            Test-NativeOk 'docker login'

            Write-Step "Building image ${EcrRepoName}:$ImageTag ..."
            docker build -t "${EcrRepoName}:$ImageTag" .
            Test-NativeOk 'docker build'

            Write-Step "Pushing $remoteTag ..."
            docker tag "${EcrRepoName}:$ImageTag" $remoteTag
            Test-NativeOk 'docker tag'
            docker push $remoteTag
            Test-NativeOk 'docker push'
            Write-Ok "Image pushed: $remoteTag"

            Write-Host ''
            Write-Host 'Done. Next step:' -ForegroundColor Green
            Write-Host "  Point your AgentCore Runtime at containerUri:"
            Write-Host "    $remoteTag"
            Write-Host '  Then set DB/Bedrock env vars on the Runtime and open DB access (SGs / VPC).'
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Host ''
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
