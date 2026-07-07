"""Single-stack read+write plane for ``schema.keri.host``.

This stack OWNS everything so all references are intra-stack (acyclic):

  1. the publish Service-AID (write plane) — a ``ServiceAidFunction``;
  2. a dedicated, private S3 content-addressed store (CAS) — CDK-generated name;
  3. CloudFront path-routed in front of both:
       - default behavior: GET /oobi/<said> -> S3 (via Origin Access Control),
         application/schema+json, long-cached, trustless;
       - /schema/*: POST CESR -> the publish Service-AID's API Gateway, uncached.

Why one stack (not two): the read-source and the write-target MUST be the SAME
bucket — CloudFront serves exactly the objects the publish Lambda writes. A
two-stack split (SchemaHost takes the service's ``write_api`` AND the service
Lambda references SchemaHost's CAS bucket for its ``SERVICEAID_CAS_BUCKET`` env +
write-grant) is a CIRCULAR STACK DEPENDENCY and ``cdk synth`` fails. Owning both
the ServiceAidFunction and the bucket here makes the bucket<->Lambda wiring
intra-stack, so it is acyclic.

The bucket is the CAS: BLOCK_ALL public access + S3-managed encryption + RETAIN.
CloudFront reads it only via Origin Access Control (OAC) — no public bucket
policy, no OAI. Writes never touch S3 directly; they go through the write API
origin so publication stays a KERI exn/CESR operation the Service-AID validates,
which THEN writes to the CAS (the Lambda holds ``s3:PutObject`` on this bucket).

CloudFront requires its ACM certificate in us-east-1; this stack is deployed
there.
"""
import json

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

try:
    from .service_aid import ServiceAidFunction
except ImportError:  # pragma: no cover
    from keri_cdk.service_aid import ServiceAidFunction


class SchemaHostStack(Stack):
    """Single-stack S3 CAS + CloudFront path-routed read plane + the publish
    Service-AID (write plane) for ``schema.keri.host``.

    Exposes ``self.svcfn`` (the ``ServiceAidFunction``), ``self.bucket`` (the CAS
    bucket, grantable) and ``self.distribution`` (the CloudFront distribution).
    """

    def __init__(
        self,
        scope: Construct,
        cid: str,
        *,
        alias: str,
        core_table,
        compute_code,
        handler_ref: str = "service:svc",
        domain_name: str,
        hosted_zone_id: str,
        witnesses=None,
        toad: int = 0,
        allowlist=None,
        **kw,
    ):
        super().__init__(scope, cid, **kw)

        # --- 1. the publish Service-AID (write plane), in THIS stack ----------
        # The developer's compute_code (schema_host_handler.py) + the two
        # framework layers over the pooled core table. The allowlist of
        # authorized publisher AIDs is injected via the framework env (an empty
        # list means any verified sender — the compute_code default).
        self.svcfn = ServiceAidFunction(
            self,
            "Publisher",
            alias=alias,
            core_table=core_table,
            compute_code=compute_code,
            handler_ref=handler_ref,
            witnesses=witnesses or [],
            toad=toad,
            environment={"SERVICEAID_ALLOWLIST": json.dumps(allowlist or [])},
        )

        # --- 2. the dedicated CAS bucket (CDK-generated name — no collision) --
        # Private CAS: no public access, S3-managed encryption, RETAIN so a
        # stack teardown never destroys published schemas. Read access is
        # granted to CloudFront only, via OAC (wired below). No fixed
        # table/bucket name → no cross-account/region name collision.
        self.bucket = s3.Bucket(
            self,
            "SchemaCas",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # --- 3. wire the Lambda to write THIS bucket (read-target == write-src)
        # Intra-stack references (no cross-stack Export/Import, no cycle): the
        # publish Lambda writes CAS objects to exactly the bucket CloudFront
        # serves. add_environment works post-construction on the Function.
        self.svcfn.function.add_environment("SERVICEAID_CAS_BUCKET",
                                            self.bucket.bucket_name)
        self.bucket.grant_put(self.svcfn.function)

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
        # Write origin: the publish Service-AID's API Gateway (intra-stack).
        api_origin = origins.RestApiOrigin(self.svcfn.api)

        # --- 4. CloudFront distribution ---------------------------------------
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
