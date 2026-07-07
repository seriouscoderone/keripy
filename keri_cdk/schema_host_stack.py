"""Read-plane for schema.keri.host: an S3 content-addressed store fronted by
CloudFront. GET /oobi/<said> -> S3 object oobi/<said> (application/schema+json,
long-cached, trustless). /schema/* -> the write API (the publish Service-AID),
POST allowed, CESR forwarded, uncached. One hostname, path-routed.

The bucket is the CAS: BLOCK_ALL public access + S3-managed encryption + RETAIN.
CloudFront reads it only via Origin Access Control (OAC) — no public bucket
policy, no OAI. Writes never touch S3 directly; they go through the write API
origin so publication stays a KERI exn/CESR operation the Service-AID validates.

CloudFront requires its ACM certificate in us-east-1; this stack is deployed
there.
"""
from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_s3 as s3,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_certificatemanager as acm,
    aws_route53 as r53,
    aws_route53_targets as targets,
)
from constructs import Construct


class SchemaHostStack(Stack):
    """S3 CAS + CloudFront path-routed read plane for ``schema.keri.host``.

    Exposes ``self.bucket`` (the CAS bucket, grantable) and ``self.distribution``
    (the CloudFront distribution).
    """

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        domain_name: str,
        hosted_zone_id: str,
        write_api,
        **kw,
    ):
        super().__init__(scope, cid, **kw)

        # --- S3 content-addressed store ---------------------------------------
        # Private CAS: no public access, S3-managed encryption, RETAIN so a
        # stack teardown never destroys published schemas. Read access is
        # granted to CloudFront only, via OAC (wired below).
        self.bucket = s3.Bucket(
            self,
            "SchemaCas",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- Hosted zone (synth-safe via from_hosted_zone_attributes) ---------
        hosted_zone = r53.HostedZone.from_hosted_zone_attributes(
            self,
            "HostedZone",
            hosted_zone_id=hosted_zone_id,
            zone_name=".".join(domain_name.split(".")[-2:]),
        )

        # --- ACM certificate (DNS-validated; MUST be us-east-1 for CloudFront) -
        cert = acm.Certificate(
            self,
            "SchemaCert",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
        )

        # --- Origins -----------------------------------------------------------
        # S3 read origin via Origin Access Control (OAC): CDK adds the OAC + the
        # bucket policy that lets only this distribution GetObject; the bucket
        # itself stays fully private.
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(self.bucket)
        # Write origin: the publish Service-AID's API Gateway.
        api_origin = origins.RestApiOrigin(write_api)

        # --- CloudFront distribution ------------------------------------------
        distribution = cf.Distribution(
            self,
            "SchemaDist",
            domain_names=[domain_name],
            certificate=cert,
            # Default behavior (covers GET /oobi/<said>): S3 origin, long-cached,
            # HTTPS-only.
            default_behavior=cf.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_OPTIMIZED,
            ),
            additional_behaviors={
                # Write path: POST CESR to the Service-AID. ALLOW_ALL enables
                # POST; caching disabled; forward viewer headers/body (except the
                # Host header, which CloudFront must set to the API GW origin).
                "/schema/*": cf.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                    cache_policy=cf.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
        )
        self.distribution = distribution

        # --- Route53 A-record alias -------------------------------------------
        r53.ARecord(
            self,
            "SchemaDnsRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=r53.RecordTarget.from_alias(
                targets.CloudFrontTarget(distribution)
            ),
        )
