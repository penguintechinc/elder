#!/usr/bin/env bash
#
# Provision (and tear down) one minimal AWS resource per Free Tier service that
# Elder's cloud discovery enumerates, so the AWS scan can be exercised against
# real infrastructure end to end.
#
# Every resource created here is Free Tier eligible. Resources that cost money
# even briefly (Route53 hosted zones, Secrets Manager secrets, KMS customer
# managed keys, ALB/NAT/EKS) are deliberately NOT created — see SKIPPED below.
#
# Usage:
#   ./scripts/aws/e2e-free-tier-assets.sh create  [--region us-east-2]
#   ./scripts/aws/e2e-free-tier-assets.sh destroy [--region us-east-2]
#   ./scripts/aws/e2e-free-tier-assets.sh list    [--region us-east-2]
#
# `destroy` is idempotent and best-effort: it keeps going past individual
# failures so a partial `create` can always be cleaned up. Always run it.
#
# Bash 3.2 compatible (macOS ships 3.2) — no associative arrays, no mapfile.

set -uo pipefail

ACTION="${1:-}"
shift || true

REGION="us-east-2"
while [ $# -gt 0 ]; do
    case "$1" in
        --region) REGION="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

AWS="${AWS_CLI:-aws}"
export AWS_PAGER=""

# Everything is tagged/named with this prefix so destroy can find it and so a
# stray resource is obviously identifiable in the console.
PREFIX="elder-e2e"
STATE_DIR="${ELDER_E2E_STATE_DIR:-${TMPDIR:-/tmp}/elder-aws-e2e}"
STATE_FILE="${STATE_DIR}/${PREFIX}-${REGION}.env"

# SKIPPED (cost money even for a short-lived test):
#   route53 hosted zone   $0.50/zone/month, billed on creation
#   secretsmanager secret $0.40/secret/month
#   kms customer key      $1/month per key
#   elasticache cluster   free tier eligible but ~10 min to provision
#   cloudfront dist       free tier eligible but ~15 min to deploy/disable
# Elder can still discover all of these; they are simply not created here.

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  [ok] %s\n' "$*"; }
warn() { printf '  [!!] %s\n' "$*" >&2; }

require_account() {
    local ident
    ident=$($AWS sts get-caller-identity --query Account --output text 2>&1) || {
        echo "cannot authenticate to AWS: $ident" >&2
        exit 1
    }
    log "account ${ident}  region ${REGION}"
}

# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------
do_create() {
    require_account
    mkdir -p "$STATE_DIR"
    local stamp
    stamp=$(date +%s)
    local name="${PREFIX}-${stamp}"
    : > "$STATE_FILE"
    echo "STAMP=${stamp}" >> "$STATE_FILE"
    echo "REGION=${REGION}" >> "$STATE_FILE"

    log "creating free-tier assets with prefix ${name}"

    # ---- S3 -------------------------------------------------------------
    if $AWS s3api create-bucket --bucket "$name" --region "$REGION" \
        --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null 2>&1; then
        $AWS s3api put-bucket-tagging --bucket "$name" \
            --tagging "TagSet=[{Key=Name,Value=${name}},{Key=Purpose,Value=elder-scan-test}]" >/dev/null 2>&1
        echo "BUCKET=${name}" >> "$STATE_FILE"; ok "s3 bucket ${name}"
    else warn "s3 bucket failed"; fi

    # ---- DynamoDB -------------------------------------------------------
    if $AWS dynamodb create-table --region "$REGION" --table-name "$name" \
        --attribute-definitions AttributeName=id,AttributeType=S \
        --key-schema AttributeName=id,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --tags "Key=Purpose,Value=elder-scan-test" >/dev/null 2>&1; then
        echo "DDB_TABLE=${name}" >> "$STATE_FILE"; ok "dynamodb table ${name}"
    else warn "dynamodb table failed"; fi

    # ---- SQS ------------------------------------------------------------
    local qurl
    qurl=$($AWS sqs create-queue --region "$REGION" --queue-name "$name" \
        --tags "Purpose=elder-scan-test" --query QueueUrl --output text 2>/dev/null)
    if [ -n "${qurl:-}" ] && [ "$qurl" != "None" ]; then
        echo "SQS_URL=${qurl}" >> "$STATE_FILE"; ok "sqs queue ${name}"
    else warn "sqs queue failed"; fi

    # ---- SNS ------------------------------------------------------------
    local topic
    topic=$($AWS sns create-topic --region "$REGION" --name "$name" \
        --tags "Key=Purpose,Value=elder-scan-test" --query TopicArn --output text 2>/dev/null)
    if [ -n "${topic:-}" ] && [ "$topic" != "None" ]; then
        echo "SNS_ARN=${topic}" >> "$STATE_FILE"; ok "sns topic ${name}"
    else warn "sns topic failed"; fi

    # ---- ECR ------------------------------------------------------------
    if $AWS ecr create-repository --region "$REGION" --repository-name "$name" \
        --tags "Key=Purpose,Value=elder-scan-test" >/dev/null 2>&1; then
        echo "ECR_REPO=${name}" >> "$STATE_FILE"; ok "ecr repository ${name}"
    else warn "ecr repository failed"; fi

    # ---- CloudWatch Logs ------------------------------------------------
    if $AWS logs create-log-group --region "$REGION" --log-group-name "$name" \
        --tags "Purpose=elder-scan-test" >/dev/null 2>&1; then
        echo "LOG_GROUP=${name}" >> "$STATE_FILE"; ok "cloudwatch log group ${name}"
    else warn "log group failed"; fi

    # ---- EventBridge ----------------------------------------------------
    if $AWS events put-rule --region "$REGION" --name "$name" \
        --schedule-expression "rate(1 day)" --state DISABLED \
        --tags "Key=Purpose,Value=elder-scan-test" >/dev/null 2>&1; then
        echo "EVENT_RULE=${name}" >> "$STATE_FILE"; ok "eventbridge rule ${name}"
    else warn "eventbridge rule failed"; fi

    # ---- API Gateway ----------------------------------------------------
    local apiid
    apiid=$($AWS apigateway create-rest-api --region "$REGION" --name "$name" \
        --tags "Purpose=elder-scan-test" --query id --output text 2>/dev/null)
    if [ -n "${apiid:-}" ] && [ "$apiid" != "None" ]; then
        echo "APIGW_ID=${apiid}" >> "$STATE_FILE"; ok "api gateway rest api ${apiid}"
    else warn "api gateway failed"; fi

    # ---- EFS ------------------------------------------------------------
    local efsid
    efsid=$($AWS efs create-file-system --region "$REGION" \
        --creation-token "$name" --performance-mode generalPurpose \
        --tags "Key=Name,Value=${name}" "Key=Purpose,Value=elder-scan-test" \
        --query FileSystemId --output text 2>/dev/null)
    if [ -n "${efsid:-}" ] && [ "$efsid" != "None" ]; then
        echo "EFS_ID=${efsid}" >> "$STATE_FILE"; ok "efs filesystem ${efsid}"
    else warn "efs filesystem failed"; fi

    # ---- IAM role (shared by Lambda + Step Functions) --------------------
    local role_name="${name}-role" role_arn
    role_arn=$($AWS iam create-role --role-name "$role_name" \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":["lambda.amazonaws.com","states.amazonaws.com"]},"Action":"sts:AssumeRole"}]}' \
        --tags "Key=Purpose,Value=elder-scan-test" \
        --query Role.Arn --output text 2>/dev/null)
    if [ -n "${role_arn:-}" ] && [ "$role_arn" != "None" ]; then
        echo "ROLE_NAME=${role_name}" >> "$STATE_FILE"; ok "iam role ${role_name}"
    else warn "iam role failed"; fi

    # ---- Lambda ---------------------------------------------------------
    if [ -n "${role_arn:-}" ]; then
        local work="${STATE_DIR}/fn"
        rm -rf "$work"; mkdir -p "$work"
        printf 'def handler(event, context):\n    return {"statusCode": 200}\n' > "$work/index.py"
        ( cd "$work" && zip -q fn.zip index.py )
        local i=0
        while [ $i -lt 6 ]; do
            if $AWS lambda create-function --region "$REGION" --function-name "$name" \
                --runtime python3.13 --handler index.handler --role "$role_arn" \
                --zip-file "fileb://${work}/fn.zip" \
                --tags "Purpose=elder-scan-test" >/dev/null 2>&1; then
                echo "LAMBDA=${name}" >> "$STATE_FILE"; ok "lambda function ${name}"; break
            fi
            i=$((i + 1)); sleep 10   # IAM role propagation
        done
        [ $i -lt 6 ] || warn "lambda function failed"

        # ---- Step Functions ---------------------------------------------
        local sm
        sm=$($AWS stepfunctions create-state-machine --region "$REGION" --name "$name" \
            --role-arn "$role_arn" \
            --definition '{"Comment":"elder e2e","StartAt":"Done","States":{"Done":{"Type":"Succeed"}}}' \
            --tags "key=Purpose,value=elder-scan-test" \
            --query stateMachineArn --output text 2>/dev/null)
        if [ -n "${sm:-}" ] && [ "$sm" != "None" ]; then
            echo "STATE_MACHINE=${sm}" >> "$STATE_FILE"; ok "step function ${name}"
        else warn "step function failed"; fi
    fi

    # ---- EC2 + security group (EBS volume comes with the instance) -------
    local vpc sg ami iid
    vpc=$($AWS ec2 describe-vpcs --region "$REGION" \
        --query 'Vpcs[?IsDefault].VpcId' --output text 2>/dev/null)
    sg=$($AWS ec2 create-security-group --region "$REGION" --group-name "${name}-sg" \
        --description "Elder E2E scan test" --vpc-id "$vpc" \
        --query GroupId --output text 2>/dev/null)
    if [ -n "${sg:-}" ] && [ "$sg" != "None" ]; then
        echo "SG_ID=${sg}" >> "$STATE_FILE"; ok "security group ${sg}"
    fi
    ami=$($AWS ssm get-parameters --region "$REGION" \
        --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --query 'Parameters[0].Value' --output text 2>/dev/null)
    # t3.micro, not t2.micro: newer accounts are not Free Tier eligible for t2.
    # The default SG is used deliberately so the instance has no inbound access.
    iid=$($AWS ec2 run-instances --region "$REGION" --image-id "$ami" \
        --instance-type t3.micro --count 1 \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${name}},{Key=Purpose,Value=elder-scan-test}]" \
        --query 'Instances[0].InstanceId' --output text 2>/dev/null)
    if [ -n "${iid:-}" ] && [ "$iid" != "None" ]; then
        echo "INSTANCE_ID=${iid}" >> "$STATE_FILE"; ok "ec2 instance ${iid}"
    else warn "ec2 instance failed"; fi

    printf '\nstate written to %s\n' "$STATE_FILE"
    printf 'run "%s destroy --region %s" when finished\n' "$0" "$REGION"
}

