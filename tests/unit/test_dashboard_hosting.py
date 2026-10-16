"""Checks the dashboard hosting added to template.yaml (private bucket, CloudFront, CORS).

None of this can be tried without a deploy, and a mistake is expensive: a public bucket, a
circular dependency (CloudFormation only finds it while deploying), or a policy that blocks the
dashboard from its own API. So the template is checked here instead.
"""

import re
from typing import Any

import pytest
from tests.unit.test_template import FUNCTIONS, RESOURCES, TEMPLATE

STACK, ACCOUNT, REGION = "gradeflow", "123456789012", "ap-south-1"
PSEUDO = {"AWS::StackName": STACK, "AWS::AccountId": ACCOUNT, "AWS::Region": REGION}

BUCKET = RESOURCES["DashboardBucket"]["Properties"]
POLICY = RESOURCES["DashboardBucketPolicy"]["Properties"]
OAC = RESOURCES["DashboardOriginAccessControl"]["Properties"]["OriginAccessControlConfig"]
HEADERS = RESOURCES["DashboardHeaders"]["Properties"]["ResponseHeadersPolicyConfig"]
DISTRIBUTION = RESOURCES["DashboardDistribution"]["Properties"]["DistributionConfig"]
RAW_CORS = RESOURCES["RawBucket"]["Properties"]["CorsConfiguration"]["CorsRules"]

CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"  # AWS managed cache policies
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


def expand(text: str) -> str:
    """Fill in ${AWS::Region} and friends, so a Sub string can be read like the real value."""
    return re.sub(r"\$\{([^}]+)\}", lambda m: PSEUDO.get(m.group(1), m.group(0)), text)


# --- no circular dependencies ----------------------------------------------------------


