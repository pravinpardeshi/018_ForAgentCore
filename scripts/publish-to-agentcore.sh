#!/usr/bin/env bash
#
# Uploads and deploys this Knowledge Agent to AWS Bedrock AgentCore.
#
# Modes:
#   code       Direct code deploy (default, no Docker needed).
#              Runs `agentcore configure` + `agentcore deploy`, then a smoke invoke.
#   container  Builds the Docker image and pushes it to Amazon ECR, then prints
#              the image URI and the next step to point an AgentCore Runtime at it.
#
# Usage:
#   ./scripts/publish-to-agentcore.sh [options]
#   ./scripts/publish-to-agentcore.sh --mode container --ecr-repo snow-ka --region us-east-1
#   ./scripts/publish-to-agentcore.sh --profile my-sso-profile --skip-smoke-test
#
# Options:
#   --mode MODE            code|container (default: code)
#   --region REGION        AWS region (default: us-east-1)
#   --profile PROFILE      AWS named profile (optional)
#   --entrypoint FILE      Agent entrypoint for `agentcore configure` (default: main.py)
#   --ecr-repo NAME        ECR repository (container mode, default: snow-ka)
#   --image-tag TAG        Docker image tag (container mode, default: latest)
#   --smoke-prompt TEXT    Prompt for the post-deploy smoke invoke
#   --skip-smoke-test      Skip the post-deploy smoke invoke
#   -h, --help             Show this help
#
# Requires AWS credentials (env, shared config, or --profile). Never commit
# secrets; set DB/Bedrock values as Runtime environment variables after deploy.

set -euo pipefail

MODE="code"
REGION="us-east-1"
PROFILE=""
ENTRYPOINT="main.py"
ECR_REPO="snow-ka"
IMAGE_TAG="latest"
SMOKE_PROMPT="VPN error 809 on Windows 11, how was it fixed before?"
SKIP_SMOKE_TEST=0

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --mode)         MODE="$2"; shift 2 ;;
        --region)       REGION="$2"; shift 2 ;;
        --profile)      PROFILE="$2"; shift 2 ;;
        --entrypoint)   ENTRYPOINT="$2"; shift 2 ;;
        --ecr-repo)     ECR_REPO="$2"; shift 2 ;;
        --image-tag)    IMAGE_TAG="$2"; shift 2 ;;
        --smoke-prompt) SMOKE_PROMPT="$2"; shift 2 ;;
        --skip-smoke-test) SKIP_SMOKE_TEST=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

if [ "$MODE" != "code" ] && [ "$MODE" != "container" ]; then
    echo "Error: --mode must be 'code' or 'container' (got '$MODE')." >&2
    exit 2
fi

# --- helpers ---------------------------------------------------------------
step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32mOK: %s\033[0m\n' "$1"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$1" >&2; exit 1; }

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        die "Required tool '$1' was not found on PATH. $2"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AWS_ARGS=()
if [ -n "$PROFILE" ]; then
    export AWS_PROFILE="$PROFILE"
    AWS_ARGS+=(--profile "$PROFILE")
fi
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

# --- prerequisites ----------------------------------------------------------
step "Checking prerequisites (mode: $MODE)..."
need_cmd aws "Install AWS CLI v2: https://aws.amazon.com/cli/"
if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    die "Python 3.10+ was not found. See https://www.python.org/downloads/"
fi
if [ "$MODE" = "code" ]; then
    need_cmd agentcore "Install the AgentCore CLI: npm install -g @aws/agentcore"
else
    need_cmd docker "Install Docker: https://docs.docker.com/engine/install/"
    [ -f "$REPO_ROOT/Dockerfile" ] || die "Dockerfile not found in $REPO_ROOT."
fi
[ -f "$REPO_ROOT/$ENTRYPOINT" ] || die "Entrypoint file not found: $REPO_ROOT/$ENTRYPOINT"
ok "All required tools found."

# --- AWS identity -----------------------------------------------------------
step "Verifying AWS identity (region $REGION)..."
IDENTITY_ARN="$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query Arn --output text)" \
    || die "aws sts get-caller-identity failed. Check credentials/profile/region."
ok "Authenticated as $IDENTITY_ARN"

if [ ! -f "$REPO_ROOT/.env" ]; then
    echo "WARNING: .env not found. Copy .env.example to .env for local runs. After deploy, set DB/Bedrock values as Runtime environment variables (never bake secrets into the image)." >&2
fi

cd "$REPO_ROOT"

if [ "$MODE" = "code" ]; then
    # --- direct code deploy ------------------------------------------------
    step "Configuring AgentCore (entrypoint: $ENTRYPOINT)..."
    agentcore configure --entrypoint "$ENTRYPOINT" --non-interactive
    step "Deploying to AgentCore Runtime (this uploads your code)..."
    agentcore deploy
    ok "Deploy finished."

    if [ "$SKIP_SMOKE_TEST" -eq 0 ]; then
        step "Smoke-testing the deployed agent..."
        PAYLOAD="$("$PY" -c 'import json,sys; print(json.dumps({"prompt": sys.argv[1]}))' "$SMOKE_PROMPT")"
        agentcore invoke "$PAYLOAD"
        ok "Smoke invoke completed."
    fi

    echo ""
    echo "Done. Next steps:"
    echo "  1. Set DB/Bedrock env vars (DB_SECRET_ARN, BEDROCK_MODEL_ID, ...) on the Runtime."
    echo "  2. Ensure the Runtime can reach PostgreSQL (security groups / VPC)."
    echo "  3. Smoke-test the cloud Runtime any time with: agentcore invoke '{\"prompt\":\"...\"}'."
else
    # --- container: build + push to ECR ------------------------------------
    ACCOUNT_ID="$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query Account --output text | tr -d '[:space:]')" \
        || die "Could not determine AWS account id."
    REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
    REMOTE_TAG="$REGISTRY/$ECR_REPO:$IMAGE_TAG"

    step "Ensuring ECR repository '$ECR_REPO' exists..."
    if aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" "${AWS_ARGS[@]}" >/dev/null 2>&1; then
        ok "ECR repository '$ECR_REPO' already exists."
    else
        aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" "${AWS_ARGS[@]}" >/dev/null \
            || die "aws ecr create-repository failed."
        ok "Created ECR repository '$ECR_REPO'."
    fi

    step "Logging Docker in to ECR..."
    aws ecr get-login-password --region "$REGION" "${AWS_ARGS[@]}" \
        | docker login --username AWS --password-stdin "$REGISTRY" \
        || die "docker login failed."

    step "Building image $ECR_REPO:$IMAGE_TAG ..."
    docker build -t "$ECR_REPO:$IMAGE_TAG" . || die "docker build failed."

    step "Pushing $REMOTE_TAG ..."
    docker tag "$ECR_REPO:$IMAGE_TAG" "$REMOTE_TAG" || die "docker tag failed."
    docker push "$REMOTE_TAG" || die "docker push failed."
    ok "Image pushed: $REMOTE_TAG"

    echo ""
    echo "Done. Next step:"
    echo "  Point your AgentCore Runtime at containerUri:"
    echo "    $REMOTE_TAG"
    echo "  Then set DB/Bedrock env vars on the Runtime and open DB access (SGs / VPC)."
fi