# --------------------------------------------------------------------------
# destroy — best effort, never aborts early
# --------------------------------------------------------------------------
do_destroy() {
    if [ ! -f "$STATE_FILE" ]; then
        echo "no state file at ${STATE_FILE}; nothing recorded to destroy" >&2
        echo "(resources may still exist — check tag Purpose=elder-scan-test)" >&2
        exit 1
    fi
    require_account
    # shellcheck disable=SC1090
    . "$STATE_FILE"
    log "tearing down assets from ${STATE_FILE}"

    if [ -n "${INSTANCE_ID:-}" ]; then
        $AWS ec2 terminate-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null 2>&1 \
            && ok "terminating ec2 ${INSTANCE_ID}" || warn "ec2 ${INSTANCE_ID} not terminated"
        # the security group cannot be deleted until the ENI is released
        $AWS ec2 wait instance-terminated --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null 2>&1
    fi
    if [ -n "${SG_ID:-}" ]; then
        $AWS ec2 delete-security-group --region "$REGION" --group-id "$SG_ID" >/dev/null 2>&1 \
            && ok "deleted security group ${SG_ID}" || warn "sg ${SG_ID} not deleted"
    fi
    if [ -n "${STATE_MACHINE:-}" ]; then
        $AWS stepfunctions delete-state-machine --region "$REGION" --state-machine-arn "$STATE_MACHINE" >/dev/null 2>&1 \
            && ok "deleted state machine" || warn "state machine not deleted"
    fi
    if [ -n "${LAMBDA:-}" ]; then
        $AWS lambda delete-function --region "$REGION" --function-name "$LAMBDA" >/dev/null 2>&1 \
            && ok "deleted lambda ${LAMBDA}" || warn "lambda not deleted"
    fi
    if [ -n "${ROLE_NAME:-}" ]; then
        $AWS iam delete-role --role-name "$ROLE_NAME" >/dev/null 2>&1 \
            && ok "deleted iam role ${ROLE_NAME}" || warn "iam role not deleted"
    fi
    if [ -n "${EFS_ID:-}" ]; then
        $AWS efs delete-file-system --region "$REGION" --file-system-id "$EFS_ID" >/dev/null 2>&1 \
            && ok "deleted efs ${EFS_ID}" || warn "efs not deleted"
    fi
    if [ -n "${APIGW_ID:-}" ]; then
        $AWS apigateway delete-rest-api --region "$REGION" --rest-api-id "$APIGW_ID" >/dev/null 2>&1 \
            && ok "deleted api gateway ${APIGW_ID}" || warn "api gateway not deleted"
    fi
    if [ -n "${EVENT_RULE:-}" ]; then
        $AWS events delete-rule --region "$REGION" --name "$EVENT_RULE" >/dev/null 2>&1 \
            && ok "deleted eventbridge rule" || warn "eventbridge rule not deleted"
    fi
    if [ -n "${LOG_GROUP:-}" ]; then
        $AWS logs delete-log-group --region "$REGION" --log-group-name "$LOG_GROUP" >/dev/null 2>&1 \
            && ok "deleted log group" || warn "log group not deleted"
    fi
    if [ -n "${ECR_REPO:-}" ]; then
        $AWS ecr delete-repository --region "$REGION" --repository-name "$ECR_REPO" --force >/dev/null 2>&1 \
            && ok "deleted ecr repo" || warn "ecr repo not deleted"
    fi
    if [ -n "${SNS_ARN:-}" ]; then
        $AWS sns delete-topic --region "$REGION" --topic-arn "$SNS_ARN" >/dev/null 2>&1 \
            && ok "deleted sns topic" || warn "sns topic not deleted"
    fi
    if [ -n "${SQS_URL:-}" ]; then
        $AWS sqs delete-queue --region "$REGION" --queue-url "$SQS_URL" >/dev/null 2>&1 \
            && ok "deleted sqs queue" || warn "sqs queue not deleted"
    fi
    if [ -n "${DDB_TABLE:-}" ]; then
        $AWS dynamodb delete-table --region "$REGION" --table-name "$DDB_TABLE" >/dev/null 2>&1 \
            && ok "deleted dynamodb table" || warn "dynamodb table not deleted"
    fi
    if [ -n "${BUCKET:-}" ]; then
        $AWS s3 rb "s3://${BUCKET}" --force >/dev/null 2>&1 \
            && ok "deleted s3 bucket" || warn "s3 bucket not deleted"
    fi

    mv "$STATE_FILE" "${STATE_FILE}.destroyed" 2>/dev/null
    printf '\nteardown complete. verify with:\n  %s list --region %s\n' "$0" "$REGION"
}

