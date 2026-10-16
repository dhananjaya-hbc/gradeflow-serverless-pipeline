# CI/CD setup (one time)

GitHub Actions checks every push and, from `main`, deploys the stack. It logs in to AWS with **OIDC**:
GitHub proves who it is for one run, AWS hands back temporary credentials, and no access key is stored
anywhere. This page is the one-time setup. You do it with your normal AWS administrator login; the
pipeline cannot do it for itself, because it creates IAM roles.

## What gets created

`deploy/github-oidc-role.yaml` is a small separate CloudFormation stack with two roles and no users or keys:

| Role | Used by | May do |
|---|---|---|
| `ci-gradeflow-deploy` | the GitHub workflow | run CloudFormation on the `gradeflow` stack, upload the build, push the container image, publish the dashboard files, clear the CDN cache, read the API key, and **hand the second role to CloudFormation**. Nothing else. |
| `ci-gradeflow-cfn-exec` | CloudFormation | create, change and delete the stack's resources (Lambda, S3, DynamoDB, SQS, SNS, API Gateway, CloudFront, IAM roles named `gradeflow-*`, and so on), each limited to the stack's names. It cannot read or write the application's data. |

Why two roles: a stolen GitHub token can then only start a CloudFormation change, not create IAM roles or
resources itself. The broad permissions sit in the second role, which only CloudFormation can assume (and
only for this stack) and which only the deploy role can pass on.

Who can log in as the deploy role: **only** a workflow of `dhananjaya-hbc/gradeflow-serverless-pipeline`
running on the `main` branch. Pull requests, forks, other branches and tags get a different identity and
are refused. The CI roles are named `ci-gradeflow-*` on purpose: the execution role may manage IAM roles
called `gradeflow-*`, so it can never change the CI roles (and is explicitly denied if it tried).

The account's existing GitHub OIDC provider is reused (an account can hold only one). Check it lists
`sts.amazonaws.com` as an audience: `aws iam get-open-id-connect-provider --open-id-connect-provider-arn
arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com`.

## Steps

**1. Create the roles.** From the repository root:

```bash
aws cloudformation deploy --template-file deploy/github-oidc-role.yaml --stack-name ci-gradeflow-roles --capabilities CAPABILITY_NAMED_IAM --region ap-south-1
```

It creates 2 roles and 4 managed policies. To use another repository or branch, add
`--parameter-overrides GitHubRepository=owner/repo GitHubBranch=main`.

**2. Give the role ARNs to GitHub as repository _secrets_.** An ARN is not a credential, but it contains
the AWS account id, and GitHub prints a *variable's* value in the run log (a secret is masked). This
repository's logs are public, so these go in as secrets:

```bash
gh secret set AWS_DEPLOY_ROLE_ARN --body "$(aws cloudformation describe-stacks --stack-name ci-gradeflow-roles --region ap-south-1 --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" --output text)"
gh secret set AWS_CFN_ROLE_ARN --body "$(aws cloudformation describe-stacks --stack-name ci-gradeflow-roles --region ap-south-1 --query "Stacks[0].Outputs[?OutputKey=='ExecutionRoleArn'].OutputValue" --output text)"
```

**3. Set the stack parameters CI must keep.** SAM forgets `--parameter-overrides` between deploys, so the
workflow passes these explicitly on every deploy. Without them a deploy would reset the stack to the
template defaults. The alert address is a secret too (it is personal, and a variable would be printed in
the public log). The rest are not sensitive, so they are plain variables:

```bash
read "?Your real email address (leave empty for no alert emails): " MAILADDR
gh secret set ALERT_EMAIL --body "$MAILADDR"
gh variable set AWS_REGION --body "ap-south-1"
gh variable set API_ALLOWED_ORIGIN --body "*"
gh variable set DASHBOARD_DEV_ORIGIN --body "http://localhost:5173"
```

`ALERT_EMAIL` receives the alarm and failure emails (AWS sends one confirmation link the first time, and
nothing is delivered until you click it). Skip the secret to get no emails at all. `API_ALLOWED_ORIGIN` is the
website allowed to call the API from a browser: `*` for now, or the dashboard's address to tighten it.
`DASHBOARD_DEV_ORIGIN` lets `npm run dev` upload straight to S3; use `none` to allow only the deployed
dashboard (GitHub does not accept an empty variable). Leave a variable unset to get its default.

**4. (Recommended) Protect `main`** so nothing reaches it, and so nothing is deployed, without green checks.
In GitHub: Settings, Rules, Rulesets, new branch ruleset for `main`, then require a pull request and require the
status checks `Python (lint and tests)`, `Dashboard (types, tests, build)` and `SAM template (validate and lint)`.

## Check it

- `gh secret list` shows the three secrets (names only: nobody can read a value back), and `gh variable list`
  shows `AWS_REGION`, `API_ALLOWED_ORIGIN` and `DASHBOARD_DEV_ORIGIN`.
- The first push to `main` after this runs the deploy job: build, `sam deploy`, publish the dashboard, then
  a smoke test that loads the page, reads `config.json`, and calls the API like a browser would. A failed
  smoke test turns the run red. The job's summary names the commit and the dashboard address.
- In the run's log, the AWS login step should say it assumed `ci-gradeflow-deploy`. The session is named
  `github-deploy-<run id>`, so CloudTrail shows which run made each call.
- A missing variable fails the very first step with a message naming it.
- If a step fails with `AccessDenied`, the message names the action and the role. CloudTrail (Event
  history, filter by user name `github-deploy-<run id>`) shows every call. Add the missing
  action to the right policy in `deploy/github-oidc-role.yaml` (the tests in `tests/unit/test_deploy_roles.py` keep
  the scope honest), then update the stack with the command from step 1.

## Undo

```bash
aws cloudformation delete-stack --stack-name ci-gradeflow-roles --region ap-south-1
gh secret delete AWS_DEPLOY_ROLE_ARN
gh secret delete AWS_CFN_ROLE_ARN
```

The application stack and the deployed site are not affected; deploys then go back to `sam deploy` by hand.

## Known limits

- The execution role can create IAM roles named `gradeflow-*` with any inline policy, because CloudFormation
  must be able to create the Lambda roles. That is the cost of infrastructure as code: treat the role, the
  `main` branch and the workflow file as one trust boundary. This is why only `main` may deploy, and why
  branch protection is recommended.
- API Gateway and CloudFront name their resources with random ids, so their permissions are limited to the
  kind of resource in this account and region, not to one name.
- If the container image's repository set changes (a second image function), SAM's companion stack needs
  ECR create rights the deploy role does not have: run that one deploy by hand.
