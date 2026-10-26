# Common Terraform mistakes

Consult this library during BYOC onboarding, alongside annotation discovery. Its findings concern
vendor Terraform and can also fail without Tensor9. Treat a pattern match as a lead: read the
producer, every relevant consumer and the configurations that connect them before reporting a bug.
For a finding, name the file and expression, a concrete failing configuration, the expected error
and a proposed repair. Separate a confirmed reproduction from a source-based finding that has not
been run. Show the diff and obtain vendor acceptance before changing source; retain unresolved
findings in the skill's report. Add entries when a real case teaches a reusable check.

## Optional resource referenced when absent

**Search terms:** `count`, `for_each`, `[0]`, keyed resource references, conditional outputs,
`jsonencode`, dashboard widgets, optional modules.

A resource, data source or module can have zero instances or omit a key. A consumer can still
unconditionally index that absent instance. The default enabled configuration may work while a
supported disabled configuration fails with an invalid index or missing-key error.

### Inspect

1. Find producers with conditional `count` or filtered/conditional `for_each`; determine exactly
   when each instance or key exists. Trace that producer's references in locals, outputs,
   resource arguments, templates and nested values such as dashboard JSON.
2. Compare each consumer's condition with the producer's existence condition. An unconditional
   `[0]` is suspect when count can be zero; `objects["key"]` is suspect when that key can be absent.
   Different feature flags do not prove the same condition. Inspect the actual allowed values.
3. Walk an enabled and a disabled/missing-key configuration. Also inspect mixed configurations
   when two flags or collections differ. Do not call a reference broken if a matching conditional
   excludes it, producer and consumer share the same instance keys, or the index is guaranteed to
   exist. Inspect `try`/`can` and length checks in context; their presence alone proves nothing.
4. Use a vendor-provided plan result to confirm a reproduction when available. This local skill
   does not deploy or read production state. Otherwise label the finding as source-based and
   describe the enabled and disabled inputs the vendor can plan to confirm it.

### Example

This Terraform excerpt works when enabled but reads an absent instance when disabled:

```hcl
variable "enabled" {
  type    = bool
  default = false
}
resource "terraform_data" "optional" {
  count = var.enabled ? 1 : 0
  input = "optional service"
}
output "optional_id" {
  value = terraform_data.optional[0].id
}
```

If the output's contract permits null when disabled, a matching conditional is one possible repair:

```hcl
output "optional_id" {
  value = var.enabled ? terraform_data.optional[0].id : null
}
```

For a list of dependent widgets, conditionally include the whole related group. For keyed
resources, use the producer's actual key set when the intended behavior is one consumer per
producer. Choose the repair from the consumer's contract: do not blanket-wrap expressions in
`try(..., null)`, enable an unwanted service, or delete unrelated consumers. Keep the enabled
behavior and ordering intact. Explain any change to outputs or other direct-deployment behavior.
Validate the syntax and plan both configurations when the vendor can do so; a parsing check alone
does not establish that a Terraform plan succeeds.

Also check the repair's conditional result types. Terraform may reject a heterogeneous widget
tuple paired with `[]`, even when the resource guard is correct. One possible form selects between
`jsonencode(widget_group)` and the string `"[]"`, then decodes the selected JSON before concatenating
the widgets. Verify both configurations and compare the complete enabled JSON with the original;
valid syntax alone does not prove compatible conditional types or preservation of widget content.

### Oumi example: ClickHouse monitoring dashboard

In original `oumi-ai/infrastructure-external` revision `2d5d965`, setting
`clickhouse_vpce_service_name = ""` gives the ClickHouse VPC endpoint zero instances. The trailing
ClickHouse widget group in `terraform/dashboard.tf` still reads `aws_vpc_endpoint.clickhouse[0]`.
This is a vendor-stack bug, independent of Tensor9; disabling ClickHouse exposes it.

Trace the endpoint's count condition and every dashboard reference in the actual revision. A
minimal proposed repair is to concatenate the always-present widgets with a ClickHouse widget
group selected only when `clickhouse_vpce_service_name != ""`, using the JSON-string form above so
both conditional results have the same type.
Keep every existing widget and its order when enabled. Check that the conditional group contains
only ClickHouse-dependent widgets. Show that diff separately from the choice to disable ClickHouse,
and apply it only after the vendor accepts it. Do not use old line numbers as edit anchors or claim
that an unrun plan has passed.

## Route declared both inline and as a standalone resource

**Search terms:** `resource "aws_route"`, `resource "aws_route_table"`, `aws_default_route_table`,
inline `route {` blocks, `cidr_block` (inline) and `destination_cidr_block` (standalone), `0.0.0.0/0`,
`gateway_id`, `nat_gateway_id`.