# --------------------------------------------------------------------------
# list — show anything still tagged for this test
# --------------------------------------------------------------------------
do_list() {
    require_account
    log "EC2 instances (non-terminated):"
    $AWS ec2 describe-instances --region "$REGION" \
        --filters "Name=tag:Purpose,Values=elder-scan-test" \
        --query 'Reservations[].Instances[?State.Name!=`terminated`].[InstanceId,State.Name]' \
        --output text 2>/dev/null
    log "S3 buckets:";        $AWS s3api list-buckets --query "Buckets[?starts_with(Name,'${PREFIX}')].Name" --output text 2>/dev/null
    log "DynamoDB tables:";   $AWS dynamodb list-tables --region "$REGION" --query "TableNames[?starts_with(@,'${PREFIX}')]" --output text 2>/dev/null
    log "SQS queues:";        $AWS sqs list-queues --region "$REGION" --queue-name-prefix "$PREFIX" --query 'QueueUrls' --output text 2>/dev/null
    log "SNS topics:";        $AWS sns list-topics --region "$REGION" --query "Topics[?contains(TopicArn,'${PREFIX}')].TopicArn" --output text 2>/dev/null
    log "ECR repos:";         $AWS ecr describe-repositories --region "$REGION" --query "repositories[?starts_with(repositoryName,'${PREFIX}')].repositoryName" --output text 2>/dev/null
    log "Log groups:";        $AWS logs describe-log-groups --region "$REGION" --log-group-name-prefix "$PREFIX" --query 'logGroups[].logGroupName' --output text 2>/dev/null
    log "Lambda functions:";  $AWS lambda list-functions --region "$REGION" --query "Functions[?starts_with(FunctionName,'${PREFIX}')].FunctionName" --output text 2>/dev/null
    log "State machines:";    $AWS stepfunctions list-state-machines --region "$REGION" --query "stateMachines[?starts_with(name,'${PREFIX}')].name" --output text 2>/dev/null
    log "EventBridge rules:"; $AWS events list-rules --region "$REGION" --name-prefix "$PREFIX" --query 'Rules[].Name' --output text 2>/dev/null
    log "API Gateway APIs:";  $AWS apigateway get-rest-apis --region "$REGION" --query "items[?starts_with(name,'${PREFIX}')].id" --output text 2>/dev/null
    log "EFS filesystems:";   $AWS efs describe-file-systems --region "$REGION" --query "FileSystems[?starts_with(CreationToken,'${PREFIX}')].FileSystemId" --output text 2>/dev/null
    log "IAM roles:";         $AWS iam list-roles --query "Roles[?starts_with(RoleName,'${PREFIX}')].RoleName" --output text 2>/dev/null
    log "Security groups:";   $AWS ec2 describe-security-groups --region "$REGION" --query "SecurityGroups[?starts_with(GroupName,'${PREFIX}')].GroupId" --output text 2>/dev/null
}

case "$ACTION" in
    create)  do_create ;;
    destroy) do_destroy ;;
    list)    do_list ;;
    *) echo "usage: $0 {create|destroy|list} [--region REGION]" >&2; exit 2 ;;
esac
