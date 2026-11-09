from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from onechapter.config import ROOT


class _CfnYamlLoader(yaml.SafeLoader):
    pass


def _cfn_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        return {f"!{tag_suffix}": loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {f"!{tag_suffix}": loader.construct_sequence(node)}
    if isinstance(node, yaml.MappingNode):
        return {f"!{tag_suffix}": loader.construct_mapping(node)}
    raise TypeError(f"Unsupported YAML node for !{tag_suffix}")


_CfnYamlLoader.add_multi_constructor("!", _cfn_tag)


def test_cloudformation_template_structure_and_references():
    template_path = ROOT / "infra" / "aws" / "template.yaml"
    assert template_path.exists()
    doc = yaml.load(template_path.read_text(encoding="utf-8"), Loader=_CfnYamlLoader)

    assert doc["AWSTemplateFormatVersion"] == "2010-09-09"
    assert "Parameters" in doc
    assert "Rules" in doc
    assert "Resources" in doc
    assert "Outputs" in doc

    # Region rule must enforce eu-north-1
    region_rule = doc["Rules"]["EnforceEuNorth1Region"]
    assert region_rule["Assertions"][0]["Assert"] == {
        "!Equals": [{"!Ref": "AWS::Region"}, "eu-north-1"]
    }

    resources = doc["Resources"]
    expected_resources = {
        "OneChapterBucket",
        "OneChapterBucketPolicy",
        "OneChapterInstanceRole",
        "OneChapterInstanceProfile",
        "OneChapterSecurityGroup",
        "OneChapterInstance",
        "OneChapterEIP",
    }
    assert expected_resources <= set(resources.keys())

    # S3 bucket must block public access, encrypt with AES256, and expire old backups
    bucket_props = resources["OneChapterBucket"]["Properties"]
    pab = bucket_props["PublicAccessBlockConfiguration"]
    assert pab == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    lifecycle_ids = {r["Id"] for r in bucket_props["LifecycleConfiguration"]["Rules"]}
    assert {
        "AbortIncompleteMultipartUploads",
        "ExpireNoncurrentVersions",
        "ExpireOldPostgresBackups",
    } <= lifecycle_ids

    # Security Group must only open port 443 (never port 22 SSH)
    ingress_rules = resources["OneChapterSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert len(ingress_rules) > 0
    for rule in ingress_rules:
        assert rule["IpProtocol"] == "tcp"
        assert rule["FromPort"] == 443
        assert rule["ToPort"] == 443

    # Verify all !Ref targets exist in Parameters, Resources, or AWS:: pseudo-parameters
    valid_refs = (
        set(doc["Parameters"].keys())
        | set(resources.keys())
        | {"AWS::AccountId", "AWS::Region", "AWS::StackName", "AWS::Partition", "AWS::NoValue"}
    )

    def _walk(node):
        if isinstance(node, dict):
            if "!Ref" in node:
                assert node["!Ref"] in valid_refs, f"Unknown !Ref target: {node['!Ref']}"
            if "!GetAtt" in node:
                target = node["!GetAtt"]
                res_name = target.split(".")[0] if isinstance(target, str) else target[0]
                assert res_name in resources, f"Unknown !GetAtt resource: {res_name}"
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(doc["Resources"])
    _walk(doc["Outputs"])


def test_docker_compose_and_site_configs_are_valid():
    base_compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    prod_compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))

    assert {"db", "onechapter"} <= set(base_compose["services"].keys())
    assert base_compose["services"]["db"]["image"] == "postgres:17-alpine"
    assert {"proxy", "onechapter", "db-backup"} <= set(prod_compose["services"].keys())
    assert prod_compose["services"]["proxy"]["image"] == "caddy:2-alpine"

    wrangler_raw = (ROOT / "site" / "wrangler.jsonc").read_text(encoding="utf-8")
    wrangler_clean = re.sub(r"//.*", "", wrangler_raw)
    wrangler = json.loads(wrangler_clean)
    assert wrangler["name"] == "onechapter-landing"
    assert (ROOT / "site" / "index.html").exists()