The AWS provider manages a route table's routes either inline (`route` blocks) or through separate
`aws_route` resources, never both. When the same route table and destination appear in both, a cold
install fails: the route table creates the route first, and the standalone `aws_route` then fails with
`RouteAlreadyExists`. A stack that was built up incrementally can look healthy in its own account
while a fresh install of the same source cannot converge.

### Inspect

1. List every `aws_route` and resolve its `route_table_id` to the producing `aws_route_table` or
   `aws_default_route_table`. For each, compare `destination_cidr_block` (or IPv6/prefix-list
   destination) with that table's inline `route` blocks, including dynamic blocks.
2. A matching destination is the finding. Compare the targets too (`gateway_id`, `nat_gateway_id`
   and so on); a different target means the two declarations also disagree about where traffic goes.
3. Check what references each side. Prefer removing the declaration nothing references and keep
   the other unchanged. When both are referenced, keep inline routes or move all of them to
   standalone resources; do not mix the two styles on one table.
4. For an existing deployment, explain the state effect of the repair. Removing a standalone route
   from configuration plans its destroy, which deletes the shared route unless its state is moved
   or the resource is removed from state first.

### Oumi example: public default route

In original Oumi `terraform/networking.tf`, the same default route appears twice on one table:

```hcl
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}
```

The inline block names its destination `cidr_block`; the standalone resource names it
`destination_cidr_block`, so search for both spellings. A live cold
install on 2026-10-16 failed with `RouteAlreadyExists` for `0.0.0.0/0`. Nothing references
`aws_route.public_internet`, so the accepted repair removes it and keeps the inline route.

## EKS cluster that no principal can administer

**Search terms:** `enable_cluster_creator_admin_permissions`,
`bootstrap_cluster_creator_admin_permissions`, `access_entries`, `aws_eks_access_entry`,
`aws_eks_access_policy_association`, `aws-auth`, empty role-name variables.

An EKS cluster grants Kubernetes access through its creator's admin permission and its access
entries. A stack can turn the creator's permission off and create access entries only when a
role-name variable is non-empty. With those variables empty, as in a checked-in default, the cluster
creates and then no principal can reach it. Helm releases and `kubernetes_*` resources fail to
authenticate, and installing CRDs, cluster roles or webhooks also needs admin, because Kubernetes
refuses to let a caller grant permissions it does not hold. The Tensor9 compiler does not repair this.

### Inspect

1. For each `aws_eks_cluster` or EKS module call, read the creator-admin setting, `access_entries`
   and every standalone access entry and policy association that targets it.
2. Resolve each entry's existence condition and its variable's default and tfvars value. Determine
   whether any configuration the vendor ships leaves zero admin principals.
3. Identify the identity that applies the Kubernetes resources. In a Tensor9 install it is the
   appliance's deploying identity, not a vendor operator, and it needs cluster admin.
4. Exclude stacks where the creator's permission stays on, an unconditional entry grants admin to the
   applying identity, or no Terraform layer talks to the cluster API.
5. Propose granting the applying identity admin on an install while leaving the vendor's direct
   deployments unchanged. Show the diff and obtain vendor acceptance.

### Oumi example

Oumi's `terraform/temporal.tf` set `enable_cluster_creator_admin_permissions = false` and created its
three SSO access entries only from role-name variables that were empty in the checked-in
configuration. The accepted repair sets that argument to `var.tensor9`.

## Task or process that requires an optional secret

**Search terms:** `secrets` in `container_definitions`, `valueFrom`, `aws_secretsmanager_secret`,
`aws_ssm_parameter`, `*_api_key` and `*_account_id` variables without `default`, import-time
environment reads.

An ECS task whose definition lists a secret it cannot resolve fails to start. Stacks often reference
every integration's secret unconditionally, so a customer who does not use an integration must supply
a placeholder for it. The same failure recurs when the application reads an optional key at import or
startup instead of where the integration runs, and when the key's variable is required with no default
and no way to turn the integration off. The Tensor9 compiler does not repair this.

### Inspect

1. Resolve each task definition's `secrets` entry to its producer. Note which values the stack
   generates and which a customer must supply.
2. For each customer-supplied secret, check whether its integration can be disabled and whether the
   reference itself is conditional (a filtered list that omits the entry rather than blanking it).
3. Find required variables for optional integrations; each forces every customer to supply a value.
4. When application source is available, search its startup path for the same names. An import-time
   read fails the process even after the infrastructure omits the secret.
