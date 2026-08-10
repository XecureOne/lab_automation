# AWS Sandbox Cleanup Engine

A plugin-based framework that cleans up AWS resources that commonly prevent
[`aws-nuke`](https://github.com/ekristen/aws-nuke) from successfully wiping a
sandbox/training account -- things like CloudFormation stacks, non-empty
versioned S3 buckets, and RDS instances/clusters with deletion protection or
replica dependencies.

**This tool is not a replacement for `aws-nuke`.** It is meant to run
*before* `aws-nuke`, to clear out the specific classes of resources that
routinely make `aws-nuke` runs fail or hang, and again *after* `aws-nuke` to
confirm the account is actually empty.

## Pipeline

```
Student Finishes Lab
        |
        v
  Inventory Engine        <- this project (before)
        |
        v
 Custom Cleanup Engine     <- this project
        |
        v
 Verification Engine       <- this project
        |
        v
    aws-nuke                (run separately, not part of this project)
        |
        v
 Verification Engine       <- this project (run again)
        |
        v
 Return Account to Pool
```

This repository implements the **Inventory Engine**, **Custom Cleanup
Engine**, and **Verification Engine** boxes above, plus the reporting layer
around them. It does not invoke `aws-nuke` itself -- that stays a separate
step in whatever automation calls this tool.

## What's implemented

| Service | discover() | cleanup() | verify() |
|---|---|---|---|
| CloudFormation | Yes | Deletes every non-nested stack, waits for `DELETE_COMPLETE` | Yes |
| S3 | Yes | Aborts multipart uploads, deletes every object version and delete marker (handles versioned and non-versioned buckets), deletes the bucket, waits for it to disappear | Yes |
| RDS | Yes | Disables deletion protection, deletes read replicas, deletes remaining instances (including cluster members), deletes clusters, waits for each deletion | Yes |
| EventBridge | Yes | Removes targets then deletes rules (leaves AWS-managed rules, e.g. GuardDuty/SecurityHub integrations, untouched), deletes schemas then their registry, deletes archives then the custom event bus they reference. Never touches the account's `default` bus/registry. | Yes |

Everything else listed below is a **placeholder plugin**: it is discovered by
the orchestrator and satisfies the `BaseCleanupService` interface, but
calling `discover()`, `cleanup()`, or `verify()` on it raises
`NotImplementedError`. They exist purely so future work is "drop a file into
`services/`," not "restructure the orchestrator."

`iam`, `eks`, `ec2`, `autoscaling`, `application_autoscaling`,
`elb`, `ecs`, `ecr`, `elasticbeanstalk`, `lambda` (`lambda_service.py`),
`apigateway`, `kms`.

By default `include_services` is empty, meaning every discovered plugin is in scope -- including the 12 remaining placeholders. Set `include_services: [cloudformation, s3, rds, eventbridge]` to restrict a run to the implemented services only. Whether restricted or not, running a placeholder makes the orchestrator invoke it, hit `NotImplementedError`, log it as a per-service failure, and continue with the rest of the run -- it will not silently no-op and it will not crash the whole run.

### EventBridge implementation notes

- **Deletion order**: targets removed from a rule → rule deleted → schemas deleted → registry deleted → archives deleted (an archive references its source bus's ARN) → custom event bus deleted. This mirrors the actual AWS dependency chain, so nothing is left orphaned or rejected mid-run.
- **Rules on the `default` bus are cleaned up too** (labs commonly add rules there directly), but a rule with `ManagedBy` set -- created by another AWS service integration such as GuardDuty or SecurityHub -- is always skipped (`aws_managed_rule:<owner>` in the skip reason) since deleting it would break that integration and AWS rejects the call regardless.
- **The account's built-in `default` event bus and `default`/`aws.*` Schemas registries are never deleted** -- AWS doesn't allow deleting `default`, and `aws.*` registries belong to AWS, not the account. Schemas *inside* the `default` registry are still cleaned up.
- **Known limitation**: EventBridge's `ListRules` API doesn't expose a rule creation timestamp, so rules (unlike RDS instances/clusters and S3 buckets) don't get a checkpoint fingerprint -- a same-named rule recreated by a later lab run could theoretically collide with a stale checkpoint entry the same way the RDS identifier-reuse issue did. Buses, archives, and registries aren't affected since AWS enforces global uniqueness on those names per account while they exist.

## Architecture

```
cleanup-engine/
    main.py              CLI entry point
    orchestrator.py       Plugin discovery + full pipeline runner
    inventory.py           Inventory Engine (runs discover() on every plugin)
    verification.py        Verification Engine (runs verify() on every plugin)
    report.py               Builds cleanup_report.json
    config.py                 Loads config.yaml + env var overrides
    logger.py                  Structured [STAGE] logging
    retry.py                     Exponential backoff decorator
    waiters.py                     Reusable polling waiters
    utils.py                         JSON I/O, chunking, Timer
    config.yaml
    requirements.txt
    base/
        aws_client.py       boto3 client factory/cache
        base_inventory.py    BaseInventoryService (discover)
        base_verifier.py      BaseVerifier (verify)
        base_cleanup.py         BaseCleanupService (discover+cleanup+verify)
                                  + NotImplementedCleanupService
    models/
        inventory_result.py    InventoryResult / ResourceRecord
        cleanup_result.py        CleanupResult / ResourceCleanupOutcome
        verification_result.py     VerificationResult
    services/
        cloudformation.py    implemented
        s3.py                  implemented
        rds.py                   implemented
        eventbridge.py, iam.py, eks.py, ec2.py, autoscaling.py,
        application_autoscaling.py, elb.py, ecs.py, ecr.py,
        elasticbeanstalk.py, lambda_service.py, apigateway.py, kms.py
                                    placeholders
```

### Plugin contract

```python
class BaseCleanupService(BaseInventoryService, BaseVerifier):
    def discover(self) -> InventoryResult: ...
    def cleanup(self, inventory: InventoryResult) -> CleanupResult: ...
    def verify(self) -> VerificationResult: ...
```

`orchestrator.py` uses `pkgutil.iter_modules(services.__path__)` to import
every module under `services/` and `inspect.getmembers` to find every class
that subclasses `BaseCleanupService`. **Adding a new AWS service is:**

1. Create `services/<name>.py`.
2. Define a class subclassing `BaseCleanupService` with a `service_name`
   attribute and real `discover()`/`cleanup()`/`verify()` implementations.
3. If `include_services` is non-empty, add `<name>` to it.

No changes to `orchestrator.py`, `inventory.py`, `verification.py`, or
`report.py` are required.

### Retry framework (`retry.py`)

`@retry_with_backoff()` wraps any boto3 call and retries with exponential
backoff + jitter on:

`ThrottlingException`, `Throttling`, `TooManyRequestsException`,
`RequestLimitExceeded`, `ConcurrentModificationException`,
`DependencyViolation`, `LimitExceededException`, `InternalFailure`,
`InternalError`, `ServiceUnavailable`, `SlowDown`,
`OperationAbortedException`, `ResourceInUseException`, plus generic
`BotoCoreError` connection issues.

Every AWS API call in `services/cloudformation.py`, `services/s3.py`, and
`services/rds.py` is wrapped with this decorator.

### Waiter framework (`waiters.py`)

`Waiter.wait_for(predicate, description, timeout, interval)` is the generic
polling primitive. Built on top of it:

- `wait_stack_deleted(cf_client, stack_name, timeout, interval)`
- `wait_bucket_deleted(s3_client, bucket_name, timeout, interval)`
- `wait_rds_instance_deleted(rds_client, db_instance_id, timeout, interval)`
- `wait_rds_cluster_deleted(rds_client, db_cluster_id, timeout, interval)`

All accept custom `timeout`/`interval`; defaults come from
`waiter_default_timeout_seconds` / `waiter_default_interval_seconds` in
`config.yaml`.

### Error handling

Every cleanup module catches exceptions per-resource: one stack, bucket, or
DB instance failing to delete is recorded on `CleanupResult` and the loop
continues to the next resource. The orchestrator additionally catches any
exception a plugin's `cleanup()` raises at the service level, so one broken
service can't stop the rest of the run. Everything is aggregated into a
final error summary in `cleanup_report.json`.

## Installation

```bash
cd cleanup-engine
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Configure AWS credentials however you normally would (environment
variables, `~/.aws/credentials` profile, an instance/task role, etc.) --
this tool uses the standard boto3 credential chain.

## Usage

```bash
# Run with config.yaml as-is
python main.py

# Dry run -- discover and report only, delete nothing
python main.py --dry-run

# Override region/profile/services from the CLI
python main.py --region us-west-2 --profile sandbox-cleanup --services cloudformation,s3,rds

# Keep a final RDS snapshot instead of skipping it
python main.py --retain-final-snapshot
```

Exit code is `0` if the overall verification passed, `1` if any enabled
service still has resources remaining after cleanup, `2` if the engine
itself hit an unrecoverable error.

## Output

Everything is written under `report_dir` (default `logs/reports`), namespaced per account/region as `<report_dir>/<account_id>/<region>/`:

- **`inventory.json`** -- pre-cleanup snapshot: per service, resource count,
  names, ARNs/IDs, and relevant metadata (e.g. stack status, bucket
  versioning, RDS deletion protection).
- **`verification.json`** -- post-cleanup check: per service PASS/FAIL and
  any remaining resource names, plus an overall PASS/FAIL that is PASS only
  if every enabled service is empty.
- **`cleanup_report.json`** -- account ID, region, start/end time, elapsed
  seconds, resources discovered/deleted/failed/remaining, success
  percentage, and per-service statistics including captured errors.
- **`cleanup_engine.log`** -- structured log using the `[STAGE]` convention,
  e.g. `[DISCOVER] Found 5 buckets`, `[CLEANUP] Deleted bucket
  student-bucket`, `[WAIT] Waiting for stack deletion`, `[VERIFY] S3 clean`.

Example `verification.json` shape:

```json
{
  "services": [
    { "service": "cloudformation", "status": "PASS", "remaining_count": 0, "remaining_resources": [] },
    { "service": "s3", "status": "FAIL", "remaining_count": 1, "remaining_resources": ["student-bucket"] },
    { "service": "rds", "status": "PASS", "remaining_count": 0, "remaining_resources": [] }
  ],
  "overall": "FAIL"
}
```

## Configuration

See the commented `config.yaml` for every option. Precedence is:
dataclass defaults < `config.yaml` < environment variables < CLI flags.

Key fields: `regions` (list -- the engine loops over each one), `include_services`/`exclude_services` (whitelist/blacklist by service_name; excluded services are still discovered and reported, never deleted), `exclude_tags` (skip any resource carrying one of these tag keys), `protected_resource_arns` (always skip these exact ARNs), `force_disable_protection` (gates disabling RDS deletion protection and CFN termination protection), `require_confirmation_phrase` (typed confirmation before any non-dry-run), `checkpoint_db` (SQLite record of already-deleted resources so a restarted run doesn't redo work), `max_verification_passes` (retries verification for AWS eventual consistency), and `assume_role_targets` (run the full pipeline against each cross-account role in turn).

Relevant environment variables: `CLEANUP_ENGINE_REGIONS` / `AWS_REGION`,
`CLEANUP_ENGINE_PROFILE` / `AWS_PROFILE`, `CLEANUP_ENGINE_DRY_RUN`,
`CLEANUP_ENGINE_PARALLEL_WORKERS`, `CLEANUP_ENGINE_RETAIN_SNAPSHOT`,
`CLEANUP_ENGINE_LOG_LEVEL`, `CLEANUP_ENGINE_REPORT_DIR`,
`CLEANUP_ENGINE_CONFIRMATION_PHRASE`.

## Required IAM permissions

At minimum, for the four implemented services:

```
cloudformation:ListStacks
cloudformation:DescribeStacks
cloudformation:DeleteStack
cloudformation:UpdateTerminationProtection

s3:ListAllMyBuckets
s3:GetBucketLocation
s3:GetBucketVersioning
s3:GetBucketTagging
s3:ListBucket
s3:ListBucketVersions
s3:ListBucketMultipartUploads
s3:AbortMultipartUpload
s3:DeleteObject
s3:DeleteObjectVersion
s3:DeleteBucket

rds:DescribeDBClusters
rds:DescribeDBInstances
rds:ListTagsForResource
rds:ModifyDBCluster
rds:ModifyDBInstance
rds:DeleteDBCluster
rds:DeleteDBInstance

events:ListEventBuses
events:ListRules
events:ListTargetsByRule
events:ListTagsForResource
events:ListArchives
events:RemoveTargets
events:DeleteRule
events:DeleteArchive
events:DeleteEventBus
schemas:ListRegistries
schemas:ListSchemas
schemas:ListTagsForResource
schemas:DeleteSchema
schemas:DeleteRegistry

sts:GetCallerIdentity
sts:AssumeRole   # only needed when assume_role_targets is non-empty
```

## Extending to a new service

1. `cp services/kms.py services/<new_service>.py` as a starting skeleton, or
   copy the structure of `services/cloudformation.py` for a closer real
   example (discover/cleanup/verify with pagination, retry, and a waiter).
2. Subclass `BaseCleanupService` (not `NotImplementedCleanupService`).
3. Implement `discover()` to page through the AWS API and populate
   `InventoryResult.resources` with `ResourceRecord(resource_id, name, arn,
   metadata)`.
4. Implement `cleanup()` to iterate `inventory.resources`, delete each one
   inside a per-resource `try/except` that calls `result.add_success(...)`
   or `result.add_failure(...)`, and use `@retry_with_backoff()` on the
   actual boto3 calls.
5. Implement `verify()` to re-scan and return a `VerificationResult`.
6. If `include_services` is non-empty, add the service's `service_name` to it.

The orchestrator will pick it up automatically on the next run.

## Known limitations of this foundation build

- S3 discovery uses `get_bucket_location` region matching, which treats the
  legacy `EU` constraint value as distinct from `eu-west-1`; this is a rare
  edge case on modern accounts.
- CloudFormation termination protection (`EnableTerminationProtection`) is
  not automatically disabled before `delete_stack` is called; a
  termination-protected stack will surface as a per-resource failure in
  `cleanup_report.json` rather than being force-deleted.
- Cross-region resource cleanup requires running the tool once per region
  (set `region` in `config.yaml` or pass `--region`).
- The 13 placeholder services are intentionally unimplemented; see
  "Extending to a new service" above.
