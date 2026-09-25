# AWS account setup checklist

Do this once, before Phase 1. None of these steps cost money.

## 1. Secure the root account
- [ ] Sign in as root → **IAM → Security credentials → Assign MFA** (use an authenticator app).
- [ ] Make sure the root user has **no access keys**. Delete any that exist.
- [ ] After this checklist, stop using root for daily work.

## 2. Protect against surprise bills
- [ ] **Billing → Billing preferences** → turn on *Receive Free Tier usage alerts* and *Receive CloudWatch billing alerts*.
- [ ] **Billing → Budgets → Create budget → "Zero spend budget"** (emails you as soon as anything costs > $0.01).
- [ ] Optional: a second monthly cost budget of e.g. **$5** with an alert at 80%.

## 3. Create a daily-use identity (not root)
Recommended: **IAM Identity Center** (short-lived credentials, nothing long-lived on disk).
- [ ] **IAM Identity Center → Enable** (choose region `ap-south-1`).
- [ ] Create a user for yourself and enable MFA for it.
- [ ] Create a permission set. `AdministratorAccess` is acceptable for a personal learning account. We will tighten the *Lambda* permissions in the project itself.
- [ ] Assign the user + permission set to your AWS account.
- [ ] Note the **AWS access portal URL** (you need it for the CLI).

Simpler alternative: an IAM user with MFA and an access key. This works, but the key is long-lived, so never commit it or paste it anywhere.

## 4. Install the tools (macOS)
```bash
brew install awscli            # already installed on this machine
brew install aws-sam-cli
sam --version
docker --version               # already installed; needed for sam build of the ML container (Phase 5)
```

## 5. Configure the CLI
With Identity Center:
```bash
aws configure sso              # profile name e.g. "gradeflow", region ap-south-1, output json
aws sso login --profile gradeflow
aws sts get-caller-identity --profile gradeflow   # should print your user, not root
export AWS_PROFILE=gradeflow   # or add to your shell profile
```

With an IAM user:
```bash
aws configure --profile gradeflow   # paste key id + secret, region ap-south-1
aws sts get-caller-identity --profile gradeflow
```

## 6. Never commit these
- `~/.aws/credentials`, `~/.aws/config`, `.env` files, access keys, your 12-digit account ID.
- The project `.gitignore` already excludes `.env*` and `.aws-sam/`. Still, check `git diff` before every commit.