def references(node: Any) -> set[str]:
    """Names of the resources a piece of template refers to (Ref, GetAtt, Sub, DependsOn)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "Ref" and isinstance(value, str):
                found.add(value)
            elif key == "GetAtt":
                name = value if isinstance(value, str) else value[0]
                found.add(name.split(".")[0])
            elif key == "Sub":
                text = value if isinstance(value, str) else value[0]
                found.update(m.split(".")[0] for m in re.findall(r"\$\{([^}!]+)\}", text))
            elif key == "DependsOn":
                found.update([value] if isinstance(value, str) else value)
            found |= references(value)
    elif isinstance(node, list):
        for item in node:
            found |= references(item)
    return found & set(RESOURCES)


def dependency_graph() -> dict[str, set[str]]:
    """What each resource needs to exist first, as CloudFormation will see it after SAM runs.

    SAM turns every function event into a separate resource (a permission, an event source
    mapping) that depends on the function AND on the thing the event names. So an event does
    not make the function depend on its API or queue, and its references are left out here.
    """
    graph = {}
    for name, resource in RESOURCES.items():
        properties = {k: v for k, v in resource.get("Properties", {}).items() if k != "Events"}
        graph[name] = references({**resource, "Properties": properties}) - {name}
    # SAM builds an API's definition from the functions that serve it: the API needs those
    # functions to exist first, although the template only shows the function naming the API.
    for function_name, properties in FUNCTIONS.items():
        for event in properties.get("Events", {}).values():
            api = event["Properties"].get("RestApiId", {}).get("Ref")
            if api in graph:
                graph[api].add(function_name)
    return graph


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = on the current path, 2 = fully explored

    def visit(name: str, path: list[str]) -> list[str] | None:
        state[name] = 1
        for dependency in sorted(graph[name]):
            if state.get(dependency) == 1:
                return (
                    [*path[path.index(dependency) :], name, dependency]
                    if dependency in path
                    else [name, dependency]
                )
            if dependency not in state and (cycle := visit(dependency, [*path, name])):
                return cycle
        state[name] = 2
        return None

    for name in sorted(graph):
        if name not in state and (cycle := visit(name, [])):
            return cycle
    return None


def test_the_template_has_no_circular_dependency() -> None:
    assert find_cycle(dependency_graph()) is None


def test_the_cycle_finder_really_finds_a_cycle() -> None:
    """The check above is only worth something if it can fail."""
    assert find_cycle({"A": {"B"}, "B": {"C"}, "C": {"A"}}) is not None
    assert find_cycle({"A": {"B"}, "B": {"C"}, "C": set()}) is None


def test_the_headers_policy_does_not_name_the_api() -> None:
    """Naming the API there would close the loop API -> Lambda -> raw bucket -> CORS -> distribution
    -> headers policy -> API."""
    assert "AnalyticsApi" not in references(HEADERS)
    assert "RawBucket" not in references(HEADERS)


# --- the site bucket is private ---------------------------------------------------------


def test_the_dashboard_bucket_blocks_all_public_access_and_is_encrypted() -> None:
    assert BUCKET["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    rule = BUCKET["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
    assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"


def test_only_this_distribution_may_read_the_bucket_and_only_to_read() -> None:
    (statement,) = POLICY["PolicyDocument"]["Statement"]
    assert POLICY["Bucket"] == {"Ref": "DashboardBucket"}
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == {"Service": "cloudfront.amazonaws.com"}  # never "*"
    assert statement["Action"] == "s3:GetObject"  # no list, no write, no delete
    assert statement["Resource"] == {"Sub": "${DashboardBucket.Arn}/*"}
    source_arn = statement["Condition"]["StringEquals"]["AWS:SourceArn"]["Sub"]
    assert source_arn.endswith("distribution/${DashboardDistribution}")  # THIS distribution
    assert "${AWS::AccountId}" in source_arn


def test_the_distribution_reads_the_bucket_with_origin_access_control() -> None:
    (origin,) = DISTRIBUTION["Origins"]
    assert origin["DomainName"] == {"GetAtt": "DashboardBucket.RegionalDomainName"}
    assert origin["OriginAccessControlId"] == {"GetAtt": "DashboardOriginAccessControl.Id"}
    assert origin["S3OriginConfig"] == {"OriginAccessIdentity": ""}  # not the legacy identity
    assert OAC["OriginAccessControlOriginType"] == "s3"
    assert OAC["SigningBehavior"] == "always"
    assert OAC["SigningProtocol"] == "sigv4"


# --- the distribution ------------------------------------------------------------------

BEHAVIORS = [DISTRIBUTION["DefaultCacheBehavior"], *DISTRIBUTION["CacheBehaviors"]]


@pytest.mark.parametrize("behavior", BEHAVIORS, ids=lambda b: b.get("PathPattern", "default"))
def test_every_behavior_is_https_only_read_only_and_adds_the_security_headers(
    behavior: dict[str, Any],
) -> None:
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    assert behavior["AllowedMethods"] == ["GET", "HEAD"]
    assert behavior["ResponseHeadersPolicyId"] == {"Ref": "DashboardHeaders"}
    assert behavior["TargetOriginId"] == DISTRIBUTION["Origins"][0]["Id"]


def test_assets_are_cached_but_config_json_never_is() -> None:
    """config.json holds the API address and key: a stale copy would keep using an old key."""
    assert DISTRIBUTION["DefaultCacheBehavior"]["CachePolicyId"] == CACHING_OPTIMIZED
    (config_behavior,) = DISTRIBUTION["CacheBehaviors"]
    assert config_behavior["PathPattern"] == "config.json"
    assert config_behavior["CachePolicyId"] == CACHING_DISABLED


def test_the_distribution_serves_index_html_cheaply_and_has_no_error_page_rewrites() -> None:
    assert DISTRIBUTION["Enabled"] is True
    assert DISTRIBUTION["DefaultRootObject"] == "index.html"
    assert DISTRIBUTION["PriceClass"] == "PriceClass_100"
    # A rewrite of 403/404 to index.html would turn a missing config.json into an HTML page.
    assert "CustomErrorResponses" not in DISTRIBUTION


# --- security headers and the content security policy ------------------------------------


def csp() -> dict[str, list[str]]:
    policy = HEADERS["SecurityHeadersConfig"]["ContentSecurityPolicy"]["ContentSecurityPolicy"]
    directives = {}
    for part in expand(policy["Sub"]).split(";"):
        name, *sources = part.split()
        directives[name] = sources
    return directives


def test_the_basic_security_headers_are_set_and_override_the_origin() -> None:
    headers = HEADERS["SecurityHeadersConfig"]
    assert headers["StrictTransportSecurity"]["AccessControlMaxAgeSec"] >= 31536000
    assert headers["ContentTypeOptions"]["Override"] is True
    assert headers["FrameOptions"]["FrameOption"] == "DENY"
    assert headers["ReferrerPolicy"]["ReferrerPolicy"] == "no-referrer"
    assert headers["ContentSecurityPolicy"]["Override"] is True


def test_the_csp_runs_only_the_sites_own_scripts() -> None:
    policy = csp()
    assert policy["default-src"] == ["'none'"]
    assert policy["script-src"] == ["'self'"]  # no unsafe-inline, no unsafe-eval, no CDN
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["base-uri"] == ["'none'"]
    assert policy["form-action"] == ["'none'"]
    assert "'unsafe-eval'" not in {s for sources in policy.values() for s in sources}


def test_the_csp_lets_the_dashboard_reach_its_api_and_the_upload_bucket_only() -> None:
    sources = csp()["connect-src"]
    assert "'self'" in sources  # config.json
    assert f"https://*.execute-api.{REGION}.amazonaws.com" in sources
    assert "*" not in sources and "https:" not in sources
    raw_name = expand(RESOURCES["RawBucket"]["Properties"]["BucketName"]["Sub"])
    assert f"https://{raw_name}.s3.{REGION}.amazonaws.com" in sources  # same name as the bucket


def test_the_csp_lets_styles_be_inline_but_scripts_never() -> None:
    policy = csp()
    assert "'unsafe-inline'" in policy["style-src"]  # the charts set style attributes
    assert "'unsafe-inline'" not in policy["script-src"]


# --- uploading from the browser: CORS on the raw bucket ------------------------------------


def test_the_raw_bucket_allows_browser_posts_from_the_dashboard_only() -> None:
    (rule,) = RAW_CORS
    assert rule["AllowedMethods"] == ["POST"]  # uploads are POSTs; no GET/PUT/DELETE from a page
    branches = rule["AllowedOrigins"]["If"]
    assert branches[0] == "HasDashboardDevOrigin"
    deployed = {"Sub": "https://${DashboardDistribution.DomainName}"}
    assert branches[1] == [deployed, {"Ref": "DashboardDevOrigin"}]
    assert branches[2] == [deployed]  # with the dev origin switched off, only the deployed site
    flat = str(branches)
    assert "'*'" not in flat  # never a wildcard origin


def test_the_dev_origin_is_a_parameter_that_can_be_switched_off_and_cannot_be_a_wildcard() -> None:
    parameter = TEMPLATE["Parameters"]["DashboardDevOrigin"]
    assert parameter["Default"] == "http://localhost:5173"  # Vite's dev server address
    pattern = re.compile(parameter["AllowedPattern"])
    for fine in ["", "http://localhost:5173", "https://dashboard.example.com"]:
        assert pattern.fullmatch(fine), fine
    for bad in [
        "*",
        "https://*.example.com",
        "http://localhost:5173/",
        "localhost:5173",
        "http://a b",
    ]:
        assert not pattern.fullmatch(bad), bad
    assert TEMPLATE["Conditions"]["HasDashboardDevOrigin"] == {
        "Not": [{"Equals": [{"Ref": "DashboardDevOrigin"}, ""]}]
    }


# --- outputs -------------------------------------------------------------------------------


def test_the_outputs_give_the_address_the_bucket_and_the_distribution() -> None:
    outputs = TEMPLATE["Outputs"]
    assert outputs["DashboardUrl"]["Value"] == {
        "Sub": "https://${DashboardDistribution.DomainName}"
    }
    assert outputs["DashboardBucketName"]["Value"] == {"Ref": "DashboardBucket"}
    assert outputs["DashboardDistributionId"]["Value"] == {"Ref": "DashboardDistribution"}


def test_no_other_bucket_became_public_or_lost_its_protection() -> None:
    for name, resource in RESOURCES.items():
        if resource["Type"] == "AWS::S3::Bucket":
            access = resource["Properties"]["PublicAccessBlockConfiguration"]
            assert all(access.values()) and len(access) == 4, name
