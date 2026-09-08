# Python vs. Rust AWS Lambda Lambdalith Benchmark

## Overview

This repository is a reproducible comparison of two small API applications deployed as
AWS Lambda container images: one implemented in Python and one in Rust. Each implementation
exposes the same five routes from a single Lambda function and uses the same DynamoDB data.
The project measures runtime behavior, resource use, deployment artifacts, and estimated
cloud cost without assuming that either language is universally preferable.

## Motivation

Language benchmarks rarely represent the decisions involved in shipping an application.
This project instead asks where runtime and implementation differences matter enough to
influence a technology choice for a small serverless API. It keeps externally observable
behavior and experiment conditions equal while allowing each language to use its normal
frameworks, concurrency model, validation, serialization, and resource lifecycle.

## What is a Lambdalith in this repository?

A Lambdalith here is a small web/API application that groups multiple routes and their
application logic in one Lambda function. It is not intended to be a large monolith. The
single-function boundary keeps routing, serialization, CPU work, and DynamoDB access in one
deployable unit so those characteristics can be compared as an application stack.

## Comparison policy

The governing principle is **same WHAT, idiomatic HOW**.

The two applications share the canonical OpenAPI contract, request and response data,
validation and error conditions, deterministic computations, DynamoDB operation counts,
dataset, Lambda memory and timeout, target architecture, and benchmark workload settings.
`spec/openapi.yaml`, `spec/workloads.yaml`, and `spec/testcases/` are the canonical inputs.

Python may use FastAPI, Pydantic, `asyncio`, and module-level resource reuse. Rust may use
Axum, Serde, Tokio, and application state. Their internal call graphs do not need to match.
The shared Contract Test sends every canonical case to both running APIs and compares the
status and complete JSON result. JSON object key order is ignored, array order and numeric
types are preserved, and only JSON Pointers explicitly declared as nondeterministic fields
are removed. Checksums and common error responses therefore remain strict comparison inputs.

## Architecture

```text
                           Benchmark Runner
                      (AWS credential chain + SigV4)
                                  |
                    +-------------+-------------+
                    |                           |
             API Gateway HTTP API       API Gateway HTTP API
                 (AWS_IAM)                  (AWS_IAM)
                    |                           |
           Python Lambdalith             Rust Lambdalith
                    |                           |
                    +----------+----------------+
                               |
                           DynamoDB

             Lambda logs and REPORT records -> CloudWatch
             Container images               -> Amazon ECR
             Infrastructure                 -> Terraform
             Raw data / reports             -> JSON, CSV, Markdown
```

Both Lambda functions use equivalent API Gateway integration, IAM permissions, logging,
memory, timeout, environment, architecture, and the same DynamoDB table. Terraform owns the
AWS resources. The Benchmark Runner remains outside the functions and calls both targets
under identical client-side conditions.

## Technology stack

- Python: CPython, FastAPI, Mangum, Pydantic, AWS SDK, pytest, Ruff, and uv.
- Rust: stable Rust, Axum, `lambda_http`, Tokio, Serde, AWS SDK for Rust, rustfmt, and Clippy.
- Runtime packaging: multi-stage Docker builds and an AL2023 Lambda OS-only final base.
- Infrastructure: AWS Lambda, API Gateway HTTP API, ECR, DynamoDB, CloudWatch, IAM, and
  Terraform.
- Benchmarking: a Python runner, deterministic YAML workloads, raw JSON/JSONL records, and
  CSV/Markdown reports.
- CI: GitHub Actions for application, contract, arm64 container, and Terraform validation.

## API endpoints

- `GET /health` measures router, framework, and runtime overhead without external I/O.
- `POST /transform` measures JSON decoding, validation, normalization, ordering, and encoding.
- `POST /compute` runs deterministic CPU-bound hashing. The MVP enables only the shared
  `sequential` profile.