5. Exclude secrets every configuration needs, such as database credentials and signing keys.
6. Propose per-secret optionality: a supplied secret flows into the task definition and a declined one
   is omitted. Report application reads separately as a code change for the vendor.

### Oumi example

Oumi's api and worker tasks referenced their LLM, billing and analytics secrets unconditionally. The
merged repair, `var.provided_secret_refs`, omits declined secrets. The Fireworks key and two GraphN
values were still read at import, and `fireworks_account_id` was a required variable with no default.

## Resource that waits on a verified identity or a person during apply

**Search terms:** `email_sending_account = "DEVELOPER"`, `email_configuration`, `source_arn`,
`from_email_address`, `aws_ses_email_identity`, `aws_sesv2_email_identity`,
`aws_sns_topic_subscription` with `protocol = "email"` or `"email-json"`.

Some resources succeed only after an action outside Terraform. Cognito refuses to create a user pool
with `email_sending_account = "DEVELOPER"` while its SES sender identity is unverified. An identity
created in the same run starts unverified and verifies only when a person opens a mailed link, so a
fresh account's first apply stops at the pool and everything that depends on it. An SNS email
subscription remains `PendingConfirmation` until a recipient confirms it; it has no ARN meanwhile, so
later applies report errors updating it. A customer account has nobody to click either link. The
Tensor9 compiler does not repair this.

### Inspect

1. Resolve each `DEVELOPER` pool's `source_arn` to its SES identity. A same-run identity, or one the
   customer account does not already hold verified, is the finding.
2. Find email-protocol SNS subscriptions and whether they are created unconditionally.
3. Exclude identities looked up by data source or supplied as existing verified ARNs, and
   subscriptions whose endpoints confirm without a person (`lambda`, `sqs`).
4. Propose a conditional email configuration that defaults to the vendor's current behavior. Omit the
   whole block to fall back to the Cognito mailer: `source_arn` and `from_email_address` are valid
   only with `DEVELOPER`. That mailer sends from an AWS address with a low daily limit; state it.
   Create subscriptions only when an address is supplied, with `moved` blocks for existing state.
   Show the diff and obtain vendor acceptance.

## Fixed names that collide across installs or redeploys

**Search terms:** `name =`, `name_prefix`, `aws_cognito_user_pool_domain`, `aws_dynamodb_table`,
`aws_iam_role`, `aws_iam_instance_profile`, `aws_secretsmanager_secret`, `recovery_window_in_days`,
`environment`, `resource_prefix`.

Many AWS names must be unique per region, per account or globally. A name built from a literal or
from a value every install shares, such as `environment`, works in one long-lived environment and
fails with "already exists" when a second install shares the account or a redeploy follows a
teardown. Cognito hosted-UI domain prefixes are unique across all AWS accounts, and AWS rejects a
prefix containing `aws`, `amazon` or `cognito`. IAM names are account-wide in every region. A deleted
Secrets Manager secret keeps its name reserved for its recovery window, 30 days by default. The
Tensor9 compiler does not repair this.

### Inspect

1. Resolve the name expression of each resource whose type requires a unique name. Flag literals and
   names composed only of values that repeat across installs.
2. Record each name's scope: global (Cognito domain prefix), account-wide (IAM roles and instance
   profiles) or regional (DynamoDB tables, secrets).
3. For a derived Cognito prefix, check whether its source can contain the reserved words. A
   precondition fails the plan instead of `CreateUserPoolDomain` after the pool already exists.
4. Check each secret's `recovery_window_in_days` and whether teardown force-deletes without recovery.
5. Exclude `name_prefix` and per-install suffixes. For a name a library requires verbatim, ask the
   vendor whether two installs ever share an account.
6. Propose deriving names from a per-install prefix. Renaming replaces an existing resource; explain
   the state effect, show the diff and obtain vendor acceptance.

### Oumi example

An Oumi install sharing an account and region with a live one hit about 44 "already exists" errors
among 48, because most names derive from `environment`.

## Vendor account checks and template tfvars

**Search terms:** `expected_account_id`, `aws_caller_identity`, `check`, `precondition`,
`allowed_account_ids`, `profile =`, `backend "s3"`, `terraform.tfvars`, `*.auto.tfvars`, `<...>`
placeholder strings.

Stacks protect the vendor's own accounts in several places: a `check` block or precondition comparing
`data.aws_caller_identity` with `var.expected_account_id`, provider `allowed_account_ids`, and a named
`profile` in provider and backend blocks. A committed `terraform.tfvars` may be a template whose
values are literal placeholders such as `"<API_DOMAIN>"`. A tfvars assignment outranks a variable
default, and Terraform loads `*.auto.tfvars` after `terraform.tfvars` and `terraform.tfvars.json`, so
a placeholder or a gitignored local `*.auto.tfvars` can replace values an install supplies.

