#!/usr/bin/env bash
# Build the dashboard and publish it to the private S3 bucket behind CloudFront.
#
#   scripts/deploy_dashboard.sh [--stack NAME] [--region REGION] [--dry-run]
#
# Needs: the stack deployed with the Phase 7 template (sam build && sam deploy), the AWS CLI
# logged in, and Node.js. Steps: read the stack outputs, build, write config.json (API address and
# key) into dashboard/dist/ (never into the source tree), upload, clear the CloudFront cache.
#
# --dry-run builds and prepares everything but uploads nothing and invalidates nothing.
#
# The API key ends up in config.json, which every visitor of the site can read. That is how a
# browser app works: the key limits and meters use, it is not a login. Do not reuse it for
# anything else. (Phase 8 and later can move to real sign-in.)

set -euo pipefail

STACK="${STACK_NAME:-gradeflow}"
REGION="${AWS_REGION:-ap-south-1}"
DRY_RUN=0

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
}

fail() {
  echo "error: $*" >&2
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --stack) [ $# -ge 2 ] || fail "--stack needs a value"; STACK="$2"; shift 2 ;;
    --region) [ $# -ge 2 ] || fail "--region needs a value"; REGION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "unknown option: $1 (try --help)" ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASHBOARD_DIR="${DASHBOARD_DIR:-$REPO_ROOT/dashboard}"

command -v aws > /dev/null || fail "the AWS CLI is not installed (see the README's AWS setup)."
command -v npm > /dev/null || fail "Node.js (npm) is not installed."
[ -f "$DASHBOARD_DIR/package.json" ] || fail "no dashboard at $DASHBOARD_DIR"

# --- 1. what was deployed ------------------------------------------------------------------

stack_output() {
  local value
  value="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text)" \
    || fail "could not read stack '$STACK' in $REGION (is it deployed, and are you logged in?)"
  # "text" output prints None (or nothing) when the output does not exist
  if [ -z "$value" ] || [ "$value" = "None" ]; then
    fail "stack '$STACK' has no output '$1'. Deploy the Phase 7 template first: sam build && sam deploy"
  fi
  printf '%s' "$value"
}

echo "Reading the outputs of stack '$STACK' ($REGION)..."
API_URL="$(stack_output ApiUrl)"
BUCKET="$(stack_output DashboardBucketName)"
DISTRIBUTION="$(stack_output DashboardDistributionId)"
SITE_URL="$(stack_output DashboardUrl)"
API_KEY_ID="$(stack_output ApiKeyId)"

API_KEY="$(aws apigateway get-api-key --api-key "$API_KEY_ID" --include-value --region "$REGION" \
  --query value --output text)" || fail "could not read the API key"
[ -n "$API_KEY" ] && [ "$API_KEY" != "None" ] || fail "the API key has no value"

# The values go into a JSON file by plain text substitution, so refuse anything that could
# break out of a JSON string rather than escape it.
API_URL="${API_URL%/}"
case "$API_URL" in https://*) ;; *) fail "the API address is not an https:// URL" ;; esac
case "$API_URL$API_KEY" in
  *[\"\\\ ]* | *$'\n'*) fail "the API address or key contains a character that is not allowed" ;;
esac

# --- 2. build ------------------------------------------------------------------------------

cd "$DASHBOARD_DIR"
[ -d node_modules ] || npm ci
echo "Running the dashboard tests..."
npm test
echo "Building..."
npm run build
[ -f dist/index.html ] || fail "the build produced no dist/index.html"

# --- 3. config.json: written next to the build, never into dashboard/public --------------------

( umask 077 && printf '{\n  "apiUrl": "%s",\n  "apiKey": "%s"\n}\n' "$API_URL" "$API_KEY" > dist/config.json )

# --- 4. upload -----------------------------------------------------------------------------

if [ "$DRY_RUN" -eq 1 ]; then
  echo
  echo "Dry run: built dashboard/dist (with config.json) and stopped before uploading."
  echo "It would upload to s3://$BUCKET and clear the cache of distribution $DISTRIBUTION."
  exit 0
fi

echo "Uploading to s3://$BUCKET ..."
# Order matters: files first, then the page that refers to them, so a visitor never gets an
# index.html that points at files that are not there yet.
# Hashed files never change, so browsers may keep them for a year; the page and config must
# always be re-checked.
if [ -d dist/assets ]; then
  aws s3 sync dist/assets "s3://$BUCKET/assets" --delete --region "$REGION" \
    --cache-control "public,max-age=31536000,immutable"
fi
aws s3 cp dist/config.json "s3://$BUCKET/config.json" --region "$REGION" \
  --cache-control "no-store" --content-type "application/json"
aws s3 cp dist/index.html "s3://$BUCKET/index.html" --region "$REGION" \
  --cache-control "no-cache" --content-type "text/html; charset=utf-8"

echo "Clearing the CloudFront cache for the page..."
aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION" --region "$REGION" \
  --paths "/index.html" "/" > /dev/null

echo
echo "Done. Open: $SITE_URL"
echo "(Note: the page can take a minute to update. The API key in config.json is visible to visitors.)"
