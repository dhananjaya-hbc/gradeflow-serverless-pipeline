#!/usr/bin/env bash
# Build and deploy the stack. Run by the CI deploy job (see docs/ci-cd-setup.md); it can also be run by
# hand with the same variables set.
#
# Settings come from the environment (the workflow fills them from GitHub repository variables):
#   AWS_CFN_ROLE_ARN       required   the role CloudFormation uses to build the stack
#   AWS_REGION             optional   default ap-south-1
#   STACK_NAME             optional   default gradeflow
#   ALERT_EMAIL            optional   address for alarm and failure emails (empty = none)
#   API_ALLOWED_ORIGIN     optional   the site allowed to call the API from a browser (default *)
#   DASHBOARD_DEV_ORIGIN   optional   default http://localhost:5173; "none" turns it off
#
# Why these parameters are passed every time: SAM forgets --parameter-overrides between deploys, so a
# deploy that left them out would quietly reset the stack to the template defaults. And SAM rejects a
# bare empty value (AlertEmail=), so every value is passed in quotes (AlertEmail="").

set -euo pipefail

STACK="${STACK_NAME:-gradeflow}"
REGION="${AWS_REGION:-ap-south-1}"
ROLE="${AWS_CFN_ROLE_ARN:-}"
ALERT_EMAIL_VALUE="${ALERT_EMAIL:-}"
ALLOWED_ORIGIN_VALUE="${API_ALLOWED_ORIGIN:-*}"
DEV_ORIGIN_VALUE="${DASHBOARD_DEV_ORIGIN:-http://localhost:5173}"
[ "$DEV_ORIGIN_VALUE" = "none" ] && DEV_ORIGIN_VALUE=""

fail() {
  echo "error: $*" >&2
  exit 1
}

# --- check everything before anything touches AWS -------------------------------------------------

[ -n "$ROLE" ] || fail "AWS_CFN_ROLE_ARN is not set (see docs/ci-cd-setup.md, step 2)"
[[ "$ROLE" =~ ^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$ ]] \
  || fail "AWS_CFN_ROLE_ARN is not an IAM role ARN"
[[ "$STACK" =~ ^[a-z][a-z0-9]*(-[a-z0-9]+)*$ ]] || fail "STACK_NAME is not a valid stack name"
[[ "$REGION" =~ ^[a-z]{2}-[a-z]+-[0-9]$ ]] || fail "AWS_REGION is not a region name"

# Values go into a quoted argument, so refuse anything that could break out of the quotes.
for pair in "ALERT_EMAIL=$ALERT_EMAIL_VALUE" "API_ALLOWED_ORIGIN=$ALLOWED_ORIGIN_VALUE" \
  "DASHBOARD_DEV_ORIGIN=$DEV_ORIGIN_VALUE"; do
  if [[ "${pair#*=}" =~ [[:space:]\"\'\\\`\$] ]]; then
    fail "${pair%%=*} contains a space, a quote, a backslash or a \$: not allowed"
  fi
done
# The same rules the template enforces, so a typo fails here with a clear message, not in CloudFormation.
[[ -z "$ALERT_EMAIL_VALUE" || "$ALERT_EMAIL_VALUE" =~ ^[^@]+@[^@]+$ ]] \
  || fail "ALERT_EMAIL is not an email address"
[[ "$ALLOWED_ORIGIN_VALUE" == "*" || "$ALLOWED_ORIGIN_VALUE" =~ ^https?://[^/*]+$ ]] \
  || fail "API_ALLOWED_ORIGIN must be * or an origin such as https://example.com (no path)"
[[ -z "$DEV_ORIGIN_VALUE" || "$DEV_ORIGIN_VALUE" =~ ^https?://[^/*]+$ ]] \
  || fail "DASHBOARD_DEV_ORIGIN must be an origin such as http://localhost:5173, or none"

masked_email="(none)"
[ -n "$ALERT_EMAIL_VALUE" ] && masked_email="***@${ALERT_EMAIL_VALUE#*@}"
echo "Deploying stack '$STACK' in $REGION"
echo "  AlertEmail=$masked_email  AllowedOrigin=$ALLOWED_ORIGIN_VALUE  DashboardDevOrigin=${DEV_ORIGIN_VALUE:-(off)}"

# --- build and deploy --------------------------------------------------------------------------------

sam build

sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --role-arn "$ROLE" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
  "AlertEmail=\"$ALERT_EMAIL_VALUE\"" \
  "AllowedOrigin=\"$ALLOWED_ORIGIN_VALUE\"" \
  "DashboardDevOrigin=\"$DEV_ORIGIN_VALUE\""