- `GET /items/{id}` performs one DynamoDB item read and returns the common not-found error.
- `POST /aggregate` performs equivalent DynamoDB reads in `sequential` or `concurrent` mode,
  normalizes by item ID, and returns a count, total, and deterministic checksum.

The response body never identifies the implementation language. Errors use the same
`error.code`, `error.message`, and optional `error.details` schema.

## Benchmark workloads

`spec/workloads.yaml` defines the controlled workloads:

- `health`
- `transform-small`
- `transform-large`
- `compute-sequential`
- `items`
- `aggregate-sequential`
- `aggregate-concurrent`

Each definition records the method, path, payload, warm-up count, measured iterations, and
client concurrency. The standard matrix uses arm64 and equal Lambda memory settings of
256 MB, 512 MB, and 1024 MB. CPU parallelism and x86_64 are outside the MVP.

## Container strategy

Both final images use the same AL2023 Lambda OS-only base. `scripts/build-images.sh`
resolves that base digest once and passes it to both builds, so a comparison run cannot pick
up two different revisions of the `al2023` tag; the resolved digest is recorded with the
build metrics. Language-specific builder stages are intentionally different. The Python
final stage contains CPython, the Lambda Runtime Interface Client, runtime dependencies, and
application code. The Rust final stage contains the release binary and required runtime
files, without Cargo or compiler artifacts. The Rust builder stage also runs on Amazon Linux
2023 so the dynamically linked binary matches the runtime glibc instead of requiring a
static musl build that would change allocator behavior.

The default build target is `linux/arm64`. Recorded artifact data includes build duration,
local image size and layers, ECR size and digest, Python runtime/dependency/application
footprints, and Rust binary size.

**Container image size is a deployment-footprint metric; image size is not the Lambda
execution price.** Runtime cost is calculated from billed duration, configured memory,
request pricing, and the configured architecture price.

## Metrics

The runner and analyzer keep build/artifact and runtime observations separate:

- Client-side latency and P50, P95, and P99.
- Lambda Duration and Billed Duration.
- Init Duration, Max Memory Used, configured memory, error count, and error rate.
- Cold and warm sample counts.
- Build duration, image/layer sizes, ECR digest and size, and language-specific footprint.
- Estimated cost per request, per workload, and per one million requests.
- Git revision, run ID, region, architecture, memory, workload, and image digests.

Missing CloudWatch values remain `null`; they are not converted to zero.

## Cost calculation

Pricing is data, not a constant scattered through application code.
`spec/pricing.yaml` records its update date, region, source, request price, and arm64/x86_64
GB-second prices. A sample's normalized estimate is:

```text
compute cost = billed duration in seconds × configured memory in GB × GB-second price
request cost = request price per request
normalized cost per 1M = (compute cost + request cost) × 1,000,000
```

The main comparison excludes the AWS Free Tier so runs remain comparable between accounts.
Any Free Tier view must be labeled separately. If pricing is unset, calculation fails
instead of inventing a value. Review the source and update date before interpreting a report.

## How to build

Prerequisites are Docker with Buildx, uv, Python 3.13, stable Rust, Terraform, and
the AWS CLI for AWS operations.

```bash
make build              # both linux/arm64 container images
make build-python
make build-rust
make terraform-validate
```

Override `PLATFORM` or `IMAGE_TAG` when needed. A controlled benchmark should retain
`PLATFORM=linux/arm64`.

## How to run locally

Run the fast local checks first:

```bash
make lint
make test
```

The shared Contract Test expects two already-running APIs and canonical cases in
`spec/testcases/`:

```bash
PYTHON_API_URL=http://127.0.0.1:8081 \
RUST_API_URL=http://127.0.0.1:8082 \
make contract-test
```

For the repository's container-oriented local path, start DynamoDB Local, load the canonical
fixtures, run the Python ASGI app, and start the Rust Lambda image through the Lambda Runtime
Interface Emulator:

```bash
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export AWS_REGION=us-east-1
docker run --rm -p 8000:8000 amazon/dynamodb-local:latest
aws dynamodb create-table \
  --endpoint-url http://127.0.0.1:8000 \
  --region us-east-1 \
  --table-name lambda-runtime-lab-contract \
  --attribute-definitions AttributeName=id,AttributeType=S \
  --key-schema AttributeName=id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST
uv run --project tests/contract python tests/contract/seed_fixtures.py \
  --table lambda-runtime-lab-contract \
  --endpoint-url http://127.0.0.1:8000

AWS_ENDPOINT_URL_DYNAMODB=http://127.0.0.1:8000 \
DYNAMODB_TABLE_NAME=lambda-runtime-lab-contract \
uv run --project apps/python --with uvicorn \
  uvicorn app.main:app --app-dir apps/python/src --port 9001

docker run --rm -p 9002:8080 \
  --env AWS_ACCESS_KEY_ID=test \
  --env AWS_SECRET_ACCESS_KEY=test \
  --env AWS_REGION=us-east-1 \
  --env AWS_ENDPOINT_URL_DYNAMODB=http://host.docker.internal:8000 \
  --env DYNAMODB_TABLE_NAME=lambda-runtime-lab-contract \
  lambda-runtime-lab-rust:local

PYTHON_CONTRACT_TRANSPORT=direct \
RUST_CONTRACT_TRANSPORT=lambda-rie \
PYTHON_API_URL=http://127.0.0.1:9001 \
RUST_API_URL=http://127.0.0.1:9002 \
make contract-test
```

On Linux, add `--add-host host.docker.internal:host-gateway` to the Rust `docker run`
command. AWS SDK credentials can be dummy values for DynamoDB Local. Cases that use `/items`
or `/aggregate` require both applications to point to this same table. Direct mode is the
default; `lambda-rie` is only a local transport adapter and does not change the canonical
HTTP request or expected response.

## How to deploy

Deployment creates billable AWS resources. Authenticate with the AWS credential chain,
build and push both images to ECR, then provide immutable image URIs where possible:

```bash
export AWS_REGION=ap-northeast-1
export PYTHON_IMAGE_URI=<account>.dkr.ecr.<region>.amazonaws.com/<repo>@sha256:<digest>
export RUST_IMAGE_URI=<account>.dkr.ecr.<region>.amazonaws.com/<repo>@sha256:<digest>
make deploy
```

Review the Terraform plan before approving it. The APIs use IAM authorization and are not
intended to remain anonymously accessible. The Benchmark Runner obtains credentials through
the standard AWS credential chain and signs API Gateway requests with AWS Signature Version
4 (SigV4); credentials are never stored in this repository.

After the experiment, remove the environment:

```bash
make destroy
```

## How to seed

Read the deployed table name from `terraform output`, then run the deterministic seed script:

```bash
DYNAMODB_TABLE_NAME=<terraform-output> \
AWS_REGION=ap-northeast-1 \
make seed
```

For DynamoDB Local, also set `DYNAMODB_ENDPOINT_URL`. Re-running the command produces the same
approximately 100 item IDs, values, and payloads.

## How to benchmark

Set both protected API endpoints. The runner adds `X-Benchmark-Run-Id`, performs warm-up,
uses identical workload settings for both targets, signs AWS requests with SigV4, and stores
raw observations for later re-analysis.

```bash
PYTHON_API_URL=<python-api-output> \
RUST_API_URL=<rust-api-output> \
WORKLOAD=aggregate-concurrent \
RUN_ID=auto \
make benchmark
```

Run the equal-memory matrix explicitly:

```bash
PYTHON_API_URL=<python-api-output> \
RUST_API_URL=<rust-api-output> \
make benchmark-matrix
```

This operation changes deployed Lambda configuration and can incur AWS charges.

## How to generate reports

Lambda Duration, Init Duration, and memory come from log records, so collect a run before
reporting it. Both steps read and write only that run's raw records:

```bash
RUN_ID=<existing-run-id> make collect
RUN_ID=<existing-run-id> make report
```

Outputs are written to `results/reports/<run-id>.md` and
`results/reports/<run-id>.csv`. Reports include conditions, sample counts, percentiles,
resource observations, image metadata, normalized costs, and measurement notes.

## Testing strategy

- Python unit, API, repository, and error tests run in the Python project.
- Rust unit, API, repository, and error tests run with Cargo.
- Both language suites also execute every vector in `spec/testcases/` against their own
  application, so contract drift fails the fast local tests without any deployment.
- Benchmark Runner, analyzer, cost, and report behavior run in the benchmark project.
- Contract harness unit tests verify canonical case loading, strict JSON comparison,
  ordering, numeric types, and explicit nondeterministic-field removal.
- Shared Contract Tests send each canonical vector to both APIs and verify canonical status,
  body, error schema, ordering, numeric results, and checksums.
- Container smoke/contract tests use arm64 images and a shared deterministic data source.
- Terraform formatting, initialization without a backend, and validation run without
  deploying resources.

`make test` does not silently contact AWS. `make contract-test` requires explicit API URLs.

## CI

`.github/workflows/ci.yml` runs on pull requests and pushes. Separate jobs check Python,
Rust, the shared Contract Test, `linux/arm64` Docker builds, and Terraform. The Contract Test
uses local Lambda containers and does not deploy to AWS.

Full AWS deployment and benchmarking are deliberately excluded from normal CI because they
can incur charges. `.github/workflows/benchmark.yml` has only a manual
`workflow_dispatch` trigger, requires an explicitly configured AWS role and image URIs, and
uses GitHub OIDC instead of stored access keys.

## Results

**Not measured yet.**

No benchmark values or language winner are claimed before a reproducible run has produced
raw data and reports.

**Not yet verified on AWS.**

Deployment, IAM authorization, SigV4 invocation, CloudWatch correlation, controlled
Cold Start collection, ECR metadata, cost estimates, and cleanup must be verified in an
owned AWS account before results are published.

## Interpretation

Results should be discussed by workload rather than collapsed into one ranking:

- Framework overhead: interpret `/health` separately from application work.
- CPU-bound work: compare deterministic `/compute` results and billed duration.
- I/O-bound work: network and DynamoDB latency may make language differences small; compare
  sequential and concurrent profiles with equal operation counts.
- Cold Start: compare Init Duration and artifact/runtime initialization separately.
- Resource use: examine memory and deployment footprint independently from execution cost.
- Development complexity: include build, dependency, observability, and maintenance trade-offs
  alongside runtime metrics.

A technology choice should follow the measured workload and operational constraints, not a
preselected conclusion.

## Limitations

- Lambda Cold Starts cannot be made perfectly identical. Init Duration identifies observed
  cold samples, while controlled environment refreshes only improve—not guarantee—isolation.
- Warm results require a documented warm-up and may still be affected by shared AWS systems,
  network variance, service throttling, and execution-environment replacement.
- API Gateway, DynamoDB, CloudWatch delivery, and client location contribute latency outside
  the language runtime.
- The MVP compares arm64 container images, three memory settings, and one AWS region; it is
  not a general statement about every architecture, region, deployment format, or workload.
- A shared OS base reduces one source of variation but the language runtime and dependency
  layers necessarily differ.
- Cost output is an estimate based on configured prices and measured billed duration, not an
  AWS invoice.

## Future work

- Compare x86_64 with arm64 under an equivalent matrix.
- Compare container-image and ZIP deployment models.
- Add a production-oriented container comparison alongside the controlled common-base run.
- Add CPU parallel profiles only after both languages implement the same logical contract.
- Expand repeated runs across regions and time windows with documented confidence intervals.
- Compare the same application contract on another compute platform such as Cloud Run.
