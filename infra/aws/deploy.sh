#!/usr/bin/env bash
set -euo pipefail

# Deploy or validate the One Chapter CloudFormation stack in eu-north-1
# Always uses --profile book-drip-local and --region eu-north-1 per project rules.

STACK_NAME="${STACK_NAME:-onechapter-prod}"
AWS_PROFILE="${AWS_PROFILE:-book-drip-local}"
AWS_REGION="eu-north-1"
TEMPLATE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/template.yaml"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.small}"

if [[ "${1:-}" == "--validate" ]]; then
    echo "Validating CloudFormation template ${TEMPLATE_FILE} in ${AWS_REGION}..."
    aws cloudformation validate-template \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --template-body "file://${TEMPLATE_FILE}"
    exit 0
fi

echo "Deploying stack ${STACK_NAME} in ${AWS_REGION} (profile: ${AWS_PROFILE}, instance: ${INSTANCE_TYPE})..."
aws cloudformation deploy \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --stack-name "${STACK_NAME}" \
    --template-file "${TEMPLATE_FILE}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "InstanceType=${INSTANCE_TYPE}"

aws cloudformation describe-stacks \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs" \
    --output table
