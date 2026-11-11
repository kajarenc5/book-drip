#!/usr/bin/env bash
set -euo pipefail

# Deploy or update the running One Chapter application on the EC2 instance in eu-north-1
# via AWS Systems Manager Session Manager (with an automatic pre-deploy S3 PostgreSQL backup).
# Always uses --profile book-drip-local and --region eu-north-1 per project rules.

STACK_NAME="${STACK_NAME:-onechapter-prod}"
AWS_PROFILE="${AWS_PROFILE:-book-drip-local}"
AWS_REGION="eu-north-1"
REPO_URL="${REPO_URL:-https://github.com/kajarenc5/book-drip.git}"
BRANCH="${BRANCH:-main}"

INSTANCE_ID="$(
    aws cloudformation describe-stacks \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --stack-name "${STACK_NAME}" \
        --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
        --output text
)"

if [[ -z "${INSTANCE_ID}" || "${INSTANCE_ID}" == "None" ]]; then
    echo "Error: Could not find InstanceId output for stack ${STACK_NAME} in ${AWS_REGION}." >&2
    exit 1
fi

echo "Releasing branch '${BRANCH}' to EC2 instance ${INSTANCE_ID} (${AWS_REGION})..."

COMMAND_ID="$(
    aws ssm send-command \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --instance-ids "${INSTANCE_ID}" \
        --document-name "AWS-RunShellScript" \
        --comment "One Chapter release (${BRANCH})" \
        --parameters "commands=[
            'set -euo pipefail',
            'mkdir -p /opt/onechapter',
            'if [ ! -d /opt/onechapter/.git ]; then git clone --branch ${BRANCH} ${REPO_URL} /opt/onechapter; fi',
            'cd /opt/onechapter',
            'git fetch origin ${BRANCH} && git checkout ${BRANCH} && git reset --hard origin/${BRANCH}',
            'if docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --status running db 2>/dev/null | grep -q db; then echo \"Running pre-deploy PostgreSQL backup to S3...\"; docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm db-backup /bin/sh /app/infra/scripts/db-backup.sh; fi',
            'docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans',
            'for i in 1 2 3 4 5 6 7 8 9 10; do if curl -fsS http://127.0.0.1:43147/health >/dev/null; then echo \"Healthcheck passed.\"; exit 0; fi; sleep 2; done; echo \"Healthcheck timed out.\" >&2; exit 1'
        ]" \
        --query "Command.CommandId" \
        --output text
)"

echo "Waiting for SSM command ${COMMAND_ID} to finish..."
aws ssm wait command-executed \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" || true

aws ssm get-command-invocation \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --query "{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}" \
    --output yaml
