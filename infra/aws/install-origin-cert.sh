#!/usr/bin/env bash
set -euo pipefail

# Install a signed Cloudflare Origin CA certificate onto the EC2 instance in eu-north-1
# using the private key (/etc/onechapter/certs/origin-cf.key) already generated on EC2,
# then restart Caddy (proxy). The private key never leaves the EC2 host.

CERT_FILE="${1:-}"
if [[ -z "${CERT_FILE}" || ! -f "${CERT_FILE}" ]]; then
    echo "Usage: $0 /path/to/cloudflare-origin.pem" >&2
    exit 1
fi

STACK_NAME="${STACK_NAME:-onechapter-prod}"
AWS_PROFILE="${AWS_PROFILE:-book-drip-local}"
AWS_REGION="eu-north-1"

INSTANCE_ID="$(
    aws cloudformation describe-stacks \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --stack-name "${STACK_NAME}" \
        --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" \
        --output text
)"

BUCKET_NAME="$(
    aws cloudformation describe-stacks \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --stack-name "${STACK_NAME}" \
        --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" \
        --output text
)"

STAGE_KEY="originals/_origin_cert.pem"

VERSION_ID="$(
    aws s3api put-object \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --bucket "${BUCKET_NAME}" \
        --key "${STAGE_KEY}" \
        --body "${CERT_FILE}" \
        --server-side-encryption AES256 \
        --query "VersionId" \
        --output text
)"

cleanup() {
    if [[ -n "${VERSION_ID}" && "${VERSION_ID}" != "None" ]]; then
        aws s3api delete-object \
            --profile "${AWS_PROFILE}" \
            --region "${AWS_REGION}" \
            --bucket "${BUCKET_NAME}" \
            --key "${STAGE_KEY}" \
            --version-id "${VERSION_ID}" >/dev/null || true
    else
        aws s3api delete-object \
            --profile "${AWS_PROFILE}" \
            --region "${AWS_REGION}" \
            --bucket "${BUCKET_NAME}" \
            --key "${STAGE_KEY}" >/dev/null || true
    fi
}
trap cleanup EXIT

COMMAND_ID="$(
    aws ssm send-command \
        --profile "${AWS_PROFILE}" \
        --region "${AWS_REGION}" \
        --instance-ids "${INSTANCE_ID}" \
        --document-name "AWS-RunShellScript" \
        --comment "Install Cloudflare Origin CA certificate and reload Caddy" \
        --parameters "commands=[
            'set -euo pipefail',
            'aws s3 cp --region ${AWS_REGION} s3://${BUCKET_NAME}/${STAGE_KEY} /etc/onechapter/certs/origin-cf.pem >/dev/null',
            'PUB_CERT=\$(openssl x509 -in /etc/onechapter/certs/origin-cf.pem -pubkey -noout)',
            'if [ -f /etc/onechapter/certs/origin-cf.key ]; then KEY_CAND=/etc/onechapter/certs/origin-cf.key; else KEY_CAND=/etc/onechapter/certs/origin.key; fi',
            'PUB_KEY=\$(openssl pkey -in \"\${KEY_CAND}\" -pubout)',
            'if [ \"\${PUB_CERT}\" != \"\${PUB_KEY}\" ]; then echo \"Error: certificate public key does not match \${KEY_CAND} on EC2.\" >&2; rm -f /etc/onechapter/certs/origin-cf.pem; exit 1; fi',
            'mv -f /etc/onechapter/certs/origin-cf.pem /etc/onechapter/certs/origin.pem',
            'if [ -f /etc/onechapter/certs/origin-cf.key ]; then mv -f /etc/onechapter/certs/origin-cf.key /etc/onechapter/certs/origin.key; fi',
            'chmod 644 /etc/onechapter/certs/origin.pem',
            'chmod 600 /etc/onechapter/certs/origin.key',
            'cd /opt/onechapter && docker compose -f docker-compose.yml -f docker-compose.prod.yml restart proxy',
            'openssl x509 -in /etc/onechapter/certs/origin.pem -noout -subject -issuer -dates'
        ]" \
        --query "Command.CommandId" \
        --output text
)"

aws ssm wait command-executed \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}"

aws ssm get-command-invocation \
    --profile "${AWS_PROFILE}" \
    --region "${AWS_REGION}" \
    --command-id "${COMMAND_ID}" \
    --instance-id "${INSTANCE_ID}" \
    --query "StandardOutputContent" \
    --output text