### Inspect

1. Record each provider and backend block's account selectors and profile.
2. Find account checks and their effect: a `check` block only warns; a precondition or validation
   fails the plan. Trace what sets the expected account for each install; a value copied from another
   environment names the wrong account.
3. Read every tfvars file in the root module, including gitignored ones present in a directory the
   vendor publishes. Flag placeholders and vendor-specific values.
4. Exclude files Terraform does not load automatically, such as `terraform.tfvars.example`.
5. Propose the standard credential chain instead of named profiles, an example file instead of
   template tfvars, and publishing from a clean tree. Show the diff and obtain vendor acceptance.

Report as informational: the compiler's `AwsTfVendorIdentityStrip` removes credential and account
selectors, including `profile`, `assume_role` and `allowed_account_ids`, from AWS provider blocks.
That strip does not change backend blocks, tfvars contents or `expected_account_id` checks.

## Hardcoded account IDs, ARNs, zone IDs and regions

**Search terms:** 12-digit numbers, `arn:aws:`, `zone_id =`, `certificate_arn`, `region =`, region
names in hostnames, `kms_key_administrators`, role names in policies.

Values that are correct in the vendor's account are wrong in each customer's: a Route 53 zone ID, ACM
certificate ARNs, IAM roles named in a KMS key policy, account IDs in SQS URLs and policy ARNs, and
regions embedded in service endpoints. A KMS key policy naming a role that does not exist fails with
`MalformedPolicyDocumentException`; a missing zone or certificate fails its consumer. The compiler's
`TfFlagHardcodedRefsOp` flags only DNS records whose zone is a literal; report every other form.

### Inspect

1. Search Terraform, tfvars, templates and container environment values for account IDs, `arn:aws:`
   literals, zone IDs and region strings.
2. Identify each value's owner. Exclude AWS-owned public AMI owners and IDs, AWS managed policy ARNs
   (`arn:aws:iam::aws:policy/...`), service principals and AWS-published service account IDs.
3. Propose a lookup or derivation: `data.aws_route53_zone` by name, `data.aws_caller_identity`,
   `data.aws_region`, `data.aws_partition`, a Terraform-managed certificate, or a variable without a
   vendor default. Show the diff and obtain vendor acceptance.
4. Note when a region literal also selects where data lives; changing it moves resources.

## Private images named by tag strings

**Search terms:** `image =` in `container_definitions`, Helm `image.repository` and `image.tag`,
`dkr.ecr.`, `latest`, `data "aws_ecr_image"`, `image_digest`, `image_uri`.

An install copies the vendor's private images into the customer's registry. The compiler recognizes
images named through `data.aws_ecr_image`; a repository URL and tag interpolated into a string give it
nothing to copy. Moving tags add a second failure: a recompile can copy the same content under new
tags, and if the apply that updates task definitions fails first, ECS cannot start tasks from tags
that no longer exist.

### Inspect

1. List images in task definitions, pod specs, Helm values and Lambda `image_uri`; separate private
   images from public ones.
2. For each private image, check whether the reference comes from `data.aws_ecr_image`, with explicit
   `registry_id` and `region` when the registry is in another account or region, or from a string.
3. Flag moving tags; prefer `image_digest` or an immutable tag.
4. Exclude public images, such as a chart's public defaults.
5. State the consequences of the repair: the tag must exist at plan time, and an output reporting the
   image should report the configured reference, not the install's local copy. The vendor's registry
   must also grant the Tensor9 vendor controller read. Show the diff and obtain vendor acceptance.

## Handled by the Tensor9 compiler (report as informational)

These patterns can fail a direct deployment too, but the compiler rewrites them on an install. Report
them as informational, naming the op, rather than asking the vendor for a change:

- Account-level singletons (GuardDuty detector, DIMENSIONAL cost anomaly monitor, EBS encryption by
  default) are skipped on an install: `AwsTfAccountSingletonsOp`.
- A BUFFERED API Gateway integration timeout above 29 s is capped; STREAM integrations are excluded:
  `AwsTfApigTimeoutQuotaOp`.
- Metric and subscription filters on implicit `/aws/lambda/...` log groups get a declared log group:
  `AwsTfLambdaLogGroupDeclOp`.
- The `aws-ebs-csi-driver` addon on an EKS Auto Mode cluster is skipped: `AwsTfEbsCsiAutoModeOp`.
- The EKS module's `before_compute` addon race with `time_sleep` gets the missing dependency:
  `AwsTfEksBeforeComputeDepsPatchOp`.
